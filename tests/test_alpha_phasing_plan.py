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
        "Q1 target with 2 deferred Phase II gaps."
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
    monkeypatch.setattr(
        "due_diligence_reporter.server._list_m1_documents_by_type",
        lambda _gc, _folder_id: {},
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


def test_apply_alpha_phasing_plan_skill_blocks_only_on_hard_requirements() -> None:
    result = asyncio.run(
        apply_alpha_phasing_plan_skill(
            site_name="Alpha Test",
            site_address="",
            drive_folder_url="",
            source_of_truth="Budget tracker",
            quality_bar_target="Q1",
            opening_target_date="08/12/2026",
            must_complete_before_opening="Code-required opening scope",
            deferred_scopes=["Lobby refresh"],
        )
    )

    assert result["status"] == "blocked"
    assert "site address" in result["missing_inputs"]
    assert "site Drive folder URL" in result["missing_inputs"]
    assert "verification.open_items" in result["report_data_fields"]


def test_apply_alpha_phasing_plan_skill_auto_accepts_recommendations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(
        "due_diligence_reporter.server._list_m1_documents_by_type",
        lambda _gc, _folder_id: {},
    )
    monkeypatch.setattr(
        "due_diligence_reporter.server.register_rhodes_document_for_upload",
        MagicMock(return_value={"status": "registered", "rhodes_doc_type": "other"}),
    )
    notify = MagicMock(
        return_value={
            "status": "created",
            "rhodes_note_id": "NOTE-P2",
            "p2_dri_found": True,
        }
    )
    monkeypatch.setattr(
        "due_diligence_reporter.server.notify_rhodes_phasing_review", notify
    )

    result = asyncio.run(
        apply_alpha_phasing_plan_skill(
            site_name="Alpha Test",
            site_address="123 Main St",
            site_id="SITE1",
            drive_folder_url="https://drive.google.com/drive/folders/root",
        )
    )

    assert result["status"] == "success"
    accepted = result["auto_accepted_inputs"]
    assert any(item.startswith("source of truth:") for item in accepted)
    assert any(item.startswith("quality bar target:") for item in accepted)
    assert any(item.startswith("opening target date:") for item in accepted)
    assert any(item.startswith("Phase I opening scope:") for item in accepted)
    assert any(item.startswith("Phase II deferred scope:") for item in accepted)
    assert result["p2_review_note"]["status"] == "created"
    notify_kwargs = notify.call_args.kwargs
    assert notify_kwargs["site_id"] == "SITE1"
    assert notify_kwargs["workbook_url"] == "https://drive/phasing"
    assert notify_kwargs["auto_accepted_inputs"] == accepted


def test_apply_alpha_phasing_plan_skill_notifies_p2_dri_on_full_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(
        "due_diligence_reporter.server._list_m1_documents_by_type",
        lambda _gc, _folder_id: {},
    )
    monkeypatch.setattr(
        "due_diligence_reporter.server.register_rhodes_document_for_upload",
        MagicMock(return_value={"status": "registered", "rhodes_doc_type": "other"}),
    )
    notify = MagicMock(return_value={"status": "created", "rhodes_note_id": "NOTE-P2"})
    monkeypatch.setattr(
        "due_diligence_reporter.server.notify_rhodes_phasing_review", notify
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
        )
    )

    assert result["status"] == "success"
    assert result["auto_accepted_inputs"] == []
    assert notify.call_args.kwargs["auto_accepted_inputs"] == []
    assert result["p2_review_note"]["status"] == "created"


def test_apply_alpha_phasing_plan_skill_reuses_existing_workbook_without_notify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gc = MagicMock()
    monkeypatch.setattr("due_diligence_reporter.server._make_google_client", lambda: gc)
    monkeypatch.setattr(
        "due_diligence_reporter.server._get_or_create_m1_folder",
        lambda _gc, _folder_id: {"id": "m1", "name": "M1 - Acquire Property"},
    )
    monkeypatch.setattr(
        "due_diligence_reporter.server._list_m1_documents_by_type",
        lambda _gc, _folder_id: {
            "alpha_phasing_plan_report": {
                "id": "existing-1",
                "webViewLink": "https://drive/existing",
                "name": "Phase 1 Phase 2 Workbook - Alpha Test - 2026-07-01.xlsx",
            }
        },
    )
    register = MagicMock(return_value={"status": "already_registered"})
    monkeypatch.setattr(
        "due_diligence_reporter.server.register_rhodes_document_for_upload", register
    )
    notify = MagicMock()
    monkeypatch.setattr(
        "due_diligence_reporter.server.notify_rhodes_phasing_review", notify
    )

    result = asyncio.run(
        apply_alpha_phasing_plan_skill(
            site_name="Alpha Test",
            site_address="123 Main St",
            site_id="SITE1",
            drive_folder_url="https://drive.google.com/drive/folders/root",
        )
    )

    assert result["status"] == "success"
    assert result["reused_existing"] is True
    assert result["workbook_id"] == "existing-1"
    assert result["p2_review_note"] == {"status": "skipped", "reason": "reused_existing"}
    notify.assert_not_called()
    gc.upload_file_to_folder.assert_not_called()
    assert register.call_args.kwargs["drive_file_id"] == "existing-1"


def test_apply_alpha_phasing_plan_skill_skips_notify_when_registration_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(
        "due_diligence_reporter.server._list_m1_documents_by_type",
        lambda _gc, _folder_id: {},
    )
    monkeypatch.setattr(
        "due_diligence_reporter.server.register_rhodes_document_for_upload",
        MagicMock(return_value={"status": "pending_user_action"}),
    )
    notify = MagicMock()
    monkeypatch.setattr(
        "due_diligence_reporter.server.notify_rhodes_phasing_review", notify
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
        )
    )

    assert result["status"] == "success"
    assert result["p2_review_note"] == {
        "status": "skipped",
        "reason": "registration_not_complete",
    }
    notify.assert_not_called()


def test_reuse_inspection_failure_blocks_regeneration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gc = MagicMock()
    monkeypatch.setattr("due_diligence_reporter.server._make_google_client", lambda: gc)
    monkeypatch.setattr(
        "due_diligence_reporter.server._get_or_create_m1_folder",
        lambda _gc, _folder_id: {"id": "m1", "name": "M1 - Acquire Property"},
    )
    monkeypatch.setattr(
        "due_diligence_reporter.server._list_m1_documents_by_type",
        MagicMock(side_effect=RuntimeError("Drive listing failed")),
    )
    notify = MagicMock()
    monkeypatch.setattr(
        "due_diligence_reporter.server.notify_rhodes_phasing_review", notify
    )

    result = asyncio.run(
        apply_alpha_phasing_plan_skill(
            site_name="Alpha Test",
            site_address="123 Main St",
            site_id="SITE1",
            drive_folder_url="https://drive.google.com/drive/folders/root",
        )
    )

    assert result["status"] == "error"
    assert "inspect existing" in result["error"]
    gc.upload_file_to_folder.assert_not_called()
    notify.assert_not_called()
