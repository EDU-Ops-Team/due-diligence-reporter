"""Tests for the vendor-gating readiness logic.

Confirms:
* Default OFF behavior matches pre-cutover (sir_found + inspection_found).
* When VENDOR_GATE_ENABLED=1 the gate requires vendor SIR + vendor BI +
  RayCon scenario JSON.
* The Tulsa false-positive scenario (AI-generated SIR present, no vendor) is
  blocked.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from due_diligence_reporter.report_pipeline import (
    _missing_required_docs,
    _notify_vendor_gate_extraction_failure,
    _resolve_readiness_result,
    _vendor_gate_enabled,
)


class TestVendorGateFlag:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("VENDOR_GATE_ENABLED", raising=False)
        assert _vendor_gate_enabled() is False

    def test_off_for_falsey_values(self, monkeypatch):
        for val in ["0", "", "false", "False"]:
            monkeypatch.setenv("VENDOR_GATE_ENABLED", val)
            assert _vendor_gate_enabled() is False, val

    def test_on_for_truthy_values(self, monkeypatch):
        for val in ["1", "true", "yes"]:
            monkeypatch.setenv("VENDOR_GATE_ENABLED", val)
            assert _vendor_gate_enabled() is True, val


class TestMissingRequiredDocsLegacy:
    """Default OFF mode matches pre-cutover behavior."""

    def test_legacy_passes_with_sir_and_bi(self, monkeypatch):
        monkeypatch.delenv("VENDOR_GATE_ENABLED", raising=False)
        readiness = {"sir_found": True, "inspection_found": True}
        assert _missing_required_docs(readiness) == []

    def test_legacy_blocks_when_sir_missing(self, monkeypatch):
        monkeypatch.delenv("VENDOR_GATE_ENABLED", raising=False)
        readiness = {"sir_found": False, "inspection_found": True}
        assert _missing_required_docs(readiness) == ["SIR"]


class TestMissingRequiredDocsVendorGate:
    """With VENDOR_GATE_ENABLED=1."""

    def setup_method(self, _method):
        os.environ["VENDOR_GATE_ENABLED"] = "1"

    def teardown_method(self, _method):
        os.environ.pop("VENDOR_GATE_ENABLED", None)

    def test_vendor_gate_passes_with_all_three(self):
        readiness = {
            "sir_found": True,
            "sir_vendor": True,
            "inspection_found": True,
            "inspection_vendor": True,
            "raycon_scenario_found": True,
        }
        assert _missing_required_docs(readiness) == []

    def test_tulsa_scenario_ai_sir_present_but_blocked(self):
        # Tulsa false-positive: AI-generated SIR present (sir_found=True) but
        # provenance check rejected it (sir_vendor=False). Gate blocks.
        readiness = {
            "sir_found": True,
            "sir_vendor": False,
            "inspection_found": False,
            "inspection_vendor": False,
            "raycon_scenario_found": False,
        }
        missing = _missing_required_docs(readiness)
        assert "Vendor SIR" in missing
        assert "Vendor Building Inspection" in missing
        assert "RayCon Scenario JSON" in missing

    def test_blocks_when_only_raycon_missing(self):
        readiness = {
            "sir_found": True,
            "sir_vendor": True,
            "inspection_found": True,
            "inspection_vendor": True,
            "raycon_scenario_found": False,
        }
        assert _missing_required_docs(readiness) == ["RayCon Scenario JSON"]

    def test_blocks_when_raycon_json_failed_validation(self):
        readiness = {
            "sir_found": True,
            "sir_vendor": True,
            "inspection_found": True,
            "inspection_vendor": True,
            "raycon_scenario_found": True,
            "raycon_scenario_usable": False,
            "raycon_scenario_status": "failed_validation",
        }
        assert _missing_required_docs(readiness) == [
            "Successful RayCon Scenario JSON"
        ]

    def test_blocks_when_inspection_is_ai_only(self):
        readiness = {
            "sir_found": True,
            "sir_vendor": True,
            "inspection_found": True,
            "inspection_vendor": False,
            "raycon_scenario_found": True,
        }
        assert _missing_required_docs(readiness) == ["Vendor Building Inspection"]


class TestReadinessResolution:
    def test_ai_sir_can_proceed_under_vendor_gate(self, monkeypatch):
        monkeypatch.setenv("VENDOR_GATE_ENABLED", "1")
        readiness = {
            "sir_found": True,
            "sir_vendor": False,
            "inspection_found": False,
            "inspection_vendor": False,
            "raycon_scenario_found": False,
            "report_exists": False,
        }
        result = _resolve_readiness_result("Alpha School Tulsa", readiness)
        # First-round publishing proceeds from AI SIR / research output.
        # Full vendor readiness is still represented by _missing_required_docs.
        assert result is None

    def test_missing_sir_still_blocks_first_round(self, monkeypatch):
        monkeypatch.setenv("VENDOR_GATE_ENABLED", "1")
        readiness = {
            "sir_found": False,
            "sir_vendor": False,
            "inspection_found": True,
            "inspection_vendor": True,
            "raycon_scenario_found": True,
            "report_exists": False,
        }
        result = _resolve_readiness_result("Alpha School Tulsa", readiness)
        assert result is not None
        assert result.status == "waiting_on_docs"
        assert result.missing_docs == ["SIR"]

    def test_tulsa_passes_under_legacy_gate_today(self, monkeypatch):
        # Confirms pre-cutover behavior is unchanged when flag is off.
        monkeypatch.delenv("VENDOR_GATE_ENABLED", raising=False)
        readiness = {
            "sir_found": True,
            "inspection_found": True,
            "report_exists": False,
        }
        result = _resolve_readiness_result("Alpha School Tulsa", readiness)
        # None means "proceed to agent" — legacy behavior, the bug we fix.
        assert result is None


class TestVendorGateAlert:
    def test_alert_posts_to_chat_webhook(self):
        with patch(
            "due_diligence_reporter.report_pipeline.post_google_chat_message"
        ) as mock_post:
            _notify_vendor_gate_extraction_failure(
                "https://chat.googleapis.com/wh/abc",
                "Alpha School Tulsa",
                drive_folder_url="https://drive.google.com/drive/folders/xyz",
                failure_reason="5 unresolved tokens",
                trace_url="https://trace.example/trace.json",
            )
        assert mock_post.call_count == 1
        url, body = mock_post.call_args.args
        assert url == "https://chat.googleapis.com/wh/abc"
        assert "Human Intervention Needed" in body
        assert "Alpha School Tulsa" in body
        assert "5 unresolved tokens" in body

    def test_alert_silent_when_no_webhook(self):
        with patch(
            "due_diligence_reporter.report_pipeline.post_google_chat_message"
        ) as mock_post:
            _notify_vendor_gate_extraction_failure(
                "", "Alpha School Tulsa", failure_reason="x"
            )
        mock_post.assert_not_called()

    def test_alert_supports_comma_separated_webhooks(self):
        with patch(
            "due_diligence_reporter.report_pipeline.post_google_chat_message"
        ) as mock_post:
            _notify_vendor_gate_extraction_failure(
                "https://hook1, https://hook2",
                "Site",
                failure_reason="reason",
            )
        assert mock_post.call_count == 2
