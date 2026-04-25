"""DD Report V3 template token schema, aliases, and normalization."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from .utils import flatten_report_data_for_replacement

logger = logging.getLogger(__name__)

# The Executive Summary "Can We Open?" card answer. Binary by design —
# matches the dashboard recommendation chip (Go / No Go). Legacy reports
# may still contain "Yes" / "No" / "Yes see notes" — see
# LEGACY_CAN_WE_ANSWER_ALIASES below for backward-compat aliasing.
ALLOWED_CAN_WE_ANSWERS: frozenset[str] = frozenset({
    "Go",
    "No Go",
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
# fastest_open_open_date <= SCHOOL_YEAR_DEADLINE → "Go"; otherwise → "No Go".
SCHOOL_YEAR_START_DATES: tuple[date, ...] = (
    date(2026, 8, 12),  # 08/12/26
    date(2026, 9, 8),   # 09/08/26
)
SCHOOL_YEAR_DEADLINE: date = max(SCHOOL_YEAR_START_DATES)  # 09/08/26

# Legacy alias map for the Can-We-Open answer. Maps any historical or
# case-variant value to the canonical "Go" / "No Go". Includes Yes/No
# from the pre-rename era and "Yes see notes" / "conditional" which
# both collapse into "Go" under the new binary system.
LEGACY_CAN_WE_ANSWER_ALIASES: dict[str, str] = {
    # New canonical values (case/whitespace tolerance)
    "go": "Go",
    "no go": "No Go",
    "no-go": "No Go",
    "nogo": "No Go",
    # Pre-rename Yes/No vocabulary
    "yes": "Go",
    "no": "No Go",
    # Pre-rename three-state vocabulary — collapse to Go (was a yes-with-caveats)
    "yes see notes": "Go",
    "yes, see notes": "Go",
    "conditional": "Go",
}

MISSING_P1_ASSIGNEE_LABEL = "[Not found - P1 Assignee not set in Wrike]"

SCENARIOS: tuple[str, ...] = (
    "fastest_open",
    "max_capacity",
)

SUMMARY_TOKEN_BASES: tuple[tuple[str, str], ...] = (
    ("capacity", "Capacity Brainlift"),
    ("capex", "RayCon"),
    ("open_date", "RayCon"),
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
        "sources.trace_link",
    ])
    return tokens


TEMPLATE_TOKENS: list[str] = _build_template_tokens()
TEMPLATE_TOKEN_SET: frozenset[str] = frozenset(TEMPLATE_TOKENS)


TOKEN_SOURCES: dict[str, str] = {
    "meta.site_name": "Wrike",
    "meta.city_state_zip": "Wrike",
    "meta.school_type": "Wrike",
    "meta.marketing_name": "Wrike",
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
    "sources.opening_plan_link": "Agent",
}

for scenario in SCENARIOS:
    for metric, source in SUMMARY_TOKEN_BASES:
        TOKEN_SOURCES[f"exec.{scenario}_{metric}"] = source

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
    "sources.trace_link",
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
    "sources.trace_link": "View Report Trace",
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
        "fastest open": "Fastest Open",
        "fastest": "Fastest Open",
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

    # Deterministic override: compute Go/No Go from fastest_open_open_date vs school year deadlines.
    # If the date is parseable it always overrides the agent's answer.
    # If unparseable, the agent's normalized answer (or absence) stands.
    fastest_date_str = flat.get("exec.fastest_open_open_date")
    if isinstance(fastest_date_str, str):
        parsed_date = parse_open_date(fastest_date_str)
        if parsed_date is not None:
            computed = "Go" if parsed_date <= SCHOOL_YEAR_DEADLINE else "No Go"
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
    return LEGACY_CAN_WE_ANSWER_ALIASES.get(value.strip().rstrip(".,;:?!").lower())


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
