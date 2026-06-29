from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from due_diligence_reporter.drive_rhodes_reconciliation import (
    build_drive_rhodes_reconciliation_telemetry,
    run_drive_rhodes_reconciliation,
)


class FakeRhodesClient:
    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []
        self.registered_documents: list[dict[str, Any]] = []

    def find_document_by_drive_file_id(
        self,
        *,
        site_id: str,
        drive_file_id: str,
        doc_type: str | None = None,
        milestone: str | None = None,
    ) -> dict[str, Any] | None:
        for document in [*self.documents, *self.registered_documents]:
            if document.get("driveFileId") == drive_file_id:
                return document
        return None

    def register_document(
        self,
        *,
        site_id: str,
        title: str,
        doc_type: str,
        drive_file_id: str,
        drive_url: str = "",
        mime_type: str = "",
        milestone: str | None = None,
        quality_bar: str | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        document = {
            "_id": f"DOC{len(self.registered_documents) + 1}",
            "siteId": site_id,
            "title": title,
            "docType": doc_type,
            "driveFileId": drive_file_id,
            "driveUrl": drive_url,
            "mimeType": mime_type,
            "milestone": milestone,
            "qualityBar": quality_bar,
            "notes": notes,
        }
        self.registered_documents.append(document)
        return document


def _site() -> dict[str, str]:
    return {
        "id": "SITE1",
        "title": "Alpha Test",
        "drive_folder_url": "https://drive.google.com/drive/folders/root",
    }


def _gc(files: list[dict[str, Any]]) -> MagicMock:
    gc = MagicMock()
    gc.list_subfolders.return_value = [
        {
            "id": "m1",
            "name": "M1 - Acquire Property",
            "webViewLink": "https://drive/m1",
        }
    ]
    gc.list_files_in_folder.return_value = files
    return gc


def test_reconciliation_registers_unlinked_m1_source_docs() -> None:
    rhodes = FakeRhodesClient()
    gc = _gc(
        [
            {
                "id": "sir-1",
                "name": "Alpha Test SIR.pdf",
                "mimeType": "application/pdf",
                "modifiedTime": "2026-05-27T10:00:00Z",
                "webViewLink": "https://drive/sir-1",
            },
            {
                "id": "school-1",
                "name": "Alpha Test School Approval Report.pdf",
                "mimeType": "application/pdf",
                "modifiedTime": "2026-05-27T11:00:00Z",
                "webViewLink": "https://drive/school-1",
            },
        ]
    )

    result = run_drive_rhodes_reconciliation(
        gc,
        site_records=[_site()],
        rhodes_client=rhodes,  # type: ignore[arg-type]
    )

    assert result["registered"] == 2
    assert result["registered_verified"] == 2
    assert result["registered_unverified"] == 0
    assert result["skipped"] == 0
    registered_rows = [row for row in result["rows"] if row["status"] == "registered"]
    assert {row["rhodes_readback_status"] for row in registered_rows} == {"verified"}
    assert rhodes.registered_documents[0]["docType"] == "siteInvestigationReport"
    assert rhodes.registered_documents[0]["milestone"] == "acquireProperty"
    assert rhodes.registered_documents[1]["docType"] == "regulatoryApproval"
    assert rhodes.registered_documents[1]["milestone"] == "acquireProperty"
    assert "Registered by DDR drive_rhodes_reconciliation." in (
        rhodes.registered_documents[0]["notes"]
    )


def test_reconciliation_skips_already_registered_drive_file() -> None:
    rhodes = FakeRhodesClient()
    rhodes.documents = [{"_id": "DOC_EXISTING", "driveFileId": "sir-1"}]
    gc = _gc(
        [
            {
                "id": "sir-1",
                "name": "Alpha Test SIR.pdf",
                "mimeType": "application/pdf",
                "webViewLink": "https://drive/sir-1",
            }
        ]
    )

    result = run_drive_rhodes_reconciliation(
        gc,
        site_records=[_site()],
        rhodes_client=rhodes,  # type: ignore[arg-type]
    )

    assert result["already_registered"] == 1
    assert result["registered"] == 0
    assert rhodes.registered_documents == []
    assert result["rows"][0]["rhodes_document_id"] == "DOC_EXISTING"


def test_reconciliation_dry_run_reports_would_register_without_writing() -> None:
    rhodes = FakeRhodesClient()
    gc = _gc(
        [
            {
                "id": "inspection-1",
                "name": "Alpha Test Building Inspection Report.pdf",
                "mimeType": "application/pdf",
                "webViewLink": "https://drive/inspection-1",
            }
        ]
    )

    result = run_drive_rhodes_reconciliation(
        gc,
        site_records=[_site()],
        dry_run=True,
        rhodes_client=rhodes,  # type: ignore[arg-type]
    )

    assert result["would_register"] == 1
    assert result["registered"] == 0
    assert rhodes.registered_documents == []
    assert result["rows"][0]["rhodes_doc_type"] == "propertyConditionAssessment"

    telemetry = build_drive_rhodes_reconciliation_telemetry(
        result,
        run_id="drive-rhodes-reconciliation-dry-run",
        started_at="2026-06-08T22:30:00+00:00",
        finished_at="2026-06-08T22:31:00+00:00",
        dry_run=True,
        trigger="workflow_dispatch",
        workflow_run_url="https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/123",
    )
    dry_run_action = next(
        action
        for action in telemetry["action_records"]
        if action["alert_type"] == "document_registration_dry_run"
    )

    assert dry_run_action["review_required"] is True
    assert dry_run_action["review_url"].endswith("/actions/runs/123")
    assert "1 document(s)" in dry_run_action["evidence_summary"]
    rendered = json.dumps(dry_run_action)
    assert "https://drive" not in rendered
    assert "inspection-1" not in rendered


def test_reconciliation_skips_site_without_m1_folder() -> None:
    gc = MagicMock()
    gc.list_subfolders.return_value = []

    result = run_drive_rhodes_reconciliation(gc, site_records=[_site()])

    assert result["recognized_files"] == 0
    assert result["skipped"] == 1
    assert result["rows"][0]["reason"] == "m1_folder_missing"


def test_reconciliation_telemetry_does_not_emit_portfolio_action_when_no_source_docs() -> None:
    gc = _gc(
        [
            {
                "id": "notes-1",
                "name": "Alpha Test notes.txt",
                "mimeType": "text/plain",
                "webViewLink": "https://drive/notes-1",
            }
        ]
    )

    result = run_drive_rhodes_reconciliation(
        gc,
        site_records=[_site()],
        dry_run=True,
    )
    telemetry = build_drive_rhodes_reconciliation_telemetry(
        result,
        run_id="drive-rhodes-reconciliation-no-source",
        started_at="2026-06-08T22:30:00+00:00",
        finished_at="2026-06-08T22:31:00+00:00",
        dry_run=True,
        trigger="workflow_dispatch",
    )

    assert result["rows"][0]["reason"] == "no_recognized_m1_files"
    assert [
        action
        for action in telemetry["action_records"]
        if action["source_workflow"] == "portfolio-gaps"
    ] == []

    rendered = json.dumps(telemetry)
    assert "https://drive" not in rendered
    assert "notes-1" not in rendered
    assert "Alpha Test notes.txt" not in rendered


def test_reconciliation_telemetry_emits_sanitized_action_records() -> None:
    rhodes = FakeRhodesClient()
    gc = _gc(
        [
            {
                "id": "sir-1",
                "name": "Alpha Test SIR.pdf",
                "mimeType": "application/pdf",
                "webViewLink": "https://drive/sir-1",
            }
        ]
    )
    result = run_drive_rhodes_reconciliation(
        gc,
        site_records=[_site()],
        rhodes_client=rhodes,  # type: ignore[arg-type]
    )

    telemetry = build_drive_rhodes_reconciliation_telemetry(
        result,
        run_id="drive-rhodes-reconciliation-123",
        started_at="2026-06-08T21:30:00+00:00",
        finished_at="2026-06-08T21:31:00+00:00",
        trigger="schedule",
        workflow_run_url="https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/123",
    )

    assert telemetry["source_type"] == "drive_rhodes_reconciliation"
    assert telemetry["workflow_id"] == "ddr"
    assert telemetry["subworkflow_id"] == "drive-rhodes-reconciliation"
    assert telemetry["status"] == "success"
    assert telemetry["counts"]["registered_verified"] == 1
    assert telemetry["steps"][3]["key"] == "readback_verification"
    assert telemetry["steps"][3]["status"] == "success"
    action = telemetry["action_records"][0]
    assert action["schema_version"] == "action_record.v1"
    assert action["source_workflow"] == "ddr"
    assert action["workflow_owner"] == "drive-rhodes-reconciliation"
    assert action["status"] == "completed"
    assert action["loop_state"] == "completed"
    assert action["decision_status"] == "not_required"
    assert action["idempotency_key"] == action["action_id"]
    assert action["sor_system"] == "rhodes"
    assert action["sor_write_status"] == "completed"
    assert action["sor_readback_status"] == "verified"
    assert action["operating_note_status"] == "not_needed"
    assert action["failure_route"] == ""
    assert action["next_step"].startswith("No operator action needed")
    assert "Rhodes readback verified 1" in action["evidence_summary"]
    assert "Rhodes readback verified 1" in action["sor_readback_summary"]
    assert [
        action
        for action in telemetry["action_records"]
        if action["source_workflow"] == "portfolio-gaps"
    ] == []

    rendered = json.dumps(telemetry)
    assert "https://drive" not in rendered
    assert "sir-1" not in rendered
    assert "Alpha Test SIR.pdf" not in rendered


def test_reconciliation_telemetry_emits_structured_noop_readback() -> None:
    result = {
        "sites_scanned": 1,
        "recognized_files": 1,
        "registered": 0,
        "registered_verified": 0,
        "registered_unverified": 0,
        "already_registered": 1,
        "would_register": 0,
        "skipped": 0,
        "errors": 0,
        "rows": [
            {
                "site_id": "SITE1",
                "site_title": "Alpha Test",
                "drive_file_id": "sir-1",
                "drive_file_name": "Alpha Test SIR.pdf",
                "status": "already_registered",
                "rhodes_readback_status": "verified",
            }
        ],
    }
    telemetry = build_drive_rhodes_reconciliation_telemetry(
        result,
        run_id="drive-rhodes-reconciliation-noop",
        started_at="2026-06-08T21:30:00+00:00",
        finished_at="2026-06-08T21:31:00+00:00",
        trigger="schedule",
    )

    action = telemetry["action_records"][0]
    assert action["alert_type"] == "document_already_registered"
    assert action["status"] == "skipped_already_corrected"
    assert action["loop_state"] == "completed"
    assert action["decision_status"] == "not_required"
    assert action["sor_system"] == "rhodes"
    assert action["sor_write_status"] == "not_needed"
    assert action["sor_readback_status"] == "verified"
    assert action["operating_note_status"] == "not_needed"
    assert action["failure_route"] == ""
    assert action["retryable"] is False
    assert "Drive file ID" in action["sor_readback_summary"]
