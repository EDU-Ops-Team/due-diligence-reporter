"""Tests for inbox scanner sender filtering.

Verifies that internal senders (trilogy.com, service accounts) are blocked
from filing attachments into shared vendor folders, preventing false readiness.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from due_diligence_reporter.config import Settings
from due_diligence_reporter.inbox_scanner import (
    _is_internal_sender,
    _parse_sender_email,
    process_email,
)


# ── _parse_sender_email ─────────────────────────────────────────────────────


class TestParseSenderEmail:
    """Extract bare address from various From header formats."""

    def test_bare_address(self):
        assert _parse_sender_email("vendor@cds.com") == "vendor@cds.com"

    def test_display_name_angle_brackets(self):
        assert _parse_sender_email("John Doe <john@vendor.com>") == "john@vendor.com"

    def test_quoted_display_name(self):
        assert _parse_sender_email('"CDS Reports" <reports@cds.com>') == "reports@cds.com"

    def test_uppercase_normalized(self):
        assert _parse_sender_email("Greg.Foote@Trilogy.COM") == "greg.foote@trilogy.com"

    def test_angle_brackets_with_spaces(self):
        assert _parse_sender_email("Jane < jane@vendor.com >") == "jane@vendor.com"

    def test_empty_string(self):
        assert _parse_sender_email("") == ""

    def test_no_at_sign(self):
        assert _parse_sender_email("noreply") == "noreply"


# ── _is_internal_sender ─────────────────────────────────────────────────────


class TestIsInternalSender:
    """Domain and explicit address checks."""

    @pytest.fixture()
    def settings(self):
        """Settings with trilogy.com as internal domain."""
        return Settings(
            inbox_internal_sender_domains="trilogy.com",
            inbox_internal_sender_addresses="bot@external-service.io",
        )

    # Domain matches
    def test_trilogy_domain_blocked(self, settings):
        assert _is_internal_sender("greg.foote@trilogy.com", settings) is True

    def test_trilogy_display_name_blocked(self, settings):
        assert _is_internal_sender("Greg Foote <greg.foote@trilogy.com>", settings) is True

    def test_trilogy_uppercase_blocked(self, settings):
        assert _is_internal_sender("Ops <EDU.OPS@TRILOGY.COM>", settings) is True

    # Explicit address match
    def test_explicit_address_blocked(self, settings):
        assert _is_internal_sender("bot@external-service.io", settings) is True

    def test_explicit_address_display_name_blocked(self, settings):
        assert _is_internal_sender("DD Bot <bot@external-service.io>", settings) is True

    # Vendor senders pass through
    def test_vendor_allowed(self, settings):
        assert _is_internal_sender("reports@cds-group.com", settings) is False

    def test_vendor_display_name_allowed(self, settings):
        assert _is_internal_sender("CDS Team <team@cds-group.com>", settings) is False

    def test_different_domain_allowed(self, settings):
        assert _is_internal_sender("inspector@buildingchecks.com", settings) is False

    # Edge cases
    def test_empty_sender(self, settings):
        assert _is_internal_sender("", settings) is False

    def test_multiple_internal_domains(self):
        s = Settings(
            inbox_internal_sender_domains="trilogy.com, alphaschool.com",
            inbox_internal_sender_addresses="",
        )
        assert _is_internal_sender("ops@alphaschool.com", s) is True
        assert _is_internal_sender("vendor@other.com", s) is False

    def test_no_internal_domains_configured(self):
        """When no domains/addresses are set, nothing is blocked."""
        s = Settings(
            inbox_internal_sender_domains="",
            inbox_internal_sender_addresses="",
        )
        assert _is_internal_sender("greg@trilogy.com", s) is False

    def test_subdomain_not_matched(self, settings):
        """mail.trilogy.com should NOT match trilogy.com (exact domain match)."""
        assert _is_internal_sender("user@mail.trilogy.com", settings) is False


# ── process_email integration ────────────────────────────────────────────────


class TestProcessEmailInternalSenderSkip:
    """Verify that process_email short-circuits for internal senders."""

    @pytest.fixture()
    def settings(self):
        return Settings(
            inbox_internal_sender_domains="trilogy.com",
            inbox_internal_sender_addresses="",
        )

    @pytest.fixture()
    def gc(self):
        mock = MagicMock()
        # Return an email from an internal sender with a PDF attachment
        mock.gmail_get_message.return_value = {
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Boca Raton SIR"},
                    {"name": "From", "value": "DD Pipeline <edu.ops@trilogy.com>"},
                    {"name": "To", "value": "edu.ops@trilogy.com"},
                ],
                "parts": [
                    {
                        "filename": "Boca Raton SIR.pdf",
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "att123"},
                    },
                ],
            },
            "snippet": "Auto-generated SIR for Boca Raton",
        }
        return mock

    def test_internal_sender_returns_skipped(self, gc, settings):
        result = process_email(
            gc, "msg001", settings,
            label_id="lbl1", review_label_id="rlbl1",
            dry_run=True,
        )
        assert result["internal_skipped"] is True
        # Should NOT have uploaded anything
        assert "uploaded" not in result

    def test_internal_sender_marks_processed(self, gc, settings):
        result = process_email(
            gc, "msg001", settings,
            label_id="lbl1", review_label_id="rlbl1",
            dry_run=False,
        )
        assert result["internal_skipped"] is True
        assert result["marked"] is True
        gc.gmail_modify_labels.assert_called_once()

    def test_internal_sender_dry_run_no_label(self, gc, settings):
        result = process_email(
            gc, "msg001", settings,
            label_id="lbl1", review_label_id="rlbl1",
            dry_run=True,
        )
        assert result["marked"] is False
        gc.gmail_modify_labels.assert_not_called()

    def test_vendor_sender_proceeds_normally(self, settings):
        """Vendor email should NOT be short-circuited."""
        gc = MagicMock()
        gc.gmail_get_message.return_value = {
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Boca Raton SIR"},
                    {"name": "From", "value": "vendor@cds-group.com"},
                    {"name": "To", "value": "edu.ops@trilogy.com"},
                ],
                "parts": [
                    {
                        "filename": "Boca Raton SIR.pdf",
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "att123"},
                    },
                ],
            },
            "snippet": "SIR attached",
        }
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"%PDF-fake"
        gc.upload_file_to_folder.return_value = {"id": "f1", "webViewLink": "https://..."}

        result = process_email(
            gc, "msg002", settings,
            label_id="lbl1", review_label_id="rlbl1",
            site_records=[{"title": "Boca Raton", "id": "w1"}],
            dry_run=False,
        )
        assert result.get("internal_skipped") is not True
        assert len(result["uploaded"]) == 1
        assert result["uploaded"][0]["doc_type"] == "sir"
