from __future__ import annotations

import json
from datetime import UTC, datetime

from due_diligence_reporter.review_execution import execute_ddr_review_requests


def test_ddr_review_execution_emits_needs_review_source_readback() -> None:
    result = execute_ddr_review_requests(
        {
            "schema_version": "review_execution_requests.v1",
            "requests": [
                {
                    "request_id": "review-request:decision-1",
                    "decision_id": "decision-1",
                    "action_id": "ddr:run-1:step:report.generate",
                    "decision": "approve",
                    "owning_workflow": "ddr",
                    "workflow_owner": "daily-dd-check",
                    "alert_type": "report_generation_failed",
                    "site_name": "Alpha Keller",
                    "action_requested": "ddr rerun --run-id run-1 --step report.generate",
                    "routing_instruction": "Rerun report.generate with operator context.",
                    "evidence_summary": "DDR run recorded report.generate=failed.",
                }
            ],
        },
        now=datetime(2026, 6, 10, 18, 0, tzinfo=UTC),
    )

    action = result["action_records"][0]
    run = result["runs"][0]

    assert result["schema_version"] == "ddr_review_execution_result.v1"
    assert result["execution"]["source"] == "ddr"
    assert result["execution"]["attempted_count"] == 1
    assert result["execution"]["needs_review_count"] == 1
    assert action["action_id"] == "ddr:run-1:step:report.generate"
    assert action["source_workflow"] == "ddr"
    assert action["owning_workflow"] == "ddr"
    assert action["workflow_owner"] == "daily-dd-check"
    assert action["status"] == "needs_review"
    assert action["review_required"] is True
    assert "source document/run context" in action["action_taken"]
    assert action["routing_instruction"] == "Rerun report.generate with operator context."
    assert result["requests"][0]["execution_action"]["source_action_id"] == action["action_id"]
    assert result["requests"][0]["execution_action"]["status"] == "needs_review"
    assert (
        result["requests"][0]["execution_action"]["routing_instruction"]
        == "Rerun report.generate with operator context."
    )
    assert run["schema_version"] == "workflow_run.v1"
    assert run["workflow_id"] == "ddr"
    assert run["subworkflow_id"] == "ddr-review-execution"
    assert run["status"] == "needs_review"


def test_ddr_review_execution_missing_drive_folder_url_is_specific_prerequisite() -> None:
    result = execute_ddr_review_requests(
        {
            "schema_version": "review_execution_requests.v1",
            "requests": [
                {
                    "request_id": "review-request:decision-folder",
                    "decision_id": "decision-folder",
                    "action_id": "ddr:run-folder:step:readiness.check",
                    "decision": "approve",
                    "owning_workflow": "ddr",
                    "workflow_owner": "ddr",
                    "alert_type": "missing_drive_folder_url",
                    "site_name": "Alpha Los Angeles 5400 Beethoven St",
                    "action_requested": (
                        "ddr rerun --run-id run-folder --step readiness.check"
                    ),
                    "action_taken": (
                        "DDR received the approved review request, but no "
                        "source-specific execution handler exists yet for this alert type."
                    ),
                    "routing_instruction": (
                        "Route to AADP/Rhodes folder provisioning, verify readback, "
                        "then rerun DDR readiness."
                    ),
                    "evidence_summary": (
                        "DDR readiness.check is blocked because Rhodes returned no "
                        "linked site Drive folder."
                    ),
                }
            ],
        },
        now=datetime(2026, 6, 16, 18, 0, tzinfo=UTC),
    )

    action = result["action_records"][0]
    request_action = result["requests"][0]["execution_action"]

    assert result["execution"]["needs_review_count"] == 1
    assert result["runs"][0]["status"] == "needs_review"
    assert action["status"] == "needs_review"
    assert action["review_required"] is True
    assert action["retryable"] is True
    assert action["error_summary"] == ""
    assert action["workflow_owner"] == "ddr"
    assert "missing Drive folder prerequisite" in action["action_taken"]
    assert "AADP/Rhodes folder provisioning" in action["action_taken"]
    assert "verify Rhodes exposes the site Drive folder URL" in action["review_reason"]
    assert "no source-specific execution handler" not in action["action_taken"]
    assert request_action["status"] == "needs_review"
    assert request_action["action_status"] == "needs_review"
    assert "AADP/Rhodes folder provisioning" in request_action["routing_instruction"]
    assert "no source-specific execution handler" not in json.dumps(result)


def test_ddr_review_execution_missing_drive_folder_url_dry_run_is_specific() -> None:
    result = execute_ddr_review_requests(
        {
            "schema_version": "review_execution_requests.v1",
            "requests": [
                {
                    "request_id": "review-request:decision-folder-dry",
                    "decision_id": "decision-folder-dry",
                    "action_id": "ddr:run-folder:step:readiness.check",
                    "decision": "approve",
                    "owning_workflow": "ddr",
                    "alert_type": "missing_drive_folder_url",
                    "site_name": "Alpha Los Angeles 5400 Beethoven St",
                }
            ],
        },
        now=datetime(2026, 6, 16, 18, 5, tzinfo=UTC),
        dry_run=True,
    )

    action = result["action_records"][0]

    assert result["execution"]["needs_review_count"] == 1
    assert action["status"] == "needs_review"
    assert "dry-run confirmed" in action["action_taken"]
    assert "site Drive folder context" in action["action_taken"]
    assert "AADP/Rhodes" in action["review_reason"]
    assert "no source-specific execution handler" not in action["action_taken"]


def test_ddr_review_execution_marks_not_applicable_as_skipped() -> None:
    result = execute_ddr_review_requests(
        {
            "schema_version": "review_execution_requests.v1",
            "requests": [
                {
                    "request_id": "review-request:decision-2",
                    "decision_id": "decision-2",
                    "action_id": "ddr:run-2:manual",
                    "decision": "markNotApplicable",
                    "owning_workflow": "ddr",
                    "alert_type": "inbox_scan_manual_review",
                }
            ],
        },
        now=datetime(2026, 6, 10, 18, 5, tzinfo=UTC),
    )

    action = result["action_records"][0]

    assert result["execution"]["status"] == "skipped"
    assert result["execution"]["skipped_count"] == 1
    assert action["status"] == "skipped_already_corrected"
    assert action["review_required"] is False
    assert result["requests"][0]["execution_action"]["status"] == "skipped"


def test_ddr_review_execution_sanitizes_echoed_request_and_action_text() -> None:
    result = execute_ddr_review_requests(
        {
            "schema_version": "review_execution_requests.v1",
            "requests": [
                {
                    "request_id": "review-request:decision-3",
                    "decision_id": "decision-3",
                    "action_id": "ddr:run-3:source.alert",
                    "decision": "approve",
                    "owning_workflow": "ddr",
                    "alert_type": "source_read_issue",
                    "site_name": "Alpha Private",
                    "action_requested": (
                        "Review GOOGLE_CHAT_WEBHOOK_URL for owner@example.com at "
                        "https://drive.google.com/file/d/abc Request ID: abc-123 "
                        "C:\\tmp\\secret\\file.json"
                    ),
                    "routing_instruction": (
                        "Route using GOOGLE_CHAT_WEBHOOK_URL for owner@example.com "
                        "at https://drive.google.com/file/d/abc Request ID: abc-123 "
                        "C:\\tmp\\secret\\file.json"
                    ),
                    "evidence_summary": "owner@example.com saw https://drive.google.com/file/d/private",
                }
            ],
        },
        now=datetime(2026, 6, 10, 18, 10, tzinfo=UTC),
    )

    rendered = json.dumps(result)

    assert "[credential name removed]" in rendered
    assert "[email removed]" in rendered
    assert "[private URL removed]" in rendered
    assert "[request id removed]" in rendered
    assert "[local path removed]" in rendered
    assert "GOOGLE_CHAT_WEBHOOK_URL" not in rendered
    assert "owner@example.com" not in rendered
    assert "drive.google.com" not in rendered
    assert "abc-123" not in rendered
    assert "C:\\tmp" not in rendered
