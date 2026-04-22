"""Tests for school approval state-history guidance."""

from __future__ import annotations

import asyncio

from due_diligence_reporter.server import _format_skill_document, apply_school_approval_skill


def test_school_approval_exec_status_not_required_for_tx() -> None:
    result = asyncio.run(apply_school_approval_skill("TX"))

    assert result["status"] == "success"
    assert result["exec_c_edreg_status"] == "Not required"
    assert result["alpha_is_operating_in_state"] is True
    assert result["report_data_fields"]["q1.school_approval_exec_status"] == "Not required"


def test_school_approval_exec_status_required_and_have_done_for_ca() -> None:
    result = asyncio.run(apply_school_approval_skill("CA"))

    assert result["status"] == "success"
    assert result["exec_c_edreg_status"] == "Required and have done"
    assert result["alpha_is_operating_in_state"] is True
    assert "currently operates in CA" in result["alpha_state_reference"]
    assert result["report_data_fields"]["q1.school_approval_exec_status"] == "Required and have done"


def test_school_approval_exec_status_required_have_not_done_for_or() -> None:
    result = asyncio.run(apply_school_approval_skill("OR"))

    assert result["status"] == "success"
    assert result["exec_c_edreg_status"] == "Required have not done"
    assert result["alpha_has_worked_in_state"] is True
    assert result["alpha_is_operating_in_state"] is False
    assert "Oregon" in result["alpha_state_reference"]
    assert result["report_data_fields"]["q1.school_approval_exec_status"] == "Required have not done"


def test_school_approval_document_format_includes_state_reference() -> None:
    result = asyncio.run(apply_school_approval_skill("GA"))

    doc_text = _format_skill_document(
        skill_name="School Approval",
        site_name="Alpha Test",
        date="04/22/2026",
        data=result,
    )

    assert "Executive Status: Required and have done" in doc_text
    assert "Alpha State Reference:" in doc_text

