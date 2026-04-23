"""Dashboard-facing projection of a DD report."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from .report_schema import COST_TOKEN_BASES, SCENARIOS

CLASSIFICATIONS = ("yes", "yes_if", "no", "review")

_YES_IF_PHRASES = (
    "yes, if",
    "yes if",
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

DEFAULT_CONFIDENCE_THRESHOLD = 0.70
_SLUG_KEEP = re.compile(r"[^a-z0-9]+")


def _finalize_classification(
    label: str,
    confidence: float,
    signals: list[str],
    threshold: float,
) -> tuple[str, float, list[str]]:
    if label != "review" and confidence < threshold:
        return "review", confidence, [*signals, f"below_threshold:{threshold:.2f}"]
    return label, confidence, signals


def classify_site(
    replacements: dict[str, str],
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> tuple[str, float, list[str]]:
    """Classify dashboard status from normalized DD replacements."""
    signals: list[str] = []

    c_answer = (replacements.get("exec.c_answer") or "").strip().lower()
    acquisition_conditions = (replacements.get("exec.acquisition_conditions") or "").lower()
    tradeoffs = (replacements.get("exec.tradeoffs_and_deficiencies") or "").lower()
    exec_text = f"{acquisition_conditions}\n{tradeoffs}"

    yes_if_hits = [phrase for phrase in _YES_IF_PHRASES if phrase in exec_text]
    no_hits = [phrase for phrase in _NO_PHRASES if phrase in exec_text]

    if c_answer == "no":
        signals.append("c_answer=No")
        if no_hits:
            signals.extend(f"no_phrase:{phrase}" for phrase in no_hits)
            return _finalize_classification("no", 0.95, signals, threshold)
        return _finalize_classification("no", 0.85, signals, threshold)

    if c_answer in {"yes see notes", "yes, see notes"}:
        signals.append("c_answer=Yes see notes")
        if yes_if_hits:
            signals.extend(f"yes_if_phrase:{phrase}" for phrase in yes_if_hits)
            return _finalize_classification("yes_if", 0.95, signals, threshold)
        return _finalize_classification("yes_if", 0.80, signals, threshold)

    if c_answer == "yes":
        signals.append("c_answer=Yes")
        if yes_if_hits:
            signals.extend(f"yes_if_phrase:{phrase}" for phrase in yes_if_hits)
            return _finalize_classification("yes_if", 0.75, signals, threshold)
        if no_hits:
            signals.extend(f"conflict_no_phrase:{phrase}" for phrase in no_hits)
            return "review", 0.40, signals
        return _finalize_classification("yes", 0.90, signals, threshold)

    signals.append(f"c_answer={c_answer or 'missing'}")
    if no_hits and not yes_if_hits:
        signals.extend(f"no_phrase:{phrase}" for phrase in no_hits)
        return _finalize_classification("no", 0.55, signals, threshold)
    if yes_if_hits and not no_hits:
        signals.extend(f"yes_if_phrase:{phrase}" for phrase in yes_if_hits)
        return _finalize_classification("yes_if", 0.55, signals, threshold)
    return "review", 0.0, signals


def _split_bullets(text: str) -> list[str]:
    """Split a newline-separated free-text section into clean bullet items."""
    if not text:
        return []
    items: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("\u2022"):
            line = line[1:].strip()
        line = line.lstrip("-*").strip()
        if line:
            items.append(line)
    return items


def site_slug(site_name: str, *, suffix: str | None = None) -> str:
    """Return a stable URL-safe slug for a site."""
    base = _SLUG_KEEP.sub("-", (site_name or "").lower()).strip("-")
    if suffix:
        normalized_suffix = _SLUG_KEEP.sub("-", suffix.lower()).strip("-")
        if normalized_suffix:
            base = f"{base}-{normalized_suffix}"
    return base or "unknown-site"


@dataclass
class ScenarioRecord:
    """One of the two active DD scenarios."""

    capacity: str = ""
    open_date: str = ""
    capex: str = ""
    costs: dict[str, str] = field(default_factory=dict)


@dataclass
class ClassificationRecord:
    label: str = "review"
    confidence: float = 0.0
    signals: list[str] = field(default_factory=list)
    tradeoffs: list[str] = field(default_factory=list)
    needs_to_go_right: list[str] = field(default_factory=list)


@dataclass
class SourceLinks:
    sir: str = ""
    inspection: str = ""
    block_plan: str = ""
    e_occupancy: str = ""
    school_approval: str = ""
    opening_plan: str = ""
    trace: str = ""
    drive_folder: str = ""
    dd_report: str = ""


@dataclass
class SiteRecord:
    """Dashboard-facing projection of a single DD report."""

    slug: str
    site_name: str
    marketing_name: str = ""
    city_state_zip: str = ""
    school_type: str = ""
    prepared_by: str = ""
    report_date: str = ""
    published_at: str = ""
    can_we_open: str = ""
    c_edreg: str = ""
    c_occupancy: str = ""
    c_zoning: str = ""
    c_permit_timeline: str = ""
    c_construction_timeline: str = ""
    direct_viable_buildout: str = ""
    alpha_fit: str = ""
    classification: ClassificationRecord = field(default_factory=ClassificationRecord)
    scenarios: dict[str, ScenarioRecord] = field(default_factory=dict)
    sources: SourceLinks = field(default_factory=SourceLinks)
    acquisition_conditions: str = ""
    tradeoffs_and_deficiencies: str = ""

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
        """Build a SiteRecord from normalized DD replacements."""

        def g(key: str) -> str:
            value = replacements.get(key, "")
            return value.strip() if isinstance(value, str) else ""

        label, confidence, signals = classify_site(
            replacements,
            threshold=classification_threshold,
        )
        acquisition_conditions = g("exec.acquisition_conditions")
        tradeoffs = g("exec.tradeoffs_and_deficiencies")

        classification = ClassificationRecord(
            label=label,
            confidence=round(confidence, 2),
            signals=signals,
            tradeoffs=_split_bullets(tradeoffs),
            needs_to_go_right=_split_bullets(acquisition_conditions),
        )

        scenarios: dict[str, ScenarioRecord] = {}
        for scenario in SCENARIOS:
            scenarios[scenario] = ScenarioRecord(
                capacity=g(f"exec.{scenario}_capacity"),
                open_date=g(f"exec.{scenario}_open_date"),
                capex=g(f"exec.{scenario}_capex"),
                costs={base: g(f"exec.{base}_{scenario}") for base in COST_TOKEN_BASES},
            )

        sources = SourceLinks(
            sir=g("sources.sir_link"),
            inspection=g("sources.inspection_link"),
            block_plan=g("sources.block_plan_link"),
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
            direct_viable_buildout=g("exec.direct_viable_buildout"),
            alpha_fit=g("exec.alpha_fit"),
            classification=classification,
            scenarios=scenarios,
            sources=sources,
            acquisition_conditions=acquisition_conditions,
            tradeoffs_and_deficiencies=tradeoffs,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable payload."""
        return asdict(self)
