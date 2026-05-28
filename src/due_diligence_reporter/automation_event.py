"""Shared AutomationEvent rendering for Rhodes notes and alerts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class AutomationEvent:
    """Canonical event shape for material automation outcomes."""

    source_system: str
    source_id: str
    site_id: str
    site_name: str
    event_type: str
    artifact_ids: dict[str, str] = field(default_factory=dict)
    decision_required: bool = False
    requested_decision: str | None = None
    mutation_status: str = ""
    retry_state: dict[str, Any] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def render_automation_event_note(event: AutomationEvent) -> str:
    """Render an AutomationEvent as a stable Rhodes note body."""

    lines = [
        "AutomationEvent v1",
        f"Source: {event.source_system}",
        f"Source ID: {event.source_id}",
        f"Kind: {event.event_type}",
        f"Site: {event.site_name}",
        f"Site ID: {event.site_id or 'unknown'}",
        f"Decision required: {'yes' if event.decision_required else 'no'}",
    ]
    if event.requested_decision:
        lines.append(f"Requested decision: {event.requested_decision}")
    if event.mutation_status:
        lines.append(f"Mutation status: {event.mutation_status}")
    if event.retry_state:
        lines.append(f"Retry state: {_format_retry_state(event.retry_state)}")
    for label, value in event.artifact_ids.items():
        lines.append(f"{label}: {value or 'unknown'}")
    for label, value in event.details.items():
        if value:
            lines.append(f"{label}: {value}")
    lines.append(f"Created at: {event.created_at}")
    return "\n".join(lines)


def build_document_registration_failed_event(
    *,
    site_summary: dict[str, Any],
    registration: dict[str, Any],
    doc_type: str,
    drive_file: dict[str, Any],
    drive_filename: str,
    original_filename: str,
    email_subject: str,
    message_id: str,
    thread_id: str,
    created_at: str | None = None,
) -> AutomationEvent:
    """Build the DDR document-registration failure event."""

    attempts = registration.get("retry_attempts") or "unknown"
    retry_limit = registration.get("retry_limit") or "unknown"
    event = AutomationEvent(
        source_system="due-diligence-reporter",
        source_id=message_id,
        site_id=str(site_summary.get("id") or site_summary.get("site_id") or "").strip(),
        site_name=str(site_summary.get("title") or "Unknown site").strip(),
        event_type="document_registration_failed",
        artifact_ids={
            "Drive file ID": str(drive_file.get("id") or "").strip(),
            "Gmail message ID": message_id,
            "Gmail thread ID": thread_id,
        },
        decision_required=True,
        requested_decision="repair or register the Rhodes document link for the Drive file",
        mutation_status=str(registration.get("status") or "failed").strip(),
        retry_state={
            "attempts": attempts,
            "limit": retry_limit,
            "exhausted": registration.get("retry_exhausted"),
        },
        details={
            "Owner": _format_owner(site_summary),
            "DDR doc type": doc_type,
            "Rhodes doc type": str(registration.get("rhodes_doc_type") or "unknown"),
            "Rhodes milestone": str(registration.get("rhodes_milestone") or "unknown"),
            "Reason": str(registration.get("reason") or "registration_failed").strip(),
            "Drive file": drive_filename,
            "Original filename": original_filename,
            "Gmail subject": email_subject,
            "Drive URL": str(drive_file.get("webViewLink") or "").strip(),
            "Error": str(registration.get("error") or "").strip(),
        },
        created_at=created_at or datetime.now(UTC).isoformat(),
    )
    return event


def build_inbox_manual_review_required_event(
    *,
    site_id: str,
    site_name: str,
    message_id: str,
    thread_id: str,
    filename: str,
    doc_type: str,
    confidence: float,
    email_subject: str,
    reason: str,
    error: str = "",
    created_at: str | None = None,
) -> AutomationEvent:
    """Build the DDR inbox manual-review decision event."""

    details = {
        "Gmail subject": email_subject.strip(),
        "Filename": filename.strip(),
        "DDR doc type": doc_type.strip(),
        "Confidence": f"{confidence:.0%}",
        "Manual review reason": reason.strip() or "manual_review",
        "Gmail thread ID": thread_id.strip(),
        "Error": error.strip(),
    }
    return AutomationEvent(
        source_system="due-diligence-reporter",
        source_id=f"{message_id}:{filename}:{reason}",
        site_id=site_id.strip(),
        site_name=site_name.strip() or "Unknown site",
        event_type="inbox_manual_review_required",
        artifact_ids={
            "Gmail message ID": message_id,
        },
        decision_required=True,
        requested_decision="review the inbound DD attachment and repair filing or site routing",
        mutation_status=reason.strip() or "manual_review",
        details=details,
        created_at=created_at or datetime.now(UTC).isoformat(),
    )


def build_dd_report_summary_event(
    *,
    site_id: str,
    site_name: str,
    run_id: str,
    doc_id: str | None,
    doc_url: str | None,
    source_event: dict[str, Any] | None = None,
    open_questions: list[dict[str, Any]] | None = None,
    closed_open_questions: list[dict[str, Any]] | None = None,
    created_at: str | None = None,
) -> AutomationEvent:
    """Build the DDR generated/updated report summary event."""

    open_items = open_questions or []
    closed_items = closed_open_questions or []
    source = source_event or {}
    is_update = bool(source)
    artifact_ids = {
        "Run ID": run_id,
    }
    if doc_id:
        artifact_ids["DD report ID"] = doc_id
    source_drive_file_id = str(source.get("drive_file_id") or "").strip()
    if source_drive_file_id:
        artifact_ids["Source Drive file ID"] = source_drive_file_id

    details = {
        "DD report URL": str(doc_url or "").strip(),
        "Trigger source": str(source.get("source_type") or "").strip(),
        "Source file": str(source.get("file_name") or "").strip(),
        "Open item count": str(len(open_items)),
        "Closed item count": str(len(closed_items)),
    }
    details.update(_indexed_item_details("Open item", open_items))
    details.update(_indexed_item_details("Closed item", closed_items))

    return AutomationEvent(
        source_system="due-diligence-reporter",
        source_id=run_id,
        site_id=site_id.strip(),
        site_name=site_name.strip() or "Unknown site",
        event_type="dd_report_updated" if is_update else "dd_report_created",
        artifact_ids=artifact_ids,
        decision_required=bool(open_items),
        requested_decision=(
            "review and resolve DDR open verification items" if open_items else None
        ),
        mutation_status="report_created",
        details=details,
        created_at=created_at or datetime.now(UTC).isoformat(),
    )


def build_source_review_required_event(
    *,
    site_id: str,
    site_name: str,
    run_id: str,
    issues: list[dict[str, str]],
    drive_folder_url: str = "",
    trace_url: str = "",
    created_at: str | None = None,
) -> AutomationEvent:
    """Build the DDR source-read manual review event."""

    details = {
        "Source issue count": str(len(issues)),
        "Drive folder": drive_folder_url.strip(),
        "Trace": trace_url.strip(),
    }
    details.update(_indexed_source_issue_details(issues))

    return AutomationEvent(
        source_system="due-diligence-reporter",
        source_id=run_id,
        site_id=site_id.strip(),
        site_name=site_name.strip() or "Unknown site",
        event_type="source_review_required",
        artifact_ids={
            "Run ID": run_id,
        },
        decision_required=True,
        requested_decision="review unreadable DDR source documents",
        mutation_status="source_read_issue",
        details=details,
        created_at=created_at or datetime.now(UTC).isoformat(),
    )


def build_vendor_gate_review_required_event(
    *,
    site_id: str,
    site_name: str,
    run_id: str,
    failure_reason: str,
    mutation_status: str,
    drive_folder_url: str = "",
    trace_url: str = "",
    created_at: str | None = None,
) -> AutomationEvent:
    """Build the DDR complete-input vendor-gate review event."""

    return AutomationEvent(
        source_system="due-diligence-reporter",
        source_id=run_id,
        site_id=site_id.strip(),
        site_name=site_name.strip() or "Unknown site",
        event_type="vendor_gate_review_required",
        artifact_ids={
            "Run ID": run_id,
        },
        decision_required=True,
        requested_decision="review complete vendor inputs and repair DDR generation",
        mutation_status=mutation_status.strip() or "vendor_gate_review_required",
        details={
            "Required inputs": (
                "vendor SIR, vendor Building Inspection, RayCon Scenario JSON"
            ),
            "Failure reason": failure_reason.strip()[:1000],
            "Drive folder": drive_folder_url.strip(),
            "Trace": trace_url.strip(),
        },
        created_at=created_at or datetime.now(UTC).isoformat(),
    )


def build_dd_report_republish_failed_event(
    *,
    site_id: str,
    site_name: str,
    reason: str,
    content_fingerprint: str,
    failure_reason: str,
    mutation_status: str,
    source_event: dict[str, Any] | None = None,
    drive_folder_url: str = "",
    run_id: str = "",
    doc_url: str = "",
    manifest_path: str = "",
    created_at: str | None = None,
) -> AutomationEvent:
    """Build the DDR republish failure review event."""

    source = source_event or {}
    artifact_ids = {
        "Content fingerprint": content_fingerprint.strip(),
    }
    if run_id:
        artifact_ids["Run ID"] = run_id
    source_drive_file_id = str(source.get("drive_file_id") or "").strip()
    if source_drive_file_id:
        artifact_ids["Source Drive file ID"] = source_drive_file_id

    details = {
        "Trigger source": str(source.get("source_type") or reason).strip(),
        "Source file": str(source.get("file_name") or "").strip(),
        "Failure reason": failure_reason.strip()[:1000],
        "Pipeline status": mutation_status.strip(),
        "Drive folder": drive_folder_url.strip(),
        "DD report URL": doc_url.strip(),
        "Manifest": manifest_path.strip(),
    }

    return AutomationEvent(
        source_system="due-diligence-reporter",
        source_id=run_id.strip() or f"{site_id}:{reason}:{content_fingerprint}".strip(":"),
        site_id=site_id.strip(),
        site_name=site_name.strip() or "Unknown site",
        event_type="dd_report_republish_failed",
        artifact_ids=artifact_ids,
        decision_required=True,
        requested_decision="review failed DDR republish and repair report generation",
        mutation_status=mutation_status.strip() or "republish_failed",
        details=details,
        created_at=created_at or datetime.now(UTC).isoformat(),
    )


def build_raycon_followup_alert_event(
    *,
    site_id: str,
    site_name: str,
    run_id: str,
    alert_type: str,
    message: str,
    drive_folder_url: str = "",
    block_plan_file_id: str = "",
    raycon_run_id: str = "",
    created_at: str | None = None,
) -> AutomationEvent:
    """Build the DDR RayCon follow-up review event."""

    artifact_ids = {
        "Run ID": run_id,
    }
    if block_plan_file_id:
        artifact_ids["Block Plan file ID"] = block_plan_file_id
    if raycon_run_id:
        artifact_ids["RayCon run ID"] = raycon_run_id

    return AutomationEvent(
        source_system="due-diligence-reporter",
        source_id=run_id,
        site_id=site_id.strip(),
        site_name=site_name.strip() or "Unknown site",
        event_type="raycon_followup_alert",
        artifact_ids=artifact_ids,
        decision_required=True,
        requested_decision="review RayCon follow-up alert and unblock scenario generation",
        mutation_status=alert_type.strip() or "raycon_followup_alert",
        details={
            "Message": message.strip()[:1000],
            "Drive folder": drive_folder_url.strip(),
        },
        created_at=created_at or datetime.now(UTC).isoformat(),
    )


def _format_owner(site_summary: dict[str, Any]) -> str:
    name = str(site_summary.get("p1_assignee_name") or "").strip()
    email = str(site_summary.get("p1_assignee_email") or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return email or name or "No owner assigned"


def _format_retry_state(retry_state: dict[str, Any]) -> str:
    attempts = retry_state.get("attempts", "unknown")
    limit = retry_state.get("limit", "unknown")
    exhausted = retry_state.get("exhausted")
    if exhausted is None:
        return f"attempts={attempts}/{limit}"
    return f"attempts={attempts}/{limit}; exhausted={str(bool(exhausted)).lower()}"


def _indexed_item_details(
    label: str,
    items: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> dict[str, str]:
    details: dict[str, str] = {}
    for index, item in enumerate(items[:limit], start=1):
        text = str(item.get("display_text") or item.get("text") or "").strip()
        if text:
            details[f"{label} {index}"] = text
    return details


def _indexed_source_issue_details(
    issues: list[dict[str, str]],
    *,
    limit: int = 5,
) -> dict[str, str]:
    details: dict[str, str] = {}
    for index, issue in enumerate(issues[:limit], start=1):
        doc_type = str(issue.get("doc_type") or "Source document").strip()
        file_name = str(issue.get("file_name") or "").strip()
        problem = str(issue.get("problem") or "").strip()
        parts = [doc_type]
        if file_name and file_name != doc_type:
            parts.append(file_name)
        if problem:
            parts.append(f"Problem: {problem}")
        details[f"Source issue {index}"] = " | ".join(parts)
    return details
