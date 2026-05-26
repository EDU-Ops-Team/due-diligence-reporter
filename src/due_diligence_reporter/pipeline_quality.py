"""Deterministic quality scoring for DD pipeline run manifests."""

from __future__ import annotations

from .pipeline_contracts import (
    PipelineRun,
    QualityBand,
    QualityCheck,
    QualityCheckStatus,
    RunQualityReport,
    StepResult,
)


def quality_band(score: int) -> QualityBand:
    if score >= 90:
        return "green"
    if score >= 70:
        return "yellow"
    if score >= 40:
        return "orange"
    return "red"


def evaluate_run_quality(
    run: PipelineRun,
    *,
    manifest_persisted: bool,
    secret_detected: bool = False,
) -> RunQualityReport:
    """Score how trustworthy and actionable a run result is."""
    checks = [
        _step_observability(run),
        _artifact_integrity(run, manifest_persisted),
        _source_quality(run),
        _report_validity(run),
        _side_effect_reliability(run),
    ]
    score = sum(check.points for check in checks)
    caps = _quality_caps(run, manifest_persisted, secret_detected)
    for _cap_name, cap_value in caps:
        if score > cap_value:
            score = cap_value
    return RunQualityReport(
        score=max(0, min(100, score)),
        band=quality_band(score),
        checks=checks,
        caps_applied=[name for name, _value in caps],
    )


def _step_observability(run: PipelineRun) -> QualityCheck:
    points = 0
    details: list[str] = []
    if run.run_id:
        points += 5
    else:
        details.append("missing run_id")
    if run.steps:
        points += 8
    else:
        details.append("no steps recorded")
    if all(step.duration_ms >= 0 for step in run.steps):
        points += 4
    failed = [step for step in run.steps if step.status in {"failed", "blocked"}]
    if failed:
        if failed[0].error:
            points += 4
        else:
            details.append("failed/blocked step missing error")
        if failed[0].rerun_command or (failed[0].error and failed[0].error.operator_action):
            points += 4
        else:
            details.append("failed/blocked step missing operator action")
    else:
        points += 8
    return QualityCheck("step_observability", _status(points, 25), points, 25, "; ".join(details))


def _artifact_integrity(run: PipelineRun, manifest_persisted: bool) -> QualityCheck:
    points = 0
    details: list[str] = []
    if manifest_persisted:
        points += 8
    else:
        details.append("manifest not persisted")
    if any(step.artifacts for step in run.steps):
        points += 6
    elif run.final_status in {"waiting_on_docs", "report_exists", "yielded_to_pipeline"}:
        points += 6
    else:
        details.append("no artifacts recorded")
    if all(_artifacts_have_refs(step) for step in run.steps):
        points += 6
    else:
        details.append("artifact missing uri or drive_file_id")
    return QualityCheck("artifact_integrity", _status(points, 20), points, 20, "; ".join(details))


def _source_quality(run: PipelineRun) -> QualityCheck:
    readiness = _step(run, "readiness.check")
    source_alert = _step(run, "source.alert")
    if readiness and readiness.status == "blocked":
        return QualityCheck("source_quality", "passed", 20, 20, "blocked with explicit missing docs")
    if source_alert and source_alert.status == "failed":
        return QualityCheck("source_quality", "warning", 12, 20, "source read issues detected")
    if readiness and readiness.status == "succeeded":
        return QualityCheck("source_quality", "passed", 20, 20)
    return QualityCheck("source_quality", "not_applicable", 16, 20, "source checks not reached")


def _report_validity(run: PipelineRun) -> QualityCheck:
    validation = _step(run, "report.validate")
    if not validation:
        if run.final_status in {"waiting_on_docs", "report_exists", "yielded_to_pipeline"}:
            return QualityCheck("report_validity", "not_applicable", 25, 25, "report not expected")
        return QualityCheck("report_validity", "warning", 12, 25, "validation not recorded")
    if validation.status == "succeeded":
        return QualityCheck("report_validity", "passed", 25, 25)
    if validation.status in {"failed", "blocked"}:
        return QualityCheck("report_validity", "failed", 0, 25, validation.error.message if validation.error else "")
    return QualityCheck("report_validity", "not_applicable", 20, 25, validation.skipped_reason or "")


def _side_effect_reliability(run: PipelineRun) -> QualityCheck:
    side_effects = [
        step for step in run.steps
        if step.step in {"manifest.save", "notify.email", "notify.chat"}
    ]
    if not side_effects:
        return QualityCheck("side_effect_reliability", "not_applicable", 8, 10)
    failed = [step.step for step in side_effects if step.status == "failed"]
    if failed:
        return QualityCheck("side_effect_reliability", "warning", 5, 10, ", ".join(failed))
    return QualityCheck("side_effect_reliability", "passed", 10, 10)


def _quality_caps(
    run: PipelineRun,
    manifest_persisted: bool,
    secret_detected: bool,
) -> list[tuple[str, int]]:
    caps: list[tuple[str, int]] = []
    if secret_detected:
        caps.append(("secret_like_value_detected", 0))
    if not run.run_id:
        caps.append(("no_run_id", 40))
    if not manifest_persisted:
        caps.append(("no_persisted_manifest", 60))
    failed = [step for step in run.steps if step.status in {"failed", "blocked"}]
    if failed and not failed[0].step:
        caps.append(("failed_or_blocked_without_failed_step", 50))
    if failed and not (failed[0].error and failed[0].error.operator_action):
        caps.append(("failed_or_blocked_without_operator_action", 70))
    validation = _step(run, "report.validate")
    if run.final_status == "report_incomplete" or (validation and validation.status == "failed"):
        caps.append(("report_created_with_unresolved_required_tokens", 69))
    source_alert = _step(run, "source.alert")
    if source_alert and source_alert.status == "failed":
        caps.append(("required_source_doc_unreadable_after_readiness", 69))
    return caps


def _status(points: int, max_points: int) -> QualityCheckStatus:
    if points == max_points:
        return "passed"
    if points == 0:
        return "failed"
    return "warning"


def _step(run: PipelineRun, name: str) -> StepResult | None:
    return next((step for step in run.steps if step.step == name), None)


def _artifacts_have_refs(step: StepResult) -> bool:
    return all(artifact.uri or artifact.drive_file_id for artifact in step.artifacts)
