"""Tests for the retry utility module and retry behaviour on external API calls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from due_diligence_reporter.retry import (
    MAX_ATTEMPTS,
    _is_retryable_http_error,
    api_retry,
    retry_config,
)

# ---------------------------------------------------------------------------
# Unit tests for _is_retryable_http_error
# ---------------------------------------------------------------------------


class TestIsRetryableHttpError:
    """Verify that the retry predicate identifies the right exceptions."""

    def _make_http_error(self, status_code: int) -> requests.HTTPError:
        resp = MagicMock(spec=requests.Response)
        resp.status_code = status_code
        return requests.HTTPError(response=resp)

    def test_retryable_status_codes(self) -> None:
        for code in (429, 500, 502, 503, 504):
            err = self._make_http_error(code)
            assert _is_retryable_http_error(err) is True, f"Expected {code} to be retryable"

    def test_non_retryable_status_codes(self) -> None:
        for code in (400, 401, 403, 404, 405, 409, 422):
            err = self._make_http_error(code)
            assert _is_retryable_http_error(err) is False, f"Expected {code} NOT to be retryable"

    def test_non_http_error(self) -> None:
        assert _is_retryable_http_error(ValueError("oops")) is False

    def test_google_http_error(self) -> None:
        """Simulate a googleapiclient HttpError (duck-typed by class name + status_code)."""

        class HttpError(Exception):
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

        assert _is_retryable_http_error(HttpError(503)) is True
        assert _is_retryable_http_error(HttpError(404)) is False


# ---------------------------------------------------------------------------
# Integration test: verify that a retried function succeeds on second attempt
# ---------------------------------------------------------------------------


class TestApiRetryBehaviour:
    """Ensure the retry decorator retries on transient errors and succeeds."""

    def test_retries_on_connection_error_then_succeeds(self) -> None:
        """Simulate a ConnectionError on the first call, success on the second."""
        call_count = 0

        @api_retry
        def flaky_call() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise requests.ConnectionError("connection reset")
            return "ok"

        result = flaky_call()
        assert result == "ok"
        assert call_count == 2

    def test_retries_on_timeout_then_succeeds(self) -> None:
        call_count = 0

        @api_retry
        def flaky_call() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise requests.Timeout("timed out")
            return "ok"

        result = flaky_call()
        assert result == "ok"
        assert call_count == 2

    def test_retries_on_429_then_succeeds(self) -> None:
        call_count = 0

        @api_retry
        def flaky_call() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = MagicMock(spec=requests.Response)
                resp.status_code = 429
                raise requests.HTTPError(response=resp)
            return "ok"

        result = flaky_call()
        assert result == "ok"
        assert call_count == 2

    def test_retries_on_503_then_succeeds(self) -> None:
        call_count = 0

        @api_retry
        def flaky_call() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = MagicMock(spec=requests.Response)
                resp.status_code = 503
                raise requests.HTTPError(response=resp)
            return "ok"

        result = flaky_call()
        assert result == "ok"
        assert call_count == 2

    def test_gives_up_after_max_attempts(self) -> None:
        """After MAX_ATTEMPTS failures, the exception should propagate."""
        call_count = 0

        @api_retry
        def always_fails() -> str:
            nonlocal call_count
            call_count += 1
            raise requests.ConnectionError("permanent failure")

        with pytest.raises(requests.ConnectionError):
            always_fails()

        assert call_count == MAX_ATTEMPTS

    def test_does_not_retry_on_non_retryable_error(self) -> None:
        """A 404 HTTPError should NOT be retried."""
        call_count = 0

        @api_retry
        def not_found() -> str:
            nonlocal call_count
            call_count += 1
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 404
            raise requests.HTTPError(response=resp)

        with pytest.raises(requests.HTTPError):
            not_found()

        assert call_count == 1


# ---------------------------------------------------------------------------
# Wrike _wrike_get integration test
# ---------------------------------------------------------------------------


class TestWrikeGetRetry:
    """Test that _wrike_get retries on transient HTTP errors."""

    @patch("due_diligence_reporter.wrike.requests.get")
    def test_wrike_get_retries_on_503(self, mock_get: MagicMock) -> None:
        from due_diligence_reporter.wrike import _wrike_get

        # First call returns 503, second succeeds
        fail_resp = MagicMock(spec=requests.Response)
        fail_resp.ok = False
        fail_resp.status_code = 503
        fail_resp.raise_for_status.side_effect = requests.HTTPError(response=fail_resp)

        ok_resp = MagicMock(spec=requests.Response)
        ok_resp.ok = True
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"data": []}

        mock_get.side_effect = [fail_resp, ok_resp]

        result = _wrike_get(
            "https://www.wrike.com/api/v4/test",
            headers={"Authorization": "bearer fake"},
        )
        assert result == ok_resp
        assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# Shovels retry integration test
# ---------------------------------------------------------------------------


class TestShovelsRetry:
    """Test that Shovels API calls retry on transient errors."""

    @patch("due_diligence_reporter.server.requests.get")
    def test_shovels_search_retries_on_timeout(self, mock_get: MagicMock) -> None:
        from due_diligence_reporter.server import _call_shovels_search

        # First call times out, second succeeds
        ok_resp = MagicMock(spec=requests.Response)
        ok_resp.status_code = 200
        ok_resp.raise_for_status.return_value = None
        ok_resp.json.return_value = {"items": [{"id": "geo123"}]}

        mock_get.side_effect = [requests.Timeout("timed out"), ok_resp]

        result = _call_shovels_search("key", "https://api.shovels.ai/v2", "123 Main St")
        assert result == {"id": "geo123"}
        assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# retry_config override test
# ---------------------------------------------------------------------------


class TestRetryConfigOverrides:
    """Test that retry_config accepts overrides."""

    def test_override_stop(self) -> None:
        from tenacity import stop_after_attempt

        custom_stop = stop_after_attempt(5)
        cfg = retry_config(stop=custom_stop)
        assert cfg["stop"] is custom_stop

    def test_default_reraise(self) -> None:
        cfg = retry_config()
        assert cfg["reraise"] is True
