"""Tests for the automated Security Due Diligence skill path."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from due_diligence_reporter.server import (
    _build_security_due_diligence_prompt,
    apply_security_due_diligence_skill,
)


def test_prompt_enforces_automation_mode_rules() -> None:
    prompt = _build_security_due_diligence_prompt(
        skill_text="SKILL BODY",
        site_name="Alpha Test",
        site_address="123 Main St, Denver, CO",
        block_plan_content="Room list",
        capacity_context='{"max": 54}',
        student_count="54",
        lease_content="",
    )

    assert "Do NOT ask intake questions" in prompt
    assert "unknown" in prompt
    assert "unverified" in prompt
    assert "SKILL BODY" in prompt
    assert "123 Main St, Denver, CO" in prompt
    assert "Room list" in prompt
    assert "Output ONLY the one-page go/no-go memo" in prompt
    assert "DRAFT LOI / LEASE TEXT" not in prompt


def _run_inline(func: Any, *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


def test_apply_security_due_diligence_publishes_and_registers(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    gc = MagicMock()
    gc.list_subfolders.return_value = [{"id": "m1", "name": "M1 - Acquire Property"}]
    gc.list_files_in_folder.return_value = []
    gc.create_document.return_value = {
        "id": "memo123",
        "webViewLink": "https://docs.google.com/document/d/memo123",
    }

    anthropic_client = MagicMock()
    anthropic_client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(text="SECURITY MEMO BODY")]
    )

    with patch(
        "due_diligence_reporter.server.asyncio.to_thread",
        new=AsyncMock(side_effect=_run_inline),
    ), patch(
        "due_diligence_reporter.server._make_google_client", return_value=gc
    ), patch(
        "due_diligence_reporter.server._list_m1_documents_by_type", return_value={}
    ), patch(
        "due_diligence_reporter.server.load_ops_skill_file",
        return_value=SimpleNamespace(source="ops-skills", text="SKILL BODY"),
    ), patch(
        "due_diligence_reporter.server.register_rhodes_document_for_upload",
        return_value={"status": "registered", "rhodes_doc_type": "other"},
    ) as mock_register, patch(
        "anthropic.Anthropic", return_value=anthropic_client
    ):
        result = asyncio.run(
            apply_security_due_diligence_skill(
                site_name="Alpha Test",
                site_address="123 Main St, Denver, CO",
                drive_folder_url="https://drive.google.com/drive/folders/folder123",
                site_id="SITE1",
                block_plan_content="Room list",
                capacity_context='{"max": 54}',
                student_count="54",
            )
        )

    assert result["status"] == "success"
    assert result["source_type"] == "security_due_diligence_report"
    assert result["doc_id"] == "memo123"
    assert result["report_data_fields"] == {}
    assert result["rhodes_registration"]["status"] == "registered"
    assert mock_register.call_args.kwargs["ddr_doc_type"] == "security_due_diligence_report"
    prompt = anthropic_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Do NOT ask intake questions" in prompt


def test_apply_security_due_diligence_requires_address() -> None:
    result = asyncio.run(
        apply_security_due_diligence_skill(site_name="Alpha Test", site_address="")
    )
    assert result["status"] == "error"


def test_executor_blocks_with_manual_fallback_when_skill_fails(monkeypatch) -> None:
    from due_diligence_reporter import m2_executor

    adapters = m2_executor.LiveM2ExecutorAdapters.__new__(
        m2_executor.LiveM2ExecutorAdapters
    )
    adapters._gc = MagicMock()

    state = {
        "site": {"id": "SITE1", "name": "Alpha Test", "address": "123 Main St"},
        "drive": {"site_folder_url": "https://drive.google.com/drive/folders/site"},
        "supporting_documents": [
            {
                "source_type": "block_plan",
                "title": "Block Plan",
                "drive_file_id": "plan1",
                "rhodes_doc_type": "floorPlan",
                "registration_status": "registered",
            },
            {
                "source_type": "alpha_capacity_analysis",
                "title": "Alpha Capacity Analysis",
                "drive_file_id": "cap1",
                "rhodes_doc_type": "capacityCalculation",
                "registration_status": "registered",
            },
        ],
        "report_data_fields": {"exec.max_capacity_capacity": "54"},
    }
    adapters._gc.download_file_bytes.return_value = b"plan bytes"

    with patch.object(
        m2_executor, "_text_from_document_bytes", return_value="Room list"
    ), patch.object(
        m2_executor.LiveM2ExecutorAdapters,
        "_read_json_file",
        return_value={"max": 54},
    ), patch(
        "due_diligence_reporter.server.apply_security_due_diligence_skill",
        MagicMock(return_value={"status": "error", "error": "Claude API call failed"}),
    ), patch.object(
        m2_executor,
        "_run_async",
        side_effect=lambda value: value,
    ):
        step = adapters.run_security_due_diligence(state)

    assert step.status == "blocked"
    assert "security_due_diligence_report" in step.raw["resume_source_types"]


def _security_state() -> dict[str, Any]:
    return {
        "site": {"id": "SITE1", "name": "Alpha Test", "address": "123 Main St"},
        "drive": {"site_folder_url": "https://drive.google.com/drive/folders/site"},
        "supporting_documents": [
            {
                "source_type": "block_plan",
                "title": "Block Plan",
                "drive_file_id": "plan1",
                "rhodes_doc_type": "floorPlan",
                "registration_status": "registered",
            },
            {
                "source_type": "alpha_capacity_analysis",
                "title": "Alpha Capacity Analysis",
                "drive_file_id": "cap1",
                "rhodes_doc_type": "capacityCalculation",
                "registration_status": "registered",
            },
        ],
        "report_data_fields": {"exec.max_capacity_capacity": "54"},
    }


def _executor_step_for_tool_result(tool_result: dict[str, Any]) -> Any:
    from due_diligence_reporter import m2_executor

    adapters = m2_executor.LiveM2ExecutorAdapters.__new__(
        m2_executor.LiveM2ExecutorAdapters
    )
    adapters._gc = MagicMock()
    adapters._gc.download_file_bytes.return_value = b"plan bytes"
    with patch.object(
        m2_executor, "_text_from_document_bytes", return_value="Room list"
    ), patch.object(
        m2_executor.LiveM2ExecutorAdapters,
        "_read_json_file",
        return_value={"max": 54},
    ), patch(
        "due_diligence_reporter.server.apply_security_due_diligence_skill",
        MagicMock(return_value=tool_result),
    ), patch.object(
        m2_executor,
        "_run_async",
        side_effect=lambda value: value,
    ):
        return adapters.run_security_due_diligence(_security_state())


def test_executor_blocks_when_publish_fails_after_generation() -> None:
    step = _executor_step_for_tool_result(
        {
            "status": "success",
            "source_type": "security_due_diligence_report",
            "memo_content": "MEMO",
            "doc_url": "",
            "doc_id": "",
            "publish_status": "failed",
            "publish_error": "Drive create failed",
            "report_data_fields": {},
        }
    )

    assert step.status == "blocked"
    assert "security_due_diligence_report" in step.raw["resume_source_types"]
    assert "ops-skills:security-due-diligence" in step.reason


def test_executor_blocks_when_registration_is_skipped() -> None:
    step = _executor_step_for_tool_result(
        {
            "status": "success",
            "source_type": "security_due_diligence_report",
            "doc_id": "memo123",
            "doc_url": "https://docs.google.com/document/d/memo123",
            "doc_name": "Security Due Diligence Report - Alpha Test",
            "rhodes_registration": {"status": "skipped", "reason": "missing_site_id"},
            "report_data_fields": {},
        }
    )

    assert step.status == "blocked"
    assert "security_due_diligence_report" in step.raw["resume_source_types"]


def test_executor_succeeds_with_registered_memo() -> None:
    step = _executor_step_for_tool_result(
        {
            "status": "success",
            "source_type": "security_due_diligence_report",
            "doc_id": "memo123",
            "doc_url": "https://docs.google.com/document/d/memo123",
            "doc_name": "Security Due Diligence Report - Alpha Test",
            "rhodes_registration": {
                "status": "registered",
                "rhodes_doc_type": "other",
            },
            "report_data_fields": {},
        }
    )

    assert step.status == "success"
    assert step.supporting_documents
    assert step.supporting_documents[0]["source_type"] == "security_due_diligence_report"


def test_truncated_memo_is_not_published(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    gc = MagicMock()
    anthropic_client = MagicMock()
    anthropic_client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(text="PARTIAL MEMO")],
        stop_reason="max_tokens",
    )

    with patch(
        "due_diligence_reporter.server.asyncio.to_thread",
        new=AsyncMock(side_effect=_run_inline),
    ), patch(
        "due_diligence_reporter.server._make_google_client", return_value=gc
    ), patch(
        "due_diligence_reporter.server._list_m1_documents_by_type", return_value={}
    ), patch(
        "due_diligence_reporter.server.load_ops_skill_file",
        return_value=SimpleNamespace(source="ops-skills", text="SKILL BODY"),
    ), patch("anthropic.Anthropic", return_value=anthropic_client):
        result = asyncio.run(
            apply_security_due_diligence_skill(
                site_name="Alpha Test",
                site_address="123 Main St",
                drive_folder_url="https://drive.google.com/drive/folders/folder123",
            )
        )

    assert result["status"] == "error"
    assert result["error"] == "Truncated output"
    gc.create_document.assert_not_called()


def test_reuse_inspection_failure_does_not_regenerate(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    gc = MagicMock()
    anthropic_client = MagicMock()

    with patch(
        "due_diligence_reporter.server.asyncio.to_thread",
        new=AsyncMock(side_effect=_run_inline),
    ), patch(
        "due_diligence_reporter.server._make_google_client", return_value=gc
    ), patch(
        "due_diligence_reporter.server._list_m1_documents_by_type",
        side_effect=RuntimeError("Drive listing failed"),
    ), patch("anthropic.Anthropic", return_value=anthropic_client):
        result = asyncio.run(
            apply_security_due_diligence_skill(
                site_name="Alpha Test",
                site_address="123 Main St",
                drive_folder_url="https://drive.google.com/drive/folders/folder123",
            )
        )

    assert result["status"] == "error"
    assert "inspect existing" in result["error"]
    anthropic_client.messages.create.assert_not_called()
    gc.create_document.assert_not_called()
