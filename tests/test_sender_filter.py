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
    scan_inbox,
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

    @patch("due_diligence_reporter.inbox_scanner.build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    def test_vendor_sender_proceeds_normally(self, mock_resolve_m1, mock_build_summary, settings):
        """Vendor email should NOT be short-circuited."""
        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive.google.com/drive/folders/m1")
        mock_build_summary.return_value = {
            "title": "Boca Raton",
            "address": "123 Main St",
            "drive_folder_url": "https://drive.google.com/drive/folders/site123",
        }
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
            site_records=[{"title": "Boca Raton", "id": "w1", "customFields": []}],
            dry_run=False,
        )
        assert result.get("internal_skipped") is not True
        assert len(result["uploaded"]) == 1
        assert result["uploaded"][0]["doc_type"] == "sir"


# ── Malformed / unusual From headers ─────────────────────────────────────────


class TestParseSenderEmailMalformed:
    """Garbage, edge-case, and real-world malformed From headers."""

    def test_multiple_angle_brackets_takes_first(self):
        """Some forwarding systems double-wrap the address."""
        assert _parse_sender_email("FW: <relay@proxy.net> <orig@vendor.com>") == "relay@proxy.net"

    def test_angle_brackets_empty(self):
        """Empty angle brackets — regex requires 1+ chars, falls back to raw."""
        result = _parse_sender_email("Name <>")
        # No valid email inside <>, so falls through to lowered raw string
        assert result == "name <>"

    def test_only_angle_brackets(self):
        assert _parse_sender_email("<user@domain.com>") == "user@domain.com"

    def test_trailing_whitespace(self):
        assert _parse_sender_email("  user@vendor.com  ") == "user@vendor.com"

    def test_newline_in_header(self):
        """Folded headers can have newlines; should still parse."""
        assert _parse_sender_email("Name\n <user@vendor.com>") == "user@vendor.com"

    def test_missing_closing_bracket(self):
        """Truncated header — no closing bracket means regex won't match."""
        result = _parse_sender_email("Name <user@vendor.com")
        # Falls through to raw lowered string
        assert result == "name <user@vendor.com"

    def test_bare_at_sign(self):
        assert _parse_sender_email("@") == "@"

    def test_unicode_display_name(self):
        assert _parse_sender_email("José García <jose@vendor.mx>") == "jose@vendor.mx"

    def test_plus_addressing(self):
        assert _parse_sender_email("user+tag@vendor.com") == "user+tag@vendor.com"


# ── Case-insensitive domain matching ─────────────────────────────────────────


class TestDomainCaseInsensitive:
    """Domain matching must be case-insensitive at every layer."""

    def test_uppercase_config_domain(self):
        """Config has uppercase domain — should still match lowercase sender."""
        s = Settings(
            inbox_internal_sender_domains="TRILOGY.COM",
            inbox_internal_sender_addresses="",
        )
        assert _is_internal_sender("greg@trilogy.com", s) is True

    def test_mixed_case_config_domain(self):
        s = Settings(
            inbox_internal_sender_domains="Trilogy.Com",
            inbox_internal_sender_addresses="",
        )
        assert _is_internal_sender("greg@TRILOGY.COM", s) is True

    def test_uppercase_sender_lowercase_config(self):
        s = Settings(
            inbox_internal_sender_domains="trilogy.com",
            inbox_internal_sender_addresses="",
        )
        assert _is_internal_sender("GREG.FOOTE@TRILOGY.COM", s) is True

    def test_mixed_case_display_name_and_domain(self):
        s = Settings(
            inbox_internal_sender_domains="trilogy.com",
            inbox_internal_sender_addresses="",
        )
        assert _is_internal_sender('"Greg FOOTE" <Greg.Foote@Trilogy.COM>', s) is True

    def test_case_insensitive_explicit_address(self):
        """Explicit address matching should also be case-insensitive."""
        s = Settings(
            inbox_internal_sender_domains="",
            inbox_internal_sender_addresses="Bot@External-Service.IO",
        )
        assert _is_internal_sender("bot@external-service.io", s) is True
        assert _is_internal_sender("BOT@EXTERNAL-SERVICE.IO", s) is True

    def test_domains_with_whitespace_in_config(self):
        """Extra whitespace around comma-separated domains should be trimmed."""
        s = Settings(
            inbox_internal_sender_domains="  trilogy.com , alphaschool.com  ,  extra.org ",
            inbox_internal_sender_addresses="",
        )
        assert _is_internal_sender("a@trilogy.com", s) is True
        assert _is_internal_sender("b@alphaschool.com", s) is True
        assert _is_internal_sender("c@extra.org", s) is True
        assert _is_internal_sender("d@other.com", s) is False


# ── Service accounts in INBOX_INTERNAL_SENDER_ADDRESSES ──────────────────────


class TestServiceAccountAddresses:
    """Explicit service account addresses on non-internal domains."""

    @pytest.fixture()
    def settings(self):
        """Two service accounts, no internal domains."""
        return Settings(
            inbox_internal_sender_domains="",
            inbox_internal_sender_addresses=(
                "dd-pipeline@cloud-runner.iam.gserviceaccount.com,"
                "noreply@sendgrid.net"
            ),
        )

    def test_gcp_service_account_blocked(self, settings):
        assert _is_internal_sender(
            "dd-pipeline@cloud-runner.iam.gserviceaccount.com", settings,
        ) is True

    def test_gcp_service_account_with_display_name(self, settings):
        assert _is_internal_sender(
            "DD Pipeline <dd-pipeline@cloud-runner.iam.gserviceaccount.com>", settings,
        ) is True

    def test_sendgrid_noreply_blocked(self, settings):
        assert _is_internal_sender("noreply@sendgrid.net", settings) is True

    def test_different_sendgrid_user_allowed(self, settings):
        """Only the exact address is blocked, not the whole domain."""
        assert _is_internal_sender("vendor-notifications@sendgrid.net", settings) is False

    def test_service_account_domain_not_implicitly_blocked(self, settings):
        """Other addresses on the same domain should pass through."""
        assert _is_internal_sender(
            "other-bot@cloud-runner.iam.gserviceaccount.com", settings,
        ) is False

    def test_combined_domain_and_address_check(self):
        """Both domain and explicit address checked together."""
        s = Settings(
            inbox_internal_sender_domains="trilogy.com",
            inbox_internal_sender_addresses="bot@external.io",
        )
        # Domain match
        assert _is_internal_sender("user@trilogy.com", s) is True
        # Explicit address match
        assert _is_internal_sender("bot@external.io", s) is True
        # Neither
        assert _is_internal_sender("vendor@cds.com", s) is False

    def test_address_takes_priority_over_domain_miss(self):
        """An explicit address should match even when its domain isn't listed."""
        s = Settings(
            inbox_internal_sender_domains="trilogy.com",
            inbox_internal_sender_addresses="alerts@monitoring.io",
        )
        assert _is_internal_sender("alerts@monitoring.io", s) is True
        assert _is_internal_sender("other@monitoring.io", s) is False

    def test_whitespace_around_addresses_trimmed(self):
        s = Settings(
            inbox_internal_sender_domains="",
            inbox_internal_sender_addresses="  bot@a.com , bot@b.com  ",
        )
        assert _is_internal_sender("bot@a.com", s) is True
        assert _is_internal_sender("bot@b.com", s) is True


# ── scan_inbox integration — internal_skipped counter ────────────────────────


class TestScanInboxInternalCounter:
    """Verify internal_skipped is properly accumulated in scan_inbox results."""

    @patch("due_diligence_reporter.inbox_scanner.build_site_summary")
    @patch("due_diligence_reporter.inbox_scanner._resolve_m1_folder")
    def test_mixed_internal_and_vendor_emails(self, mock_resolve_m1, mock_build_summary):
        settings = Settings(
            inbox_internal_sender_domains="trilogy.com",
            inbox_internal_sender_addresses="",
            inbox_scan_query="has:attachment",
            inbox_processed_label="DD-Processed",
            inbox_manual_review_label="DD-Manual-Review",
        )

        gc = MagicMock()
        gc.gmail_get_or_create_label.return_value = "lbl1"
        gc.gmail_search.return_value = [
            {"id": "msg_internal"},
            {"id": "msg_vendor"},
        ]

        def get_message(msg_id):
            if msg_id == "msg_internal":
                return {
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "AI SIR - Tampa"},
                            {"name": "From", "value": "pipeline@trilogy.com"},
                        ],
                        "parts": [{
                            "filename": "Tampa SIR.pdf",
                            "mimeType": "application/pdf",
                            "body": {"attachmentId": "a1"},
                        }],
                    },
                    "snippet": "",
                }
            return {
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Tampa SIR"},
                        {"name": "From", "value": "inspector@vendor.com"},
                    ],
                    "parts": [{
                        "filename": "Tampa SIR.pdf",
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "a2"},
                    }],
                },
                "snippet": "",
            }

        gc.gmail_get_message.side_effect = get_message
        gc.file_exists_in_folder.return_value = False
        gc.gmail_get_attachment.return_value = b"%PDF-fake"
        gc.upload_file_to_folder.return_value = {"id": "f1", "webViewLink": "https://..."}

        mock_resolve_m1.return_value = ("m1_folder_id", "https://drive.google.com/drive/folders/m1")
        mock_build_summary.return_value = {
            "title": "Tampa",
            "address": "",
            "drive_folder_url": "https://drive.google.com/drive/folders/tampa",
        }

        results = scan_inbox(
            gc,
            site_records=[{"id": "WTAMPA", "title": "Tampa", "customFields": []}],
            settings=settings,
            dry_run=False,
        )

        assert results["internal_skipped"] == 1
        assert results["attachments_uploaded"] == 1
        assert results["emails_processed"] >= 1
