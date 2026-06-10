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
    assert result["requests"][0]["execution_action"]["source_action_id"] == action["action_id"]
    assert result["requests"][0]["execution_action"]["status"] == "needs_review"
    assert run["schema_version"] == "workflow_run.v1"
    assert run["workflow_id"] == "ddr"
    assert run["subworkflow_id"] == "ddr-review-execution"
    assert run["status"] == "needs_review"


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
