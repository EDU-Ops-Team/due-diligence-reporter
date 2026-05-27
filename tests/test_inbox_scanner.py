"""Tests for the inbox scanner module."""

from __future__ import annotations

import re
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from due_diligence_reporter.inbox_scanner import (
    AUTO_FILE_CONFIDENCE,
    DOC_TYPE_FILENAME_TEMPLATES,
    SUPPORTED_DOC_TYPES,
    EmailMetadata,
    _generate_drive_filename,
    _is_internal_sender,
    _record_rhodes_registration_failure_event,
    _run_block_plan_downstream,
    _run_doc_arrival_folder_ping,
    _walk_parts,
    build_scan_summary,
    has_site_identity,
    process_email,
    scan_inbox,
)

# ---------------------------------------------------------------------------
# Filename generation
# ---------------------------------------------------------------------------


def test_scan_summary_includes_manual_review_reason() -> None:
    summary = build_scan_summary(
        {
            "emails_found": 1,
            "emails_processed": 1,
            "attachments_uploaded": 0,
            "attachments_skipped": 1,
            "manual_review": [
                {
                    "filename": "Alpha Keller SIR.pdf",
                    "doc_type": "sir",
                    "reason": "unmatched_site",
                    "confidence": 0.95,
                    "email_subject": "Fwd: SIR",
                }
            ],
            "low_confidence": [],
            "uploads": [],
            "errors": [],
        }
    )

    assert "Needs manual review:" in summary
    assert "reason: unmatched_site" in summary
    assert "confidence: 95%" in summary


class TestGenerateDriveFilename:
    """Generated filenames must work with existing _classify_document_type() patterns."""

    def test_sir_filename_contains_sir_keyword(self):
        name = _generate_drive_filename("Alpha Keller", "sir")
        assert "SIR" in name
        assert "Alpha Keller" in name
        assert name.endswith(".pdf")

    def test_building_inspection_filename_contains_inspection(self):
        name = _generate_drive_filename("Alpha Boca Raton", "building_inspection")
        assert "Inspection" in name
        assert "Alpha Boca Raton" in name
        assert name.endswith(".pdf")

    def test_isp_filename_contains_isp_keyword(self):
        name = _generate_drive_filename("Alpha Southlake", "isp")
        assert "ISP" in name
        assert "Alpha Southlake" in name
        assert name.endswith(".pdf")

    def test_block_plan_filename_contains_block_plan_keyword(self):
        name = _generate_drive_filename("Alpha Southlake", "block_plan")
        assert "Block Plan" in name
        assert "Alpha Southlake" in name
        assert name.endswith(".pdf")

    def test_filename_starts_with_date(self):
        name = _generate_drive_filename("Alpha Keller", "sir")
        today = datetime.now().strftime("%b %d %Y")
        assert name.startswith(today)

    def test_sir_matches_classify_document_type_pattern(self):
        """The generated SIR filename must be classified as 'sir' by the server's regex."""
        name = _generate_drive_filename("Alpha Southlake", "sir")
        assert re.search(r"\bsir\b", name.lower())

    def test_inspection_matches_classify_document_type_pattern(self):
        """The generated inspection filename must be classified as 'building_inspection'."""
        name = _generate_drive_filename("Alpha Norwalk", "building_inspection")
        assert "inspection" in name.lower()

    def test_all_supported_doc_types_have_templates(self):
        for doc_type in SUPPORTED_DOC_TYPES:
            assert doc_type in DOC_TYPE_FILENAME_TEMPLATES


# ---------------------------------------------------------------------------
# Classification â€” now uses classify_document() from classifier.py
# ---------------------------------------------------------------------------


class TestClassification:
    """Verify classify_document is used and doc_type gates the upload."""

    def test_confidence_threshold_is_reasonable(self):
        assert 0.5 <= AUTO_FILE_CONFIDENCE <= 0.9

    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_unknown_doc_type_skipped(self, mock_extract, mock_classify):
        mock_extract.return_value = MagicMock(
            message_id="msg_1",
            subject="Hello",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="Some text",
            attachments=[
                {"filename": "notes.pdf", "attachment_id": "a1", "mime_type": "application/pdf"}
            ],
        )
        mock_classify.return_value = ("unknown", 0.0)

        gc = MagicMock()
        result = process_email(gc, "msg_1", MagicMock(), "label_123", "review_123")

        assert result["skipped"] == 1
        assert len(result["uploaded"]) == 0

    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_low_confidence_flagged_for_review(self, mock_extract, mock_classify):
        mock_extract.return_value = MagicMock(
            message_id="msg_2",
            subject="Something",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            attachments=[
                {"filename": "report.pdf", "attachment_id": "a2", "mime_type": "application/pdf"}
            ],
        )
        mock_classify.return_value = ("sir", 0.5)  # below AUTO_FILE_CONFIDENCE

        gc = MagicMock()
        result = process_email(gc, "msg_2", MagicMock(), "label_123", "review_123")

        assert len(result["low_confidence"]) == 1
        assert result["low_confidence"][0]["doc_type"] == "sir"
        assert result["low_confidence"][0]["reason"] == "low_confidence"
        assert result["manual_review"][0]["reason"] == "low_confidence"
        assert result["marked"] is True
        gc.gmail_modify_labels.assert_called_once_with(
            "msg_2",
            add_labels=["label_123", "review_123"],
            remove_labels=[],
        )

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner.classify_by_content_llm")
    @patch("due_diligence_reporter.inbox_scanner.extract_text_from_pdf_bytes")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_vague_filename_uses_pdf_content_fallback(
        self,
        mock_extract,
        mock_classify,
        mock_extract_pdf,
        mock_content_classify,
        mock_resolve_m1,
        mock_build_summary,
    ):
        mock_extract.return_value = MagicMock(
            message_id="msg_content_1",
            subject="Alpha Keller due diligence packet",
            sender="vendor@example.com",
            effective_sender="vendor@example.com",
            body_snippet="",
            attachments=[
                {
                    "filename": "report.pdf",
                    "attachment_id": "content1",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("unknown", 0.0)
        mock_extract_pdf.return_value = "Site Investigation Report for Alpha Keller"
        mock_content_classify.return_value = ("sir", 0.92)
        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive/folders/m1")
        mock_build_summary.return_value = {
            "title": "Alpha Keller",
            "address": "123 Main St",
            "drive_folder_url": "https://drive.google.com/drive/folders/site123",
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"%PDF-vague"
        gc.upload_file_to_folder.return_value = {
            "id": "sir_id",
            "webViewLink": "https://drive.google.com/file/d/sir_id",
        }

        result = process_email(
            gc,
            "msg_content_1",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=[{"id": "IEKELLER", "title": "Alpha Keller", "customFields": []}],
        )

        assert len(result["uploaded"]) == 1
        assert result["uploaded"][0]["doc_type"] == "sir"
        assert result["uploaded"][0]["site_title"] == "Alpha Keller"
        gc.gmail_get_attachment.assert_called_once_with("msg_content_1", "content1")
        gc.upload_file_to_folder.assert_called_once_with(
            folder_id="m1_folder_id",
            file_name=result["uploaded"][0]["drive_filename"],
            file_bytes=b"%PDF-vague",
        )

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_upload_failure_is_returned_as_error(
        self, mock_extract, mock_classify, mock_resolve_m1, mock_build_summary
    ):
        mock_extract.return_value = MagicMock(
            message_id="msg_3",
            subject="Alpha Keller SIR",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            attachments=[
                {
                    "filename": "Alpha Keller SIR.pdf",
                    "attachment_id": "a3",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("sir", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive.google.com/drive/folders/m1")
        mock_build_summary.return_value = {
            "title": "Alpha Keller",
            "address": "123 Main St, Keller, TX 76248",
            "drive_folder_url": "https://drive.google.com/drive/folders/site123",
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.side_effect = RuntimeError("upload boom")

        site_records = [{"id": "IESIR123", "title": "Alpha Keller", "customFields": []}]

        result = process_email(
            gc,
            "msg_3",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=site_records,
        )

        assert result["marked"] is True
        assert len(result["uploaded"]) == 0
        assert len(result["errors"]) == 1
        assert result["errors"][0]["filename"] == "Alpha Keller SIR.pdf"
        assert result["errors"][0]["error"] == "upload boom"
        assert result["manual_review"][0]["reason"] == "upload_failed"

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_sir_routes_to_m1_subfolder(
        self, mock_extract, mock_classify, mock_resolve_m1, mock_build_summary
    ):
        """SIRs land in the matched site's M1 folder, not the legacy SIR folder."""
        mock_extract.return_value = MagicMock(
            message_id="msg_sir_m1",
            subject="Alpha Keller SIR",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            attachments=[
                {
                    "filename": "Alpha Keller SIR.pdf",
                    "attachment_id": "sir1",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("sir", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive.google.com/drive/folders/m1")
        mock_build_summary.return_value = {
            "title": "Alpha Keller",
            "address": "123 Main St",
            "drive_folder_url": "https://drive.google.com/drive/folders/site123",
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.return_value = {
            "id": "sir_id",
            "webViewLink": "https://drive.google.com/file/d/sir_id",
        }

        site_records = [{"id": "IEKELLER", "title": "Alpha Keller", "customFields": []}]

        result = process_email(
            gc,
            "msg_sir_m1",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=site_records,
        )

        assert result["marked"] is True
        assert len(result["uploaded"]) == 1
        upload = result["uploaded"][0]
        assert upload["doc_type"] == "sir"
        assert upload["site_title"] == "Alpha Keller"
        gc.upload_file_to_folder.assert_called_once_with(
            folder_id="m1_folder_id",
            file_name=upload["drive_filename"],
            file_bytes=b"pdf",
        )
        first_resolve_call = mock_resolve_m1.call_args_list[0]
        assert first_resolve_call.kwargs == {"allow_legacy_fallback": False}

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_building_inspection_routes_to_m1_subfolder(
        self, mock_extract, mock_classify, mock_resolve_m1, mock_build_summary
    ):
        """Building Inspection PDFs land in M1, mirroring SIR/Block Plan routing."""
        mock_extract.return_value = MagicMock(
            message_id="msg_bi_m1",
            subject="Building Inspection Report - Alpha Keller",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            attachments=[
                {
                    "filename": "Alpha Keller Building Inspection Report.pdf",
                    "attachment_id": "bi1",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("building_inspection", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive.google.com/drive/folders/m1")
        mock_build_summary.return_value = {
            "title": "Alpha Keller",
            "address": "123 Main St",
            "drive_folder_url": "https://drive.google.com/drive/folders/site123",
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.return_value = {
            "id": "bi_id",
            "webViewLink": "https://drive.google.com/file/d/bi_id",
        }

        site_records = [{"id": "IEKELLER", "title": "Alpha Keller", "customFields": []}]

        result = process_email(
            gc,
            "msg_bi_m1",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=site_records,
        )

        assert len(result["uploaded"]) == 1
        gc.upload_file_to_folder.assert_called_once()
        call_kwargs = gc.upload_file_to_folder.call_args.kwargs
        assert call_kwargs["folder_id"] == "m1_folder_id"
        first_resolve_call = mock_resolve_m1.call_args_list[0]
        assert first_resolve_call.kwargs == {"allow_legacy_fallback": False}

    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_unmatched_sir_goes_to_manual_review(self, mock_extract, mock_classify):
        """With no site match, SIR/BI/ISP follow the same review path as Block Plan.

        This guards the uniform fallback: every supported doc type now needs
        a matched site to know which M1 to upload into; without one, we leave
        the email in review rather than dumping the file in a generic folder.
        """
        mock_extract.return_value = MagicMock(
            message_id="msg_sir_unmatched",
            subject="SIR for unknown site",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            attachments=[
                {
                    "filename": "Random SIR.pdf",
                    "attachment_id": "u1",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("sir", 0.95)

        gc = MagicMock()
        result = process_email(gc, "msg_sir_unmatched", MagicMock(), "label_123", "review_123")

        assert result["uploaded"] == []
        assert result["skipped"] == 1
        assert result["marked"] is True
        assert result["low_confidence"][0]["doc_type"] == "sir"
        assert result["low_confidence"][0]["reason"] == "unmatched_site"
        assert result["manual_review"][0]["reason"] == "unmatched_site"
        # The scanner must NOT have tried to upload anything when site is unmatched.
        gc.upload_file_to_folder.assert_not_called()

    @patch("due_diligence_reporter.inbox_scanner.extract_text_from_pdf_bytes")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_unmatched_summer_camp_is_skipped_without_review(
        self,
        mock_extract,
        mock_classify,
        mock_pdf_text,
    ):
        mock_extract.return_value = MagicMock(
            message_id="msg_summer_camp",
            subject="Chicago, IL Summer Camp SIR",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            attachments=[
                {
                    "filename": "Alpha School (Summer Camp) - Chicago, IL SIR.pdf",
                    "attachment_id": "sc1",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("sir", 0.95)

        gc = MagicMock()
        result = process_email(gc, "msg_summer_camp", MagicMock(), "label_123", "review_123")

        assert result["uploaded"] == []
        assert result["skipped"] == 1
        assert result["manual_review"] == []
        assert result["low_confidence"] == []
        assert result["marked"] is True
        mock_pdf_text.assert_not_called()
        gc.upload_file_to_folder.assert_not_called()

    @patch("due_diligence_reporter.inbox_scanner._register_uploaded_document_in_rhodes")
    @patch("due_diligence_reporter.inbox_scanner._run_doc_arrival_folder_ping")
    @patch("due_diligence_reporter.inbox_scanner.extract_text_from_pdf_bytes")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_pdf_text_fallback_can_resolve_ambiguous_site(
        self,
        mock_extract,
        mock_classify,
        mock_resolve_m1,
        mock_pdf_text,
        mock_ping,
        mock_register,
    ):
        mock_extract.return_value = MagicMock(
            message_id="msg_bi_ambiguous",
            subject="May 8 Alpha Miami Beach Building Inspection",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            attachments=[
                {
                    "filename": "May 8 Alpha Miami Beach Building Inspection.pdf",
                    "attachment_id": "bi_mb",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("building_inspection", 0.95)
        mock_pdf_text.return_value = "Inspection address: 1021 Biarritz Dr, Miami Beach, FL"
        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive.google.com/drive/folders/m1")
        mock_ping.return_value = {"status": "accepted"}
        mock_register.return_value = {"status": "registered"}

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"%PDF-fake"
        gc.upload_file_to_folder.return_value = {
            "id": "bi_drive_id",
            "webViewLink": "https://drive.google.com/file/d/bi_drive_id",
        }

        site_records = [
            {
                "id": "SITE300",
                "title": "Alpha Miami Beach 300 71st St",
                "address": "300 71st St, Miami Beach, FL",
                "drive_folder_url": "https://drive.google.com/drive/folders/site300",
                "customFields": [],
            },
            {
                "id": "SITE1021",
                "title": "Alpha Miami Beach 1021 Biarritz Dr",
                "address": "1021 Biarritz Dr, Miami Beach, FL",
                "drive_folder_url": "https://drive.google.com/drive/folders/site1021",
                "customFields": [],
            },
        ]

        result = process_email(
            gc,
            "msg_bi_ambiguous",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=site_records,
        )

        assert result["manual_review"] == []
        assert len(result["uploaded"]) == 1
        assert result["uploaded"][0]["site_title"] == "Alpha Miami Beach 1021 Biarritz Dr"
        assert result["uploaded"][0]["matched_site_id"] == "SITE1021"
        assert result["uploaded"][0]["drive_filename"].endswith(
            "Alpha Miami Beach 1021 Biarritz Dr Building Inspection Report.pdf"
        )

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_sir_with_no_drive_folder_flags_review(
        self, mock_extract, mock_classify, mock_resolve_m1, mock_build_summary
    ):
        """Matched site missing a Drive folder URL flags review, never falls back to a shared folder."""
        mock_extract.return_value = MagicMock(
            message_id="msg_no_drive",
            subject="Alpha Keller SIR",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            attachments=[
                {
                    "filename": "Alpha Keller SIR.pdf",
                    "attachment_id": "nd1",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("sir", 0.95)
        # Site metadata exists but has no drive_folder_url — the upstream gap
        # we explicitly want surfaced rather than papered over.
        mock_build_summary.return_value = {"title": "Alpha Keller", "drive_folder_url": ""}

        gc = MagicMock()
        site_records = [{"id": "IEKELLER", "title": "Alpha Keller", "customFields": []}]
        result = process_email(
            gc,
            "msg_no_drive",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=site_records,
        )

        assert result["uploaded"] == []
        assert result["marked"] is True
        assert any(
            err.get("error") == "Matched site has no Google Drive folder URL"
            for err in result["errors"]
        )
        assert result["manual_review"][0]["reason"] == "missing_drive_folder"
        mock_resolve_m1.assert_not_called()
        gc.upload_file_to_folder.assert_not_called()

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_cancelled_site_with_no_drive_folder_is_suppressed(
        self, mock_extract, mock_classify, mock_resolve_m1, mock_build_summary
    ):
        mock_extract.return_value = MagicMock(
            message_id="msg_cancelled_no_drive",
            subject="RE: New Site Kickoff: 22600 Crenshaw Blvd, Torrance, CA",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="May 6 Alpha Torrance Building Inspection",
            attachments=[
                {
                    "filename": "May 6 Alpha Torrance Building Inspection.pdf",
                    "attachment_id": "nd1",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("building_inspection", 0.95)
        mock_build_summary.return_value = {
            "title": "Alpha Torrance 22600 Crenshaw Blvd",
            "drive_folder_url": "",
            "rhodes_status": "cancelled",
        }

        gc = MagicMock()
        site_records = [
            {
                "id": "IETORRANCE",
                "title": "Alpha Torrance 22600 Crenshaw Blvd",
                "rhodes_status": "cancelled",
                "customFields": [],
            }
        ]
        result = process_email(
            gc,
            "msg_cancelled_no_drive",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=site_records,
        )

        assert result["uploaded"] == []
        assert result["skipped"] == 1
        assert result["errors"] == []
        assert result["manual_review"] == []
        assert result["marked"] is True
        mock_resolve_m1.assert_not_called()
        gc.upload_file_to_folder.assert_not_called()

    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_unmatched_block_plan_goes_to_manual_review(self, mock_extract, mock_classify):
        mock_extract.return_value = MagicMock(
            message_id="msg_block_1",
            subject="Block Plan attached",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            attachments=[
                {
                    "filename": "Alpha Keller Block Plan.pdf",
                    "attachment_id": "bp1",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("block_plan", 0.95)

        gc = MagicMock()
        result = process_email(gc, "msg_block_1", MagicMock(), "label_123", "review_123")

        assert result["uploaded"] == []
        assert result["skipped"] == 1
        assert result["marked"] is True
        assert result["low_confidence"][0]["doc_type"] == "block_plan"
        assert result["manual_review"][0]["reason"] == "unmatched_site"

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._run_block_plan_downstream")
    @patch("due_diligence_reporter.inbox_scanner.extract_text_from_pdf_bytes")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_block_plan_uploads_to_m1_and_runs_downstream(
        self,
        mock_extract,
        mock_classify,
        mock_resolve_m1,
        mock_extract_pdf,
        mock_downstream,
        mock_build_summary,
    ):
        mock_extract.return_value = MagicMock(
            message_id="msg_block_2",
            subject="Alpha Keller Block Plan",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            attachments=[
                {
                    "filename": "Alpha Keller Block Plan.pdf",
                    "attachment_id": "bp2",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("block_plan", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive.google.com/drive/folders/m1")
        mock_extract_pdf.return_value = "block plan text"
        mock_downstream.return_value = [
            {
                "doc_type": "capacity_brainlift_report",
                "doc_url": "https://docs.google.com/document/d/cap",
            },
            {
                "doc_type": "raycon_scenario_report",
                "doc_url": "https://docs.google.com/document/d/ray",
            },
        ]
        mock_build_summary.return_value = {
            "title": "Alpha Keller",
            "address": "123 Main St, Keller, TX 76248",
            "drive_folder_url": "https://drive.google.com/drive/folders/site123",
            "total_building_sf": 12000,
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.return_value = {
            "id": "block123",
            "webViewLink": "https://drive.google.com/file/d/block123",
        }

        site_records = [{"id": "IEBLOCK123", "title": "Alpha Keller", "customFields": []}]

        result = process_email(
            gc,
            "msg_block_2",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=site_records,
        )

        assert result["marked"] is True
        assert len(result["uploaded"]) == 1
        upload = result["uploaded"][0]
        assert upload["doc_type"] == "block_plan"
        assert upload["site_title"] == "Alpha Keller"
        assert upload["drive_filename"].endswith("Alpha Keller Block Plan.pdf")
        assert len(upload["derived_documents"]) == 2
        gc.upload_file_to_folder.assert_called_once_with(
            folder_id="m1_folder_id",
            file_name=upload["drive_filename"],
            file_bytes=b"pdf",
        )
        first_resolve_call = mock_resolve_m1.call_args_list[0]
        assert first_resolve_call.kwargs == {"allow_legacy_fallback": False}
        mock_downstream.assert_called_once()

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._list_m1_documents_by_type")
    @patch("due_diligence_reporter.inbox_scanner._run_block_plan_downstream")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_duplicate_block_plan_skips_when_derived_docs_exist(
        self,
        mock_extract,
        mock_classify,
        mock_resolve_m1,
        mock_downstream,
        mock_list_docs,
        mock_build_summary,
    ):
        mock_extract.return_value = MagicMock(
            message_id="msg_block_3",
            subject="Alpha Keller Block Plan",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            label_ids=[],
            attachments=[
                {
                    "filename": "Alpha Keller Block Plan.pdf",
                    "attachment_id": "bp3",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("block_plan", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive.google.com/drive/folders/m1")
        mock_list_docs.return_value = {
            "block_plan": {"id": "block123", "name": "Apr 22 2026 - Alpha Keller Block Plan.pdf"},
            "raycon_scenario_json": {"id": "ray-json-123", "name": "raycon_scenario.json"},
        }
        mock_build_summary.return_value = {
            "title": "Alpha Keller",
            "address": "123 Main St, Keller, TX 76248",
            "drive_folder_url": "https://drive.google.com/drive/folders/site123",
            "total_building_sf": 12000,
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = True

        site_records = [{"id": "IEBLOCK123", "title": "Alpha Keller", "customFields": []}]

        result = process_email(
            gc,
            "msg_block_3",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=site_records,
        )

        assert result["uploaded"] == []
        assert result["skipped"] == 1
        gc.upload_file_to_folder.assert_not_called()
        mock_downstream.assert_not_called()

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._list_m1_documents_by_type")
    @patch("due_diligence_reporter.inbox_scanner._run_block_plan_downstream")
    @patch("due_diligence_reporter.inbox_scanner.extract_text_from_pdf_bytes")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_duplicate_block_plan_reruns_downstream_when_derived_docs_missing(
        self,
        mock_extract,
        mock_classify,
        mock_resolve_m1,
        mock_extract_pdf,
        mock_downstream,
        mock_list_docs,
        mock_build_summary,
    ):
        mock_extract.return_value = MagicMock(
            message_id="msg_block_retry",
            subject="Alpha Keller Block Plan",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            label_ids=[],
            attachments=[
                {
                    "filename": "Alpha Keller Block Plan.pdf",
                    "attachment_id": "bp4",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("block_plan", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive.google.com/drive/folders/m1")
        mock_extract_pdf.return_value = "block plan text"
        mock_list_docs.return_value = {
            "block_plan": {
                "id": "block123",
                "name": "Apr 22 2026 - Alpha Keller Block Plan.pdf",
                "webViewLink": "https://drive.google.com/file/d/block123",
            },
        }
        mock_downstream.return_value = [
            {
                "doc_type": "capacity_brainlift_report",
                "doc_url": "https://docs.google.com/document/d/cap",
            },
            {
                "doc_type": "raycon_scenario_report",
                "doc_url": "https://docs.google.com/document/d/ray",
            },
        ]
        mock_build_summary.return_value = {
            "title": "Alpha Keller",
            "address": "123 Main St, Keller, TX 76248",
            "drive_folder_url": "https://drive.google.com/drive/folders/site123",
            "total_building_sf": 12000,
            "p1_assignee_email": "owner@example.com",
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = True
        gc.gmail_get_attachment.return_value = b"pdf"

        site_records = [{"id": "IEBLOCK123", "title": "Alpha Keller", "customFields": []}]

        result = process_email(
            gc,
            "msg_block_retry",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=site_records,
        )

        assert result["skipped"] == 0
        assert len(result["uploaded"]) == 1
        assert result["uploaded"][0]["retry_existing_upload"] is True
        assert len(result["uploaded"][0]["derived_documents"]) == 2
        gc.upload_file_to_folder.assert_not_called()
        mock_downstream.assert_called_once()

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._send_block_plan_failure_notification")
    @patch("due_diligence_reporter.inbox_scanner._run_block_plan_downstream")
    @patch("due_diligence_reporter.inbox_scanner.extract_text_from_pdf_bytes")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_block_plan_downstream_failure_is_non_fatal_to_upload(
        self,
        mock_extract,
        mock_classify,
        mock_resolve_m1,
        mock_extract_pdf,
        mock_downstream,
        mock_notify,
        mock_build_summary,
    ):
        mock_extract.return_value = MagicMock(
            message_id="msg_block_fail",
            subject="Alpha Keller Block Plan",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            label_ids=[],
            attachments=[
                {
                    "filename": "Alpha Keller Block Plan.pdf",
                    "attachment_id": "bp5",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("block_plan", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive.google.com/drive/folders/m1")
        mock_extract_pdf.return_value = "block plan text"
        mock_downstream.side_effect = RuntimeError("RayCon failed")
        mock_build_summary.return_value = {
            "title": "Alpha Keller",
            "address": "123 Main St, Keller, TX 76248",
            "drive_folder_url": "https://drive.google.com/drive/folders/site123",
            "total_building_sf": 12000,
            "p1_assignee_email": "owner@example.com",
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.return_value = {
            "id": "block123",
            "webViewLink": "https://drive.google.com/file/d/block123",
        }

        site_records = [{"id": "IEBLOCK123", "title": "Alpha Keller", "customFields": []}]

        result = process_email(
            gc,
            "msg_block_fail",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=site_records,
        )

        assert result["marked"] is True
        assert result["errors"] == []
        assert result["uploaded"][0]["raycon_dispatch_error"] == "RayCon failed"
        assert result["uploaded"][0]["raycon_dispatch_status"] == "dispatch_failed"
        gc.gmail_modify_labels.assert_called_once_with(
            "msg_block_fail",
            add_labels=["label_123"],
            remove_labels=["UNREAD", "review_123"],
        )
        mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# MIME part walking
# ---------------------------------------------------------------------------


class TestWalkParts:
    """Test recursive MIME part extraction."""

    def test_extracts_pdf_attachments(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"size": 100},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "report.pdf",
                    "body": {"attachmentId": "att_123", "size": 50000},
                },
            ],
        }
        attachments: list = []
        _walk_parts(payload, attachments)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "report.pdf"
        assert attachments[0]["attachment_id"] == "att_123"

    def test_skips_non_pdf_attachments(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "image/png",
                    "filename": "logo.png",
                    "body": {"attachmentId": "att_456", "size": 1000},
                },
            ],
        }
        attachments: list = []
        _walk_parts(payload, attachments)
        assert len(attachments) == 0

    def test_extracts_pdf_by_filename_even_with_generic_mime(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "application/octet-stream",
                    "filename": "report.pdf",
                    "body": {"attachmentId": "att_456", "size": 1000},
                },
            ],
        }
        attachments: list = []
        _walk_parts(payload, attachments)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "report.pdf"

    def test_handles_nested_parts(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "application/pdf",
                            "filename": "nested.pdf",
                            "body": {"attachmentId": "att_789", "size": 30000},
                        },
                    ],
                },
            ],
        }
        attachments: list = []
        _walk_parts(payload, attachments)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "nested.pdf"


# ---------------------------------------------------------------------------
# Idempotency â€” already-processed emails
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Inbox search must not hide new attachments in previously labeled threads."""

    def test_scan_query_does_not_exclude_processed_threads(self):
        """A processed kickoff thread can later receive a vendor SIR reply."""
        gc = MagicMock()
        gc.gmail_get_or_create_label.side_effect = ["processed", "review", "internal"]
        gc.gmail_search.return_value = []

        settings = MagicMock()
        settings.inbox_scan_query = "in:inbox has:attachment filename:pdf"
        settings.inbox_processed_label = "DD-Processed"
        settings.inbox_manual_review_label = "DD-Manual-Review"
        settings.inbox_internal_skip_label = "DD-Internal-Skipped"
        settings.inbox_scan_max_results = 50

        scan_inbox(gc, [], settings)

        gc.gmail_search.assert_called_once_with(
            "in:inbox has:attachment filename:pdf",
            max_results=50,
        )

    def test_gmail_search_query_covers_forums_category(self):
        """The default scan query must cover CATEGORY_FORUMS so Google Group-routed
        emails (e.g. via auth.permitting@trilogy.com) are not silently dropped.

        Regression: 2026-04-23 Providence Croft Schools SIRs (and several other CDS
        deliveries via the auth.permitting Google Group) landed in CATEGORY_FORUMS
        and were never picked up by the default Gmail search, which only returns
        the Primary tab.

        Acceptable forms:
          - explicit positive: contains 'category:forums' (UI-bar form, not
            honored by the REST API but kept here for forward-compat).
          - negative-form: excludes promotions and social (REST-API safe; this
            is the form the production default uses).
        """
        from due_diligence_reporter.config import Settings

        settings = Settings()
        q = settings.inbox_scan_query
        positive_form = "category:forums" in q
        negative_form = "-category:promotions" in q and "-category:social" in q
        assert positive_form or negative_form, (
            f"default inbox_scan_query does not cover CATEGORY_FORUMS: {q!r}"
        )

    def test_gmail_search_query_does_not_self_reference_recipient(self):
        """The scanner runs OAuth-authed AS the recipient mailbox
        (edu.ops@trilogy.com). Querying `to:edu.ops cc:edu.ops` from inside
        that same mailbox returns 0 messages -- especially for Group-routed
        mail where the recipient appears in Delivered-To rather than the
        rendered To/Cc headers the API matcher inspects.

        The default query must therefore rely on in:inbox (or equivalent)
        rather than self-referencing to:/cc: filters.

        Regression: 2026-04-28 catch-up sweeps (runs 25068686981 and
        25068942447) returned 0 messages despite the same query strings
        returning matches when run from a third-party mailbox via the
        Gmail connector.
        """
        from due_diligence_reporter.config import Settings

        settings = Settings()
        q = settings.inbox_scan_query
        assert "to:edu.ops" not in q, f"inbox_scan_query must not self-reference to:edu.ops: {q!r}"
        assert "cc:edu.ops" not in q, f"inbox_scan_query must not self-reference cc:edu.ops: {q!r}"
        assert "in:inbox" in q, (
            f"inbox_scan_query must use in:inbox to scope to received mail: {q!r}"
        )

    def test_gmail_search_query_includes_pdf_attachments(self):
        """Default query must filter for PDF attachments.

        DOCX support is a known follow-up: it requires touching the
        attachment processor (_walk_parts), classifier, drive uploader
        filename templates, and reporter. Tracked separately.
        """
        from due_diligence_reporter.config import Settings

        settings = Settings()
        q = settings.inbox_scan_query
        assert "pdf" in q, f"inbox_scan_query must include pdf attachments: {q!r}"


class TestEffectiveSender:
    """Sender classification must use X-Original-Sender for Group-routed mail.

    Regression: 2026-04-28 the catch-up sweep skipped 40 emails as 'internal
    sender' because Google-Group-routed CDS / regulator / vendor deliveries
    have their visible From: rewritten to auth.permitting@trilogy.com (the
    group address), which matches inbox_internal_sender_domains=trilogy.com.
    The actual external sender is preserved in X-Original-Sender.
    """

    def _meta(self, sender: str, original_sender: str = "") -> EmailMetadata:
        return EmailMetadata(
            message_id="m1",
            subject="Test",
            sender=sender,
            body_snippet="",
            label_ids=[],
            attachments=[],
            original_sender=original_sender,
        )

    def test_effective_sender_falls_back_to_from_when_no_original(self):
        m = self._meta("alice@external.com")
        assert m.effective_sender == "alice@external.com"

    def test_effective_sender_prefers_x_original_sender(self):
        m = self._meta(
            "'Monica Swannie' via Alpha Authorization and Permitting <auth.permitting@trilogy.com>",
            original_sender="mswannie@cdsdevelopment.com",
        )
        assert m.effective_sender == "mswannie@cdsdevelopment.com"

    def test_group_routed_external_is_not_classified_internal(self):
        """The Croft-pattern email: visible From: is a trilogy.com group,
        but the actual sender is mswannie@cdsdevelopment.com. Must NOT be
        classified internal.
        """
        from due_diligence_reporter.config import Settings

        settings = Settings()  # default inbox_internal_sender_domains=trilogy.com
        m = self._meta(
            "'Monica Swannie' via Alpha Authorization and Permitting <auth.permitting@trilogy.com>",
            original_sender="mswannie@cdsdevelopment.com",
        )
        assert not _is_internal_sender(m.effective_sender, settings)

    def test_genuinely_internal_still_classified_internal(self):
        """Direct internal mail (no Group routing) must still be skipped
        so AI-generated documents do not create false readiness.
        """
        from due_diligence_reporter.config import Settings

        settings = Settings()
        m = self._meta("Greg Foote <greg.foote@trilogy.com>")
        assert _is_internal_sender(m.effective_sender, settings)

    def test_internal_via_group_still_internal(self):
        """If an internal trilogy person posts to a trilogy group, the
        X-Original-Sender will also be internal -- still skip.
        """
        from due_diligence_reporter.config import Settings

        settings = Settings()
        m = self._meta(
            "'Greg Foote' via Alpha Authorization and Permitting <auth.permitting@trilogy.com>",
            original_sender="greg.foote@trilogy.com",
        )
        assert _is_internal_sender(m.effective_sender, settings)

    @patch("due_diligence_reporter.inbox_scanner._mark_email_processed")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_internal_skip_applies_internal_label_not_processed(self, mock_extract, mock_mark):
        """Internal-skip path must apply DD-Internal-Skipped, NOT DD-Processed.

        Keeping the label distinct from DD-Processed means future heuristic
        bugs are recoverable by clearing this single label, without burning
        the audit trail of legitimate uploads.
        """
        from due_diligence_reporter.config import Settings

        settings = Settings()
        mock_extract.return_value = MagicMock(
            message_id="msg_int",
            subject="Internal note",
            sender="Greg Foote <greg.foote@trilogy.com>",
            effective_sender="Greg Foote <greg.foote@trilogy.com>",
            body_snippet="",
            attachments=[],
            label_ids=[],
        )

        gc = MagicMock()
        result = process_email(
            gc,
            "msg_int",
            settings,
            label_id="PROCESSED_LABEL_ID",
            review_label_id="REVIEW_LABEL_ID",
            internal_skip_label_id="INTERNAL_SKIP_LABEL_ID",
        )

        assert result["internal_skipped"] is True
        # _mark_email_processed should be called with the internal-skip label,
        # NOT the processed label.
        mock_mark.assert_called_once()
        called_with_label = mock_mark.call_args[0][2]
        assert called_with_label == "INTERNAL_SKIP_LABEL_ID", (
            f"Expected internal-skip label, got {called_with_label!r}"
        )

    @patch("due_diligence_reporter.inbox_scanner._mark_email_processed")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_internal_skip_falls_back_to_processed_label_if_unset(self, mock_extract, mock_mark):
        """Backward-compat: legacy callers that don't pass
        internal_skip_label_id should still get a label applied (the
        processed label, since they pre-date the new label).
        """
        from due_diligence_reporter.config import Settings

        settings = Settings()
        mock_extract.return_value = MagicMock(
            message_id="msg_int2",
            subject="Internal note",
            sender="greg.foote@trilogy.com",
            effective_sender="greg.foote@trilogy.com",
            body_snippet="",
            attachments=[],
            label_ids=[],
        )

        gc = MagicMock()
        result = process_email(
            gc,
            "msg_int2",
            settings,
            label_id="PROCESSED_LABEL_ID",
            review_label_id="REVIEW_LABEL_ID",
            # internal_skip_label_id intentionally omitted
        )

        assert result["internal_skipped"] is True
        mock_mark.assert_called_once()
        called_with_label = mock_mark.call_args[0][2]
        assert called_with_label == "PROCESSED_LABEL_ID"

    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_unknown_doc_type_email_marked_processed(self, mock_extract, mock_classify):
        """Emails with only unknown-type attachments are marked processed (no re-scan needed)."""
        mock_extract.return_value = MagicMock(
            message_id="msg_1",
            subject="Hello",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="Some text",
            attachments=[
                {"filename": "notes.pdf", "attachment_id": "a1", "mime_type": "application/pdf"}
            ],
        )
        mock_classify.return_value = ("unknown", 0.0)

        gc = MagicMock()
        result = process_email(gc, "msg_1", MagicMock(), "label_123", "review_123")

        assert result["skipped"] == 1
        assert len(result["uploaded"]) == 0
        assert result["marked"] is True  # mark so we don't re-scan it forever

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_site_identity_is_attached_to_uploads(
        self, mock_extract, mock_classify, mock_resolve_m1, mock_build_summary
    ):
        mock_extract.return_value = MagicMock(
            message_id="msg_4",
            subject="Alpha Keller SIR",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            attachments=[
                {
                    "filename": "Alpha Keller SIR.pdf",
                    "attachment_id": "a4",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("sir", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive.google.com/drive/folders/m1")
        mock_build_summary.return_value = {
            "title": "Alpha Keller",
            "address": "123 Main St",
            "drive_folder_url": "https://drive.google.com/drive/folders/site123",
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.return_value = {
            "id": "file123",
            "webViewLink": "https://drive/file123",
        }

        site_records = [{"id": "IEABCD123", "title": "Alpha Keller", "customFields": []}]

        result = process_email(
            gc,
            "msg_4",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=site_records,
        )

        assert result["marked"] is True
        assert result["uploaded"][0]["site_title"] == "Alpha Keller"
        assert result["uploaded"][0]["matched_site_id"] == "IEABCD123"
        assert result["uploaded"][0]["drive_filename"].endswith("Alpha Keller SIR.pdf")
        assert has_site_identity(result["uploaded"]) is True

    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_missing_pdf_extraction_goes_to_manual_review(self, mock_extract):
        mock_extract.return_value = MagicMock(
            message_id="msg_5",
            subject="Has PDF",
            sender="test@example.com",
            effective_sender="test@example.com",
            body_snippet="",
            attachments=[],
        )

        gc = MagicMock()
        result = process_email(gc, "msg_5", MagicMock(), "label_123", "review_123")

        assert result["marked"] is True
        assert len(result["errors"]) == 1
        gc.gmail_modify_labels.assert_called_once_with(
            "msg_5",
            add_labels=["label_123", "review_123"],
            remove_labels=[],
        )


class TestScanResults:
    def test_scan_inbox_aggregates_email_errors(self):
        gc = MagicMock()
        gc.gmail_get_or_create_label.return_value = "label_123"
        gc.gmail_search.return_value = [{"id": "msg_1"}]

        settings = MagicMock()
        settings.inbox_processed_label = "DD-Processed"
        settings.inbox_scan_query = "query"
        settings.inbox_scan_max_results = 10

        with patch("due_diligence_reporter.inbox_scanner.process_email") as mock_process:
            mock_process.return_value = {
                "uploaded": [],
                "skipped": 0,
                "low_confidence": [],
                "errors": [{"message_id": "msg_1", "filename": "sir.pdf", "error": "boom"}],
                "marked": False,
            }
            result = scan_inbox(gc, [], settings)

        assert len(result["errors"]) == 1
        assert result["errors"][0]["filename"] == "sir.pdf"

    def test_has_site_identity_requires_title_or_id(self):
        assert has_site_identity([{"site_title": None, "matched_site_id": None}]) is False
        assert has_site_identity([{"site_title": "Alpha Keller", "matched_site_id": None}]) is True


class TestBlockPlanDownstream:
    """Block Plan downstream now pings RayCon's async /v1/jobs endpoint and exits.

    DDR no longer runs Capacity Brainlift or calls RayCon synchronously. RayCon
    reads SIR/BI/Block Plan from Drive itself, derives rooms, and writes
    `raycon_scenario.json` back into the site's M1 folder. The followup script
    publishes the report Doc.
    """

    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.raycon_client.post_raycon_job")
    def test_pings_raycon_with_full_spec_payload_and_returns_request_record(
        self,
        mock_post,
        mock_resolve_m1,
    ):
        mock_post.return_value = {
            "status": "queued",
            "job_id": "job-abc-123",
            "raycon_run_id": "run-abc-123",
            "idempotency_key": "block_plan|IEBLOCK123|block123",
            "retry_after_seconds": 30,
            "status_url": "https://raycon.test/v1/jobs/status/job-abc-123?token=opaque",
            "cached": False,
            "queued_at": "2026-04-30T13:45:00Z",
        }
        mock_resolve_m1.return_value = ("m1-folder-id", "M1")

        gc = MagicMock()
        result = _run_block_plan_downstream(
            gc,
            site_summary={
                "id": "IEBLOCK123",
                "title": "Alpha Keller",
                "address": "123 Main St, Keller, TX 76248",
                "drive_folder_url": "https://drive.google.com/drive/folders/site_folder_456",
                "total_building_sf": 12000,
            },
            block_plan_content="BLOCK PLAN FULL TEXT",
            block_plan_url="https://drive.google.com/file/d/block123/view",
            block_plan_file_id="block123",
        )

        # Exactly one record describing the request — no Capacity Brainlift,
        # no synchronous RayCon, no Doc publication from the scanner.
        assert len(result) == 1
        record = result[0]
        assert record["doc_type"] == "raycon_scenario_request"
        assert record["job_id"] == "job-abc-123"
        assert record["raycon_run_id"] == "run-abc-123"
        assert record["idempotency_key"] == "block_plan|IEBLOCK123|block123"
        assert record["retry_after_seconds"] == "30"
        assert record["status_url_present"] is True
        assert record["status"] == "queued"
        assert record["block_plan_file_id"] == "block123"

        kwargs = mock_post.call_args.kwargs
        # All 11 spec §1.2 fields should reach post_raycon_job.
        assert kwargs["site_id"] == "IEBLOCK123"
        assert kwargs["site_name"] == "Alpha Keller"
        assert kwargs["address"] == "123 Main St, Keller, TX 76248"
        assert kwargs["drive_folder_url"].endswith("/site_folder_456")
        assert kwargs["m1_folder_id"] == "m1-folder-id"
        assert kwargs["block_plan_file_id"] == "block123"
        assert kwargs["block_plan_url"].endswith("/view")
        assert kwargs["total_building_sf"] == 12000

    @patch("due_diligence_reporter.raycon_client.post_raycon_job")
    def test_raises_when_drive_folder_url_missing(self, mock_post):
        gc = MagicMock()
        with pytest.raises(RuntimeError, match="site_id, title, address"):
            _run_block_plan_downstream(
                gc,
                site_summary={
                    "id": "IEBLOCK123",
                    "title": "Alpha Keller",
                    "address": "123 Main St",
                    "drive_folder_url": "",
                    "total_building_sf": 12000,
                },
                block_plan_content="BLOCK PLAN FULL TEXT",
                block_plan_url="https://drive.google.com/file/d/block123",
                block_plan_file_id="block123",
            )
        mock_post.assert_not_called()

    @patch("due_diligence_reporter.raycon_client.post_raycon_job")
    def test_raises_when_block_plan_file_id_missing(self, mock_post):
        gc = MagicMock()
        with pytest.raises(RuntimeError, match="block_plan_file_id"):
            _run_block_plan_downstream(
                gc,
                site_summary={
                    "id": "IEBLOCK123",
                    "title": "Alpha Keller",
                    "address": "123 Main St",
                    "drive_folder_url": "https://drive.google.com/drive/folders/abc",
                    "total_building_sf": 12000,
                },
                block_plan_content="BLOCK PLAN FULL TEXT",
                block_plan_url="https://drive.google.com/file/d/block123",
                block_plan_file_id="",
            )
        mock_post.assert_not_called()

    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.raycon_client.post_raycon_job")
    def test_raises_when_m1_folder_cannot_be_resolved(
        self,
        mock_post,
        mock_resolve_m1,
    ):
        # m1_folder_id is required by spec §1.2; if Drive can't resolve it,
        # we must NOT silently send a malformed payload to RayCon.
        mock_resolve_m1.return_value = (None, None)
        gc = MagicMock()
        with pytest.raises(RuntimeError, match="M1 folder"):
            _run_block_plan_downstream(
                gc,
                site_summary={
                    "id": "IEBLOCK123",
                    "title": "Alpha Keller",
                    "address": "123 Main St",
                    "drive_folder_url": "https://drive.google.com/drive/folders/abc",
                    "total_building_sf": 12000,
                },
                block_plan_content="BLOCK PLAN FULL TEXT",
                block_plan_url="https://drive.google.com/file/d/block123",
                block_plan_file_id="block123",
            )
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# Rhodes document registration side effect
# ---------------------------------------------------------------------------


class TestRhodesDocumentRegistration:
    """Successful Drive uploads should be linked to Rhodes without blocking filing."""

    @staticmethod
    def _settings() -> SimpleNamespace:
        return SimpleNamespace(
            inbox_internal_sender_addresses="",
            inbox_internal_sender_domains="trilogy.com",
            google_chat_webhook_url="",
        )

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner._run_doc_arrival_folder_ping")
    @patch("due_diligence_reporter.inbox_scanner._register_uploaded_document_in_rhodes")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_successful_upload_records_rhodes_registration(
        self,
        mock_extract,
        mock_classify,
        mock_register,
        mock_ping,
        mock_resolve_m1,
        mock_build_summary,
    ):
        mock_extract.return_value = MagicMock(
            message_id="msg_rhodes",
            subject="Alpha Keller ISP",
            sender="vendor@example.com",
            effective_sender="vendor@example.com",
            body_snippet="",
            attachments=[
                {
                    "filename": "Alpha Keller ISP.pdf",
                    "attachment_id": "att_isp",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("isp", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "M1")
        mock_build_summary.return_value = {
            "id": "SITE1",
            "title": "Alpha Keller",
            "address": "123 Main St",
            "drive_folder_url": "https://drive.google.com/drive/folders/site_abc",
        }
        mock_register.return_value = {
            "status": "registered",
            "rhodes_doc_type": "other",
            "rhodes_document_id": "DOC1",
        }
        mock_ping.return_value = {"status": "accepted"}

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.return_value = {
            "id": "isp_drive_id",
            "webViewLink": "https://drive.google.com/file/d/isp_drive_id",
        }

        result = process_email(
            gc,
            "msg_rhodes",
            self._settings(),
            "label_123",
            "review_123",
            site_records=[{"id": "SITE1", "title": "Alpha Keller", "customFields": []}],
        )

        assert result["uploaded"][0]["rhodes_registration"] == {
            "status": "registered",
            "rhodes_doc_type": "other",
            "rhodes_document_id": "DOC1",
        }
        kwargs = mock_register.call_args.kwargs
        assert kwargs["site_summary"]["id"] == "SITE1"
        assert kwargs["ddr_doc_type"] == "isp"
        assert kwargs["drive_file"]["id"] == "isp_drive_id"
        assert kwargs["message_id"] == "msg_rhodes"
        assert kwargs["attachment"]["attachment_id"] == "att_isp"

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner._run_doc_arrival_folder_ping")
    @patch("due_diligence_reporter.inbox_scanner._register_uploaded_document_in_rhodes")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_existing_duplicate_drive_file_records_rhodes_registration(
        self,
        mock_extract,
        mock_classify,
        mock_register,
        mock_ping,
        mock_resolve_m1,
        mock_build_summary,
    ):
        drive_filename = f"{datetime.now().strftime('%b %d %Y')} - Alpha Keller ISP.pdf"
        mock_extract.return_value = MagicMock(
            message_id="msg_rhodes_existing",
            subject="Alpha Keller ISP",
            sender="vendor@example.com",
            effective_sender="vendor@example.com",
            body_snippet="",
            attachments=[
                {
                    "filename": "Alpha Keller ISP.pdf",
                    "attachment_id": "att_isp",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("isp", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "M1")
        mock_build_summary.return_value = {
            "id": "SITE1",
            "title": "Alpha Keller",
            "address": "123 Main St",
            "drive_folder_url": "https://drive.google.com/drive/folders/site_abc",
        }
        mock_register.return_value = {
            "status": "already_registered",
            "rhodes_doc_type": "other",
            "rhodes_document_id": "DOC1",
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = True
        gc.list_files_in_folder.return_value = [
            {
                "id": "isp_drive_id_old",
                "name": drive_filename,
                "webViewLink": "https://drive.google.com/file/d/isp_drive_id_old",
                "modifiedTime": "2026-05-26T10:00:00Z",
            },
            {
                "id": "isp_drive_id_existing",
                "name": drive_filename,
                "webViewLink": "https://drive.google.com/file/d/isp_drive_id_existing",
                "modifiedTime": "2026-05-27T10:00:00Z",
            },
        ]

        result = process_email(
            gc,
            "msg_rhodes_existing",
            self._settings(),
            "label_123",
            "review_123",
            site_records=[{"id": "SITE1", "title": "Alpha Keller", "customFields": []}],
            rhodes_retry_state={},
        )

        assert result["skipped"] == 0
        assert result["manual_review"] == []
        assert result["uploaded"][0]["existing_drive_file"] is True
        assert result["uploaded"][0]["drive_file_id"] == "isp_drive_id_existing"
        assert result["uploaded"][0]["rhodes_registration"] == {
            "status": "already_registered",
            "rhodes_doc_type": "other",
            "rhodes_document_id": "DOC1",
        }
        gc.gmail_get_attachment.assert_not_called()
        gc.upload_file_to_folder.assert_not_called()
        mock_ping.assert_not_called()
        kwargs = mock_register.call_args.kwargs
        assert kwargs["drive_file"]["id"] == "isp_drive_id_existing"
        assert kwargs["drive_filename"] == drive_filename
        assert kwargs["message_id"] == "msg_rhodes_existing"

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner._run_doc_arrival_folder_ping")
    @patch("due_diligence_reporter.inbox_scanner._register_uploaded_document_in_rhodes")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_rhodes_registration_failure_records_retry_without_blocking_drive_filing(
        self,
        mock_extract,
        mock_classify,
        mock_register,
        mock_ping,
        mock_resolve_m1,
        mock_build_summary,
    ):
        mock_extract.return_value = MagicMock(
            message_id="msg_rhodes_retry",
            subject="Alpha Keller ISP",
            sender="vendor@example.com",
            effective_sender="vendor@example.com",
            body_snippet="",
            label_ids=[],
            attachments=[
                {
                    "filename": "Alpha Keller ISP.pdf",
                    "attachment_id": "att_isp",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("isp", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "M1")
        mock_build_summary.return_value = {
            "id": "SITE1",
            "title": "Alpha Keller",
            "address": "123 Main St",
            "drive_folder_url": "https://drive.google.com/drive/folders/site_abc",
        }
        mock_register.return_value = {
            "status": "failed",
            "reason": "rhodes_error",
            "error": "timeout",
        }
        mock_ping.return_value = {"status": "accepted"}

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.return_value = {
            "id": "isp_drive_id",
            "webViewLink": "https://drive.google.com/file/d/isp_drive_id",
        }
        retry_state: dict[str, dict[str, object]] = {}

        result = process_email(
            gc,
            "msg_rhodes_retry",
            self._settings(),
            "label_123",
            "review_123",
            site_records=[{"id": "SITE1", "title": "Alpha Keller", "customFields": []}],
            rhodes_retry_state=retry_state,
        )

        assert result["uploaded"][0]["rhodes_registration"]["status"] == "failed"
        assert result["manual_review"] == []
        assert result["marked"] is True
        assert list(retry_state.values())[0]["attempts"] == 1
        assert list(retry_state.values())[0]["drive_file_id"] == "isp_drive_id"

    @patch("due_diligence_reporter.inbox_scanner._post_google_chat_to_configured_webhooks")
    @patch("due_diligence_reporter.inbox_scanner.add_rhodes_site_note")
    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner._run_doc_arrival_folder_ping")
    @patch("due_diligence_reporter.inbox_scanner._register_uploaded_document_in_rhodes")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_rhodes_registration_retry_exhaustion_marks_manual_review(
        self,
        mock_extract,
        mock_classify,
        mock_register,
        mock_ping,
        mock_resolve_m1,
        mock_build_summary,
        mock_add_note,
        mock_chat,
    ):
        drive_filename = f"{datetime.now().strftime('%b %d %Y')} - Alpha Keller ISP.pdf"
        retry_state = {
            "SITE1|isp|Alpha Keller ISP.pdf": {
                "attempts": 2,
                "site_id": "SITE1",
                "site_title": "Alpha Keller",
                "doc_type": "isp",
                "drive_filename": drive_filename,
                "original_filename": "Alpha Keller ISP.pdf",
                "drive_file_id": "isp_drive_id",
                "drive_link": "https://drive.google.com/file/d/isp_drive_id",
            }
        }
        mock_extract.return_value = MagicMock(
            message_id="msg_rhodes_retry_exhausted",
            subject="Alpha Keller ISP",
            sender="vendor@example.com",
            effective_sender="vendor@example.com",
            body_snippet="",
            label_ids=[],
            attachments=[
                {
                    "filename": "Alpha Keller ISP.pdf",
                    "attachment_id": "att_isp",
                    "mime_type": "application/pdf",
                }
            ],
        )
        mock_classify.return_value = ("isp", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "M1")
        mock_build_summary.return_value = {
            "id": "SITE1",
            "title": "Alpha Keller",
            "address": "123 Main St",
            "drive_folder_url": "https://drive.google.com/drive/folders/site_abc",
            "p1_assignee_name": "Owner One",
            "p1_assignee_email": "owner@example.com",
            "p1_assignee_user_id": "USER1",
        }
        mock_register.return_value = {
            "status": "failed",
            "reason": "rhodes_error",
            "error": "still down",
        }
        mock_ping.return_value = {"status": "accepted"}
        mock_add_note.return_value = {
            "status": "created",
            "rhodes_note_id": "NOTE1",
            "owner_notification": "mentioned",
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = True

        result = process_email(
            gc,
            "msg_rhodes_retry_exhausted",
            self._settings(),
            "label_123",
            "review_123",
            site_records=[{"id": "SITE1", "title": "Alpha Keller", "customFields": []}],
            rhodes_retry_state=retry_state,
        )

        assert result["uploaded"][0]["retry_existing_upload"] is True
        assert result["manual_review"][0]["reason"] == "rhodes_registration_retry_exhausted"
        assert result["manual_review"][0]["rhodes_failure_event"]["rhodes_note_id"] == "NOTE1"
        assert result["uploaded"][0]["rhodes_failure_event"]["owner_notification"] == "mentioned"
        assert list(retry_state.values())[0]["attempts"] == 3
        assert list(retry_state.values())[0]["rhodes_failure_note_id"] == "NOTE1"
        mock_add_note.assert_called_once()
        note_kwargs = mock_add_note.call_args.kwargs
        assert note_kwargs["site_id"] == "SITE1"
        assert note_kwargs["owner_user_id"] == "USER1"
        assert "AutomationEvent v1" in note_kwargs["body"]
        assert "Kind: document_registration_failed" in note_kwargs["body"]
        mock_chat.assert_not_called()

    @patch("due_diligence_reporter.inbox_scanner._post_google_chat_to_configured_webhooks")
    @patch("due_diligence_reporter.inbox_scanner.add_rhodes_site_note")
    def test_registration_failure_event_posts_chat_when_owner_missing(
        self,
        mock_add_note,
        mock_chat,
    ):
        retry_state: dict[str, dict[str, object]] = {"SITE1|isp|Alpha Keller ISP.pdf": {}}
        mock_add_note.return_value = {
            "status": "created",
            "rhodes_note_id": "NOTE1",
            "owner_notification": "none",
        }
        mock_chat.return_value = {"status": "sent", "posted": 1}

        result = _record_rhodes_registration_failure_event(
            settings=SimpleNamespace(google_chat_webhook_url="https://chat.example/hook"),
            retry_state=retry_state,
            retry_key="SITE1|isp|Alpha Keller ISP.pdf",
            site_summary={"id": "SITE1", "title": "Alpha Keller"},
            registration={
                "status": "failed",
                "reason": "rhodes_error",
                "error": "timeout",
                "rhodes_doc_type": "other",
                "rhodes_milestone": "acquireProperty",
                "retry_attempts": 3,
                "retry_limit": 2,
            },
            doc_type="isp",
            drive_file={
                "id": "isp_drive_id",
                "webViewLink": "https://drive.google.com/file/d/isp_drive_id",
            },
            drive_filename="May 27 2026 - Alpha Keller ISP.pdf",
            original_filename="Alpha Keller ISP.pdf",
            email_subject="Alpha Keller ISP",
            message_id="msg_rhodes_retry_exhausted",
            thread_id="thread_rhodes_retry_exhausted",
        )

        assert result["status"] == "created"
        assert result["google_chat"] == {"status": "sent", "posted": 1}
        mock_add_note.assert_called_once()
        assert mock_add_note.call_args.kwargs["owner_user_id"] == ""
        assert mock_add_note.call_args.kwargs["owner_email"] == ""
        mock_chat.assert_called_once()
        assert mock_chat.call_args.args[0] == "https://chat.example/hook"
        assert "Owner: No owner assigned" in mock_chat.call_args.args[1]
        assert retry_state["SITE1|isp|Alpha Keller ISP.pdf"]["rhodes_failure_note_id"] == "NOTE1"
        assert retry_state["SITE1|isp|Alpha Keller ISP.pdf"]["rhodes_failure_chat_status"] == "sent"

    def test_build_scan_summary_includes_rhodes_registration_counts(self):
        from due_diligence_reporter.inbox_scanner import build_scan_summary

        summary = build_scan_summary(
            {
                "emails_found": 1,
                "emails_processed": 1,
                "attachments_uploaded": 2,
                "attachments_skipped": 0,
                "uploads": [
                    {
                        "doc_type": "sir",
                        "drive_filename": "Alpha Keller SIR.pdf",
                        "rhodes_registration": {"status": "registered"},
                    },
                    {
                        "doc_type": "isp",
                        "drive_filename": "Alpha Keller ISP.pdf",
                        "rhodes_registration": {
                            "status": "failed",
                            "reason": "rhodes_error",
                            "error": "timeout",
                        },
                    },
                ],
                "low_confidence": [],
                "errors": [],
            }
        )

        assert "Rhodes document links: failed=1 registered=1" in summary
        assert "Rhodes: failed (rhodes_error: timeout)" in summary


# ---------------------------------------------------------------------------
# Per-doc folder ping (RayCon /v1/jobs lightweight notification)
# ---------------------------------------------------------------------------


class TestDocArrivalFolderPing:
    """On every classified upload — SIR, Worksmith inspection, ISP, Block
    Plan — we ping RayCon's /v1/jobs with the folder URL so it can decide
    whether the document set is complete enough to start computing."""

    _SITE = {
        "id": "IEBLOCK123",
        "title": "Alpha Keller",
        "address": "123 Main St, Keller, TX",
        "drive_folder_url": "https://drive.google.com/drive/folders/site_abc",
    }
    _DRIVE_FILE = {
        "id": "file-1",
        "webViewLink": "https://drive.google.com/file/d/file-1/view",
    }

    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.raycon_client.post_raycon_folder_ping")
    def test_ping_fires_for_sir_upload(self, mock_ping, mock_resolve_m1):
        mock_resolve_m1.return_value = ("m1-folder-id", "M1")
        mock_ping.return_value = {"status": "accepted"}
        gc = MagicMock()

        result = _run_doc_arrival_folder_ping(
            gc,
            site_summary=self._SITE,
            doc_type="sir",
            drive_file=self._DRIVE_FILE,
        )

        assert result["status"] == "accepted"
        assert result["doc_type"] == "sir"
        kwargs = mock_ping.call_args.kwargs
        assert kwargs["site_id"] == "IEBLOCK123"
        assert kwargs["site_name"] == "Alpha Keller"
        assert kwargs["address"] == "123 Main St, Keller, TX"
        assert kwargs["drive_folder_url"].endswith("/site_abc")
        assert kwargs["m1_folder_id"] == "m1-folder-id"
        assert kwargs["doc_type"] == "sir"
        assert kwargs["file_id"] == "file-1"
        assert kwargs["file_url"].endswith("/view")

    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.raycon_client.post_raycon_folder_ping")
    def test_ping_fires_for_each_doc_type(self, mock_ping, mock_resolve_m1):
        mock_resolve_m1.return_value = ("m1-folder-id", "M1")
        mock_ping.return_value = {"status": "accepted"}
        gc = MagicMock()

        for dt in ("sir", "building_inspection", "isp", "block_plan"):
            mock_ping.reset_mock()
            result = _run_doc_arrival_folder_ping(
                gc,
                site_summary=self._SITE,
                doc_type=dt,
                drive_file=self._DRIVE_FILE,
            )
            assert result["status"] == "accepted", dt
            assert mock_ping.call_args.kwargs["doc_type"] == dt

    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.raycon_client.post_raycon_folder_ping")
    def test_ping_failure_does_not_raise(self, mock_ping, mock_resolve_m1):
        """A flaky RayCon must not break a successful Drive upload."""
        mock_resolve_m1.return_value = ("m1-folder-id", "M1")
        mock_ping.side_effect = RuntimeError("RayCon 503")
        gc = MagicMock()

        result = _run_doc_arrival_folder_ping(
            gc,
            site_summary=self._SITE,
            doc_type="sir",
            drive_file=self._DRIVE_FILE,
        )

        assert result["status"] == "error"
        assert "RayCon 503" in result["error"]

    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.raycon_client.post_raycon_folder_ping")
    def test_ping_skipped_when_site_summary_incomplete(self, mock_ping, mock_resolve_m1):
        """Missing site_id / address / drive_folder_url → graceful skip,
        not an error. Inbox classifier already gated on these earlier."""
        gc = MagicMock()
        result = _run_doc_arrival_folder_ping(
            gc,
            site_summary={
                "id": "",  # incomplete
                "title": "Alpha Keller",
                "address": "123 Main",
                "drive_folder_url": "https://drive.google.com/drive/folders/site_abc",
            },
            doc_type="sir",
            drive_file=self._DRIVE_FILE,
        )
        assert result["status"] == "skipped"
        assert "missing" in result["reason"]
        mock_resolve_m1.assert_not_called()
        mock_ping.assert_not_called()

    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.raycon_client.post_raycon_folder_ping")
    def test_ping_skipped_when_m1_folder_unresolvable(self, mock_ping, mock_resolve_m1):
        mock_resolve_m1.return_value = (None, None)
        gc = MagicMock()
        result = _run_doc_arrival_folder_ping(
            gc,
            site_summary=self._SITE,
            doc_type="sir",
            drive_file=self._DRIVE_FILE,
        )
        assert result["status"] == "skipped"
        assert "M1" in result["reason"]
        mock_ping.assert_not_called()

    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.raycon_client.post_raycon_folder_ping")
    def test_ping_returns_error_when_m1_resolution_raises(self, mock_ping, mock_resolve_m1):
        mock_resolve_m1.side_effect = RuntimeError("Drive 401")
        gc = MagicMock()
        result = _run_doc_arrival_folder_ping(
            gc,
            site_summary=self._SITE,
            doc_type="isp",
            drive_file=self._DRIVE_FILE,
        )
        assert result["status"] == "error"
        assert "resolve M1" in result["error"]
        mock_ping.assert_not_called()


# ---------------------------------------------------------------------------
# Rec. 3 — vendor SIR / Building Inspection arrival fires DD republish
# ---------------------------------------------------------------------------


class TestDDRepublishCallbackWiring:
    """When a vendor SIR or Building Inspection lands, the inbox scanner
    must invoke the supplied ``dd_republish_callback`` with the right
    fingerprint so the shared helper can decide whether to regenerate
    the DD Report.

    These tests assert the wiring (callback invoked at all, with the
    right ``reason`` and ``fingerprint``); the helper's republish-vs-skip
    decision is exercised end-to-end in ``tests/test_dd_republish.py``.
    """

    def _common_mocks(self, doc_type: str, filename: str):
        """Set up the standard process_email mocks for a vendor doc upload."""
        extract = MagicMock(
            message_id=f"msg_{doc_type}",
            subject=filename,
            sender="vendor@external.com",
            effective_sender="vendor@external.com",
            body_snippet="",
            attachments=[
                {
                    "filename": filename,
                    "attachment_id": f"att_{doc_type}",
                    "mime_type": "application/pdf",
                }
            ],
        )
        return extract

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner._run_doc_arrival_folder_ping")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_sir_arrival_fires_callback_with_vendor_sir_reason(
        self,
        mock_extract,
        mock_classify,
        mock_ping,
        mock_resolve_m1,
        mock_build_summary,
    ):
        mock_extract.return_value = self._common_mocks("sir", "Alpha Keller SIR.pdf")
        mock_classify.return_value = ("sir", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "M1")
        mock_ping.return_value = {"status": "accepted"}
        mock_build_summary.return_value = {
            "id": "site-1",
            "title": "Alpha Keller",
            "address": "123 Main St",
            "drive_folder_url": "https://drive.google.com/drive/folders/site_abc",
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.return_value = {
            "id": "sir_drive_id",
            "webViewLink": "https://drive.google.com/file/d/sir_drive_id",
            "modifiedTime": "2026-05-05T10:00:00Z",
        }

        site_records = [{"id": "site-1", "title": "Alpha Keller", "customFields": []}]
        callback = MagicMock(return_value={"dd_report_republish": "republish"})

        result = process_email(
            gc,
            "msg_sir",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=site_records,
            dd_republish_callback=callback,
        )

        assert len(result["uploaded"]) == 1
        callback.assert_called_once()
        kwargs = callback.call_args.kwargs
        assert kwargs["reason"] == "vendor_sir"
        # Fingerprint is "{drive_file_id}:{modifiedTime}".
        assert kwargs["fingerprint"] == "sir_drive_id:2026-05-05T10:00:00Z"
        assert kwargs["site_summary"]["title"] == "Alpha Keller"

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner._run_doc_arrival_folder_ping")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_building_inspection_arrival_fires_callback_with_bi_reason(
        self,
        mock_extract,
        mock_classify,
        mock_ping,
        mock_resolve_m1,
        mock_build_summary,
    ):
        mock_extract.return_value = self._common_mocks(
            "building_inspection",
            "Alpha Keller Building Inspection Report.pdf",
        )
        mock_classify.return_value = ("building_inspection", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "M1")
        mock_ping.return_value = {"status": "accepted"}
        mock_build_summary.return_value = {
            "id": "site-1",
            "title": "Alpha Keller",
            "address": "123 Main St",
            "drive_folder_url": "https://drive.google.com/drive/folders/site_abc",
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.return_value = {
            "id": "bi_drive_id",
            "webViewLink": "https://drive.google.com/file/d/bi_drive_id",
            "modifiedTime": "2026-05-06T14:00:00Z",
        }

        site_records = [{"id": "site-1", "title": "Alpha Keller", "customFields": []}]
        callback = MagicMock(return_value={"dd_report_republish": "republish"})

        result = process_email(
            gc,
            "msg_bi",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=site_records,
            dd_republish_callback=callback,
        )

        assert len(result["uploaded"]) == 1
        callback.assert_called_once()
        kwargs = callback.call_args.kwargs
        assert kwargs["reason"] == "building_inspection"
        assert kwargs["fingerprint"] == "bi_drive_id:2026-05-06T14:00:00Z"

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner._run_doc_arrival_folder_ping")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_isp_arrival_does_not_fire_callback(
        self,
        mock_extract,
        mock_classify,
        mock_ping,
        mock_resolve_m1,
        mock_build_summary,
    ):
        """ISP is not an authoritative DD input — no republish on arrival."""
        mock_extract.return_value = self._common_mocks("isp", "Alpha Keller ISP.pdf")
        mock_classify.return_value = ("isp", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "M1")
        mock_ping.return_value = {"status": "accepted"}
        mock_build_summary.return_value = {
            "id": "site-1",
            "title": "Alpha Keller",
            "address": "123 Main St",
            "drive_folder_url": "https://drive.google.com/drive/folders/site_abc",
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.return_value = {
            "id": "isp_drive_id",
            "webViewLink": "https://drive.google.com/file/d/isp_drive_id",
            "modifiedTime": "2026-05-05T10:00:00Z",
        }
        site_records = [{"id": "site-1", "title": "Alpha Keller", "customFields": []}]
        callback = MagicMock()
        process_email(
            gc,
            "msg_isp",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=site_records,
            dd_republish_callback=callback,
        )
        callback.assert_not_called()

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner._run_doc_arrival_folder_ping")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_sir_arrival_with_no_callback_is_noop(
        self,
        mock_extract,
        mock_classify,
        mock_ping,
        mock_resolve_m1,
        mock_build_summary,
    ):
        """No callback supplied → upload still succeeds, no republish field."""
        mock_extract.return_value = self._common_mocks("sir", "Alpha Keller SIR.pdf")
        mock_classify.return_value = ("sir", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "M1")
        mock_ping.return_value = {"status": "accepted"}
        mock_build_summary.return_value = {
            "id": "site-1",
            "title": "Alpha Keller",
            "address": "123 Main St",
            "drive_folder_url": "https://drive.google.com/drive/folders/site_abc",
        }
        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.return_value = {
            "id": "sir_drive_id",
            "modifiedTime": "2026-05-05T10:00:00Z",
        }

        result = process_email(
            gc,
            "msg_sir",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=[{"id": "site-1", "title": "Alpha Keller", "customFields": []}],
            dd_republish_callback=None,
        )
        assert len(result["uploaded"]) == 1
        assert "dd_report_republish" not in result["uploaded"][0]

    @patch("due_diligence_reporter.inbox_scanner._build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner._run_doc_arrival_folder_ping")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_callback_exception_does_not_break_upload(
        self,
        mock_extract,
        mock_classify,
        mock_ping,
        mock_resolve_m1,
        mock_build_summary,
    ):
        """Callback raising → upload still recorded, failure surfaced."""
        mock_extract.return_value = self._common_mocks("sir", "Alpha Keller SIR.pdf")
        mock_classify.return_value = ("sir", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "M1")
        mock_ping.return_value = {"status": "accepted"}
        mock_build_summary.return_value = {
            "id": "site-1",
            "title": "Alpha Keller",
            "address": "123 Main St",
            "drive_folder_url": "https://drive.google.com/drive/folders/site_abc",
        }

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.return_value = {
            "id": "sir_drive_id",
            "modifiedTime": "2026-05-05T10:00:00Z",
        }
        callback = MagicMock(side_effect=RuntimeError("pipeline blew up"))

        result = process_email(
            gc,
            "msg_sir",
            MagicMock(),
            "label_123",
            "review_123",
            site_records=[{"id": "site-1", "title": "Alpha Keller", "customFields": []}],
            dd_republish_callback=callback,
        )
        assert len(result["uploaded"]) == 1
        republish = result["uploaded"][0].get("dd_report_republish") or {}
        assert republish.get("status") == "failed"
        # `reason` is the normalized envelope key (Fix 2).
        assert "pipeline blew up" in republish.get("reason", "")


# ---------------------------------------------------------------------------
# _maybe_fire_dd_republish envelope shape (Fix 2)
# ---------------------------------------------------------------------------


class TestMaybeFireDDRepublishEnvelopeShapes:
    """All four return paths share the normalized DDRepublishResult shape."""

    REQUIRED_KEYS = {"status", "reason"}
    VALID_STATUSES = {"skipped", "fired", "failed"}

    def _drive_file(self, **overrides):
        base = {"id": "fid", "modifiedTime": "2026-05-08T10:00:00Z"}
        base.update(overrides)
        return base

    def test_skipped_unrecognized_doc_type(self):
        from due_diligence_reporter.inbox_scanner import _maybe_fire_dd_republish

        result = _maybe_fire_dd_republish(
            callback=MagicMock(),
            gc=MagicMock(),
            site_summary={"title": "S"},
            doc_type="unknown_artifact",
            drive_file=self._drive_file(),
            dry_run=False,
        )
        assert self.REQUIRED_KEYS <= set(result.keys())
        assert result["status"] == "skipped"
        assert result["status"] in self.VALID_STATUSES

    def test_skipped_missing_drive_file_id(self):
        from due_diligence_reporter.inbox_scanner import _maybe_fire_dd_republish

        result = _maybe_fire_dd_republish(
            callback=MagicMock(),
            gc=MagicMock(),
            site_summary={"title": "S"},
            doc_type="sir",
            drive_file={"id": "", "modifiedTime": "t"},
            dry_run=False,
        )
        assert self.REQUIRED_KEYS <= set(result.keys())
        assert result["status"] == "skipped"

    @patch("due_diligence_reporter.provenance.is_vendor_sourced", return_value=True)
    def test_fired_callback_keys_preserved(self, _vendor):
        from due_diligence_reporter.inbox_scanner import _maybe_fire_dd_republish

        callback = MagicMock(
            return_value={
                "dd_report_republish": "republish",
                "republish_reason": "vendor_sir",
                "doc_url": "https://docs.google.com/x",
            }
        )
        result = _maybe_fire_dd_republish(
            callback=callback,
            gc=MagicMock(),
            site_summary={"title": "S"},
            doc_type="sir",
            drive_file=self._drive_file(),
            dry_run=False,
        )
        assert self.REQUIRED_KEYS <= set(result.keys())
        assert result["status"] == "fired"
        # Callback fields must survive the wrap.
        assert result.get("dd_report_republish") == "republish"
        assert result.get("republish_reason") == "vendor_sir"
        assert result.get("doc_url") == "https://docs.google.com/x"

    @patch("due_diligence_reporter.provenance.is_vendor_sourced", return_value=True)
    def test_failed_callback_normalized(self, _vendor):
        from due_diligence_reporter.inbox_scanner import _maybe_fire_dd_republish

        callback = MagicMock(side_effect=RuntimeError("kaboom"))
        result = _maybe_fire_dd_republish(
            callback=callback,
            gc=MagicMock(),
            site_summary={"title": "S"},
            doc_type="sir",
            drive_file=self._drive_file(),
            dry_run=False,
        )
        assert self.REQUIRED_KEYS <= set(result.keys())
        assert result["status"] == "failed"
        assert "kaboom" in result["reason"]


# ---------------------------------------------------------------------------
# Provenance gate inside _maybe_fire_dd_republish (Fix 5)
# ---------------------------------------------------------------------------


class TestMaybeFireDDRepublishProvenanceGate:
    """AI-named files must be skipped before the callback fires."""

    @patch("due_diligence_reporter.provenance.is_vendor_sourced", return_value=False)
    def test_skips_ai_named(self, mock_is_vendor):
        from due_diligence_reporter.inbox_scanner import _maybe_fire_dd_republish

        callback = MagicMock()
        result = _maybe_fire_dd_republish(
            callback=callback,
            gc=MagicMock(),
            site_summary={"title": "S"},
            doc_type="sir",
            drive_file={"id": "fid", "modifiedTime": "t"},
            dry_run=False,
            m1_folder_id="m1_folder",
        )
        assert result == {"status": "skipped", "reason": "ai_named_skipped"}
        callback.assert_not_called()
        mock_is_vendor.assert_called_once()
