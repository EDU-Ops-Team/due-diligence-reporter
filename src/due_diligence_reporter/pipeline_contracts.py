"""Typed contracts for DD pipeline run observability."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

StepStatus = Literal["pending", "running", "succeeded", "failed", "blocked", "skipped"]
QualityBand = Literal["green", "yellow", "orange", "red"]
QualityCheckStatus = Literal["passed", "failed", "warning", "not_applicable"]


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat()


def make_run_id(site_title: str) -> str:
    """Build a stable-enough, human-readable run id for one pipeline run."""
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in site_title).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    safe = safe[:48] or "site"
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"{stamp}-{safe}-{uuid4().hex[:8]}"


@dataclass
class PipelineError:
    code: str
    message: str
    retryable: bool
    operator_action: str
    cause: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "operator_action": self.operator_action,
            "cause": self.cause,
        }


@dataclass
class ArtifactRef:
    kind: str
    name: str
    uri: str | None = None
    drive_file_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "uri": self.uri,
            "drive_file_id": self.drive_file_id,
            "metadata": self.metadata,
        }


@dataclass
class StepResult:
    run_id: str
    step: str
    status: StepStatus
    started_at: str
    ended_at: str
    duration_ms: int
    attempt: int = 1
    error: PipelineError | None = None
    artifacts: list[ArtifactRef] = field(default_factory=list)
    rerun_command: str | None = None
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "step": self.step,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "attempt": self.attempt,
            "error": self.error.to_dict() if self.error else None,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "rerun_command": self.rerun_command,
            "skipped_reason": self.skipped_reason,
        }


@dataclass
class QualityCheck:
    name: str
    status: QualityCheckStatus
    points: int
    max_points: int
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "points": self.points,
            "max_points": self.max_points,
            "details": self.details,
        }


@dataclass
class RunQualityReport:
    score: int
    band: QualityBand
    checks: list[QualityCheck]
    caps_applied: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "band": self.band,
            "checks": [check.to_dict() for check in self.checks],
            "caps_applied": self.caps_applied,
        }


@dataclass
class PipelineRun:
    run_id: str
    site_title: str
    site_id: str | None
    started_at: str
    ended_at: str | None
    final_status: str
    steps: list[StepResult]
    quality: RunQualityReport | None = None
    sir_learning_review: dict[str, Any] | None = None
    source_event: dict[str, Any] | None = None
    open_questions: list[dict[str, Any]] = field(default_factory=list)
    closed_open_questions: list[dict[str, Any]] = field(default_factory=list)
    republish_summary: dict[str, Any] | None = None
    manifest_path: str | None = None
    manifest_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "site_title": self.site_title,
            "site_id": self.site_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "final_status": self.final_status,
            "steps": [step.to_dict() for step in self.steps],
            "quality": self.quality.to_dict() if self.quality else None,
            "sir_learning_review": self.sir_learning_review,
            "source_event": self.source_event,
            "open_questions": self.open_questions,
            "closed_open_questions": self.closed_open_questions,
            "republish_summary": self.republish_summary,
            "manifest_path": self.manifest_path,
            "manifest_url": self.manifest_url,
            "failed_step": failed_step_name(self.steps),
            "next_operator_action": next_operator_action(self.steps),
        }


def failed_step_name(steps: list[StepResult]) -> str | None:
    """Return the first failed or blocked step name."""
    for step in steps:
        if step.status in {"failed", "blocked"}:
            return step.step
    return None


def next_operator_action(steps: list[StepResult]) -> str | None:
    """Return the first operator action attached to a failed or blocked step."""
    for step in steps:
        if step.status in {"failed", "blocked"} and step.error:
            return step.error.operator_action
    return None
