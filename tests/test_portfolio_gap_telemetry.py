from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from due_diligence_reporter.portfolio_automation_gaps import (
    build_portfolio_automation_gap_snapshot,
)
from due_diligence_reporter.portfolio_gap_telemetry import (
    build_portfolio_gap_workflow_telemetry,
)

from .test_portfolio_automation_gaps import FakeRhodesPortfolioClient

ROOT = Path(__file__).resolve().parents[1]


def test_portfolio_gap_workflow_telemetry_emits_run_and_actions_without_private_context() -> None:
    snapshot = build_portfolio_automation_gap_snapshot(
        client=FakeRhodesPortfolioClient(),  # type: ignore[arg-type]
        include_clean=False,
    )

    telemetry = build_portfolio_gap_workflow_telemetry(
        snapshot,
        run_id="portfolio-gaps-123",
        started_at="2026-06-09T16:00:00+00:00",
        finished_at="2026-06-09T16:01:00+00:00",
        trigger="schedule",
        workflow_run_url="https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/123",
        notification_result={"status": "sent", "posted": 1, "sites_with_gaps": 1},
    )

    rendered = json.dumps(telemetry)
    assert telemetry["schema_version"] == "workflow_run.v1"
    assert telemetry["source_type"] == "portfolio_automation_gaps"
    assert telemetry["workflow_id"] == "portfolio-gaps"
    assert telemetry["run_id"] == "portfolio-gaps-123"
    assert telemetry["status"] == "needs_review"
    assert telemetry["counts"]["sites"] == 1
    assert telemetry["counts"]["missing_p1_dri"] == 1
    assert telemetry["counts"]["missing_drive_folder"] == 1
    assert "missing_required_documents" not in telemetry["counts"]
    assert {record["gap_type"] for record in telemetry["action_records"]} == {
        "missing_p1_dri",
        "missing_drive_folder",
        "open_automation_failures",
        "pending_review_tasks",
    }
    assert telemetry["site_gaps"][0]["site_name"] == "Alpha Tulsa 6940 S Utica Ave"
    assert telemetry["site_gaps"][0]["current_milestone"] == "Acquiring Property"
    assert telemetry["site_gaps"][0]["alert_actions"][0]["action_status"] == "queued"
    assert "Chat notification sent" in telemetry["summary"]
    assert "https://drive.google.com" not in rendered
    assert "owner@example.com" not in rendered
    assert "propertyConditionAssessment" not in rendered
    assert "floorPlan" not in rendered


def test_portfolio_gap_workflow_telemetry_records_failed_run_without_snapshot() -> None:
    telemetry = build_portfolio_gap_workflow_telemetry(
        {"status": "failed", "generated_at": "2026-06-09T16:01:00+00:00"},
        run_id="portfolio-gaps-124",
        started_at="2026-06-09T16:00:00+00:00",
        finished_at="2026-06-09T16:01:00+00:00",
        trigger="workflow_dispatch",
        source_status="failure",
    )

    assert telemetry["status"] == "failed"
    assert telemetry["steps"][0]["status"] == "failed"
    assert telemetry["counts"]["sites"] == 0
    assert telemetry["action_records"] == []


def test_build_portfolio_gap_telemetry_script_writes_workflow_artifact(tmp_path: Path) -> None:
    snapshot = build_portfolio_automation_gap_snapshot(
        client=FakeRhodesPortfolioClient(),  # type: ignore[arg-type]
        include_clean=False,
    )
    snapshot_path = tmp_path / "portfolio-automation-gaps.json"
    notification_path = tmp_path / "portfolio-automation-gaps-notification.json"
    output_path = tmp_path / "reports" / "telemetry" / "portfolio-automation-gaps-telemetry.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    notification_path.write_text(
        json.dumps({"status": "skipped", "reason": "missing_google_chat_webhook_url"}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_portfolio_gap_telemetry.py",
            "--snapshot",
            str(snapshot_path),
            "--notification-result",
            str(notification_path),
            "--output",
            str(output_path),
            "--run-id",
            "portfolio-gaps-456",
            "--trigger",
            "schedule",
            "--started-at",
            "2026-06-09T16:00:00+00:00",
            "--finished-at",
            "2026-06-09T16:01:00+00:00",
            "--workflow-run-url",
            "https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/456",
            "--source-status",
            "success",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    rendered = json.dumps(payload)
    assert payload["schema_version"] == "workflow_run.v1"
    assert payload["run_id"] == "portfolio-gaps-456"
    assert payload["status"] == "needs_review"
    assert payload["counts"]["sites"] == 1
    assert payload["action_records"]
    assert payload["steps"][-1]["key"] == "telemetry_artifact"
    assert "https://drive.google.com" not in rendered
    assert "owner@example.com" not in rendered
    assert "portfolio-gaps-456" in result.stdout
