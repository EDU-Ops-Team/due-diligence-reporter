"""End-to-end integration tests for scripts/scan_inbox.py main() flow.

These test the full chain from main() through classification, upload,
SIR notification, and CDS overlay dispatch. External services are mocked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch, call

import pytest

# scan_inbox.py manipulates sys.path; import main directly
from scripts.scan_inbox import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Fake SIR text that contains B/C confidence tags for CDS overlay testing
_FAKE_SIR_TEXT_WITH_BC = """\
# Site Investigation Report — Alpha Tampa

## Zoning

| Item | Finding | Confidence |
|------|---------|------------|
| Zoning classification | C-2 Commercial — [A] | High |
| Conditional use permit | Required — [B] | Medium |

## AHJ / Building Code

| Item | Finding | Confidence |
|------|---------|------------|
| Fire alarm | Existing system adequate — [A] | High |
| ADA compliance | Needs assessment — [C] | Low |
"""

# Fake SIR text with only [A] tags (no B/C items)
_FAKE_SIR_TEXT_ALL_A = """\
# Site Investigation Report — Alpha Tampa

## Zoning

| Item | Finding | Confidence |
|------|---------|------------|
| Zoning classification | C-2 Commercial — [A] | High |
| Conditional use permit | Not required — [A] | High |
"""


@dataclass
class _FakeVerificationReport:
    """Stand-in for cds_verification.VerificationReport."""

    markdown: str = "# CDS Overlay"
    bc_item_count: int = 2
    sections_with_items: list[str] = field(
        default_factory=lambda: ["Zoning", "AHJ / Building Code"]
    )


def _make_scan_result(
    uploads: list[dict] | None = None,
    low_confidence: list[dict] | None = None,
    errors: list[dict] | None = None,
) -> dict:
    """Build a scan_inbox()-shaped result dict."""
    uploads = uploads or []
    return {
        "emails_found": max(len(uploads), 1),
        "attachments_uploaded": len(uploads),
        "attachments_skipped": 0,
        "emails_processed": max(len(uploads), 1),
        "uploads": uploads,
        "low_confidence": low_confidence or [],
        "errors": errors or [],
    }


def _sir_upload(
    site: str = "Alpha Tampa",
    drive_file_id: str = "drive_id_1",
) -> dict:
    return {
        "original_filename": f"{site} SIR.pdf",
        "drive_filename": f"Apr 16 2026 - {site} SIR.pdf",
        "doc_type": "sir",
        "site_title": site,
        "matched_site_id": "IEABC123",
        "drive_file_id": drive_file_id,
        "drive_link": "https://drive.google.com/file/d/drive_id_1/view",
    }


def _bi_upload(site: str = "Alpha Tampa") -> dict:
    return {
        "original_filename": f"Building Inspection {site}.pdf",
        "drive_filename": f"Apr 16 2026 - {site} Building Inspection Report.pdf",
        "doc_type": "building_inspection",
        "site_title": site,
        "matched_site_id": None,
        "drive_file_id": "drive_id_bi",
        "drive_link": "https://drive.google.com/file/d/drive_id_bi/view",
    }


def _isp_upload(site: str = "Alpha Tampa") -> dict:
    return {
        "original_filename": f"{site} ISP.pdf",
        "drive_filename": f"Apr 16 2026 - {site} ISP.pdf",
        "doc_type": "isp",
        "site_title": site,
        "matched_site_id": None,
        "drive_file_id": "drive_id_isp",
        "drive_link": "https://drive.google.com/file/d/drive_id_isp/view",
    }


def _unknown_upload() -> dict:
    return {
        "original_filename": "random_attachment.pdf",
        "drive_filename": "Apr 16 2026 - random_attachment.pdf",
        "doc_type": "unknown",
        "site_title": None,
        "matched_site_id": None,
        "drive_file_id": None,
        "drive_link": None,
    }


# Base set of patches applied to every test via the autouse fixture
_MODULE = "scripts.scan_inbox"


@pytest.fixture(autouse=True)
def _patch_externals(monkeypatch):
    """Provide default settings and suppress real OAuth / Wrike calls."""
    # Settings
    monkeypatch.setenv("GOOGLE_CLIENT_CONFIG", "/fake/client.json")
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", "/fake/token.json")
    monkeypatch.setenv("SIR_FOLDER_ID", "sir_folder")
    monkeypatch.setenv("ISP_FOLDER_ID", "isp_folder")
    monkeypatch.setenv("BUILDING_INSPECTION_FOLDER_ID", "bi_folder")
    monkeypatch.setenv("EMAIL_SENDER", "bot@example.com")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "apppass")
    monkeypatch.setenv("SIR_NOTIFICATION_RECIPIENTS", "team@example.com")
    monkeypatch.setenv("CDS_NOTIFICATION_RECIPIENTS", "cds@example.com")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHappyPathSIRWithCDS:
    """SIR arrives -> classified -> uploaded -> notified -> CDS overlay sent."""

    @patch(f"{_MODULE}.send_email")
    @patch(f"{_MODULE}.generate_cds_verification_report")
    @patch(f"{_MODULE}.extract_text_from_pdf_bytes")
    @patch(f"{_MODULE}.post_google_chat_message")
    @patch(f"{_MODULE}.scan_inbox")
    @patch(f"{_MODULE}.load_wrike_config")
    @patch(f"{_MODULE}.GoogleClient")
    @patch(f"{_MODULE}.get_settings")
    def test_sir_full_cds_flow(
        self,
        mock_settings_fn,
        mock_gc_cls,
        mock_wrike_cfg,
        mock_scan,
        mock_chat,
        mock_extract,
        mock_cds_gen,
        mock_send,
    ):
        settings = MagicMock()
        settings.sir_notification_recipients = "team@example.com"
        settings.cds_notification_recipients = "cds@example.com"
        settings.email_sender = "bot@example.com"
        settings.email_app_password = "apppass"
        settings.sir_folder_id = "sir_folder"
        settings.google_chat_webhook_url = ""
        mock_settings_fn.return_value = settings

        gc = MagicMock()
        mock_gc_cls.from_oauth_config.return_value = gc

        # Wrike fails gracefully
        mock_wrike_cfg.side_effect = RuntimeError("wrike down")

        sir = _sir_upload()
        mock_scan.return_value = _make_scan_result(uploads=[sir])

        # CDS overlay chain
        gc.download_file_bytes.return_value = b"%PDF-sir-bytes"
        mock_extract.return_value = _FAKE_SIR_TEXT_WITH_BC
        mock_cds_gen.return_value = _FakeVerificationReport()
        gc.upload_file_to_folder.return_value = {
            "id": "overlay_id",
            "webViewLink": "https://drive.google.com/overlay",
        }

        main(scan_only=True)

        # SIR notification email sent
        sir_email_calls = [
            c for c in mock_send.call_args_list if "SIR Received" in str(c)
        ]
        assert len(sir_email_calls) == 1

        # CDS email sent
        cds_email_calls = [
            c for c in mock_send.call_args_list if "CDS Verification" in str(c)
        ]
        assert len(cds_email_calls) == 1

        # CDS overlay uploaded to Drive
        gc.upload_file_to_folder.assert_called_once()
        upload_call = gc.upload_file_to_folder.call_args
        assert upload_call.kwargs["folder_id"] == "sir_folder"
        assert "CDS Verification" in upload_call.kwargs["file_name"]

        # download_file_bytes called for SIR PDF
        gc.download_file_bytes.assert_called_once_with("drive_id_1")


class TestBuildingInspectionNoCDS:
    """Building inspection upload should NOT trigger CDS overlay."""

    @patch(f"{_MODULE}.send_email")
    @patch(f"{_MODULE}.extract_text_from_pdf_bytes")
    @patch(f"{_MODULE}.scan_inbox")
    @patch(f"{_MODULE}.load_wrike_config")
    @patch(f"{_MODULE}.GoogleClient")
    @patch(f"{_MODULE}.get_settings")
    def test_no_cds_for_building_inspection(
        self,
        mock_settings_fn,
        mock_gc_cls,
        mock_wrike_cfg,
        mock_scan,
        mock_extract,
        mock_send,
    ):
        settings = MagicMock()
        settings.sir_notification_recipients = ""
        settings.cds_notification_recipients = "cds@example.com"
        settings.email_sender = "bot@example.com"
        settings.email_app_password = "apppass"
        settings.google_chat_webhook_url = ""
        mock_settings_fn.return_value = settings

        gc = MagicMock()
        mock_gc_cls.from_oauth_config.return_value = gc
        mock_wrike_cfg.side_effect = RuntimeError("skip wrike")

        mock_scan.return_value = _make_scan_result(uploads=[_bi_upload()])

        main(scan_only=True)

        # No SIR notification or CDS emails
        mock_send.assert_not_called()
        gc.download_file_bytes.assert_not_called()


class TestISPNoCDS:
    """ISP upload should NOT trigger CDS overlay."""

    @patch(f"{_MODULE}.send_email")
    @patch(f"{_MODULE}.scan_inbox")
    @patch(f"{_MODULE}.load_wrike_config")
    @patch(f"{_MODULE}.GoogleClient")
    @patch(f"{_MODULE}.get_settings")
    def test_no_cds_for_isp(
        self, mock_settings_fn, mock_gc_cls, mock_wrike_cfg, mock_scan, mock_send
    ):
        settings = MagicMock()
        settings.sir_notification_recipients = ""
        settings.cds_notification_recipients = "cds@example.com"
        settings.email_sender = "bot@example.com"
        settings.email_app_password = "apppass"
        settings.google_chat_webhook_url = ""
        mock_settings_fn.return_value = settings

        gc = MagicMock()
        mock_gc_cls.from_oauth_config.return_value = gc
        mock_wrike_cfg.side_effect = RuntimeError("skip wrike")

        mock_scan.return_value = _make_scan_result(uploads=[_isp_upload()])

        main(scan_only=True)

        mock_send.assert_not_called()


class TestUnknownDocTypeManualReview:
    """Unknown doc type produces low_confidence list; no CDS overlay."""

    @patch(f"{_MODULE}.send_email")
    @patch(f"{_MODULE}.scan_inbox")
    @patch(f"{_MODULE}.load_wrike_config")
    @patch(f"{_MODULE}.GoogleClient")
    @patch(f"{_MODULE}.get_settings")
    def test_unknown_doc_type(
        self, mock_settings_fn, mock_gc_cls, mock_wrike_cfg, mock_scan, mock_send
    ):
        settings = MagicMock()
        settings.sir_notification_recipients = ""
        settings.cds_notification_recipients = ""
        settings.email_sender = "bot@example.com"
        settings.email_app_password = "apppass"
        settings.google_chat_webhook_url = ""
        mock_settings_fn.return_value = settings

        gc = MagicMock()
        mock_gc_cls.from_oauth_config.return_value = gc
        mock_wrike_cfg.side_effect = RuntimeError("skip wrike")

        # scan returns no uploads — unknown docs are skipped / flagged
        mock_scan.return_value = _make_scan_result(
            uploads=[],
            low_confidence=[{
                "filename": "random_attachment.pdf",
                "doc_type": "unknown",
                "confidence": 0.3,
                "email_subject": "Fwd: docs",
                "site_title": None,
            }],
        )

        main(scan_only=True)

        # No CDS or SIR emails
        mock_send.assert_not_called()


class TestMultipleAttachments:
    """Multiple attachments (SIR + ISP) in one email are both uploaded."""

    @patch(f"{_MODULE}.send_email")
    @patch(f"{_MODULE}.generate_cds_verification_report")
    @patch(f"{_MODULE}.extract_text_from_pdf_bytes")
    @patch(f"{_MODULE}.scan_inbox")
    @patch(f"{_MODULE}.load_wrike_config")
    @patch(f"{_MODULE}.GoogleClient")
    @patch(f"{_MODULE}.get_settings")
    def test_multiple_attachments_uploaded(
        self,
        mock_settings_fn,
        mock_gc_cls,
        mock_wrike_cfg,
        mock_scan,
        mock_extract,
        mock_cds_gen,
        mock_send,
    ):
        settings = MagicMock()
        settings.sir_notification_recipients = "team@example.com"
        settings.cds_notification_recipients = "cds@example.com"
        settings.email_sender = "bot@example.com"
        settings.email_app_password = "apppass"
        settings.sir_folder_id = "sir_folder"
        settings.google_chat_webhook_url = ""
        mock_settings_fn.return_value = settings

        gc = MagicMock()
        mock_gc_cls.from_oauth_config.return_value = gc
        mock_wrike_cfg.side_effect = RuntimeError("skip wrike")

        sir = _sir_upload()
        isp = _isp_upload()
        mock_scan.return_value = _make_scan_result(uploads=[sir, isp])

        gc.download_file_bytes.return_value = b"%PDF-sir"
        mock_extract.return_value = _FAKE_SIR_TEXT_WITH_BC
        mock_cds_gen.return_value = _FakeVerificationReport()
        gc.upload_file_to_folder.return_value = {
            "id": "overlay_id",
            "webViewLink": "https://drive.google.com/overlay",
        }

        main(scan_only=True)

        # Both uploads counted
        assert mock_scan.return_value["attachments_uploaded"] == 2

        # SIR notification sent (only for the SIR, not the ISP)
        sir_calls = [c for c in mock_send.call_args_list if "SIR Received" in str(c)]
        assert len(sir_calls) == 1

        # CDS overlay sent (only for the SIR)
        cds_calls = [c for c in mock_send.call_args_list if "CDS Verification" in str(c)]
        assert len(cds_calls) == 1


class TestCDSSkippedNoBCItems:
    """CDS overlay skipped when SIR text has only [A] confidence tags."""

    @patch(f"{_MODULE}.send_email")
    @patch(f"{_MODULE}.generate_cds_verification_report")
    @patch(f"{_MODULE}.extract_text_from_pdf_bytes")
    @patch(f"{_MODULE}.scan_inbox")
    @patch(f"{_MODULE}.load_wrike_config")
    @patch(f"{_MODULE}.GoogleClient")
    @patch(f"{_MODULE}.get_settings")
    def test_cds_skipped_zero_bc(
        self,
        mock_settings_fn,
        mock_gc_cls,
        mock_wrike_cfg,
        mock_scan,
        mock_extract,
        mock_cds_gen,
        mock_send,
    ):
        settings = MagicMock()
        settings.sir_notification_recipients = "team@example.com"
        settings.cds_notification_recipients = "cds@example.com"
        settings.email_sender = "bot@example.com"
        settings.email_app_password = "apppass"
        settings.sir_folder_id = "sir_folder"
        settings.google_chat_webhook_url = ""
        mock_settings_fn.return_value = settings

        gc = MagicMock()
        mock_gc_cls.from_oauth_config.return_value = gc
        mock_wrike_cfg.side_effect = RuntimeError("skip wrike")

        sir = _sir_upload()
        mock_scan.return_value = _make_scan_result(uploads=[sir])

        gc.download_file_bytes.return_value = b"%PDF-all-A"
        mock_extract.return_value = _FAKE_SIR_TEXT_ALL_A
        mock_cds_gen.return_value = _FakeVerificationReport(
            bc_item_count=0, sections_with_items=[]
        )

        main(scan_only=True)

        # SIR notification sent
        sir_calls = [c for c in mock_send.call_args_list if "SIR Received" in str(c)]
        assert len(sir_calls) == 1

        # CDS email NOT sent (bc_item_count == 0)
        cds_calls = [c for c in mock_send.call_args_list if "CDS Verification" in str(c)]
        assert len(cds_calls) == 0

        # Overlay NOT uploaded to Drive
        gc.upload_file_to_folder.assert_not_called()


class TestCDSSkippedNoRecipients:
    """CDS overlay not generated when cds_notification_recipients is empty."""

    @patch(f"{_MODULE}.send_email")
    @patch(f"{_MODULE}.generate_cds_verification_report")
    @patch(f"{_MODULE}.extract_text_from_pdf_bytes")
    @patch(f"{_MODULE}.scan_inbox")
    @patch(f"{_MODULE}.load_wrike_config")
    @patch(f"{_MODULE}.GoogleClient")
    @patch(f"{_MODULE}.get_settings")
    def test_cds_skipped_empty_recipients(
        self,
        mock_settings_fn,
        mock_gc_cls,
        mock_wrike_cfg,
        mock_scan,
        mock_extract,
        mock_cds_gen,
        mock_send,
    ):
        settings = MagicMock()
        settings.sir_notification_recipients = "team@example.com"
        settings.cds_notification_recipients = ""  # empty
        settings.email_sender = "bot@example.com"
        settings.email_app_password = "apppass"
        settings.google_chat_webhook_url = ""
        mock_settings_fn.return_value = settings

        gc = MagicMock()
        mock_gc_cls.from_oauth_config.return_value = gc
        mock_wrike_cfg.side_effect = RuntimeError("skip wrike")

        sir = _sir_upload()
        mock_scan.return_value = _make_scan_result(uploads=[sir])

        main(scan_only=True)

        # SIR notification sent
        sir_calls = [c for c in mock_send.call_args_list if "SIR Received" in str(c)]
        assert len(sir_calls) == 1

        # CDS generation never called
        mock_cds_gen.assert_not_called()
        mock_extract.assert_not_called()
        gc.download_file_bytes.assert_not_called()


class TestUploadFailureResilience:
    """Upload failure for CDS overlay shouldn't crash the scan."""

    @patch(f"{_MODULE}.send_email")
    @patch(f"{_MODULE}.generate_cds_verification_report")
    @patch(f"{_MODULE}.extract_text_from_pdf_bytes")
    @patch(f"{_MODULE}.scan_inbox")
    @patch(f"{_MODULE}.load_wrike_config")
    @patch(f"{_MODULE}.GoogleClient")
    @patch(f"{_MODULE}.get_settings")
    def test_upload_failure_continues(
        self,
        mock_settings_fn,
        mock_gc_cls,
        mock_wrike_cfg,
        mock_scan,
        mock_extract,
        mock_cds_gen,
        mock_send,
    ):
        settings = MagicMock()
        settings.sir_notification_recipients = "team@example.com"
        settings.cds_notification_recipients = "cds@example.com"
        settings.email_sender = "bot@example.com"
        settings.email_app_password = "apppass"
        settings.sir_folder_id = "sir_folder"
        settings.google_chat_webhook_url = ""
        mock_settings_fn.return_value = settings

        gc = MagicMock()
        mock_gc_cls.from_oauth_config.return_value = gc
        mock_wrike_cfg.side_effect = RuntimeError("skip wrike")

        sir = _sir_upload()
        mock_scan.return_value = _make_scan_result(uploads=[sir])

        gc.download_file_bytes.return_value = b"%PDF-sir"
        mock_extract.return_value = _FAKE_SIR_TEXT_WITH_BC
        mock_cds_gen.return_value = _FakeVerificationReport()
        # CDS overlay upload fails
        gc.upload_file_to_folder.side_effect = RuntimeError("upload boom")

        # main() should NOT raise
        main(scan_only=True)

        # SIR notification still sent
        sir_calls = [c for c in mock_send.call_args_list if "SIR Received" in str(c)]
        assert len(sir_calls) == 1


class TestGmailAPIFailure:
    """Gmail API failure in scan_inbox raises (expected behavior)."""

    @patch(f"{_MODULE}.scan_inbox")
    @patch(f"{_MODULE}.load_wrike_config")
    @patch(f"{_MODULE}.GoogleClient")
    @patch(f"{_MODULE}.get_settings")
    def test_gmail_failure_raises(
        self, mock_settings_fn, mock_gc_cls, mock_wrike_cfg, mock_scan
    ):
        settings = MagicMock()
        settings.sir_notification_recipients = ""
        settings.cds_notification_recipients = ""
        settings.google_chat_webhook_url = ""
        mock_settings_fn.return_value = settings

        gc = MagicMock()
        mock_gc_cls.from_oauth_config.return_value = gc
        mock_wrike_cfg.side_effect = RuntimeError("skip wrike")

        mock_scan.side_effect = RuntimeError("Gmail API failure")

        with pytest.raises(RuntimeError, match="Gmail API failure"):
            main(scan_only=True)
