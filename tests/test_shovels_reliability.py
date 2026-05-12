"""Reliability tests for the Shovels.ai integration.

Covers:

* ``shovels_status()`` preflight (missing / placeholder / whitespace / ok)
* ``get_permit_history`` tool behavior when the key is unconfigured,
  when the upstream times out, and when the upstream returns malformed
  payloads. All failure paths must return a structured ``gap_label``
  rather than raising, so the DD report run does not crash.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
import requests

from due_diligence_reporter.config import Settings, shovels_status
from due_diligence_reporter.server import (
    SHOVELS_GAP_LABEL_API_ERROR,
    SHOVELS_GAP_LABEL_NOT_CONFIGURED,
    SHOVELS_GAP_LABEL_NOT_FOUND,
    get_permit_history,
)

# ---------------------------------------------------------------------------
# shovels_status() — preflight
# ---------------------------------------------------------------------------


def _settings(key: str) -> Settings:
    """Build a Settings instance bypassing .env / env vars."""
    return Settings.model_construct(
        shovels_api_key=key,
        shovels_api_base_url="https://api.shovels.ai/v2",
    )


class TestShovelsStatus:
    def test_missing_key_reports_missing(self) -> None:
        status = shovels_status(_settings(""))
        assert status["configured"] is False
        assert status["reason"] == "missing"
        assert status["base_url"] == "https://api.shovels.ai/v2"

    def test_whitespace_only_key_reports_whitespace(self) -> None:
        status = shovels_status(_settings("   \n\t"))
        assert status["configured"] is False
        assert status["reason"] == "whitespace_only"

    def test_placeholder_key_reports_placeholder(self) -> None:
        status = shovels_status(_settings("your_shovels_api_key_here"))
        assert status["configured"] is False
        assert status["reason"] == "placeholder"

    def test_placeholder_key_case_insensitive(self) -> None:
        status = shovels_status(_settings("YOUR_SHOVELS_API_KEY_HERE"))
        assert status["configured"] is False
        assert status["reason"] == "placeholder"

    def test_real_looking_key_reports_ok(self) -> None:
        status = shovels_status(_settings("sk_live_abc123def456"))
        assert status["configured"] is True
        assert status["reason"] == "ok"

    def test_status_never_leaks_raw_key(self) -> None:
        secret = "sk_live_supersecret_should_never_be_logged"
        status = shovels_status(_settings(secret))
        # The status dict is intended to be safe to log. Make sure no field
        # carries the raw key — regression guard if someone adds it later.
        assert all(secret not in str(v) for v in status.values())


# ---------------------------------------------------------------------------
# get_permit_history — preflight failure
# ---------------------------------------------------------------------------


class TestGetPermitHistoryUnconfigured:
    def test_missing_key_returns_gap_label_not_raise(self) -> None:
        # Patch get_settings INSIDE the server module so the tool sees an
        # unconfigured Settings without touching any real .env.
        fake_settings = _settings("")
        with patch("due_diligence_reporter.server.get_settings", return_value=fake_settings):
            result = asyncio.run(get_permit_history("123 Main St"))

        assert result["status"] == "error"
        assert result["gap_label"] == SHOVELS_GAP_LABEL_NOT_CONFIGURED
        # Downstream report-builder code indexes these keys unconditionally.
        assert result["risk_flags"] == []
        assert result["report_data_fields"] == {
            "exec.acquisition_conditions": "",
            "exec.tradeoffs_and_deficiencies": "",
        }
        # The status payload is included so operators see *why* it's unconfigured.
        assert result["shovels_status"]["reason"] == "missing"

    def test_placeholder_key_returns_gap_label(self) -> None:
        fake_settings = _settings("your_shovels_api_key_here")
        with patch("due_diligence_reporter.server.get_settings", return_value=fake_settings):
            result = asyncio.run(get_permit_history("123 Main St"))

        assert result["status"] == "error"
        assert result["gap_label"] == SHOVELS_GAP_LABEL_NOT_CONFIGURED
        assert result["shovels_status"]["reason"] == "placeholder"


# ---------------------------------------------------------------------------
# get_permit_history — happy path & not_found path still produce gap_label
# ---------------------------------------------------------------------------


def _shovels_response(json_data, status_code=200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.raise_for_status.return_value = None
    return mock


class TestGetPermitHistoryCoverage:
    def test_not_found_includes_gap_label(self) -> None:
        fake_settings = _settings("sk_live_ok")
        with (
            patch("due_diligence_reporter.server.get_settings", return_value=fake_settings),
            patch(
                "due_diligence_reporter.server.requests.get",
                return_value=_shovels_response({"items": []}),
            ),
        ):
            result = asyncio.run(get_permit_history("Unknown Address"))

        assert result["status"] == "success"
        assert result["coverage"] == "not_found"
        assert result["gap_label"] == SHOVELS_GAP_LABEL_NOT_FOUND


# ---------------------------------------------------------------------------
# get_permit_history — API failure / timeout / malformed response
# ---------------------------------------------------------------------------


class TestGetPermitHistoryAPIFailures:
    def test_timeout_returns_gap_label(self) -> None:
        fake_settings = _settings("sk_live_ok")
        with (
            patch("due_diligence_reporter.server.get_settings", return_value=fake_settings),
            patch(
                "due_diligence_reporter.server.requests.get",
                side_effect=requests.Timeout("connect timeout"),
            ),
        ):
            result = asyncio.run(get_permit_history("123 Main St"))

        assert result["status"] == "error"
        assert result["gap_label"] == SHOVELS_GAP_LABEL_API_ERROR
        assert result["risk_flags"] == []
        assert "exec.acquisition_conditions" in result["report_data_fields"]

    def test_500_returns_gap_label(self) -> None:
        fake_settings = _settings("sk_live_ok")
        err_response = MagicMock()
        err_response.status_code = 500
        http_err = requests.HTTPError("500 Server Error", response=err_response)

        with (
            patch("due_diligence_reporter.server.get_settings", return_value=fake_settings),
            patch(
                "due_diligence_reporter.server.requests.get",
                side_effect=http_err,
            ),
        ):
            result = asyncio.run(get_permit_history("123 Main St"))

        assert result["status"] == "error"
        assert result["gap_label"] == SHOVELS_GAP_LABEL_API_ERROR

    def test_malformed_metrics_payload_returns_gap_label(self) -> None:
        """Metrics endpoint returns a list instead of a dict — should not crash."""
        fake_settings = _settings("sk_live_ok")

        # search returns a real geo_id, metrics returns a non-dict
        search_resp = _shovels_response({"items": [{"geo_id": "g1", "name": "Norm Addr"}]})
        bad_metrics_resp = _shovels_response(["unexpected", "list", "payload"])

        with (
            patch("due_diligence_reporter.server.get_settings", return_value=fake_settings),
            patch(
                "due_diligence_reporter.server.requests.get",
                side_effect=[search_resp, bad_metrics_resp],
            ),
        ):
            result = asyncio.run(get_permit_history("123 Main St"))

        assert result["status"] == "error"
        assert result["gap_label"] == SHOVELS_GAP_LABEL_API_ERROR
        assert "malformed" in result["message"].lower()

    def test_malformed_permits_payload_returns_gap_label(self) -> None:
        """Permits endpoint returns a string instead of {items: [...]} — should not crash."""
        fake_settings = _settings("sk_live_ok")

        search_resp = _shovels_response({"items": [{"geo_id": "g1", "name": "Norm Addr"}]})
        metrics_resp = _shovels_response({
            "permit_count": 5,
            "permit_active_count": 0,
            "permit_in_review_count": 0,
            "permit_final_count": 5,
            "permit_inactive_count": 0,
            "total_job_value": 0,
            "avg_inspection_pass_rate": 0.9,
        })
        # Permits endpoint returns a JSON object whose ``items`` is a string.
        # _call_shovels_permits does ``.get("items", [])`` so it'll surface
        # a string — get_permit_history must detect this without crashing.
        bad_permits_resp = _shovels_response({"items": "not-a-list"})

        with (
            patch("due_diligence_reporter.server.get_settings", return_value=fake_settings),
            patch(
                "due_diligence_reporter.server.requests.get",
                side_effect=[search_resp, metrics_resp, bad_permits_resp],
            ),
        ):
            result = asyncio.run(get_permit_history("123 Main St"))

        assert result["status"] == "error"
        assert result["gap_label"] == SHOVELS_GAP_LABEL_API_ERROR
        assert "malformed" in result["message"].lower()


# ---------------------------------------------------------------------------
# get_permit_history — happy path with key present
# ---------------------------------------------------------------------------


class TestGetPermitHistoryHappyPath:
    def test_key_present_returns_success(self) -> None:
        fake_settings = _settings("sk_live_ok")
        search_resp = _shovels_response({
            "items": [{"geo_id": "g1", "name": "123 Main St, Atlanta, GA 30308"}]
        })
        metrics_resp = _shovels_response({
            "permit_count": 3,
            "permit_active_count": 0,
            "permit_in_review_count": 0,
            "permit_final_count": 3,
            "permit_inactive_count": 0,
            "total_job_value": 0,
            "avg_inspection_pass_rate": 0.95,
        })
        permits_resp = _shovels_response({"items": [
            {"type": "electrical", "tags": ["electrical"], "description": "",
             "file_date": "2021-01-01", "status": "final"},
        ]})

        with (
            patch("due_diligence_reporter.server.get_settings", return_value=fake_settings),
            patch(
                "due_diligence_reporter.server.requests.get",
                side_effect=[search_resp, metrics_resp, permits_resp],
            ),
        ):
            result = asyncio.run(get_permit_history("123 Main St"))

        assert result["status"] == "success"
        assert result["coverage"] == "found"
        assert result["metrics"]["permit_count"] == 3
        # gap_label is not present on the success path — callers branch on status/coverage.
        assert "gap_label" not in result or result.get("gap_label") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
