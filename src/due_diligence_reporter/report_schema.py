"""Current DD report template token schema, aliases, and normalization."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from .utils import flatten_report_data_for_replacement

logger = logging.getLogger(__name__)

# The Executive Summary "Can We Open?" card answer. Binary plain-English
# Yes / No — the literal answer to "Can this be a school by [date]?".
#
# Note: legacy exports may still carry the old Go / No Go vocabulary. Keep
# the mapping below for backward-compatible normalization.
#
# Legacy reports may still contain "Go" / "No Go" / "Yes see notes" /
# "Conditional" on this field — see LEGACY_CAN_WE_ANSWER_ALIASES for
# backward-compat aliasing.
ALLOWED_CAN_WE_ANSWERS: frozenset[str] = frozenset({
    "Yes",
    "No",
})

ALLOWED_ZONING_STATUSES: frozenset[str] = frozenset({
    "Permitted",
    "Use Permit Required (admin)",
    "Use Permit Required (public)",
    "Prohibited",
})

ALLOWED_VIABLE_BUILDOUTS: frozenset[str] = frozenset({
    "Fastest Open",
    "Max Capacity",
    "None",
})

ALLOWED_ALPHA_FIT_VALUES: frozenset[str] = frozenset({
    "Yes",
    "No",
})

# School-year start dates for deterministic c_answer computation.
# fastest_open_open_date <= SCHOOL_YEAR_DEADLINE → "Yes"; otherwise → "No".
SCHOOL_YEAR_START_DATES: tuple[date, ...] = (
    date(2026, 8, 12),  # 08/12/26
    date(2026, 9, 8),   # 09/08/26
)
SCHOOL_YEAR_DEADLINE: date = max(SCHOOL_YEAR_START_DATES)  # 09/08/26

# Legacy alias map for the Can-We-Open answer. Maps any historical or
# case-variant value to the canonical "Yes" / "No". Includes Go/No Go
# from the brief Go/No-Go-on-c_answer experiment, and "Yes see notes" /
# "Conditional" from the original three-state vocabulary which both
# collapse into "Yes" under the binary system.
LEGACY_CAN_WE_ANSWER_ALIASES: dict[str, str] = {
    # Canonical values (case/whitespace tolerance)
    "yes": "Yes",
    "no": "No",
    # Legacy Go / No Go vocabulary; collapse back to report Yes/No.
    "go": "Yes",
    "no go": "No",
    "no-go": "No",
    "nogo": "No",
    # Pre-rename three-state vocabulary — collapse to Yes (was a yes-with-caveats)
    "yes see notes": "Yes",
    "yes, see notes": "Yes",
    "conditional": "Yes",
}

# Legacy Go / No Go derivation retained for historical payload readers.
DD_RECOMMENDATION_FROM_C_ANSWER: dict[str, str] = {
    "Yes": "go",
    "No": "no_go",
}

# --- Phase 3: dd_site_score + band ---
#
# The DD site score is a 0–100 numeric derived from the
# `apply_e_occupancy_skill` MCP tool, which lands the value on the
# `q2.e_occupancy_score` token. The publisher promotes this to a top-level
# legacy `dd_site_score` field (Phase 3, Rhodes data dictionary, 4/24 review).
#
# Band thresholds mirror the E-Occupancy Rating Bands defined in the
# `ease-of-conversion` user skill
# (references/site-eval-brainlift.md, lines 66–71):
#   GREEN  80–100  Strong candidate — proceed to detailed assessment
#   YELLOW 60– 79  Viable with known challenges — proceed with caution
#   ORANGE 40– 59  Significant barriers — requires explicit business
#                  justification to continue
#   RED     0– 39  Fatal flaws likely — recommend passing
#
# Score is the source of truth; band is always derived to keep the two in sync.
ALLOWED_SITE_SCORE_BANDS: frozenset[str] = frozenset({
    "green",
    "yellow",
    "orange",
    "red",
})

SITE_SCORE_BAND_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (80, "green"),
    (60, "yellow"),
    (40, "orange"),
    (0, "red"),
)


def site_score_band(score: int | float | None) -> str | None:
    """Map a 0–100 numeric site score to its E-Occupancy band.

    Returns one of "green" / "yellow" / "orange" / "red" for in-range
    inputs, or None when the score is missing, NaN, or outside [0, 100].
    The publisher uses this to derive `dd_site_score_band` from
    `dd_site_score` whenever the caller does not supply an explicit band.
    """
    if score is None:
        return None
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if s != s:  # NaN check
        return None
    if s < 0 or s > 100:
        return None
    for threshold, label in SITE_SCORE_BAND_THRESHOLDS:
        if s >= threshold:
            return label
    return None  # unreachable: 0 threshold catches everything in [0, 100]

# --- Phase 4: dd_risk_flags[] canonical enum ---
#
# A single, canonical, deduplicated list of risk flags surfaced from the
# multiple flag-like signals in the DD report (permit_history, e_occupancy
# IBC gates, school_approval archetype, SIR Risk Watch).
#
# Categories cover the topical buckets we surface across all archetypes.
# `septic` deliberately rolls up under `environmental` (per Phase 4 design
# review on issue #20) — septic-specific findings are emitted as
# environmental flags whose summary text indicates septic.
ALLOWED_RISK_FLAG_CATEGORIES: frozenset[str] = frozenset({
    "zoning",
    "occupancy",
    "ahj_history",
    "parking",
    "traffic",
    "environmental",
    "flood_zone",
    "historic_district",
    "accessibility",
    "ed_reg",
})

ALLOWED_RISK_FLAG_SEVERITIES: frozenset[str] = frozenset({
    "high",
    "medium",
    "low",
})

ALLOWED_RISK_FLAG_SOURCES: frozenset[str] = frozenset({
    "permit_history",
    "e_occupancy",
    "school_approval",
    "sir_risk_watch",
})

# Severity ordering for sort/dedup: higher severity wins when the same
# (category, source) pair appears multiple times.
RISK_FLAG_SEVERITY_RANK: dict[str, int] = {
    "high": 3,
    "medium": 2,
    "low": 1,
}

MISSING_P1_ASSIGNEE_LABEL = "[Not found - P1 DRI not assigned]"

SCENARIOS: tuple[str, ...] = (
    "fastest_open",
    "max_capacity",
)

SUMMARY_TOKEN_BASES: tuple[tuple[str, str], ...] = (
    ("capacity", "Alpha Capacity Analysis"),
    ("capex", "RayCon"),
    ("open_date", "RayCon"),
)

ALPHA_PHASING_TOKENS: tuple[str, ...] = (
    "exec.alpha_phasing_phase_i_scope",
    "exec.alpha_phasing_phase_ii_scope",
    "exec.alpha_phasing_phase_ii_allowance",
    "exec.alpha_phasing_recommended_timing",
    "exec.alpha_phasing_quality_bar_status",
)

COST_TOKEN_BASES: tuple[str, ...] = (
    "cost_demolition",
    "cost_framing_doors",
    "cost_mep_fire_life_safety",
    "cost_plumbing_bathrooms",
    "cost_finish_work",
    "cost_furniture",
    "cost_tech_security_signage",
    "cost_other_hard_costs",
    "cost_soft_costs",
    "cost_gc_fee",
    "cost_contingency",
    "cost_grand_total",
)


def _build_template_tokens() -> list[str]:
    tokens: list[str] = [
        "meta.site_name",
        "meta.city_state_zip",
        "meta.school_type",
        "meta.marketing_name",
        "meta.report_date",
        "meta.prepared_by",
        "meta.rebl_site_id",
        "meta.drive_folder_url",
        "exec.c_answer",
        "exec.c_edreg",
        "exec.c_occupancy",
        "exec.c_zoning",
        "exec.c_permit_timeline",
        "exec.c_construction_timeline",
        "exec.direct_viable_buildout",
        "exec.alpha_fit",
    ]

    for scenario in SCENARIOS:
        for metric, _source in SUMMARY_TOKEN_BASES:
            tokens.append(f"exec.{scenario}_{metric}")

    tokens.extend(ALPHA_PHASING_TOKENS)

    for base in COST_TOKEN_BASES:
        for scenario in SCENARIOS:
            tokens.append(f"exec.{base}_{scenario}")

    tokens.extend([
        "exec.acquisition_conditions",
        "exec.tradeoffs_and_deficiencies",
        "sources.sir_link",
        "sources.inspection_link",
        "sources.block_plan_link",
        "sources.rebl_link",
        "sources.e_occupancy_link",
        "sources.school_approval_link",
        "sources.opening_plan_link",
        "sources.alpha_phasing_plan_link",
    ])
    return tokens


TEMPLATE_TOKENS: list[str] = _build_template_tokens()
TEMPLATE_TOKEN_SET: frozenset[str] = frozenset(TEMPLATE_TOKENS)


TOKEN_SOURCES: dict[str, str] = {
    "meta.site_name": "Site context",
    "meta.city_state_zip": "Site context",
    "meta.school_type": "Site context",
    "meta.marketing_name": "Site context",
    "meta.report_date": "System",
    "meta.prepared_by": "System",
    "meta.rebl_site_id": "REBL",
    "exec.c_answer": "Agent",
    "exec.c_zoning": "SIR",
    "exec.c_occupancy": "E-Occupancy",
    "exec.c_edreg": "School Approval",
    "exec.c_permit_timeline": "Agent",
    "exec.c_construction_timeline": "Agent",
    "exec.direct_viable_buildout": "Agent",
    "exec.alpha_fit": "Agent",
    "exec.acquisition_conditions": "Agent",
    "exec.tradeoffs_and_deficiencies": "Agent",
    "sources.rebl_link": "REBL",
    "sources.opening_plan_link": "Opening Plan",
    "sources.alpha_phasing_plan_link": "Alpha Phasing Plan",
}

for scenario in SCENARIOS:
    for metric, source in SUMMARY_TOKEN_BASES:
        TOKEN_SOURCES[f"exec.{scenario}_{metric}"] = source

for token in ALPHA_PHASING_TOKENS:
    TOKEN_SOURCES[token] = "Alpha Phasing Plan"

for base in COST_TOKEN_BASES:
    for scenario in SCENARIOS:
        source = "Agent"
        if scenario in {"fastest_open", "max_capacity"}:
            source = "RayCon"
        TOKEN_SOURCES[f"exec.{base}_{scenario}"] = source


LINK_TOKENS: frozenset[str] = frozenset({
    "sources.sir_link",
    "sources.inspection_link",
    "sources.block_plan_link",
    "sources.rebl_link",
    "sources.e_occupancy_link",
    "sources.school_approval_link",
    "sources.opening_plan_link",
    "sources.alpha_phasing_plan_link",
    "meta.drive_folder_url",
})


LINK_DISPLAY_LABELS: dict[str, str] = {
    "sources.sir_link": "View SIR",
    "sources.inspection_link": "View Inspection",
    "sources.block_plan_link": "View Block Plan",
    "sources.rebl_link": "View REBL Site",
    "sources.e_occupancy_link": "View E-Occupancy",
    "sources.school_approval_link": "View School Approval",
    "sources.opening_plan_link": "View Opening Plan",
    "sources.alpha_phasing_plan_link": "View Alpha Phasing Plan",
    "meta.drive_folder_url": "View Site Folder",
}


AGENT_KEY_ALIASES: dict[str, str] = {
    "site_name": "meta.site_name",
    "report_date": "meta.report_date",
    "doc_url": "meta.drive_folder_url",
    "site.name": "meta.site_name",
    "site.city_state_zip": "meta.city_state_zip",
    "site.address": "meta.city_state_zip",
    "site.school_type": "meta.school_type",
    "site.marketing_name": "meta.marketing_name",
    "site.prepared_by": "meta.prepared_by",
    "site.p1_assignee_name": "meta.prepared_by",
    "rebl.site_id": "meta.rebl_site_id",
    "rebl.url": "sources.rebl_link",
    "p1_assignee_name": "meta.prepared_by",
    "exec_summary.acquisition_conditions": "exec.acquisition_conditions",
    "exec_summary.direct_viable_buildout": "exec.direct_viable_buildout",
    "exec_summary.alpha_fit": "exec.alpha_fit",
    "exec_summary.tradeoffs_and_deficiencies": "exec.tradeoffs_and_deficiencies",
    "exec.c_permitting": "exec.c_edreg",
    "appendix.sir_link": "sources.sir_link",
    "appendix.inspection_link": "sources.inspection_link",
    "appendix.building_inspection_link": "sources.inspection_link",
    "appendix.block_plan_link": "sources.block_plan_link",
    "appendix.rebl_link": "sources.rebl_link",
    "appendix.floorplan_viability_link": "sources.block_plan_link",
    "appendix.isp_link": "sources.block_plan_link",
    "appendix.alpha_phasing_plan_link": "sources.alpha_phasing_plan_link",
    "sources.alpha_phasing_link": "sources.alpha_phasing_plan_link",
}

LEGACY_V2_ALIASES: dict[str, str] = {
    "exec.e_mvp_capacity": "exec.fastest_open_capacity",
    "exec.e_mvp_cost": "exec.fastest_open_capex",
    "exec.f_mvp_ready": "exec.fastest_open_open_date",
    "exec.e_max_capacity_capacity": "exec.max_capacity_capacity",
    "exec.e_max_capacity_cost": "exec.max_capacity_capex",
    "exec.f_max_capacity_ready": "exec.max_capacity_open_date",
    "exec.f_ready_mm_yy": "exec.fastest_open_open_date",
    "e_ideal_capacity": "exec.max_capacity_capacity",
    "e_ideal_cost": "exec.max_capacity_capex",
    "f_ideal_ready": "exec.max_capacity_open_date",
    "ideal_capacity": "exec.max_capacity_capacity",
    "ideal_cost": "exec.max_capacity_capex",
    "ideal_ready": "exec.max_capacity_open_date",
    "exec.e_ideal_capacity": "exec.max_capacity_capacity",
    "exec.e_ideal_capcity": "exec.max_capacity_capacity",
    "exec.e_ideal_cost": "exec.max_capacity_capex",
    "exec.f_ideal_ready": "exec.max_capacity_open_date",
}

for base in COST_TOKEN_BASES:
    LEGACY_V2_ALIASES[f"exec.{base}_mvp"] = f"exec.{base}_fastest_open"

# 2026-04: the canonical scenario key is "fastest_open", aligning with the
# upstream pipeline (alpha-dd-pipeline). PR A briefly renamed it to
# "furniture_only"; this block keeps any reports or RayCon payloads that were
# emitted with that interim name working, mapping them back to the canonical
# fastest_open_* names. Safe to remove once we're confident no in-flight data
# uses furniture_only_* (~30 days after the rollback ships).
FURNITURE_ONLY_INTERIM_ALIASES: dict[str, str] = {
    "exec.furniture_only_capacity": "exec.fastest_open_capacity",
    "exec.furniture_only_capex": "exec.fastest_open_capex",
    "exec.furniture_only_open_date": "exec.fastest_open_open_date",
}
for base in COST_TOKEN_BASES:
    FURNITURE_ONLY_INTERIM_ALIASES[f"exec.{base}_furniture_only"] = (
        f"exec.{base}_fastest_open"
    )
LEGACY_V2_ALIASES.update(FURNITURE_ONLY_INTERIM_ALIASES)

AGENT_KEY_ALIASES.update(LEGACY_V2_ALIASES)


def _resolve_prepared_by(flat: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return the preferred Prepared By value and its source label."""
    candidates = (
        ("p1_assignee_name", "p1_assignee"),
        ("site.p1_assignee_name", "p1_assignee"),
        ("meta.prepared_by", "agent"),
        ("site.prepared_by", "agent"),
    )
    for key, source in candidates:
        value = flat.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), source
    return None, None


def _clean_exec_section_value(value: Any, *, section: str) -> str | None:
    """Normalize lease/tradeoff text and remove mixed headings."""
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    lower = text.lower()
    if section == "acquisition_conditions":
        for prefix in ("conditions:", "lease conditions:"):
            if lower.startswith(prefix):
                text = text[len(prefix):].strip()
                lower = text.lower()
                break
        split_markers = (
            "trade-offs and deficiencies:",
            "tradeoffs and deficiencies:",
            "risks to note:",
            "risk to note:",
        )
        for marker in split_markers:
            idx = text.lower().find(marker)
            if idx != -1:
                text = text[:idx].strip()
                break
    elif section == "tradeoffs_and_deficiencies":
        split_markers = (
            "trade-offs and deficiencies:",
            "tradeoffs and deficiencies:",
            "risks to note:",
            "risk to note:",
        )
        for marker in split_markers:
            idx = lower.find(marker)
            if idx != -1:
                text = text[idx + len(marker):].strip()
                break
        if text.lower().startswith(("conditions:", "lease conditions:")):
            return None

    return text or None


def _normalize_optional_field(
    flat: dict[str, Any],
    token_sources: dict[str, str],
    token: str,
    normalizer: Callable[[str], str | None],
) -> None:
    value = flat.get(token)
    if not isinstance(value, str):
        return

    normalized = normalizer(value)
    if normalized is None:
        logger.warning("Dropping invalid %s value: %r", token, value)
        flat.pop(token, None)
        token_sources.pop(token, None)
        return

    flat[token] = normalized


def normalize_zoning_status(value: str) -> str | None:
    """Normalize zoning output to one of the four allowed status labels."""
    cleaned = value.strip().rstrip(".,;:?!").lower()
    if cleaned in {"permitted", "permitted by right"}:
        return "Permitted"
    if "prohibit" in cleaned:
        return "Prohibited"
    if "admin" in cleaned:
        return "Use Permit Required (admin)"
    if "public" in cleaned:
        return "Use Permit Required (public)"
    return None


def normalize_viable_buildout(value: str) -> str | None:
    """Normalize the direct buildout answer to the allowed labels."""
    cleaned = value.strip().rstrip(".,;:?!").lower()
    mapping = {
        "furniture only": "Fastest Open",
        "fastest": "Fastest Open",
        "fastest open": "Fastest Open",  # legacy label, pre-2026-04 rename
        "max capacity": "Max Capacity",
        "maximum capacity": "Max Capacity",
        "none": "None",
        "no": "None",
        "neither": "None",
    }
    return mapping.get(cleaned)


def normalize_alpha_fit(value: str) -> str | None:
    """Normalize the Alpha-fit answer to Yes/No."""
    cleaned = value.strip().rstrip(".,;:?!").lower()
    if cleaned == "yes":
        return "Yes"
    if cleaned == "no":
        return "No"
    return None


def normalize_report_data(
    report_data: dict[str, Any],
    site_name: str,
    report_date: str,
) -> tuple[dict[str, str], list[str], list[str], dict[str, str]]:
    """Normalize agent output into template-ready replacements."""
    flat = flatten_report_data_for_replacement(report_data)
    token_sources: dict[str, str] = {}

    for key in flat:
        if key in TEMPLATE_TOKEN_SET:
            token_sources[key] = TOKEN_SOURCES.get(key, "agent")

    if "meta.site_name" not in flat:
        flat["meta.site_name"] = site_name.strip()
        token_sources["meta.site_name"] = "default"
    if "meta.report_date" not in flat:
        flat["meta.report_date"] = report_date
        token_sources["meta.report_date"] = "default"
    if "meta.school_type" not in flat:
        flat["meta.school_type"] = "K-8 Private (Alpha School model)"
        token_sources["meta.school_type"] = "default"

    for alias, canonical in AGENT_KEY_ALIASES.items():
        if alias in flat and canonical not in flat:
            flat[canonical] = flat[alias]
            if canonical in TEMPLATE_TOKEN_SET:
                token_sources[canonical] = f"alias:{alias}"

    prepared_by, prepared_by_source = _resolve_prepared_by(flat)
    if prepared_by:
        flat["meta.prepared_by"] = prepared_by
        token_sources["meta.prepared_by"] = prepared_by_source or "agent"
    else:
        flat["meta.prepared_by"] = MISSING_P1_ASSIGNEE_LABEL
        token_sources["meta.prepared_by"] = "missing_p1_assignee"

    for section in ("acquisition_conditions", "tradeoffs_and_deficiencies"):
        cleaned = _clean_exec_section_value(flat.get(f"exec.{section}"), section=section)
        if cleaned is not None:
            flat[f"exec.{section}"] = cleaned

    _normalize_optional_field(flat, token_sources, "exec.c_zoning", normalize_zoning_status)
    _normalize_optional_field(
        flat,
        token_sources,
        "exec.direct_viable_buildout",
        normalize_viable_buildout,
    )
    _normalize_optional_field(flat, token_sources, "exec.alpha_fit", normalize_alpha_fit)
    _normalize_optional_field(flat, token_sources, "exec.c_answer", normalize_can_we_answer)

    # Deterministic override: compute Yes/No from fastest_open_open_date vs school year deadlines.
    # If the date is parseable it always overrides the agent's answer.
    # If unparseable, the agent's normalized answer (or absence) stands.
    fastest_date_str = flat.get("exec.fastest_open_open_date")
    if isinstance(fastest_date_str, str):
        parsed_date = parse_open_date(fastest_date_str)
        if parsed_date is not None:
            computed = "Yes" if parsed_date <= SCHOOL_YEAR_DEADLINE else "No"
            flat["exec.c_answer"] = computed
            token_sources["exec.c_answer"] = "computed:date_comparison"
        else:
            logger.warning(
                "Could not parse fastest_open_open_date %r; keeping agent c_answer",
                fastest_date_str,
            )

    replacements: dict[str, str] = {}
    unmatched_keys: list[str] = []
    for key, value in flat.items():
        if key in TEMPLATE_TOKEN_SET:
            replacements[key] = value
        else:
            unmatched_keys.append(key)

    unfilled_tokens = [token for token in TEMPLATE_TOKENS if token not in replacements]
    for token in unfilled_tokens:
        token_sources[token] = "unfilled"

    logger.info(
        "normalize_report_data: %d replacements, %d unmatched, %d unfilled",
        len(replacements),
        len(unmatched_keys),
        len(unfilled_tokens),
    )
    if unmatched_keys:
        logger.info("Unmatched agent keys: %s", sorted(unmatched_keys))

    return replacements, sorted(unmatched_keys), sorted(unfilled_tokens), token_sources


def normalize_can_we_answer(value: str) -> str | None:
    """Normalize legacy or case-variant answers to canonical allowed values."""
    cleaned = value.strip().rstrip(".,;:?!").lower()
    alias = LEGACY_CAN_WE_ANSWER_ALIASES.get(cleaned)
    if alias:
        return alias
    if re.match(r"^yes\b", cleaned):
        return "Yes"
    if re.match(r"^no\b", cleaned):
        return "No"
    return None


def parse_open_date(value: str) -> date | None:
    """Parse an open date string to a date object.

    Accepts MM/DD/YY (preferred) or MM/YY (legacy, assumes 1st of month).
    Returns None if the value cannot be parsed.
    """
    cleaned = value.strip()
    for fmt in ("%m/%d/%y", "%m/%y"):
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.date() if fmt == "%m/%d/%y" else date(dt.year, dt.month, 1)
        except ValueError:
            continue
    return None
