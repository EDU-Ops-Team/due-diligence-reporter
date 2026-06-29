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

    if event.event_type in {"dd_report_created", "dd_report_updated"}:
        return _render_dd_report_event_note(event)
    if event.event_type == "dd_report_republish_candidate_created":
        return _render_dd_report_candidate_note(event)
    if event.event_type == "document_registration_failed":
        return _render_document_registration_failed_note(event)
    if event.event_type == "inbox_manual_review_required":
        return _render_inbox_manual_review_required_note(event)
    if event.event_type == "source_review_required":
        return _render_source_review_required_note(event)
    if event.event_type == "vendor_gate_review_required":
        return _render_vendor_gate_review_required_note(event)
    if event.event_type == "dd_report_republish_failed":
        return _render_dd_report_republish_failed_note(event)
    if event.event_type == "raycon_followup_alert":
        return _render_raycon_followup_alert_note(event)

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


def _render_document_registration_failed_note(event: AutomationEvent) -> str:
    lines = [
        "Document filing review",
        "Action needed: Review a document that did not finish filing in Rhodes.",
        f"Site: {event.site_name}",
        "Status: Rhodes document registration did not complete.",
    ]
    _append_detail_line(lines, "Document", event.details.get("Drive file"))
    _append_detail_line(lines, "Type", event.details.get("DDR doc type"))
    _append_detail_line(lines, "Drive link", event.details.get("Drive URL"))
    lines.append("Next steps:")
    lines.extend([
        "- Review the document in Drive.",
        "- Register or repair the Rhodes document link.",
    ])
    return "\n".join(lines)


def _render_inbox_manual_review_required_note(event: AutomationEvent) -> str:
    lines = [
        "Document intake review",
        "Action needed: Review an inbound due diligence attachment before filing.",
        f"Site: {event.site_name}",
        "Status: Needs manual review.",
    ]
    _append_detail_line(lines, "Document", event.details.get("Filename"))
    _append_detail_line(lines, "Type", event.details.get("DDR doc type"))
    lines.append("Next steps:")
    lines.extend([
        "- Confirm the correct site and document type.",
        "- File or reroute the attachment.",
    ])
    return "\n".join(lines)


def _render_source_review_required_note(event: AutomationEvent) -> str:
    issue_count = _detail_int(event, "Source issue count")
    issue_text = (
        f"{issue_count} source document(s) need review."
        if issue_count
        else "One or more source documents need review."
    )
    lines = [
        "Source document review",
        "Action needed: Review source documents DDR could not read.",
        f"Site: {event.site_name}",
        f"Status: {issue_text}",
        "Next steps:",
        "- Open the source documents in Drive.",
        "- Replace, repair, or re-upload unreadable files.",
        "- Rerun DDR when the source files are readable.",
    ]
    return "\n".join(lines)


def _render_vendor_gate_review_required_note(event: AutomationEvent) -> str:
    lines = [
        "DDR source review",
        "Action needed: Review complete vendor inputs before DDR can finish.",
        f"Site: {event.site_name}",
        "Status: DDR could not produce a complete report from the available inputs.",
    ]
    _append_detail_line(lines, "Required inputs", event.details.get("Required inputs"))
    lines.append("Next steps:")
    lines.extend([
        "- Review the vendor SIR, Building Inspection, and RayCon Scenario.",
        "- Repair the source issue.",
        "- Rerun DDR.",
    ])
    return "\n".join(lines)


def _render_dd_report_republish_failed_note(event: AutomationEvent) -> str:
    trigger_source = event.details.get("Trigger source", "").strip()
    source_file = event.details.get("Source file", "").strip()
    lines = [
        "DD report republish review",
        "Action needed: Review a failed DD report republish.",
        f"Site: {event.site_name}",
        "Status: DDR could not republish the report.",
    ]
    if trigger_source:
        lines.append(
            f"Latest source reviewed: {_format_republish_source(trigger_source, source_file)}"
        )
    _append_detail_line(lines, "DD report", event.details.get("DD report URL"))
    lines.append("Next steps:")
    lines.extend([
        "- Review the source update.",
        "- Repair the report generation issue.",
        "- Rerun the republish.",
    ])
    return "\n".join(lines)


def _render_raycon_followup_alert_note(event: AutomationEvent) -> str:
    lines = [
        "RayCon follow-up review",
        "Action needed: Review RayCon scenario generation for this site.",
        f"Site: {event.site_name}",
        "Status: RayCon scenario generation needs review.",
        "Next steps:",
        "- Check the Block Plan and RayCon Scenario inputs.",
        "- Repair the source issue.",
        "- Rerun RayCon follow-up.",
    ]
    return "\n".join(lines)


def _append_detail_line(lines: list[str], label: str, value: str | None) -> None:
    cleaned = str(value or "").strip()
    if _has_real_value(cleaned):
        lines.append(f"{label}: {cleaned}")


def _render_dd_report_event_note(event: AutomationEvent) -> str:
    """Render DD report activity as a concise user-facing site note."""

    open_count = _detail_int(event, "Open item count")
    closed_count = _detail_int(event, "Closed item count")
    doc_url = event.details.get("DD report URL", "").strip()
    trigger_source = event.details.get("Trigger source", "").strip()
    source_file = event.details.get("Source file", "").strip()
    outstanding_docs = event.details.get("Outstanding vendor docs", "").strip()
    rhodes_update = event.details.get("Rhodes due diligence update", "").strip()
    source_packet_status = event.details.get("M2 source packet", "").strip()

    lines = [
        "DD report update",
        f"Action needed: {_dd_report_action_needed(event, open_count)}",
        f"Site: {event.site_name}",
        f"Status: {_dd_report_status_line(event, rhodes_update)}",
    ]
    if doc_url:
        lines.append(f"DD report: {doc_url}")
    if open_count:
        lines.append(f"Open verification items: {open_count}")
    if _has_real_value(outstanding_docs):
        lines.append(f"Missing vendor docs: {outstanding_docs}")
    if trigger_source:
        source_label = _format_republish_source(trigger_source, source_file)
        lines.append(f"Latest source reviewed: {source_label}")
    if closed_count:
        lines.append(f"Resolved this run: {closed_count}")
    source_packet_lines = _m2_source_packet_lines(event.details)
    if source_packet_status or source_packet_lines:
        lines.append(f"M2 source packet: {source_packet_status or 'updated'}")
        lines.extend(source_packet_lines)
    if open_count > 0:
        lines.append(
            "Close open items after the answer is added to the DD report or source record."
        )
    lines.append("Next steps:")
    lines.extend(_dd_report_next_steps(open_count, rhodes_update))
    return "\n".join(lines)


def _render_dd_report_candidate_note(event: AutomationEvent) -> str:
    trigger_source = event.details.get("Trigger source", "").strip()
    source_file = event.details.get("Source file", "").strip()
    outstanding_docs = event.details.get("Outstanding vendor docs", "").strip()
    active_url = event.details.get("Active DD report URL", "").strip()
    candidate_url = event.details.get("Candidate DD report URL", "").strip()
    rhodes_update = event.details.get("Rhodes due diligence update", "").strip()

    lines = [
        "DD report candidate review",
        f"Action needed: {_dd_report_candidate_action_needed(event)}",
        f"Site: {event.site_name}",
        "Status: Candidate DD report created. Active report was not overwritten.",
    ]
    if rhodes_update:
        lines.append(f"Rhodes fields: {_dd_report_rhodes_fields_line(rhodes_update)}")
    if trigger_source:
        lines.append(
            f"Candidate created due to: {_format_republish_source(trigger_source, source_file)}"
        )
    if _has_real_value(outstanding_docs):
        lines.append(f"Missing vendor docs: {outstanding_docs}")
    if active_url:
        lines.append(f"Active DD report: {active_url}")
    if candidate_url:
        lines.append(f"Candidate DD report: {candidate_url}")
    lines.append("Next steps:")
    lines.extend(_dd_report_candidate_next_steps(rhodes_update))
    return "\n".join(lines)


def _dd_report_candidate_action_needed(event: AutomationEvent) -> str:
    rhodes_update = event.details.get("Rhodes due diligence update", "").strip()
    if rhodes_update.startswith("failed"):
        return (
            "Review the candidate DD report before replacing the active report."
        )
    if rhodes_update:
        return (
            "Review the Rhodes due diligence fields and candidate DD report before "
            "replacing the active report."
        )
    return "Review the candidate DD report before replacing the active report."


def _has_real_value(value: str) -> bool:
    return bool(value and value.strip().lower() not in {"none", "n/a", "na"})


def _format_republish_source(source_type: str, source_file: str = "") -> str:
    labels = {
        "vendor_sir": "Vendor SIR",
        "building_inspection": "Vendor Building Inspection",
        "raycon_scenario": "RayCon Scenario JSON",
        "e_occupancy_report": "E-Occupancy report",
        "school_approval_report": "School Approval report",
    }
    label = labels.get(source_type, source_type.replace("_", " ").title())
    return f"{label} ({source_file})" if source_file else label


def _dd_report_action_needed(event: AutomationEvent, open_count: int) -> str:
    rhodes_update = event.details.get("Rhodes due diligence update", "").strip()
    if rhodes_update.startswith("failed"):
        if open_count <= 0:
            return "Review the DD report and confirm the Rhodes field update."
        item_word = "ask" if open_count == 1 else "asks"
        return (
            f"Review the DD report, confirm the Rhodes field update, and close "
            f"{open_count} open verification {item_word}."
        )
    if rhodes_update:
        if open_count <= 0:
            return "Review the Rhodes due diligence fields and DD report."
        item_word = "ask" if open_count == 1 else "asks"
        return (
            f"Review the Rhodes due diligence fields and DD report, then close "
            f"{open_count} open verification {item_word}."
        )
    if not event.decision_required or open_count <= 0:
        return "No operator action needed; DD report event is recorded."
    item_word = "ask" if open_count == 1 else "asks"
    return (
        f"Review the DD report and close {open_count} open verification {item_word}. "
        "Update the source document, Rhodes record, or DD report evidence when resolved."
    )


def _dd_report_status_line(event: AutomationEvent, rhodes_update: str) -> str:
    report_action = (
        "DD report updated"
        if event.event_type == "dd_report_updated"
        else "DD report created"
    )
    fields_status = _dd_report_rhodes_fields_line(rhodes_update)
    if fields_status:
        return f"{report_action}. Rhodes fields: {fields_status}"
    return f"{report_action}."


def _dd_report_rhodes_fields_line(rhodes_update: str) -> str:
    if not rhodes_update:
        return ""
    if rhodes_update.startswith("failed"):
        return "Did not update. Technical details are in the run record."
    return "Updated."


def _dd_report_next_steps(open_count: int, rhodes_update: str) -> list[str]:
    steps = ["- Review the DD report."]
    if rhodes_update.startswith("failed"):
        steps.append("- Confirm the Rhodes field update is repaired.")
    elif rhodes_update:
        steps.append("- Confirm the Rhodes fields are correct.")
    if open_count > 0:
        steps.append("- Close open verification items after the evidence is added.")
    return steps


def _dd_report_candidate_next_steps(rhodes_update: str) -> list[str]:
    steps = [
        "- Review the candidate report.",
        "- Decide whether it should replace the active report.",
    ]
    if rhodes_update.startswith("failed"):
        steps.append("- Confirm the Rhodes field update is repaired.")
    elif rhodes_update:
        steps.append("- Confirm the Rhodes fields are correct.")
    return steps


def _dd_report_close_instructions() -> list[str]:
    return [
        "How to close: These asks come from the DD report Open Items to Verify section.",
        (
            "Move the answer/evidence into the right DD report section or Rhodes/source record, "
            "then remove the ask from Open Items to Verify."
        ),
        "If an answer is left under the ask, it still counts as open.",
    ]


def _dd_report_ask_lines(details: dict[str, str], *, limit: int = 5) -> list[str]:
    rows: list[str] = []
    for index in range(1, limit + 1):
        text = details.get(f"Open item {index}", "").strip()
        if text:
            rows.append(f"Ask {index}: {text}")
    return rows


def _dd_report_resolved_lines(details: dict[str, str], *, limit: int = 3) -> list[str]:
    rows: list[str] = []
    for index in range(1, limit + 1):
        text = details.get(f"Closed item {index}", "").strip()
        if text:
            rows.append(f"Resolved {index}: {text}")
    return rows


def _m2_source_packet_lines(details: dict[str, str], *, limit: int = 6) -> list[str]:
    rows: list[str] = []
    for index in range(1, limit + 1):
        text = details.get(f"M2 source line {index}", "").strip()
        if text:
            rows.append(f"- {text}")
    return rows


def _detail_int(event: AutomationEvent, label: str) -> int:
    try:
        return int(event.details.get(label, "0"))
    except (TypeError, ValueError):
        return 0


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
    missing_vendor_docs: list[str] | None = None,
    due_diligence_update: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> AutomationEvent:
    """Build the DDR generated/updated report summary event."""

    open_items = open_questions or []
    closed_items = closed_open_questions or []
    source = source_event or {}
    is_update = bool(source)
    due_diligence_update_data = (
        due_diligence_update if isinstance(due_diligence_update, dict) else None
    )
    due_diligence_status = (
        str(due_diligence_update_data.get("status") or "").strip().lower()
        if due_diligence_update_data is not None
        else ""
    )
    due_diligence_written = due_diligence_status == "updated"
    due_diligence_failed = due_diligence_status == "failed"
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
        "Outstanding vendor docs": _format_missing_docs(missing_vendor_docs),
    }
    if due_diligence_written or due_diligence_failed:
        assert due_diligence_update_data is not None
        details["Rhodes due diligence update"] = _format_due_diligence_update(
            due_diligence_update_data
        )
        details.update(_source_packet_details(due_diligence_update_data))
    details.update(_indexed_item_details("Open item", open_items))
    details.update(_indexed_item_details("Closed item", closed_items))

    decision_required = bool(open_items) or due_diligence_written or due_diligence_failed
    return AutomationEvent(
        source_system="due-diligence-reporter",
        source_id=run_id,
        site_id=site_id.strip(),
        site_name=site_name.strip() or "Unknown site",
        event_type="dd_report_updated" if is_update else "dd_report_created",
        artifact_ids=artifact_ids,
        decision_required=decision_required,
        requested_decision=(
            "review failed Rhodes due diligence write and resolve DDR open verification items"
            if open_items and due_diligence_failed
            else "review failed Rhodes due diligence write and DD report"
            if due_diligence_failed
            else
            "review Rhodes due diligence fields and resolve DDR open verification items"
            if open_items and due_diligence_written
            else "review Rhodes due diligence fields and DD report"
            if due_diligence_written
            else "review and resolve DDR open verification items"
            if open_items
            else None
        ),
        mutation_status="report_created",
        details=details,
        created_at=created_at or datetime.now(UTC).isoformat(),
    )


def build_dd_report_republish_candidate_event(
    *,
    site_id: str,
    site_name: str,
    run_id: str,
    candidate_doc_id: str | None,
    candidate_doc_url: str | None,
    source_event: dict[str, Any] | None = None,
    missing_vendor_docs: list[str] | None = None,
    overwrite_guard: dict[str, Any] | None = None,
    due_diligence_update: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> AutomationEvent:
    """Build a review event when republish creates a candidate instead of overwriting."""

    source = source_event or {}
    guard = overwrite_guard or {}
    due_diligence_update_data = (
        due_diligence_update if isinstance(due_diligence_update, dict) else None
    )
    due_diligence_status = (
        str(due_diligence_update_data.get("status") or "").strip().lower()
        if due_diligence_update_data is not None
        else ""
    )
    due_diligence_written = due_diligence_status == "updated"
    due_diligence_failed = due_diligence_status == "failed"
    artifact_ids = {"Run ID": run_id}
    if candidate_doc_id:
        artifact_ids["Candidate DD report ID"] = candidate_doc_id
    active_doc_id = str(guard.get("active_doc_id") or "").strip()
    if active_doc_id:
        artifact_ids["Active DD report ID"] = active_doc_id
    details = {
        "Trigger source": str(source.get("source_type") or "").strip(),
        "Source file": str(source.get("file_name") or "").strip(),
        "Outstanding vendor docs": _format_missing_docs(missing_vendor_docs),
        "Active DD report URL": str(guard.get("active_doc_url") or "").strip(),
        "Candidate DD report URL": str(candidate_doc_url or "").strip(),
        "Guard reason": str(guard.get("reason") or "").strip(),
    }
    if due_diligence_written or due_diligence_failed:
        assert due_diligence_update_data is not None
        details["Rhodes due diligence update"] = _format_due_diligence_update(
            due_diligence_update_data
        )

    return AutomationEvent(
        source_system="due-diligence-reporter",
        source_id=run_id,
        site_id=site_id.strip(),
        site_name=site_name.strip() or "Unknown site",
        event_type="dd_report_republish_candidate_created",
        artifact_ids=artifact_ids,
        decision_required=True,
        requested_decision=(
            "review failed Rhodes due diligence write and candidate DD report"
            if due_diligence_failed
            else "review Rhodes due diligence fields and candidate DD report before replacing active report"
            if due_diligence_written
            else "review candidate DD report and decide whether to replace the active report"
        ),
        mutation_status="candidate_created",
        details=details,
        created_at=created_at or datetime.now(UTC).isoformat(),
    )


def _format_missing_docs(missing_docs: list[str] | None) -> str:
    if not missing_docs:
        return "None"
    return ", ".join(str(item).strip() for item in missing_docs if str(item).strip())


def _format_due_diligence_update(update: dict[str, Any]) -> str:
    status = str(update.get("status") or "").strip().lower()
    fields = update.get("updated_fields")
    field_text = ""
    if isinstance(fields, list):
        clean_fields = [str(field).strip() for field in fields if str(field).strip()]
        if clean_fields:
            field_text = ", ".join(clean_fields)
    if status == "failed":
        error = str(
            update.get("error_summary")
            or update.get("error")
            or update.get("reason")
            or "unknown error"
        ).strip()
        if field_text:
            return f"failed to update {field_text}: {error}"
        return f"failed: {error}"
    if field_text:
        return f"updated {field_text}"
    return "updated"


def _source_packet_details(update: dict[str, Any], *, limit: int = 6) -> dict[str, str]:
    lines = update.get("source_note_lines")
    if not isinstance(lines, list):
        return {}
    details: dict[str, str] = {}
    status = str(update.get("source_packet_status") or "").strip()
    complete = update.get("m2_source_packet_complete")
    if status:
        details["M2 source packet"] = status
    elif complete is True:
        details["M2 source packet"] = "complete"
    elif complete is False:
        details["M2 source packet"] = "blocked"
    for index, line in enumerate(lines[:limit], start=1):
        clean = str(line or "").strip()
        if clean:
            details[f"M2 source line {index}"] = clean
    return details


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
