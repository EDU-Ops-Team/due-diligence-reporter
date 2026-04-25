"""Phase 4: canonical risk-flag derivation for the dashboard publisher.

Single source of truth for converting the four flag-like signals in a DD
report's token bag into a canonical, deduped ``dd_risk_flags[]`` list:

    {
      "category": <ALLOWED_RISK_FLAG_CATEGORIES>,
      "severity": "high" | "medium" | "low",
      "source":   <ALLOWED_RISK_FLAG_SOURCES>,
      "summary":  <short human string>
    }

Sources canonicalized
---------------------
1. ``permit_history.risk_flags`` (structured list from ``get_permit_history``)
   — categorized as ``ahj_history``. Severity rule:
       acquisition_condition → high
       risk_note             → medium
       info                  → omit (evidence only, not a DD risk)

2. ``q2.ibc_flags`` (structured list from ``apply_e_occupancy_skill``;
   falls back to keyword-matching ``q2.e_occupancy_ibc_summary`` text +
   ``q2.e_occupancy_zone`` when the structured token is absent)
   — categorized into ``occupancy``, ``accessibility``, ``parking`` per
   keyword map. Severity:
       hard-fail / blocking   → high
       soft-flag / advisory   → medium
   Zone-based fallback: zone == "Red" emits one ``occupancy`` high flag.

3. ``school_approval`` (q1.school_approval_exec_status + zone)
   — categorized as ``ed_reg``. Severity:
       red zone     → high
       yellow zone  → medium
       green zone   → omit (no risk to surface)

4. ``sir.risk_watch`` (free-text list from SIR Risk Watch section, when
   present in the token bag) — canonicalized via keyword map. Severity:
       contains "blocking" / "fatal" → high
       default                       → medium

Caller-wins precedence
----------------------
``derive_risk_flags`` is called from the publisher only when the caller
did not supply explicit ``dd_risk_flags=[…]``. The publisher itself
applies the caller-wins gate and validation; this module focuses on the
clean derivation path.

Dedup
-----
Output is deduped on ``(category, source)``. When the same pair appears
multiple times, the entry with the higher severity wins
(``RISK_FLAG_SEVERITY_RANK``); summaries are joined with " | " up to a
~200-char cap to keep payloads tidy.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .report_schema import (
    ALLOWED_RISK_FLAG_CATEGORIES,
    ALLOWED_RISK_FLAG_SEVERITIES,
    ALLOWED_RISK_FLAG_SOURCES,
    RISK_FLAG_SEVERITY_RANK,
)

_SUMMARY_MAX_LEN = 200


# --- Keyword maps ---------------------------------------------------------

# IBC-flag → canonical category (e_occupancy source). Keys are lowercased
# substring matches against the structured ibc_flags entry text or the
# free-text ibc_summary when that's all we have.
_IBC_KEYWORD_TO_CATEGORY: tuple[tuple[str, str], ...] = (
    ("sprinkler", "occupancy"),
    ("travel distance", "occupancy"),
    ("exit", "occupancy"),
    ("egress", "occupancy"),
    ("occupant load", "occupancy"),
    ("ada", "accessibility"),
    ("accessib", "accessibility"),  # accessible / accessibility
    ("ramp", "accessibility"),
    ("parking", "parking"),
    ("loading", "parking"),
)

# IBC keywords that signal a hard-fail / blocking gate (high severity).
# Anything not matching here defaults to medium.
_IBC_HARDFAIL_KEYWORDS: frozenset[str] = frozenset({
    "fail",
    "exceeds",
    "insufficient",
    "below minimum",
    "not meet",
    "blocking",
    "must add",
    "must install",
})

# SIR Risk Watch free-text → canonical category. First match wins; order
# matters (more specific phrases before generic ones). Anything that
# doesn't match drops to ``ahj_history`` as the most generic bucket.
_SIR_RISK_WATCH_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("flood", "flood_zone"),
    ("fema", "flood_zone"),
    ("wetland", "environmental"),
    ("septic", "environmental"),
    ("contamination", "environmental"),
    ("brownfield", "environmental"),
    ("environmental", "environmental"),
    ("historic", "historic_district"),
    ("preservation", "historic_district"),
    ("traffic", "traffic"),
    ("pickup", "traffic"),
    ("drop-off", "traffic"),
    ("dropoff", "traffic"),
    ("queue", "traffic"),
    ("parking", "parking"),
    ("zoning", "zoning"),
    ("variance", "zoning"),
    ("conditional use", "zoning"),
    ("special use", "zoning"),
    ("ada", "accessibility"),
    ("accessib", "accessibility"),
    ("occupancy", "occupancy"),
    ("group e", "occupancy"),
    ("state approval", "ed_reg"),
    ("private school", "ed_reg"),
    ("registration", "ed_reg"),
)

_SIR_HIGH_KEYWORDS: frozenset[str] = frozenset({
    "blocking",
    "fatal",
    "show-stopper",
    "showstopper",
    "deal-breaker",
    "dealbreaker",
})


# --- Public API -----------------------------------------------------------


def derive_risk_flags(report_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Build canonical ``dd_risk_flags[]`` from a report's token bag.

    Returns a list sorted by (severity desc, category asc, source asc) so
    output is deterministic for tests and stable for the dashboard.
    Empty list when no upstream signals are present.
    """
    if not isinstance(report_data, dict):
        return []

    raw: list[dict[str, Any]] = []
    raw.extend(_from_permit_history(report_data))
    raw.extend(_from_e_occupancy(report_data))
    raw.extend(_from_school_approval(report_data))
    raw.extend(_from_sir_risk_watch(report_data))

    # Validate + dedup
    cleaned = [_validate_flag(f) for f in raw]
    cleaned = [f for f in cleaned if f is not None]
    return _dedup_and_sort(cleaned)


def normalize_caller_flags(
    flags: Iterable[Any] | None,
) -> list[dict[str, Any]]:
    """Validate + dedup caller-supplied flags (caller-wins path).

    Drops invalid entries silently. Same dedup rules as the derivation
    path so callers can hand-mix sources without blowing the contract.
    """
    if not flags:
        return []
    cleaned: list[dict[str, Any]] = []
    for entry in flags:
        v = _validate_flag(entry)
        if v is not None:
            cleaned.append(v)
    return _dedup_and_sort(cleaned)


# --- Source ingesters -----------------------------------------------------


def _from_permit_history(report_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Permit-history flags → canonical ``ahj_history`` flags.

    Reads the structured ``permit_history.risk_flags`` token (a list of
    dicts shaped by ``_analyze_permit_flags`` in server.py). Info-severity
    flags are evidence-only and never surface as DD risks.
    """
    raw = report_data.get("permit_history.risk_flags")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        upstream_severity = str(entry.get("severity") or "").strip().lower()
        if upstream_severity == "acquisition_condition":
            sev = "high"
        elif upstream_severity == "risk_note":
            sev = "medium"
        else:
            # info (or unknown) → not a DD risk
            continue
        summary = _short(str(entry.get("description") or entry.get("flag_type") or "permit-history flag"))
        out.append({
            "category": "ahj_history",
            "severity": sev,
            "source": "permit_history",
            "summary": summary,
        })
    return out


def _from_e_occupancy(report_data: dict[str, Any]) -> list[dict[str, Any]]:
    """E-Occupancy IBC flags → canonical occupancy/accessibility/parking flags.

    Prefers the structured ``q2.ibc_flags`` token when present; falls back
    to keyword-matching ``q2.e_occupancy_ibc_summary`` text. Adds a
    zone-derived ``occupancy:high`` flag when zone == "Red" and no
    structured flag already covers it (caught by dedup on category).
    """
    out: list[dict[str, Any]] = []
    structured = report_data.get("q2.ibc_flags")
    flag_texts: list[str] = []
    if isinstance(structured, list) and structured:
        flag_texts = [str(f) for f in structured if str(f).strip()]
    else:
        summary = str(report_data.get("q2.e_occupancy_ibc_summary") or "").strip()
        if summary:
            # Split on bullets/newlines so each gate becomes its own flag
            flag_texts = [
                t.strip(" -•\t")
                for t in re.split(r"[\n\r]+|(?:^|\s)[-•]\s+", summary)
                if t.strip(" -•\t")
            ]

    for text in flag_texts:
        category = _ibc_text_to_category(text)
        if category is None:
            continue
        sev = _ibc_severity_from_text(text)
        out.append({
            "category": category,
            "severity": sev,
            "source": "e_occupancy",
            "summary": _short(text),
        })

    # Zone-based fallback: red zone without any occupancy flag → emit one.
    zone = str(report_data.get("q2.e_occupancy_zone") or "").strip().lower()
    if zone == "red" and not any(f["category"] == "occupancy" for f in out):
        out.append({
            "category": "occupancy",
            "severity": "high",
            "source": "e_occupancy",
            "summary": "E-Occupancy assessment is in the Red zone — fatal flaws likely",
        })

    return out


def _from_school_approval(report_data: dict[str, Any]) -> list[dict[str, Any]]:
    """School-approval archetype → canonical ``ed_reg`` flag.

    Severity from the score-derived zone (mirrors ``_school_zone`` in
    server.py: <40 red, <60 orange, <80 yellow, else green). Green zone
    emits no flag — there's no DD risk to surface.
    """
    # Prefer explicit zone token when present; else derive from score.
    zone = str(report_data.get("q1.school_approval_zone") or "").strip().lower()
    if not zone:
        # apply_school_approval_skill uses _school_zone() with the same
        # bands as e_occupancy. Try to recover from any numeric score
        # token if present (defensive — the skill doesn't currently emit
        # one, but a future revision might).
        return []

    if zone in {"green", "ok"}:
        return []
    if zone in {"red"}:
        sev = "high"
    elif zone in {"yellow", "orange"}:
        # Yellow → medium; Orange → high (matches e_occupancy "significant
        # barriers requires explicit business justification" framing).
        sev = "high" if zone == "orange" else "medium"
    else:
        return []

    approval_type = str(report_data.get("q1.school_approval_type") or "").strip()
    timeline_days = str(report_data.get("q1.school_approval_timeline_days") or "").strip()
    bits = ["State school approval"]
    if approval_type:
        bits.append(approval_type)
    if timeline_days:
        bits.append(f"~{timeline_days}-day timeline")
    return [{
        "category": "ed_reg",
        "severity": sev,
        "source": "school_approval",
        "summary": _short(" — ".join(bits)),
    }]


def _from_sir_risk_watch(report_data: dict[str, Any]) -> list[dict[str, Any]]:
    """SIR Risk Watch entries → canonical flags via keyword map.

    Accepts either a structured list (``sir.risk_watch`` = list of dicts
    or strings) or a single concatenated string on ``sir.risk_watch_text``.
    Free-text entries that don't match any keyword are skipped.
    """
    raw: list[str] = []
    structured = report_data.get("sir.risk_watch")
    if isinstance(structured, list):
        for entry in structured:
            if isinstance(entry, dict):
                txt = str(entry.get("description") or entry.get("text") or entry.get("title") or "")
            else:
                txt = str(entry or "")
            if txt.strip():
                raw.append(txt.strip())
    text_blob = report_data.get("sir.risk_watch_text")
    if isinstance(text_blob, str) and text_blob.strip():
        for line in text_blob.splitlines():
            line = line.strip(" -•\t")
            if line:
                raw.append(line)

    out: list[dict[str, Any]] = []
    for entry_text in raw:
        category = _sir_text_to_category(entry_text)
        if category is None:
            continue
        lower = entry_text.lower()
        sev = "high" if any(k in lower for k in _SIR_HIGH_KEYWORDS) else "medium"
        out.append({
            "category": category,
            "severity": sev,
            "source": "sir_risk_watch",
            "summary": _short(entry_text),
        })
    return out


# --- Helpers --------------------------------------------------------------


def _ibc_text_to_category(text: str) -> str | None:
    lower = text.lower()
    for kw, cat in _IBC_KEYWORD_TO_CATEGORY:
        if kw in lower:
            return cat
    return None


def _ibc_severity_from_text(text: str) -> str:
    lower = text.lower()
    if any(k in lower for k in _IBC_HARDFAIL_KEYWORDS):
        return "high"
    return "medium"


def _sir_text_to_category(text: str) -> str | None:
    lower = text.lower()
    for kw, cat in _SIR_RISK_WATCH_KEYWORDS:
        if kw in lower:
            return cat
    return None


def _short(text: str) -> str:
    """Trim whitespace + cap length so payloads stay tidy."""
    s = " ".join(text.split())  # collapse internal whitespace
    if len(s) > _SUMMARY_MAX_LEN:
        return s[: _SUMMARY_MAX_LEN - 1].rstrip() + "…"
    return s


def _validate_flag(entry: Any) -> dict[str, Any] | None:
    """Validate one flag dict; return cleaned copy or None if invalid."""
    if not isinstance(entry, dict):
        return None
    category = str(entry.get("category") or "").strip().lower()
    severity = str(entry.get("severity") or "").strip().lower()
    source = str(entry.get("source") or "").strip().lower()
    summary = str(entry.get("summary") or "").strip()
    if category not in ALLOWED_RISK_FLAG_CATEGORIES:
        return None
    if severity not in ALLOWED_RISK_FLAG_SEVERITIES:
        return None
    if source not in ALLOWED_RISK_FLAG_SOURCES:
        return None
    if not summary:
        return None
    return {
        "category": category,
        "severity": severity,
        "source": source,
        "summary": _short(summary),
    }


def _dedup_and_sort(flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedup on (category, source); higher severity wins; merge summaries.

    Output sort order: severity desc, category asc, source asc.
    """
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for f in flags:
        key = (f["category"], f["source"])
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = dict(f)
            continue
        # Severity tie-break: keep higher rank
        if RISK_FLAG_SEVERITY_RANK[f["severity"]] > RISK_FLAG_SEVERITY_RANK[existing["severity"]]:
            existing["severity"] = f["severity"]
        # Merge summaries (cap total length)
        if f["summary"] and f["summary"] not in existing["summary"]:
            merged = f"{existing['summary']} | {f['summary']}"
            existing["summary"] = _short(merged)

    return sorted(
        by_key.values(),
        key=lambda f: (
            -RISK_FLAG_SEVERITY_RANK[f["severity"]],
            f["category"],
            f["source"],
        ),
    )
