"""Tests for the inbox scanner module."""

from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from due_diligence_reporter.inbox_scanner import (
    AUTO_FILE_CONFIDENCE,
    DOC_TYPE_FILENAME_TEMPLATES,
    SUPPORTED_DOC_TYPES,
    _generate_drive_filename,
    _run_block_plan_downstream,
    _walk_parts,
    has_site_identity,
    process_email,
    scan_inbox,
)

# ---------------------------------------------------------------------------
# Filename generation
# ---------------------------------------------------------------------------


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
            body_snippet="Some text",
            attachments=[{"filename": "notes.pdf", "attachment_id": "a1", "mime_type": "application/pdf"}],
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
            body_snippet="",
            attachments=[{"filename": "report.pdf", "attachment_id": "a2", "mime_type": "application/pdf"}],
        )
        mock_classify.return_value = ("sir", 0.5)  # below AUTO_FILE_CONFIDENCE

        gc = MagicMock()
        result = process_email(gc, "msg_2", MagicMock(), "label_123", "review_123")

        assert len(result["low_confidence"]) == 1
        assert result["low_confidence"][0]["doc_type"] == "sir"
        assert result["marked"] is True
        gc.gmail_modify_labels.assert_called_once_with(
            "msg_2",
            add_labels=["label_123", "review_123"],
            remove_labels=[],
        )

    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_upload_failure_is_returned_as_error(self, mock_extract, mock_classify):
        mock_extract.return_value = MagicMock(
            message_id="msg_3",
            subject="SIR attached",
            sender="test@example.com",
            body_snippet="",
            attachments=[{"filename": "sir.pdf", "attachment_id": "a3", "mime_type": "application/pdf"}],
        )
        mock_classify.return_value = ("sir", 0.95)

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.side_effect = RuntimeError("upload boom")

        result = process_email(
            gc,
            "msg_3",
            MagicMock(sir_folder_id="folder123"),
            "label_123",
            "review_123",
        )

        assert result["marked"] is True
        assert len(result["uploaded"]) == 0
        assert len(result["errors"]) == 1
        assert result["errors"][0]["filename"] == "sir.pdf"
        assert result["errors"][0]["error"] == "upload boom"

    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_unmatched_block_plan_goes_to_manual_review(self, mock_extract, mock_classify):
        mock_extract.return_value = MagicMock(
            message_id="msg_block_1",
            subject="Block Plan attached",
            sender="test@example.com",
            body_snippet="",
            attachments=[{"filename": "Alpha Keller Block Plan.pdf", "attachment_id": "bp1", "mime_type": "application/pdf"}],
        )
        mock_classify.return_value = ("block_plan", 0.95)

        gc = MagicMock()
        result = process_email(gc, "msg_block_1", MagicMock(), "label_123", "review_123")

        assert result["uploaded"] == []
        assert result["skipped"] == 1
        assert result["marked"] is True
        assert result["low_confidence"][0]["doc_type"] == "block_plan"

    @patch("due_diligence_reporter.inbox_scanner.build_site_summary")
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
            body_snippet="",
            attachments=[{"filename": "Alpha Keller Block Plan.pdf", "attachment_id": "bp2", "mime_type": "application/pdf"}],
        )
        mock_classify.return_value = ("block_plan", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive.google.com/drive/folders/m1")
        mock_extract_pdf.return_value = "block plan text"
        mock_downstream.return_value = [
            {"doc_type": "capacity_brainlift_report", "doc_url": "https://docs.google.com/document/d/cap"},
            {"doc_type": "raycon_scenario_report", "doc_url": "https://docs.google.com/document/d/ray"},
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
        gc.upload_file_to_folder.return_value = {"id": "block123", "webViewLink": "https://drive.google.com/file/d/block123"}

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
        mock_downstream.assert_called_once()

    @patch("due_diligence_reporter.inbox_scanner.build_site_summary")
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
            body_snippet="",
            label_ids=[],
            attachments=[{"filename": "Alpha Keller Block Plan.pdf", "attachment_id": "bp3", "mime_type": "application/pdf"}],
        )
        mock_classify.return_value = ("block_plan", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive.google.com/drive/folders/m1")
        mock_list_docs.return_value = {
            "block_plan": {"id": "block123", "name": "Apr 22 2026 - Alpha Keller Block Plan.pdf"},
            "capacity_brainlift_report": {"id": "cap123", "name": "Capacity Brainlift - Alpha Keller"},
            "raycon_scenario_report": {"id": "ray123", "name": "RayCon Scenario - Alpha Keller"},
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

    @patch("due_diligence_reporter.inbox_scanner.build_site_summary")
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
            body_snippet="",
            label_ids=[],
            attachments=[{"filename": "Alpha Keller Block Plan.pdf", "attachment_id": "bp4", "mime_type": "application/pdf"}],
        )
        mock_classify.return_value = ("block_plan", 0.95)
        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive.google.com/drive/folders/m1")
        mock_extract_pdf.return_value = "block plan text"
        mock_list_docs.return_value = {
            "block_plan": {"id": "block123", "name": "Apr 22 2026 - Alpha Keller Block Plan.pdf", "webViewLink": "https://drive.google.com/file/d/block123"},
        }
        mock_downstream.return_value = [
            {"doc_type": "capacity_brainlift_report", "doc_url": "https://docs.google.com/document/d/cap"},
            {"doc_type": "raycon_scenario_report", "doc_url": "https://docs.google.com/document/d/ray"},
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

    @patch("due_diligence_reporter.inbox_scanner.build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._send_block_plan_failure_notification")
    @patch("due_diligence_reporter.inbox_scanner._run_block_plan_downstream")
    @patch("due_diligence_reporter.inbox_scanner.extract_text_from_pdf_bytes")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_block_plan_downstream_failure_stays_unprocessed_and_notifies_owner(
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
            body_snippet="",
            label_ids=[],
            attachments=[{"filename": "Alpha Keller Block Plan.pdf", "attachment_id": "bp5", "mime_type": "application/pdf"}],
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
        gc.upload_file_to_folder.return_value = {"id": "block123", "webViewLink": "https://drive.google.com/file/d/block123"}

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
        assert len(result["errors"]) == 1
        gc.gmail_modify_labels.assert_called_once_with(
            "msg_block_fail",
            add_labels=["review_123"],
            remove_labels=[],
        )
        mock_notify.assert_called_once()


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
    """Emails with the DD-Processed label should not be re-processed."""

    def test_gmail_search_query_excludes_processed_label(self):
        """The scan query must exclude already-labeled messages."""
        from due_diligence_reporter.config import Settings

        settings = Settings()
        query = f"{settings.inbox_scan_query} -label:{settings.inbox_processed_label}"
        assert "-label:DD-Processed" in query

    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_unknown_doc_type_email_marked_processed(self, mock_extract, mock_classify):
        """Emails with only unknown-type attachments are marked processed (no re-scan needed)."""
        mock_extract.return_value = MagicMock(
            message_id="msg_1",
            subject="Hello",
            sender="test@example.com",
            body_snippet="Some text",
            attachments=[{"filename": "notes.pdf", "attachment_id": "a1", "mime_type": "application/pdf"}],
        )
        mock_classify.return_value = ("unknown", 0.0)

        gc = MagicMock()
        result = process_email(gc, "msg_1", MagicMock(), "label_123", "review_123")

        assert result["skipped"] == 1
        assert len(result["uploaded"]) == 0
        assert result["marked"] is True  # mark so we don't re-scan it forever

    @patch("due_diligence_reporter.inbox_scanner.classify_document")
    @patch("due_diligence_reporter.inbox_scanner._extract_email_metadata")
    def test_site_identity_is_attached_to_uploads(self, mock_extract, mock_classify):
        mock_extract.return_value = MagicMock(
            message_id="msg_4",
            subject="Alpha Keller SIR",
            sender="test@example.com",
            body_snippet="",
            attachments=[{"filename": "Alpha Keller SIR.pdf", "attachment_id": "a4", "mime_type": "application/pdf"}],
        )
        mock_classify.return_value = ("sir", 0.95)

        gc = MagicMock()
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"pdf"
        gc.upload_file_to_folder.return_value = {"id": "file123", "webViewLink": "https://drive/file123"}

        settings = MagicMock(sir_folder_id="folder123")
        site_records = [{"id": "IEABCD123", "title": "Alpha Keller", "customFields": []}]

        result = process_email(
            gc,
            "msg_4",
            settings,
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
    @patch("due_diligence_reporter.server._register_file_read_context")
    @patch("due_diligence_reporter.server.save_skill_report", new_callable=AsyncMock)
    @patch("due_diligence_reporter.server.get_cost_estimate", new_callable=AsyncMock)
    @patch("due_diligence_reporter.server.apply_capacity_brainlift_skill", new_callable=AsyncMock)
    @patch("due_diligence_reporter.server.read_drive_document", new_callable=AsyncMock)
    @patch("due_diligence_reporter.server._find_site_docs_in_shared_folders")
    def test_passes_sir_and_inspection_content_to_raycon(
        self,
        mock_find_shared,
        mock_read_doc,
        mock_capacity,
        mock_raycon,
        mock_save,
        mock_register_context,
    ):
        mock_find_shared.return_value = {
            "sir": {"id": "sir123", "name": "Alpha Keller SIR.pdf"},
            "building_inspection": {"id": "bi123", "name": "Alpha Keller Building Inspection Report.pdf"},
            "isp": None,
        }
        mock_read_doc.side_effect = [
            {"status": "success", "content": "SIR FULL TEXT"},
            {"status": "success", "content": "BUILDING INSPECTION FULL TEXT"},
        ]
        mock_capacity.return_value = {
            "status": "success",
            "block_plan_summary": "Plan summary",
            "raycon_rooms": [{"type": "learningroom", "sqft": 650, "name": "Classroom 1"}],
            "furniture_only": {"classroom_count": 4},
            "max_capacity": {"classroom_count": 6},
            "report_data_fields": {
                "exec.furniture_only_capacity": "36",
                "exec.max_capacity_capacity": "54",
            },
            "doc_url": "https://docs.google.com/document/d/cap",
        }
        mock_raycon.return_value = {
            "status": "success",
            "report_data_fields": {
                "exec.furniture_only_capex": "$100,000",
                "exec.max_capacity_capex": "$200,000",
            },
        }
        mock_save.return_value = {
            "status": "success",
            "doc_url": "https://docs.google.com/document/d/ray",
            "doc_id": "ray123",
        }

        gc = MagicMock()
        result = _run_block_plan_downstream(
            gc,
            site_summary={
                "title": "Alpha Keller",
                "address": "123 Main St, Keller, TX 76248",
                "drive_folder_url": "https://drive.google.com/drive/folders/site123",
                "total_building_sf": 12000,
            },
            block_plan_content="BLOCK PLAN FULL TEXT",
            block_plan_url="https://drive.google.com/file/d/block123",
        )

        assert len(result) == 2
        raycon_kwargs = mock_raycon.call_args.kwargs
        assert raycon_kwargs["sir_content"] == "SIR FULL TEXT"
        assert raycon_kwargs["inspection_content"] == "BUILDING INSPECTION FULL TEXT"
        assert raycon_kwargs["block_plan_content"] == "BLOCK PLAN FULL TEXT"
        assert raycon_kwargs["region"] == "TX"
        mock_register_context.assert_called_once()

