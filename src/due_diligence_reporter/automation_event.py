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
