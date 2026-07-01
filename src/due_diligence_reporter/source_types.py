"""Canonical DDR source type aliases."""

from __future__ import annotations


def canonical_source_type(value: str) -> str:
    """Return the source-packet source type used by DDR internals."""

    normalized = value.strip().replace("-", "_").replace(" ", "_")
    compact = "".join(ch for ch in normalized.casefold() if ch.isalnum() or ch == "_")
    collapsed = compact.replace("_", "")
    aliases = {
        "site_investigation_report": "sir",
        "siteinvestigationreport": "sir",
        "school_approval": "school_approval_report",
        "schoolapprovalreport": "school_approval_report",
        "regulatoryapproval": "school_approval_report",
        "capacitycalculation": "alpha_capacity_analysis",
        "initialcostestimate": "cost_timeline_estimate",
        "initial_cost_estimate": "cost_timeline_estimate",
        "cost_and_timeline_estimate": "cost_timeline_estimate",
        "phasing": "alpha_phasing_plan_report",
        "phasing_plan": "alpha_phasing_plan_report",
        "alpha_phasing_plan": "alpha_phasing_plan_report",
        "alpha_phasing_plan_workbook": "alpha_phasing_plan_report",
        "phase_1_phase_2": "alpha_phasing_plan_report",
        "phase_1_phase_2_report": "alpha_phasing_plan_report",
        "phase1phase2": "alpha_phasing_plan_report",
        "phase1phase2report": "alpha_phasing_plan_report",
        "phase_i_phase_ii": "alpha_phasing_plan_report",
        "phase_i_phase_ii_report": "alpha_phasing_plan_report",
        "phaseiphaseii": "alpha_phasing_plan_report",
        "phaseiphaseiireport": "alpha_phasing_plan_report",
        "phase_scope_register": "alpha_phasing_plan_report",
        "phasescoperegister": "alpha_phasing_plan_report",
        "security_due_diligence": "security_due_diligence_report",
        "securityduediligence": "security_due_diligence_report",
        "securityduediligencereport": "security_due_diligence_report",
    }
    return aliases.get(
        normalized,
        aliases.get(compact, aliases.get(collapsed, normalized)),
    )
