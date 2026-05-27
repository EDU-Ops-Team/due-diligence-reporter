from __future__ import annotations

from due_diligence_reporter.automation_event import (
    build_document_registration_failed_event,
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
