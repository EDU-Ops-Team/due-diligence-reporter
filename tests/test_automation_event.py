from __future__ import annotations

from due_diligence_reporter.automation_event import (
    build_dd_report_republish_candidate_event,
    build_dd_report_republish_failed_event,
    build_dd_report_summary_event,
    build_document_registration_failed_event,
    build_inbox_manual_review_required_event,
    build_raycon_followup_alert_event,
    build_source_review_required_event,
    build_vendor_gate_review_required_event,
    render_automation_event_note,
)


def test_document_registration_failed_event_renders_shared_contract_fields() -> None:
    event = build_document_registration_failed_event(
        site_summary={
            "id": "SITE1",
            "title": "Alpha Keller",
            "p1_assignee_name": "Owner One",
            "p1_assignee_email": "owner@example.com",
        },
        registration={
            "status": "failed",
            "reason": "rhodes_error",
            "error": "timeout",
            "rhodes_doc_type": "other",
            "rhodes_milestone": "acquireProperty",
            "retry_attempts": 3,
            "retry_limit": 2,
            "retry_exhausted": True,
        },
        doc_type="isp",
        drive_file={
            "id": "drive-file-1",
            "webViewLink": "https://drive.example/file/drive-file-1",
        },
        drive_filename="May 27 2026 - Alpha Keller ISP.pdf",
        original_filename="Alpha Keller ISP.pdf",
        email_subject="Alpha Keller ISP",
        message_id="msg-1",
        thread_id="thread-1",
        created_at="2026-05-27T16:45:00+00:00",
    )

    assert event.source_system == "due-diligence-reporter"
    assert event.source_id == "msg-1"
    assert event.site_id == "SITE1"
    assert event.event_type == "document_registration_failed"
    assert event.decision_required is True

    note = render_automation_event_note(event)

    assert "AutomationEvent v1" in note
    assert "Source: due-diligence-reporter" in note
    assert "Source ID: msg-1" in note
    assert "Kind: document_registration_failed" in note
    assert "Site ID: SITE1" in note
    assert "Decision required: yes" in note
    assert (
        "Requested decision: repair or register the Rhodes document link for the Drive file"
        in note
    )
    assert "Mutation status: failed" in note
    assert "Retry state: attempts=3/2; exhausted=true" in note
    assert "Drive file ID: drive-file-1" in note
    assert "Gmail message ID: msg-1" in note
    assert "Owner: Owner One <owner@example.com>" in note
    assert "Created at: 2026-05-27T16:45:00+00:00" in note


def test_document_registration_event_handles_missing_owner() -> None:
    event = build_document_registration_failed_event(
        site_summary={"id": "SITE1", "title": "Alpha Keller"},
        registration={
            "status": "failed",
            "reason": "rhodes_error",
            "retry_attempts": 3,
            "retry_limit": 2,
        },
        doc_type="isp",
        drive_file={"id": "drive-file-1"},
        drive_filename="May 27 2026 - Alpha Keller ISP.pdf",
        original_filename="Alpha Keller ISP.pdf",
        email_subject="Alpha Keller ISP",
        message_id="msg-1",
        thread_id="thread-1",
        created_at="2026-05-27T16:45:00+00:00",
    )

    note = render_automation_event_note(event)

    assert "Owner: No owner assigned" in note


def test_inbox_manual_review_event_renders_decision_context() -> None:
    event = build_inbox_manual_review_required_event(
        site_id="SITE1",
        site_name="Alpha Keller",
        message_id="msg-1",
        thread_id="thread-1",
        filename="Alpha Keller SIR.pdf",
        doc_type="sir",
        confidence=0.95,
        email_subject="Fwd: Alpha Keller SIR",
        reason="missing_drive_folder",
        error="Matched site has no Google Drive folder URL",
        created_at="2026-05-28T12:00:00+00:00",
    )

    assert event.source_id == "msg-1:Alpha Keller SIR.pdf:missing_drive_folder"
    assert event.event_type == "inbox_manual_review_required"
    assert event.decision_required is True

    note = render_automation_event_note(event)

    assert "Kind: inbox_manual_review_required" in note
    assert "Site ID: SITE1" in note
    assert (
        "Requested decision: review the inbound DD attachment and repair filing or site routing"
        in note
    )
    assert "Mutation status: missing_drive_folder" in note
    assert "Gmail message ID: msg-1" in note
    assert "Gmail thread ID: thread-1" in note
    assert "Filename: Alpha Keller SIR.pdf" in note
    assert "Manual review reason: missing_drive_folder" in note
    assert "Error: Matched site has no Google Drive folder URL" in note


def test_dd_report_summary_event_renders_open_and_closed_items() -> None:
    event = build_dd_report_summary_event(
        site_id="SITE1",
        site_name="Alpha Keller",
        run_id="run-1",
        doc_id="doc-1",
        doc_url="https://docs.google.com/document/d/doc-1",
        source_event={
            "source_type": "vendor_sir",
            "drive_file_id": "drive-file-1",
            "file_name": "Alpha Keller SIR.pdf",
        },
        open_questions=[
            {
                "display_text": "Confirm zoning use from the vendor SIR",
                "expected_source_type": "vendor_sir",
            }
        ],
        closed_open_questions=[
            {
                "display_text": "Resolve construction timeline from RayCon",
                "expected_source_type": "raycon_scenario",
            }
        ],
        created_at="2026-05-27T18:15:00+00:00",
    )

    assert event.event_type == "dd_report_updated"
    assert event.source_id == "run-1"
    assert event.decision_required is True
    assert event.requested_decision == "review and resolve DDR open verification items"

    note = render_automation_event_note(event)

    assert "Kind: dd_report_updated" in note
    assert (
        "Action needed: Review the DD report and close 1 open verification ask. "
        "Update the source document, Rhodes record, or DD report evidence when resolved."
        in note
    )
    assert "Asks to close:" in note
    assert "How to close: These asks come from the DD report Open Items to Verify section." in note
    assert (
        "Move the answer/evidence into the right DD report section or Rhodes/source record, "
        "then remove the ask from Open Items to Verify."
        in note
    )
    assert "If an answer is left under the ask, it still counts as open." in note
    assert "Ask 1: Confirm zoning use from the vendor SIR" in note
    assert "Resolved in this update:" in note
    assert "Resolved 1: Resolve construction timeline from RayCon" in note
    assert "System details:" in note
    assert "Mutation status: report_created" in note
    assert "Run ID: run-1" in note
    assert "DD report ID: doc-1" in note
    assert "Source Drive file ID: drive-file-1" in note
    assert "DD report: https://docs.google.com/document/d/doc-1" in note
    assert "Latest source reviewed: vendor_sir - Alpha Keller SIR.pdf" in note
    assert "Open item count: 1" in note
    assert "Closed item count: 1" in note


def test_dd_report_summary_event_rolls_up_long_open_item_list() -> None:
    event = build_dd_report_summary_event(
        site_id="SITE1",
        site_name="Alpha Keller",
        run_id="run-1",
        doc_id="doc-1",
        doc_url="https://docs.google.com/document/d/doc-1",
        open_questions=[
            {"display_text": f"Resolve verification ask {index}"}
            for index in range(1, 8)
        ],
        created_at="2026-05-27T18:15:00+00:00",
    )

    note = render_automation_event_note(event)
    lines = note.splitlines()

    assert lines[0] == "AutomationEvent v1"
    assert lines[1].startswith("Action needed: Review the DD report and close 7 open")
    assert "Ask 1: Resolve verification ask 1" in note
    assert "Ask 5: Resolve verification ask 5" in note
    assert "Resolve verification ask 6" not in note
    assert "Additional asks: 2 more open item(s) are listed in the DD report." in note


def test_dd_report_summary_event_renders_failed_due_diligence_write() -> None:
    event = build_dd_report_summary_event(
        site_id="SITE1",
        site_name="Alpha Keller",
        run_id="run-1",
        doc_id="doc-1",
        doc_url="https://docs.google.com/document/d/doc-1",
        due_diligence_update={
            "status": "failed",
            "reason": "rhodes_error",
            "error": "updateDueDiligence rejected",
            "updated_fields": ["foCapacity", "status"],
        },
        created_at="2026-06-18T13:30:00+00:00",
    )

    assert event.decision_required is True
    assert event.requested_decision == (
        "review failed Rhodes due diligence write and DD report"
    )

    note = render_automation_event_note(event)

    assert (
        "Action needed: Review the failed Rhodes due diligence write and DD report."
        in note
    )
    assert (
        "Rhodes due diligence update: failed to update foCapacity, status: "
        "updateDueDiligence rejected"
    ) in note
    assert (
        "Requested decision: review failed Rhodes due diligence write and DD report"
        in note
    )


def test_dd_report_candidate_event_renders_due_diligence_write() -> None:
    event = build_dd_report_republish_candidate_event(
        site_id="SITE1",
        site_name="Alpha Keller",
        run_id="run-1",
        candidate_doc_id="doc-candidate",
        candidate_doc_url="https://docs.google.com/document/d/doc-candidate",
        overwrite_guard={
            "active_doc_id": "doc-active",
            "active_doc_url": "https://docs.google.com/document/d/doc-active",
            "reason": "missing_automation_revision",
        },
        due_diligence_update={
            "status": "updated",
            "reason": "ok",
            "updated_fields": ["foCapacity", "status"],
        },
        created_at="2026-06-18T13:30:00+00:00",
    )

    assert event.decision_required is True
    assert event.requested_decision == (
        "review Rhodes due diligence fields and candidate DDR before replacing active report"
    )

    note = render_automation_event_note(event)

    assert (
        "Action needed: Review the Rhodes due diligence fields and candidate DDR "
        "before replacing the active report."
    ) in note
    assert "Rhodes due diligence update: updated foCapacity, status" in note
    assert "Active DD report: https://docs.google.com/document/d/doc-active" in note
    assert "Candidate DD report: https://docs.google.com/document/d/doc-candidate" in note
    assert (
        "Requested decision: review Rhodes due diligence fields and candidate DDR "
        "before replacing active report"
    ) in note


def test_source_review_required_event_renders_source_issues() -> None:
    event = build_source_review_required_event(
        site_id="SITE1",
        site_name="Alpha Keller",
        run_id="run-1",
        issues=[
            {
                "doc_type": "SIR",
                "file_name": "Alpha Keller SIR.pdf",
                "problem": "Failed to read document",
            },
            {
                "doc_type": "Building Inspection",
                "file_name": "Alpha Keller Building Inspection.pdf",
                "problem": "Document returned no text",
            },
        ],
        drive_folder_url="https://drive.google.com/drive/folders/abc123",
        trace_url="https://drive.google.com/file/d/trace123",
        created_at="2026-05-27T18:45:00+00:00",
    )

    assert event.event_type == "source_review_required"
    assert event.source_id == "run-1"
    assert event.decision_required is True
    assert event.requested_decision == "review unreadable DDR source documents"

    note = render_automation_event_note(event)

    assert "Kind: source_review_required" in note
    assert "Decision required: yes" in note
    assert "Mutation status: source_read_issue" in note
    assert "Run ID: run-1" in note
    assert "Source issue count: 2" in note
    assert (
        "Source issue 1: SIR | Alpha Keller SIR.pdf | Problem: Failed to read document"
        in note
    )
    assert (
        "Source issue 2: Building Inspection | Alpha Keller Building Inspection.pdf | "
        "Problem: Document returned no text"
        in note
    )
    assert "Drive folder: https://drive.google.com/drive/folders/abc123" in note
    assert "Trace: https://drive.google.com/file/d/trace123" in note


def test_vendor_gate_review_required_event_renders_failure_context() -> None:
    event = build_vendor_gate_review_required_event(
        site_id="SITE1",
        site_name="Alpha Tulsa 421 E 11th St",
        run_id="run-1",
        failure_reason="Report NOT ready to send. 1 raw template token(s).",
        mutation_status="report_incomplete",
        drive_folder_url="https://drive.google.com/drive/folders/abc123",
        trace_url="https://drive.google.com/file/d/trace123",
        created_at="2026-05-27T19:15:00+00:00",
    )

    assert event.event_type == "vendor_gate_review_required"
    assert event.source_id == "run-1"
    assert event.decision_required is True
    assert (
        event.requested_decision
        == "review complete vendor inputs and repair DDR generation"
    )

    note = render_automation_event_note(event)

    assert "Kind: vendor_gate_review_required" in note
    assert "Decision required: yes" in note
    assert "Mutation status: report_incomplete" in note
    assert "Run ID: run-1" in note
    assert (
        "Required inputs: vendor SIR, vendor Building Inspection, RayCon Scenario JSON"
        in note
    )
    assert "Failure reason: Report NOT ready to send. 1 raw template token(s)." in note
    assert "Drive folder: https://drive.google.com/drive/folders/abc123" in note
    assert "Trace: https://drive.google.com/file/d/trace123" in note


def test_dd_report_republish_failed_event_renders_failure_context() -> None:
    event = build_dd_report_republish_failed_event(
        site_id="SITE1",
        site_name="Alpha Keller",
        reason="vendor_sir",
        content_fingerprint="sir-1:2026-05-28T10:00:00Z",
        failure_reason="Anthropic 500",
        mutation_status="generation_failed",
        source_event={
            "source_type": "vendor_sir",
            "drive_file_id": "sir-1",
            "file_name": "Alpha Keller SIR.pdf",
        },
        drive_folder_url="https://drive.google.com/drive/folders/abc123",
        run_id="run-1",
        manifest_path=".ddr-runs/run-1.json",
        created_at="2026-05-28T16:45:00+00:00",
    )

    assert event.event_type == "dd_report_republish_failed"
    assert event.source_id == "run-1"
    assert event.decision_required is True
    assert (
        event.requested_decision
        == "review failed DDR republish and repair report generation"
    )

    note = render_automation_event_note(event)

    assert "Kind: dd_report_republish_failed" in note
    assert "Decision required: yes" in note
    assert "Mutation status: generation_failed" in note
    assert "Run ID: run-1" in note
    assert "Content fingerprint: sir-1:2026-05-28T10:00:00Z" in note
    assert "Source Drive file ID: sir-1" in note
    assert "Trigger source: vendor_sir" in note
    assert "Source file: Alpha Keller SIR.pdf" in note
    assert "Failure reason: Anthropic 500" in note
    assert "Manifest: .ddr-runs/run-1.json" in note


def test_raycon_followup_alert_event_renders_review_context() -> None:
    event = build_raycon_followup_alert_event(
        site_id="SITE1",
        site_name="Alpha Keller",
        run_id="raycon-followup-20260527213000",
        alert_type="stuck_site",
        message="no raycon_scenario.json after 1:00:00",
        drive_folder_url="https://drive.google.com/drive/folders/abc123",
        block_plan_file_id="block-plan-1",
        raycon_run_id="raycon-run-1",
        created_at="2026-05-27T21:30:00+00:00",
    )

    assert event.event_type == "raycon_followup_alert"
    assert event.source_id == "raycon-followup-20260527213000"
    assert event.decision_required is True
    assert (
        event.requested_decision
        == "review RayCon follow-up alert and unblock scenario generation"
    )

    note = render_automation_event_note(event)

    assert "Kind: raycon_followup_alert" in note
    assert "Decision required: yes" in note
    assert "Mutation status: stuck_site" in note
    assert "Run ID: raycon-followup-20260527213000" in note
    assert "Block Plan file ID: block-plan-1" in note
    assert "RayCon run ID: raycon-run-1" in note
    assert "Message: no raycon_scenario.json after 1:00:00" in note
    assert "Drive folder: https://drive.google.com/drive/folders/abc123" in note
