from __future__ import annotations

import json

from due_diligence_reporter.pipeline_contracts import (
    ArtifactRef,
    PipelineError,
    PipelineRun,
    QualityCheck,
    RunQualityReport,
    StepResult,
)
from due_diligence_reporter.pipeline_manifest import manifest_has_secret_like_value
from due_diligence_reporter.pipeline_quality import evaluate_run_quality


def _step(
    status: str = "succeeded",
    *,
    step: str = "readiness.check",
    error: PipelineError | None = None,
) -> StepResult:
    return StepResult(
        run_id="run-1",
        step=step,
        status=status,  # type: ignore[arg-type]
        started_at="2026-05-14T00:00:00+00:00",
        ended_at="2026-05-14T00:00:01+00:00",
        duration_ms=100,
        error=error,
        rerun_command=f"ddr rerun --run-id run-1 --step {step}" if error else None,
    )


def test_pipeline_run_serializes_to_json() -> None:
    run = PipelineRun(
        run_id="run-1",
        site_title="Alpha Keller",
        site_id="site-1",
        started_at="2026-05-14T00:00:00+00:00",
        ended_at="2026-05-14T00:00:02+00:00",
        final_status="report_created",
        steps=[
            StepResult(
                run_id="run-1",
                step="report.validate",
                status="succeeded",
                started_at="2026-05-14T00:00:00+00:00",
                ended_at="2026-05-14T00:00:01+00:00",
                duration_ms=100,
                artifacts=[
                    ArtifactRef(
                        kind="google_doc",
                        name="DD report",
                        uri="https://docs.google.com/document/d/doc123",
                        drive_file_id="doc123",
                    )
                ],
            )
        ],
        quality=RunQualityReport(
            score=100,
            band="green",
            checks=[QualityCheck("step_observability", "passed", 25, 25)],
        ),
    )

    parsed = json.loads(json.dumps(run.to_dict()))

    assert parsed["run_id"] == "run-1"
    assert parsed["steps"][0]["artifacts"][0]["drive_file_id"] == "doc123"
    assert parsed["quality"]["score"] == 100


def test_pipeline_run_emits_action_record_for_failed_step() -> None:
    err = PipelineError(
        code="report_generation_failed",
        message="Agent completed without creating a report",
        retryable=True,
        operator_action="ddr rerun --run-id run-1 --step report.generate",
    )
    run = PipelineRun(
        run_id="run-1",
        site_title="Alpha Keller",
        site_id="SITE1",
        started_at="2026-05-14T00:00:00+00:00",
        ended_at="2026-05-14T00:00:02+00:00",
        final_status="generation_failed",
        steps=[_step("failed", step="report.generate", error=err)],
    )

    record = run.to_dict()["action_records"][0]

    assert record["schema_version"] == "action_record.v1"
    assert record["action_id"] == "ddr:run-1:step:report.generate"
    assert record["source_workflow"] == "ddr"
    assert record["owning_workflow"] == "ddr"
    assert record["alert_type"] == "report_generation_failed"
    assert record["site_id"] == "SITE1"
    assert record["site_name"] == "Alpha Keller"
    assert record["site"] == {
        "site_id": "SITE1",
        "name": "Alpha Keller",
        "current_milestone": "",
    }
    assert record["status"] == "error"
    assert record["review"]["required"] is True
    assert record["error"] == {
        "summary": "Agent completed without creating a report",
        "retryable": True,
    }


def test_pipeline_run_marks_missing_drive_folder_without_site_id_as_source_context_blocked() -> None:
    err = PipelineError(
        code="missing_drive_folder_url",
        message=(
            "No Drive folder URL was supplied and Rhodes did not return a linked "
            "Google Drive folder for this site."
        ),
        retryable=False,
        operator_action="ddr rerun --run-id run-1 --step readiness.check",
    )
    run = PipelineRun(
        run_id="run-1",
        site_title="Alpha Los Angeles 5400 Beethoven St",
        site_id=None,
        started_at="2026-05-14T00:00:00+00:00",
        ended_at="2026-05-14T00:00:02+00:00",
        final_status="error",
        steps=[_step("blocked", step="readiness.check", error=err)],
    )

    record = run.to_dict()["action_records"][0]

    assert record["alert_type"] == "missing_drive_folder_url"
    assert record["site_id"] == ""
    assert record["site"]["site_id"] == ""
    assert "Resolve the Rhodes site ID" in record["action_requested"]
    assert "verified site ID" in record["review"]["reason"]
    assert "verified Rhodes site ID" in record["action_taken"]
    assert record["owning_workflow"] == "ddr"
    assert record["workflow_owner"] == "ddr"


def test_pipeline_run_emits_sanitized_action_record_for_open_question() -> None:
    run = PipelineRun(
        run_id="run-1",
        site_title="Alpha Keller",
        site_id="SITE1",
        started_at="2026-05-14T00:00:00+00:00",
        ended_at="2026-05-14T00:00:02+00:00",
        final_status="report_incomplete",
        steps=[_step("succeeded", step="report.generate")],
        open_questions=[
            {
                "open_question_id": "zoning-use",
                "display_text": "Confirm zoning use from the vendor SIR",
            }
        ],
    )

    record = run.to_dict()["action_records"][0]
    serialized = json.dumps(record)

    assert record["action_id"] == "ddr:run-1:open_question:zoning_use"
    assert record["alert_type"] == "open_verification_item"
    assert record["status"] == "needs_review"
    assert record["action_requested"] == (
        "Resolve DDR verification item and rerun or republish if needed."
    )
    assert record["review"]["reason"] == "DDR open verification item needs operator review."
    assert "Confirm zoning use" not in serialized


def test_pipeline_run_emits_completed_actions_for_rhodes_writes() -> None:
    run = PipelineRun(
        run_id="run-1",
        site_title="Alpha Keller",
        site_id="SITE1",
        started_at="2026-05-14T00:00:00+00:00",
        ended_at="2026-05-14T00:00:02+00:00",
        final_status="report_created",
        steps=[
            _step("succeeded", step="rhodes.due_diligence_update"),
            _step("succeeded", step="rhodes.report_event"),
        ],
        rhodes_due_diligence_update={
            "status": "updated",
            "updated_fields": ["status", "dateCompleted", "ddReportLink"],
        },
        rhodes_report_event={
            "status": "created",
            "rhodes_note_id": "NOTE1",
            "owner_notification": "mentioned",
        },
    )

    actions = {record["alert_type"]: record for record in run.to_dict()["action_records"]}

    assert actions["ddr_sor_updated"]["status"] == "completed"
    assert actions["ddr_sor_updated"]["review_required"] is False
    assert actions["ddr_sor_updated"]["site_id"] == "SITE1"
    assert "ddReportLink" in actions["ddr_sor_updated"]["evidence_summary"]
    assert actions["ddr_p1_note_created"]["status"] == "completed"
    assert actions["ddr_p1_note_created"]["review_required"] is False
    assert "note_id=NOTE1" in actions["ddr_p1_note_created"]["evidence_summary"]


def test_pipeline_run_emits_aadp_route_for_missing_p1_dri() -> None:
    run = PipelineRun(
        run_id="run-1",
        site_title="Alpha Keller",
        site_id="SITE1",
        started_at="2026-05-14T00:00:00+00:00",
        ended_at="2026-05-14T00:00:02+00:00",
        final_status="report_created",
        steps=[_step("succeeded", step="rhodes.owner_lookup")],
        p1_dri_missing=True,
    )

    record = run.to_dict()["action_records"][0]

    assert record["schema_version"] == "action_record.v1"
    assert record["action_id"] == "ddr:site:SITE1:missing_p1_dri"
    assert record["source_workflow"] == "ddr"
    assert record["owning_workflow"] == "aadp"
    assert record["workflow_owner"] == "aadp"
    assert record["alert_type"] == "missing_p1_dri"
    assert record["status"] == "queued"
    assert record["review_required"] is False
    assert record["retryable"] is True
    assert "current P1 DRI" in record["evidence_summary"]


def test_quality_caps_failed_step_without_operator_action() -> None:
    run = PipelineRun(
        run_id="run-1",
        site_title="Alpha Keller",
        site_id=None,
        started_at="2026-05-14T00:00:00+00:00",
        ended_at="2026-05-14T00:00:01+00:00",
        final_status="generation_failed",
        steps=[_step("failed", step="report.generate")],
    )

    quality = evaluate_run_quality(run, manifest_persisted=True)

    assert quality.score <= 70
    assert "failed_or_blocked_without_operator_action" in quality.caps_applied


def test_waiting_on_docs_can_score_high_when_actionable() -> None:
    err = PipelineError(
        code="missing_required_documents",
        message="SIR",
        retryable=False,
        operator_action="ddr rerun --run-id run-1 --step readiness.check",
    )
    run = PipelineRun(
        run_id="run-1",
        site_title="Alpha Keller",
        site_id=None,
        started_at="2026-05-14T00:00:00+00:00",
        ended_at="2026-05-14T00:00:01+00:00",
        final_status="waiting_on_docs",
        steps=[_step("blocked", step="readiness.check", error=err)],
    )

    quality = evaluate_run_quality(run, manifest_persisted=True)

    assert quality.score >= 70
    assert quality.band in {"green", "yellow"}


def test_manifest_secret_detection_rejects_sensitive_keys() -> None:
    payload = {
        "run_id": "run-1",
        "steps": [
            {
                "step": "bad",
                "metadata": {"access_token": "ya29.this-is-a-secret-looking-value"},
            }
        ],
    }

    assert manifest_has_secret_like_value(payload) is True


def test_manifest_secret_detection_allows_unresolved_tokens_label() -> None:
    payload = {
        "run_id": "run-1",
        "unresolved_tokens": ["exec.c_answer"],
    }

    assert manifest_has_secret_like_value(payload) is False
