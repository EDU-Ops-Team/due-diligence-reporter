"""DD Report template token schema, agent key aliases, and normalization.

This module is the single source of truth for the {{token}} placeholders
in the V2 Google Doc DD report template. It also provides an alias map that
translates commonly-observed agent key variations to their canonical template
token, and a ``normalize_report_data`` function used by ``create_dd_report``
to maximize the number of placeholders that get filled.
"""

from __future__ import annotations

import logging
from typing import Any

from .utils import flatten_report_data_for_replacement

logger = logging.getLogger(__name__)

ALLOWED_CAN_WE_ANSWERS: frozenset[str] = frozenset({
    "Yes",
    "Yes see notes",
    "No",
})

LEGACY_CAN_WE_ANSWER_ALIASES: dict[str, str] = {
    "yes": "Yes",
    "yes see notes": "Yes see notes",
    "no": "No",
    "conditional": "Yes see notes",
}


TEMPLATE_TOKENS: list[str] = [
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
    "exec.e_mvp_capacity",
    "exec.e_mvp_cost",
    "exec.f_mvp_ready",
    "exec.e_max_capacity_capacity",
    "exec.e_max_capacity_cost",
    "exec.f_max_capacity_ready",
    "exec.e_max_value_capacity",
    "exec.e_max_value_cost",
    "exec.f_max_value_ready",
    "exec.cost_demolition_mvp",
    "exec.cost_demolition_max_capacity",
    "exec.cost_demolition_max_value",
    "exec.cost_framing_doors_mvp",
    "exec.cost_framing_doors_max_capacity",
    "exec.cost_framing_doors_max_value",
    "exec.cost_mep_fire_life_safety_mvp",
    "exec.cost_mep_fire_life_safety_max_capacity",
    "exec.cost_mep_fire_life_safety_max_value",
    "exec.cost_plumbing_bathrooms_mvp",
    "exec.cost_plumbing_bathrooms_max_capacity",
    "exec.cost_plumbing_bathrooms_max_value",
    "exec.cost_finish_work_mvp",
    "exec.cost_finish_work_max_capacity",
    "exec.cost_finish_work_max_value",
    "exec.cost_furniture_mvp",
    "exec.cost_furniture_max_capacity",
    "exec.cost_furniture_max_value",
    "exec.cost_tech_security_signage_mvp",
    "exec.cost_tech_security_signage_max_capacity",
    "exec.cost_tech_security_signage_max_value",
    "exec.cost_other_hard_costs_mvp",
    "exec.cost_other_hard_costs_max_capacity",
    "exec.cost_other_hard_costs_max_value",
    "exec.cost_soft_costs_mvp",
    "exec.cost_soft_costs_max_capacity",
    "exec.cost_soft_costs_max_value",
    "exec.cost_gc_fee_mvp",
    "exec.cost_gc_fee_max_capacity",
    "exec.cost_gc_fee_max_value",
    "exec.cost_contingency_mvp",
    "exec.cost_contingency_max_capacity",
    "exec.cost_contingency_max_value",
    "exec.cost_grand_total_mvp",
    "exec.cost_grand_total_max_capacity",
    "exec.cost_grand_total_max_value",
    "exec.delta_max_capacity_capacity",
    "exec.delta_max_capacity_cost",
    "exec.delta_max_capacity_ready",
    "exec.delta_max_value_capacity",
    "exec.delta_max_value_cost",
    "exec.delta_max_value_ready",
    "exec.acquisition_conditions",
    "exec.risk_notes",
    "sources.sir_link",
    "sources.inspection_link",
    "sources.isp_link",
    "sources.e_occupancy_link",
    "sources.school_approval_link",
    "sources.trace_link",
]

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
    "exec.e_mvp_capacity": "ISP",
    "exec.e_mvp_cost": "ISP",
    "exec.f_mvp_ready": "Agent",
    "exec.e_max_capacity_capacity": "ISP",
    "exec.e_max_capacity_cost": "Agent",
    "exec.f_max_capacity_ready": "Agent",
    "exec.e_max_value_capacity": "Agent",
    "exec.e_max_value_cost": "Agent",
    "exec.f_max_value_ready": "Agent",
    "exec.cost_demolition_mvp": "RayCon",
    "exec.cost_demolition_max_capacity": "RayCon",
    "exec.cost_demolition_max_value": "Agent",
    "exec.cost_framing_doors_mvp": "RayCon",
    "exec.cost_framing_doors_max_capacity": "RayCon",
    "exec.cost_framing_doors_max_value": "Agent",
    "exec.cost_mep_fire_life_safety_mvp": "RayCon",
    "exec.cost_mep_fire_life_safety_max_capacity": "RayCon",
    "exec.cost_mep_fire_life_safety_max_value": "Agent",
    "exec.cost_plumbing_bathrooms_mvp": "RayCon",
    "exec.cost_plumbing_bathrooms_max_capacity": "RayCon",
    "exec.cost_plumbing_bathrooms_max_value": "Agent",
    "exec.cost_finish_work_mvp": "RayCon",
    "exec.cost_finish_work_max_capacity": "RayCon",
    "exec.cost_finish_work_max_value": "Agent",
    "exec.cost_furniture_mvp": "RayCon",
    "exec.cost_furniture_max_capacity": "RayCon",
    "exec.cost_furniture_max_value": "Agent",
    "exec.cost_tech_security_signage_mvp": "RayCon",
    "exec.cost_tech_security_signage_max_capacity": "RayCon",
    "exec.cost_tech_security_signage_max_value": "Agent",
    "exec.cost_other_hard_costs_mvp": "RayCon",
    "exec.cost_other_hard_costs_max_capacity": "RayCon",
    "exec.cost_other_hard_costs_max_value": "Agent",
    "exec.cost_soft_costs_mvp": "RayCon",
    "exec.cost_soft_costs_max_capacity": "RayCon",
    "exec.cost_soft_costs_max_value": "Agent",
    "exec.cost_gc_fee_mvp": "RayCon",
    "exec.cost_gc_fee_max_capacity": "RayCon",
    "exec.cost_gc_fee_max_value": "Agent",
    "exec.cost_contingency_mvp": "RayCon",
    "exec.cost_contingency_max_capacity": "RayCon",
    "exec.cost_contingency_max_value": "Agent",
    "exec.cost_grand_total_mvp": "RayCon",
    "exec.cost_grand_total_max_capacity": "RayCon",
    "exec.cost_grand_total_max_value": "Agent",
    "exec.delta_max_capacity_capacity": "Computed",
    "exec.delta_max_capacity_cost": "Computed",
    "exec.delta_max_capacity_ready": "Computed",
    "exec.delta_max_value_capacity": "Computed",
    "exec.delta_max_value_cost": "Computed",
    "exec.delta_max_value_ready": "Computed",
    "exec.acquisition_conditions": "Agent",
    "exec.risk_notes": "Agent",
}


LINK_TOKENS: frozenset[str] = frozenset({
    "sources.sir_link",
    "sources.inspection_link",
    "sources.isp_link",
    "sources.e_occupancy_link",
    "sources.school_approval_link",
    "sources.trace_link",
    "meta.drive_folder_url",
})


LINK_DISPLAY_LABELS: dict[str, str] = {
    "sources.sir_link": "View SIR",
    "sources.inspection_link": "View Inspection",
    "sources.isp_link": "View ISP",
    "sources.e_occupancy_link": "View E-Occupancy",
    "sources.school_approval_link": "View School Approval",
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
    "p1_assignee_name": "meta.prepared_by",
    "exec_summary.acquisition_conditions": "exec.acquisition_conditions",
    "exec_summary.risk_notes": "exec.risk_notes",
    "exec.c_permitting": "exec.c_edreg",
    "exec.f_ready_mm_yy": "exec.f_mvp_ready",
    "exec.e_ideal_capacity": "exec.e_max_capacity_capacity",
    "exec.e_ideal_cost": "exec.e_max_capacity_cost",
    "exec.f_ideal_ready": "exec.f_max_capacity_ready",
    "exec.delta_capacity": "exec.delta_max_capacity_capacity",
    "exec.delta_cost": "exec.delta_max_capacity_cost",
    "exec.delta_ready": "exec.delta_max_capacity_ready",
    "exec.e_ideal_capcity": "exec.e_max_capacity_capacity",
    "appendix.sir_link": "sources.sir_link",
    "appendix.inspection_link": "sources.inspection_link",
    "appendix.building_inspection_link": "sources.inspection_link",
    "appendix.floorplan_viability_link": "sources.isp_link",
    "appendix.isp_link": "sources.isp_link",
}


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

    if "meta.prepared_by" not in flat:
        flat["meta.prepared_by"] = "EDU Ops Team"
        token_sources["meta.prepared_by"] = "default"

    can_we_answer = flat.get("exec.c_answer")
    if isinstance(can_we_answer, str):
        normalized_answer = normalize_can_we_answer(can_we_answer)
        if normalized_answer is None:
            logger.warning("Dropping invalid exec.c_answer value: %r", can_we_answer)
            flat.pop("exec.c_answer", None)
            token_sources.pop("exec.c_answer", None)
        else:
            flat["exec.c_answer"] = normalized_answer

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
    if unfilled_tokens:
        logger.debug("Unfilled tokens: %s", sorted(unfilled_tokens))

    return replacements, sorted(unmatched_keys), sorted(unfilled_tokens), token_sources


def normalize_can_we_answer(value: str) -> str | None:
    """Normalize legacy or case-variant answers to the canonical allowed values."""
    return LEGACY_CAN_WE_ANSWER_ALIASES.get(value.strip().lower())


def _parse_dollar(value: str) -> int | None:
    """Parse a dollar string like '$185,000' into an integer."""
    cleaned = value.replace("$", "").replace(",", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        try:
            return int(float(cleaned))
        except ValueError:
            return None


def _format_dollar(amount: int) -> str:
    """Format an integer as a dollar string like '$105,000'."""
    if amount < 0:
        return f"-${abs(amount):,}"
    return f"${amount:,}"


def _parse_mm_yy(value: str) -> tuple[int, int] | None:
    """Parse 'MM/YY' into (month, year_2digit). Returns None on failure."""
    parts = value.strip().split("/")
    if len(parts) != 2:
        return None
    try:
        month, year = int(parts[0]), int(parts[1])
        if 1 <= month <= 12 and 0 <= year <= 99:
            return month, year
        return None
    except ValueError:
        return None


def _month_diff(base: tuple[int, int], comparison: tuple[int, int]) -> int:
    """Compute month difference (comparison - base) from (month, year_2digit) tuples."""
    base_total = base[1] * 12 + base[0]
    comparison_total = comparison[1] * 12 + comparison[0]
    return comparison_total - base_total


def _compute_capacity_delta(
    replacements: dict[str, str],
    comparison_token: str,
    delta_token: str,
    label: str,
) -> None:
    base_value = replacements.get("exec.e_mvp_capacity", "").strip()
    comparison_value = replacements.get(comparison_token, "").strip()
    if not base_value or not comparison_value or delta_token in replacements:
        return
    try:
        delta = int(comparison_value) - int(base_value)
        sign = "+" if delta > 0 else ""
        replacements[delta_token] = f"{sign}{delta}"
    except ValueError:
        logger.debug(
            "Could not compute %s capacity delta: minwork=%s, comparison=%s",
            label,
            base_value,
            comparison_value,
        )


def _compute_cost_delta(
    replacements: dict[str, str],
    comparison_token: str,
    delta_token: str,
    label: str,
) -> None:
    base_value = replacements.get("exec.e_mvp_cost", "").strip()
    comparison_value = replacements.get(comparison_token, "").strip()
    if not base_value or not comparison_value or delta_token in replacements:
        return
    base_cost = _parse_dollar(base_value)
    comparison_cost = _parse_dollar(comparison_value)
    if base_cost is None or comparison_cost is None:
        logger.debug(
            "Could not parse %s cost values: minwork=%s, comparison=%s",
            label,
            base_value,
            comparison_value,
        )
        return
    delta = comparison_cost - base_cost
    sign = "+" if delta > 0 else ""
    replacements[delta_token] = f"{sign}{_format_dollar(delta)}"


def _compute_ready_delta(
    replacements: dict[str, str],
    comparison_token: str,
    delta_token: str,
    label: str,
) -> None:
    base_value = replacements.get("exec.f_mvp_ready", "").strip()
    comparison_value = replacements.get(comparison_token, "").strip()
    if not base_value or not comparison_value or delta_token in replacements:
        return
    base_ready = _parse_mm_yy(base_value)
    comparison_ready = _parse_mm_yy(comparison_value)
    if not base_ready or not comparison_ready:
        logger.debug(
            "Could not parse %s timeline values: minwork=%s, comparison=%s",
            label,
            base_value,
            comparison_value,
        )
        return
    diff = _month_diff(base_ready, comparison_ready)
    sign = "+" if diff > 0 else ""
    replacements[delta_token] = f"{sign}{diff} mo"


def compute_deltas(replacements: dict[str, str]) -> None:
    """Compute delta columns for MaxCapacity and MaxValue against MinWork."""
    comparisons = (
        (
            "max_capacity",
            "exec.e_max_capacity_capacity",
            "exec.e_max_capacity_cost",
            "exec.f_max_capacity_ready",
            "exec.delta_max_capacity_capacity",
            "exec.delta_max_capacity_cost",
            "exec.delta_max_capacity_ready",
        ),
        (
            "max_value",
            "exec.e_max_value_capacity",
            "exec.e_max_value_cost",
            "exec.f_max_value_ready",
            "exec.delta_max_value_capacity",
            "exec.delta_max_value_cost",
            "exec.delta_max_value_ready",
        ),
    )

    for label, cap_token, cost_token, ready_token, cap_delta, cost_delta, ready_delta in comparisons:
        _compute_capacity_delta(replacements, cap_token, cap_delta, label)
        _compute_cost_delta(replacements, cost_token, cost_delta, label)
        _compute_ready_delta(replacements, ready_token, ready_delta, label)
