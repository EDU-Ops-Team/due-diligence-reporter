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
