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
