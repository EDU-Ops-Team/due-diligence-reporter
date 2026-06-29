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

    assert note.splitlines()[0] == "Document filing review"
    assert (
        "Action needed: Review a document that did not finish filing in Rhodes."
        in note
    )
    assert "Site: Alpha Keller" in note
    assert "Status: Rhodes document registration did not complete." in note
    assert "Document: May 27 2026 - Alpha Keller ISP.pdf" in note
    assert "Type: isp" in note
    assert "Drive link: https://drive.example/file/drive-file-1" in note
    assert "Next steps:" in note
    assert "- Register or repair the Rhodes document link." in note
    assert "AutomationEvent v1" not in note
    assert "Source ID: msg-1" not in note
    assert "Kind: document_registration_failed" not in note
    assert "Site ID: SITE1" not in note
    assert "Retry state:" not in note
    assert "Drive file ID:" not in note
    assert "Gmail message ID:" not in note
    assert "Owner One <owner@example.com>" not in note
    assert "timeout" not in note
    assert "Created at:" not in note


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

    assert "Document filing review" in note
    assert "No owner assigned" not in note


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

    assert note.splitlines()[0] == "Document intake review"
    assert (
        "Action needed: Review an inbound due diligence attachment before filing."
        in note
    )
    assert "Site: Alpha Keller" in note
    assert "Status: Needs manual review." in note
    assert "Document: Alpha Keller SIR.pdf" in note
    assert "Type: sir" in note
    assert "- Confirm the correct site and document type." in note
    assert "Kind: inbox_manual_review_required" not in note
    assert "Site ID: SITE1" not in note
    assert "Requested decision:" not in note
    assert "Mutation status:" not in note
    assert "Gmail message ID:" not in note
    assert "Gmail thread ID:" not in note
    assert "missing_drive_folder" not in note
    assert "Matched site has no Google Drive folder URL" not in note


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

    assert note.splitlines()[0] == "DD report update"
    assert (
        "Action needed: Review the DD report and close 1 open verification ask. "
        "Update the source document, Rhodes record, or DD report evidence when resolved."
        in note
    )
    assert "Status: DD report updated." in note
    assert "DD report: https://docs.google.com/document/d/doc-1" in note
    assert "Open verification items: 1" in note
    assert "Latest source reviewed: Vendor SIR (Alpha Keller SIR.pdf)" in note
    assert "Resolved this run: 1" in note
    assert (
        "Close open items after the answer is added to the DD report or source record."
        in note
    )
    assert "Next steps:" in note
    assert "- Review the DD report." in note
    assert "- Close open verification items after the evidence is added." in note
    assert "System details:" not in note
    assert "Run ID: run-1" not in note
    assert "DD report ID: doc-1" not in note
    assert "Source Drive file ID: drive-file-1" not in note
    assert "Kind: dd_report_updated" not in note
    assert "Ask 1: Confirm zoning use from the vendor SIR" not in note


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

    assert lines[0] == "DD report update"
    assert lines[1].startswith("Action needed: Review the DD report and close 7 open")
    assert "Open verification items: 7" in note
    assert "Next steps:" in note
    assert "Ask 1: Resolve verification ask 1" not in note
    assert "Ask 5: Resolve verification ask 5" not in note
    assert "Resolve verification ask 6" not in note
    assert "Additional asks:" not in note


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

    assert note.splitlines()[0] == "DD report update"
    assert (
        "Action needed: Review the DD report and confirm the Rhodes field update."
        in note
    )
    assert (
        "Status: DD report created. Rhodes fields: Did not update. "
        "Technical details are in the run record."
    ) in note
    assert "- Confirm the Rhodes field update is repaired." in note
    assert "updateDueDiligence rejected" not in note
    assert "foCapacity" not in note
    assert "Requested decision:" not in note


def test_dd_report_summary_event_renders_m2_source_packet_lines() -> None:
    event = build_dd_report_summary_event(
        site_id="SITE1",
        site_name="Alpha Keller",
        run_id="run-1",
        doc_id=None,
        doc_url=None,
        due_diligence_update={
            "status": "updated",
            "reason": "ok",
            "updated_fields": ["playAreaScore", "playAreaComment"],
            "source_packet_status": "complete",
            "m2_source_packet_complete": True,
            "source_note_lines": [
                "play_area_score -> 1 -> Outdoor Play Space Report",
                "play_area_comment -> Passes with B confidence -> Outdoor Play Space Report",
            ],
        },
        created_at="2026-06-29T13:30:00+00:00",
    )

    note = render_automation_event_note(event)

    assert "M2 source packet: complete" in note
    assert "play_area_score -> 1 -> Outdoor Play Space Report" in note
    assert "play_area_comment -> Passes with B confidence -> Outdoor Play Space Report" in note
    assert "DDR as source" not in note
    assert "drive-file" not in note


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
        "review Rhodes due diligence fields and candidate DD report before replacing active report"
    )

    note = render_automation_event_note(event)

    assert note.splitlines()[0] == "DD report candidate review"
    assert (
        "Action needed: Review the Rhodes due diligence fields and candidate DD report "
        "before replacing the active report."
    ) in note
    assert (
        "Status: Candidate DD report created. Active report was not overwritten."
        in note
    )
    assert "Rhodes fields: Updated." in note
    assert "Active DD report: https://docs.google.com/document/d/doc-active" in note
    assert "Candidate DD report: https://docs.google.com/document/d/doc-candidate" in note
    assert "- Review the candidate report." in note
    assert "- Decide whether it should replace the active report." in note
    assert "System details:" not in note
    assert "Requested decision:" not in note
    assert "Run ID: run-1" not in note


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

    assert note.splitlines()[0] == "Source document review"
    assert "Action needed: Review source documents DDR could not read." in note
    assert "Status: 2 source document(s) need review." in note
    assert "- Open the source documents in Drive." in note
    assert "- Rerun DDR when the source files are readable." in note
    assert "Kind: source_review_required" not in note
    assert "Decision required: yes" not in note
    assert "Mutation status:" not in note
    assert "Run ID: run-1" not in note
    assert "Source issue 1:" not in note
    assert "Failed to read document" not in note
    assert "Drive folder:" not in note
    assert "Trace:" not in note


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

    assert note.splitlines()[0] == "DDR source review"
    assert (
        "Action needed: Review complete vendor inputs before DDR can finish."
        in note
    )
    assert (
        "Status: DDR could not produce a complete report from the available inputs."
        in note
    )
    assert (
        "Required inputs: vendor SIR, vendor Building Inspection, RayCon Scenario JSON"
        in note
    )
    assert "- Rerun DDR." in note
    assert "Kind: vendor_gate_review_required" not in note
    assert "Decision required: yes" not in note
    assert "Mutation status:" not in note
    assert "Run ID: run-1" not in note
    assert "Failure reason:" not in note
    assert "Report NOT ready to send" not in note
    assert "Drive folder:" not in note
    assert "Trace:" not in note


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

    assert note.splitlines()[0] == "DD report republish review"
    assert "Action needed: Review a failed DD report republish." in note
    assert "Status: DDR could not republish the report." in note
    assert "Latest source reviewed: Vendor SIR (Alpha Keller SIR.pdf)" in note
    assert "- Repair the report generation issue." in note
    assert "Kind: dd_report_republish_failed" not in note
    assert "Decision required: yes" not in note
    assert "Mutation status:" not in note
    assert "Run ID: run-1" not in note
    assert "Content fingerprint:" not in note
    assert "Source Drive file ID:" not in note
    assert "Failure reason:" not in note
    assert "Anthropic 500" not in note
    assert "Manifest:" not in note


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

    assert note.splitlines()[0] == "RayCon follow-up review"
    assert "Action needed: Review RayCon scenario generation for this site." in note
    assert "Status: RayCon scenario generation needs review." in note
    assert "- Check the Block Plan and RayCon Scenario inputs." in note
    assert "- Rerun RayCon follow-up." in note
    assert "Kind: raycon_followup_alert" not in note
    assert "Decision required: yes" not in note
    assert "Mutation status:" not in note
    assert "Run ID: raycon-followup-20260527213000" not in note
    assert "Block Plan file ID:" not in note
    assert "RayCon run ID:" not in note
    assert "no raycon_scenario.json" not in note
    assert "Drive folder:" not in note
