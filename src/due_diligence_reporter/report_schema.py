"""DD Report V3 template token schema, aliases, and normalization."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from .utils import flatten_report_data_for_replacement

logger = logging.getLogger(__name__)

ALLOWED_CAN_WE_ANSWERS: frozenset[str] = frozenset({
    "Yes",
    "Yes see notes",
    "No",
})

# School-year start dates for deterministic c_answer computation.
# fastest_open_open_date <= SCHOOL_YEAR_DEADLINE → "Yes"; otherwise → "No".
SCHOOL_YEAR_START_DATES: tuple[date, ...] = (
    date(2026, 8, 12),  # 08/12/26
    date(2026, 9, 8),   # 09/08/26
)
SCHOOL_YEAR_DEADLINE: date = max(SCHOOL_YEAR_START_DATES)  # 09/08/26

LEGACY_CAN_WE_ANSWER_ALIASES: dict[str, str] = {
    "yes": "Yes",
    "yes see notes": "Yes see notes",
    "no": "No",
    "conditional": "Yes see notes",
}

MISSING_P1_ASSIGNEE_LABEL = "[Not found - P1 Assignee not set in Wrike]"

SCENARIOS: tuple[str, ...] = (
    "recommended_path",
    "fastest_open",
    "max_capacity",
    "max_value",
)

SUMMARY_TOKEN_BASES: tuple[tuple[str, str], ...] = (
    ("capacity", "ISP"),
    ("capex", "Agent"),
    ("open_date", "Agent"),
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
        "meta.drive_folder_url",
        "exec.c_answer",
        "exec.c_edreg",
        "exec.c_occupancy",
        "exec.c_zoning",
        "exec.c_permit_timeline",
        "exec.c_construction_timeline",
    ]

    for scenario in SCENARIOS:
        for metric, _source in SUMMARY_TOKEN_BASES:
            tokens.append(f"exec.{scenario}_{metric}")

    for base in COST_TOKEN_BASES:
        for scenario in SCENARIOS:
            tokens.append(f"exec.{base}_{scenario}")

    tokens.extend([
        "exec.acquisition_conditions",
        "exec.risk_notes",
        "sources.sir_link",
        "sources.inspection_link",
        "sources.isp_link",
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
    "exec.c_answer": "Agent",
    "exec.c_zoning": "SIR",
    "exec.c_occupancy": "E-Occupancy",
    "exec.c_edreg": "School Approval",
    "exec.c_permit_timeline": "Agent",
    "exec.c_construction_timeline": "Agent",
    "exec.acquisition_conditions": "Agent",
    "exec.risk_notes": "Agent",
    "sources.opening_plan_link": "Agent",
}

for scenario in SCENARIOS:
    for metric, default_source in SUMMARY_TOKEN_BASES:
        source = default_source
        if scenario in {"fastest_open", "max_capacity"} and metric == "capex":
            source = "RayCon"
        if scenario == "recommended_path" and metric == "capacity":
            source = "Agent"
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
    "sources.isp_link",
    "sources.e_occupancy_link",
    "sources.school_approval_link",
    "sources.opening_plan_link",
    "sources.trace_link",
    "meta.drive_folder_url",
})


LINK_DISPLAY_LABELS: dict[str, str] = {
    "sources.sir_link": "View SIR",
    "sources.inspection_link": "View Inspection",
    "sources.isp_link": "View ISP",
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
    "p1_assignee_name": "meta.prepared_by",
    "exec_summary.acquisition_conditions": "exec.acquisition_conditions",
    "exec_summary.risk_notes": "exec.risk_notes",
    "exec.c_permitting": "exec.c_edreg",
    "appendix.sir_link": "sources.sir_link",
    "appendix.inspection_link": "sources.inspection_link",
    "appendix.building_inspection_link": "sources.inspection_link",
    "appendix.floorplan_viability_link": "sources.isp_link",
    "appendix.isp_link": "sources.isp_link",
}

LEGACY_V2_ALIASES: dict[str, str] = {
    "exec.e_mvp_capacity": "exec.fastest_open_capacity",
    "exec.e_mvp_cost": "exec.fastest_open_capex",
    "exec.f_mvp_ready": "exec.fastest_open_open_date",
    "exec.e_max_capacity_capacity": "exec.max_capacity_capacity",
    "exec.e_max_capacity_cost": "exec.max_capacity_capex",
    "exec.f_max_capacity_ready": "exec.max_capacity_open_date",
    "exec.e_max_value_capacity": "exec.max_value_capacity",
    "exec.e_max_value_cost": "exec.max_value_capex",
    "exec.f_max_value_ready": "exec.max_value_open_date",
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
    """Normalize acquisition/risk section text and remove mixed headings."""
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    lower = text.lower()
    if section == "acquisition_conditions":
        if lower.startswith("conditions:"):
            text = text[len("conditions:"):].strip()
        split_markers = ("risks to note:", "risk to note:")
        for marker in split_markers:
            idx = text.lower().find(marker)
            if idx != -1:
                text = text[:idx].strip()
                break
    elif section == "risk_notes":
        split_markers = ("risks to note:", "risk to note:")
        for marker in split_markers:
            idx = lower.find(marker)
            if idx != -1:
                text = text[idx + len(marker):].strip()
                break
        if text.lower().startswith("conditions:"):
            return None

    return text or None


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
            token_sources[key] = "agent"

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

    for section in ("acquisition_conditions", "risk_notes"):
        cleaned = _clean_exec_section_value(flat.get(f"exec.{section}"), section=section)
        if cleaned is not None:
            flat[f"exec.{section}"] = cleaned

    can_we_answer = flat.get("exec.c_answer")
    if isinstance(can_we_answer, str):
        normalized_answer = normalize_can_we_answer(can_we_answer)
        if normalized_answer is None:
            logger.warning("Dropping invalid exec.c_answer value: %r", can_we_answer)
            flat.pop("exec.c_answer", None)
            token_sources.pop("exec.c_answer", None)
        else:
            flat["exec.c_answer"] = normalized_answer

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
