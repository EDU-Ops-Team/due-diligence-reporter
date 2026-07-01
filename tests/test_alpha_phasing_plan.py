from __future__ import annotations

import asyncio
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from due_diligence_reporter.alpha_phasing_plan import (
    AlphaPhasingSkill,
    build_alpha_phasing_report_fields,
    build_alpha_phasing_workbook,
    missing_alpha_phasing_inputs,
)
from due_diligence_reporter.server import apply_alpha_phasing_plan_skill

_HOSTED_SKILL_TEXT = """---
name: alpha-phasing-plan
description: Test phasing skill
metadata:
  scorecard:
    themeId: construction-cost-commercial-review
  version: '0.3'
---

# Phase 1 Phase 2 workbook
"""


@pytest.fixture(autouse=True)
def _hosted_alpha_phasing_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    skill_dir = tmp_path / "Ops-Skills" / "skills" / "alpha-phasing-plan"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_HOSTED_SKILL_TEXT, encoding="utf-8")
    monkeypatch.setenv("OPS_SKILLS_REPO_PATH", str(tmp_path / "Ops-Skills"))
    return skill_dir


def test_missing_inputs_require_confirmed_deferred_scope() -> None:
    missing = missing_alpha_phasing_inputs(
        site_name="Alpha Test",
        site_address="123 Main St",
        source_of_truth="Budget tracker",
        quality_bar_target="Q1",
        opening_target_date="08/12/2026",
        must_complete_before_opening="Code-required opening scope",
        deferred_scopes=[],
    )

    assert missing == ["confirmed Phase II deferred scope"]


def test_alpha_phasing_report_fields_include_ddr_tokens() -> None:
    fields = build_alpha_phasing_report_fields(
        workbook_url="https://drive/phasing",
        phase_i_scope_summary="Open with four classrooms.",
        deferred_scopes=["Lobby refresh", "Outdoor shade"],
        phase_ii_budget_items=[
            {"line_item": "Lobby refresh", "rom_cost": "$40k"},
            {"line_item": "Outdoor shade", "rom_cost": "$25k"},
        ],
        recommended_timing="Winter break.",
        quality_bar_target="Q1",
    )

    assert fields["sources.alpha_phasing_plan_link"] == "https://drive/phasing"
    assert fields["exec.alpha_phasing_phase_ii_allowance"] == "$65k"
    assert fields["exec.alpha_phasing_quality_bar_status"] == (
        "Q1 target with 2 confirmed Phase II gaps."
    )


def test_build_alpha_phasing_workbook_contains_required_tabs() -> None:
    skill = AlphaPhasingSkill(
        version="0.3",
        source="test skill",
        scorecard_theme_id="construction-cost-commercial-review",
    )
    workbook = build_alpha_phasing_workbook(
        site_name="Alpha Test",
        site_address="123 Main St",
        source_of_truth="Budget tracker",
        quality_bar_target="Q1",
        opening_target_date="08/12/2026",
        must_complete_before_opening="Code-required opening scope",
        deferred_scopes=["Lobby refresh"],
        phase_ii_budget_items=[{"line_item": "Lobby refresh", "rom_cost": "$40k"}],
        recommended_timing="Winter break",
        skill=skill,
    )

    with zipfile.ZipFile(BytesIO(workbook)) as zf:
        workbook_xml = zf.read("xl/workbook.xml").decode("utf-8")
        phase_ii_xml = zf.read("xl/worksheets/sheet4.xml").decode("utf-8")

    for sheet_name in (
        "Executive Summary",
        "Quality Bar Matrix",
        "Phase I Budget Schedule",
        "Phase II Budget Schedule",
        "Render Deck Inputs",
        "Source Notes",
    ):
        assert sheet_name in workbook_xml
    assert "Lobby refresh" in phase_ii_xml
    assert "<f>SUM(B2:B2)</f>" in phase_ii_xml


def test_apply_alpha_phasing_plan_skill_uploads_workbook(monkeypatch: pytest.MonkeyPatch) -> None:
    gc = MagicMock()
    gc.upload_file_to_folder.return_value = {
        "id": "file-1",
        "webViewLink": "https://drive/phasing",
    }
    monkeypatch.setattr("due_diligence_reporter.server._make_google_client", lambda: gc)
    monkeypatch.setattr(
        "due_diligence_reporter.server._get_or_create_m1_folder",
        lambda _gc, _folder_id: {"id": "m1", "name": "M1 - Acquire Property"},
    )
    register_rhodes = MagicMock(
        return_value={
            "status": "registered",
            "rhodes_doc_type": "other",
            "rhodes_milestone": "acquireProperty",
            "rhodes_document_id": "DOC1",
        }
    )
    monkeypatch.setattr(
        "due_diligence_reporter.server.register_rhodes_document_for_upload",
        register_rhodes,
    )

    result = asyncio.run(
        apply_alpha_phasing_plan_skill(
            site_name="Alpha Test",
            site_address="123 Main St",
            site_id="SITE1",
            drive_folder_url="https://drive.google.com/drive/folders/root",
            source_of_truth="Budget tracker",
            quality_bar_target="Q1",
            opening_target_date="08/12/2026",
            must_complete_before_opening="Code-required opening scope",
            deferred_scopes=["Lobby refresh"],
            phase_ii_budget_items=[{"line_item": "Lobby refresh", "rom_cost": "$40k"}],
            recommended_timing="Winter break",
        )
    )

    assert result["status"] == "success"
    assert result["doc_type"] == "alpha_phasing_plan_report"
    assert result["workbook_id"] == "file-1"
    assert result["workbook_url"] == "https://drive/phasing"
    assert result["rhodes_registration"]["status"] == "registered"
    assert (
        result["report_data_fields"]["sources.alpha_phasing_plan_link"]
        == "https://drive/phasing"
    )
    gc.upload_file_to_folder.assert_called_once()
    assert gc.upload_file_to_folder.call_args.args[0] == "m1"
    assert gc.upload_file_to_folder.call_args.args[1].startswith(
        "Phase 1 Phase 2 Workbook - Alpha Test - "
    )
    assert gc.upload_file_to_folder.call_args.kwargs["mime_type"].endswith(
        "spreadsheetml.sheet"
    )
    register_kwargs = register_rhodes.call_args.kwargs
    assert register_kwargs["site_id"] == "SITE1"
    assert register_kwargs["ddr_doc_type"] == "alpha_phasing_plan_report"
    assert register_kwargs["drive_file_id"] == "file-1"
    assert register_kwargs["drive_url"] == "https://drive/phasing"
    assert register_kwargs["mime_type"].endswith("spreadsheetml.sheet")
    assert register_kwargs["source"] == "apply_alpha_phasing_plan_skill"


def test_apply_alpha_phasing_plan_skill_returns_open_items_when_blocked() -> None:
    result = asyncio.run(
        apply_alpha_phasing_plan_skill(
            site_name="Alpha Test",
            site_address="123 Main St",
            drive_folder_url="https://drive.google.com/drive/folders/root",
            source_of_truth="Budget tracker",
            quality_bar_target="Q1",
            opening_target_date="08/12/2026",
            must_complete_before_opening="Code-required opening scope",
            deferred_scopes=[],
        )
    )

    assert result["status"] == "blocked"
    assert "confirmed Phase II deferred scope" in result["missing_inputs"]
    assert "verification.open_items" in result["report_data_fields"]
    assert "Confirm Phase 1 Phase 2 workbook input" in result["report_data_fields"][
        "verification.open_items"
    ]
