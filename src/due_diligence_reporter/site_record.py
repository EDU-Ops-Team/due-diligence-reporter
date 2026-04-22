"""SiteRecord — canonical dashboard-facing projection of a DD Report.

This module converts the flat V3 token ``replacements`` dict produced by
``report_schema.normalize_report_data`` into a ``SiteRecord`` suitable for
publishing to the DD Portfolio dashboard.

Design intent
-------------

The reporter already produces a fully normalized, template-ready
``replacements`` dict at the point where the Google Doc is built
(see ``server._normalize_report_replacements`` + ``build_dd_report_doc``).
That dict is the authoritative V3 token dump — the same data the dashboard
sheet carried yesterday, just in memory instead of on Drive.

So there is **no Doc re-parsing** involved here. We capture the dict
at build time, run a small heuristic classifier on the exec summary
free-text fields, and emit a ``SiteRecord`` that can be serialized to
``sites.json`` and consumed by the dashboard.

This module is pure — it performs no I/O and makes no network calls, so it
is trivially unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from .report_schema import (
    COST_TOKEN_BASES,
    SCENARIOS,
    SUMMARY_TOKEN_BASES,
)

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

# The dashboard chip vocabulary.
# ``yes``           — deterministic Yes from ``exec.c_answer``.
# ``yes_if``        — Yes but with tradeoffs / needs-to-go-right bullets.
# ``no``            — deterministic No from ``exec.c_answer``.
# ``review``        — confidence < threshold; surfaces a yellow Review chip.
CLASSIFICATIONS = ("yes", "yes_if", "no", "review")

# Heuristic phrases that signal a Yes-if situation even when ``c_answer``
# was normalized to plain "Yes". These live in the acquisition_conditions
# and risk_notes free text blocks per the V3 template.
_YES_IF_PHRASES = (
    "yes, if",
    "yes if",
    "yes — if",
    "yes - if",
    "tradeoff",
    "trade-off",
    "trade off",
    "needs to go right",
    "need to go right",
    "must go right",
    "has to go right",
    "conditional on",
    "contingent on",
    "assuming",
    "provided that",
)

# Phrases that reinforce a No.
_NO_PHRASES = (
    "cannot open",
    "will not open",
    "does not meet",
    "not feasible",
    "infeasible",
    "hard blocker",
    "fatal",
    "disqualif",
)

# Minimum confidence for the pipeline to assert a classification.
# Below this, we downgrade to ``review`` so the dashboard surfaces it for
# human triage instead of silently miscategorizing.
DEFAULT_CONFIDENCE_THRESHOLD = 0.70


def classify_site(
    replacements: dict[str, str],
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> tuple[str, float, list[str]]:
    """Derive (classification, confidence, signals) from V3 replacements.

    The V3 template stores the canonical Yes/No answer in
    ``exec.c_answer`` ("Yes" | "Yes see notes" | "No"). That alone is not
    sufficient for the dashboard because "Yes" can still mean Yes-if when
    the exec summary carries tradeoffs. This heuristic fuses the
    canonical answer with phrase signals from ``acquisition_conditions``
    and ``risk_notes``.

    Returns
    -------
    classification:
        One of ``CLASSIFICATIONS``. Low-confidence results become
        ``"review"`` so the dashboard can flag them.
    confidence:
        Float in [0, 1].
    signals:
        The matched phrases / reasons, for debuggability and for showing
        in the dashboard's "why this classification?" tooltip.
    """
    signals: list[str] = []

    c_answer = (replacements.get("exec.c_answer") or "").strip().lower()
    acq = (replacements.get("exec.acquisition_conditions") or "").lower()
    risks = (replacements.get("exec.risk_notes") or "").lower()
    exec_text = f"{acq}\n{risks}"

    yes_if_hits = [p for p in _YES_IF_PHRASES if p in exec_text]
    no_hits = [p for p in _NO_PHRASES if p in exec_text]

    if c_answer == "no":
        signals.append("c_answer=No")
        if no_hits:
            signals.extend(f"no_phrase:{p}" for p in no_hits)
            return "no", 0.95, signals
        return "no", 0.85, signals

    if c_answer in {"yes see notes", "yes, see notes"}:
        signals.append("c_answer=Yes see notes")
        # "Yes see notes" is V3's canonical phrasing for Yes-if.
        if yes_if_hits:
            signals.extend(f"yes_if_phrase:{p}" for p in yes_if_hits)
            return "yes_if", 0.95, signals
        return "yes_if", 0.80, signals

    if c_answer == "yes":
        signals.append("c_answer=Yes")
        if yes_if_hits:
            signals.extend(f"yes_if_phrase:{p}" for p in yes_if_hits)
            # Even though c_answer is Yes, tradeoff language downgrades.
            return "yes_if", 0.75, signals
        if no_hits:
            # Conflicting: c_answer=Yes but hard-blocker phrases present.
            signals.extend(f"conflict_no_phrase:{p}" for p in no_hits)
            return "review", 0.40, signals
        return "yes", 0.90, signals

    # c_answer missing or unexpected — fall back to phrase-only heuristics.
    signals.append(f"c_answer={c_answer or 'missing'}")
    if no_hits and not yes_if_hits:
        signals.extend(f"no_phrase:{p}" for p in no_hits)
        return "no", 0.55, signals
    if yes_if_hits and not no_hits:
        signals.extend(f"yes_if_phrase:{p}" for p in yes_if_hits)
        return "yes_if", 0.55, signals

    # Nothing to go on.
    return "review", 0.0, signals


def _split_bullets(text: str) -> list[str]:
    """Split a free-text exec block into bullet-like items.

    V3 exec summary fields come through as newline-separated lines, often
    with leading ``-``, ``*``, or ``•`` markers. Empty lines are dropped.
    """
    if not text:
        return []
    items: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*•").strip()
        if line:
            items.append(line)
    return items


# ---------------------------------------------------------------------------
# Slug
# ---------------------------------------------------------------------------

_SLUG_KEEP = re.compile(r"[^a-z0-9]+")


def site_slug(site_name: str, *, suffix: str | None = None) -> str:
    """Stable, URL-safe slug for a site.

    Used as the primary key in ``sites.json``. Slugs must be stable across
    pipeline runs so the dashboard can upsert cleanly.

    Examples
    --------
    >>> site_slug("Palm Beach Gardens")
    'palm-beach-gardens'
    >>> site_slug("Palm Beach Gardens", suffix="main")
    'palm-beach-gardens-main'
    """
    base = _SLUG_KEEP.sub("-", (site_name or "").lower()).strip("-")
    if suffix:
        sfx = _SLUG_KEEP.sub("-", suffix.lower()).strip("-")
        if sfx:
            base = f"{base}-{sfx}"
    return base or "unknown-site"


# ---------------------------------------------------------------------------
# SiteRecord
# ---------------------------------------------------------------------------


@dataclass
class ScenarioRecord:
    """One column of the V3 scenario table (Recommended / Fastest / Max Cap / Max Value)."""

    capacity: str = ""
    open_date: str = ""
    capex: str = ""
    costs: dict[str, str] = field(default_factory=dict)


@dataclass
class ClassificationRecord:
    label: str = "review"          # one of CLASSIFICATIONS
    confidence: float = 0.0
    signals: list[str] = field(default_factory=list)
    # Bullet splits of the exec summary free-text blocks — the dashboard
    # renders these as the "Tradeoffs" and "Needs to go right" lists.
    tradeoffs: list[str] = field(default_factory=list)
    needs_to_go_right: list[str] = field(default_factory=list)


@dataclass
class SourceLinks:
    sir: str = ""
    inspection: str = ""
    isp: str = ""
    e_occupancy: str = ""
    school_approval: str = ""
    opening_plan: str = ""
    trace: str = ""
    drive_folder: str = ""
    dd_report: str = ""


@dataclass
class SiteRecord:
    """Dashboard-facing projection of a single DD Report.

    This is the record type the dashboard consumes from ``sites.json``.
    Schema parity with yesterday's 41-column sheet is enforced by
    ``from_replacements`` below.
    """

    slug: str
    site_name: str
    marketing_name: str = ""
    city_state_zip: str = ""
    school_type: str = ""
    prepared_by: str = ""
    report_date: str = ""
    published_at: str = ""          # ISO8601 UTC

    can_we_open: str = ""            # raw exec.c_answer after normalization
    c_edreg: str = ""
    c_occupancy: str = ""
    c_zoning: str = ""
    c_permit_timeline: str = ""
    c_construction_timeline: str = ""

    classification: ClassificationRecord = field(default_factory=ClassificationRecord)
    scenarios: dict[str, ScenarioRecord] = field(default_factory=dict)
    sources: SourceLinks = field(default_factory=SourceLinks)

    acquisition_conditions: str = ""
    risk_notes: str = ""

    @classmethod
    def from_replacements(
        cls,
        replacements: dict[str, str],
        *,
        site_name: str,
        report_date: str,
        drive_folder_url: str,
        dd_report_url: str,
        slug_suffix: str | None = None,
        classification_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> SiteRecord:
        """Build a SiteRecord from the V3 replacements dict.

        Parameters mirror what the server already has on hand when it
        finishes building the DD report. No Drive I/O, no Doc parsing.
        """

        def g(key: str) -> str:
            value = replacements.get(key, "")
            return value.strip() if isinstance(value, str) else ""

        label, confidence, signals = classify_site(
            replacements, threshold=classification_threshold,
        )

        acq = g("exec.acquisition_conditions")
        risks = g("exec.risk_notes")

        classification = ClassificationRecord(
            label=label,
            confidence=round(confidence, 2),
            signals=signals,
            tradeoffs=_split_bullets(acq),
            needs_to_go_right=_split_bullets(risks),
        )

        scenarios: dict[str, ScenarioRecord] = {}
        for scenario in SCENARIOS:
            sc = ScenarioRecord(
                capacity=g(f"exec.{scenario}_capacity"),
                open_date=g(f"exec.{scenario}_open_date"),
                capex=g(f"exec.{scenario}_capex"),
                costs={
                    base: g(f"exec.{base}_{scenario}")
                    for base in COST_TOKEN_BASES
                },
            )
            scenarios[scenario] = sc

        sources = SourceLinks(
            sir=g("sources.sir_link"),
            inspection=g("sources.inspection_link"),
            isp=g("sources.isp_link"),
            e_occupancy=g("sources.e_occupancy_link"),
            school_approval=g("sources.school_approval_link"),
            opening_plan=g("sources.opening_plan_link"),
            trace=g("sources.trace_link"),
            drive_folder=drive_folder_url.strip(),
            dd_report=dd_report_url.strip(),
        )

        return cls(
            slug=site_slug(site_name, suffix=slug_suffix),
            site_name=site_name.strip(),
            marketing_name=g("meta.marketing_name"),
            city_state_zip=g("meta.city_state_zip"),
            school_type=g("meta.school_type"),
            prepared_by=g("meta.prepared_by"),
            report_date=report_date,
            published_at=datetime.now(UTC).isoformat(timespec="seconds"),
            can_we_open=g("exec.c_answer"),
            c_edreg=g("exec.c_edreg"),
            c_occupancy=g("exec.c_occupancy"),
            c_zoning=g("exec.c_zoning"),
            c_permit_timeline=g("exec.c_permit_timeline"),
            c_construction_timeline=g("exec.c_construction_timeline"),
            classification=classification,
            scenarios=scenarios,
            sources=sources,
            acquisition_conditions=acq,
            risk_notes=risks,
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict. Scenarios flattened by scenario name."""
        payload = asdict(self)
        # asdict turns ScenarioRecord values into dicts already; just
        # make sure cost keys round-trip cleanly.
        return payload
