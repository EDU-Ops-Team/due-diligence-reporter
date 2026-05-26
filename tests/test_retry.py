"""Tests for the retry utility module and retry behaviour on external API calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

from due_diligence_reporter.retry import (
    _RETRY_AFTER_MAX_SECONDS,
    MAX_ATTEMPTS,
    _is_retryable_http_error,
    _parse_retry_after_seconds,
    _rate_limit_aware_wait,
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
# Tests for _parse_retry_after_seconds
# ---------------------------------------------------------------------------


class TestParseRetryAfterSeconds:
    """Verify Retry-After parsing from Google API 429 error messages."""

    def test_parses_google_api_retry_after_timestamp(self) -> None:
        """Google API style: 'Retry after 2026-04-15T22:48:00.602Z'"""
        # Create a fake error with a timestamp ~15 minutes in the future
        future = datetime.now(UTC) + timedelta(minutes=15)
        ts = future.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        exc = Exception(
            f'<HttpError 429 when requesting https://gmail.googleapis.com/... '
            f'"User-rate limit exceeded.  Retry after {ts}">'
        )
        result = _parse_retry_after_seconds(exc)
        assert result is not None
        # Should be ~900 seconds (15 min) + 5 second buffer, give or take
        assert 890 < result < 920

    def test_returns_none_for_no_retry_after(self) -> None:
        exc = Exception("Some random error with no timestamp")
        assert _parse_retry_after_seconds(exc) is None

    def test_returns_minimum_1_second_for_past_timestamp(self) -> None:
        """If the retry-after time is already past, return 1 second."""
        exc = Exception(
            '<HttpError 429 ... "Retry after 2020-01-01T00:00:00.000Z">'
        )
        result = _parse_retry_after_seconds(exc)
        assert result is not None
        assert result == 1  # max(negative + 5, 1) = 1

    def test_parses_retry_after_from_response_headers(self) -> None:
        """requests.HTTPError carries the header on exc.response.headers.

        Regression test for the iter1 fix: prior to the dual-source probe,
        ``hasattr(exc, "headers")`` returned False for requests.HTTPError
        and rate-limit-aware waits were silently disabled for every
        requests-based client.
        """
        response = MagicMock(spec=requests.Response)
        response.headers = {"Retry-After": "45"}
        exc = requests.HTTPError(response=response)
        assert _parse_retry_after_seconds(exc) == 45.0

    def test_parses_retry_after_from_exc_headers(self) -> None:
        """googleapiclient.errors.HttpError exposes headers directly on the exception."""

        class _FakeGoogleHttpError(Exception):
            headers = {"Retry-After": "30"}

        assert _parse_retry_after_seconds(_FakeGoogleHttpError()) == 30.0

    def test_caps_retry_after_at_20_minutes(self) -> None:
        """Defense-in-depth: cap parsed Retry-After at the constant in the parser.

        Asserts against ``_RETRY_AFTER_MAX_SECONDS`` so that if the cap
        ever shifts, this test follows in lockstep with production code.
        /check iter2 IMP-C caught the prior literal ``1200.0`` here
        defeating iter1's constant unification.
        """
        response = MagicMock(spec=requests.Response)
        response.headers = {"Retry-After": "999999999"}
        exc = requests.HTTPError(response=response)
        assert _parse_retry_after_seconds(exc) == _RETRY_AFTER_MAX_SECONDS

    def test_no_clamp_at_exact_boundary(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """At ``Retry-After: 1200`` (exact boundary) the parser must NOT clamp.

        retry.py uses ``if value > _RETRY_AFTER_MAX_SECONDS`` (strict ``>``)
        so the boundary value passes through unchanged. /check iter2 LE-1
        called this out as missing coverage. iter3 hardening: the value
        comparison alone can't distinguish ``>`` from ``>=`` (both return
        1200.0 when input is 1200), so we also assert that the clamping
        WARNING is NOT emitted at the boundary. A regression to ``>=``
        would emit the warning and fail this test.
        """
        import logging

        response = MagicMock(spec=requests.Response)
        response.headers = {"Retry-After": str(int(_RETRY_AFTER_MAX_SECONDS))}
        exc = requests.HTTPError(response=response)
        with caplog.at_level(logging.WARNING, logger="due_diligence_reporter.retry"):
            result = _parse_retry_after_seconds(exc)
        assert result == _RETRY_AFTER_MAX_SECONDS
        # Strict ``>`` means the boundary input passes through without a
        # clamp; no WARNING should fire. Future change to ``>=`` would
        # emit the WARNING and fail this assertion.
        assert not any(
            "clamping" in r.getMessage() for r in caplog.records
        ), f"Boundary value 1200 should NOT clamp, but got: {[r.getMessage() for r in caplog.records]}"

    def test_ignores_non_integer_retry_after_header(self) -> None:
        """HTTP-date format is not handled; non-digit headers return None."""
        response = MagicMock(spec=requests.Response)
        response.headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
        exc = requests.HTTPError(response=response)
        assert _parse_retry_after_seconds(exc) is None

    def test_response_headers_take_precedence_over_exc_headers(self) -> None:
        """When both exc.headers and exc.response.headers exist, prefer the wire value."""
        response = MagicMock(spec=requests.Response)
        response.headers = {"Retry-After": "60"}

        class _DualSource(Exception):
            headers = {"Retry-After": "10"}

        exc = _DualSource()
        exc.response = response  # type: ignore[attr-defined]
        assert _parse_retry_after_seconds(exc) == 60.0

    def test_clamp_logs_warning_when_header_exceeds_cap(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When the parser clamps an outsized header, it must log a WARNING
        so operators can spot abnormal upstream behaviour during incidents."""
        import logging

        response = MagicMock(spec=requests.Response)
        response.headers = {"Retry-After": "99999"}
        exc = requests.HTTPError(response=response)
        with caplog.at_level(logging.WARNING, logger="due_diligence_reporter.retry"):
            result = _parse_retry_after_seconds(exc)
        assert result == _RETRY_AFTER_MAX_SECONDS
        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "clamping" in r.getMessage()
        ]
        assert len(warnings) == 1, (
            f"Expected exactly one clamp warning, got {[r.getMessage() for r in warnings]}"
        )
        assert "99999" in warnings[0].getMessage()
        # /check iter2 IMP-A: lock the logger name so future renames can't
        # silently break log-pipeline filters that key off the dotted path.
        assert warnings[0].name == "due_diligence_reporter.retry"

    def test_no_warning_when_header_within_cap(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Headers below the cap must not trigger the clamp warning."""
        import logging

        response = MagicMock(spec=requests.Response)
        response.headers = {"Retry-After": "60"}
        exc = requests.HTTPError(response=response)
        with caplog.at_level(logging.WARNING, logger="due_diligence_reporter.retry"):
            result = _parse_retry_after_seconds(exc)
        assert result == 60.0
        assert not any("clamping" in r.getMessage() for r in caplog.records)
        # No warning record at all should belong to the retry logger when
        # the header is within cap.
        assert not any(
            r.name == "due_diligence_reporter.retry" and r.levelno >= logging.WARNING
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# _rate_limit_aware_wait integration tests (caller-level cap)
# ---------------------------------------------------------------------------


class TestRateLimitAwareWaitCap:
    """The caller-level cap in _rate_limit_aware_wait covers BOTH the
    integer-header path (already capped at parser level) and the
    ISO-timestamp path (NOT capped at parser level). This test class locks
    that contract so future refactors can't regress either branch."""

    def _make_retry_state(self, exc: BaseException) -> object:
        """Build a minimal RetryCallState-shaped object for _rate_limit_aware_wait."""
        outcome = MagicMock()
        outcome.exception.return_value = exc
        state = MagicMock()
        state.outcome = outcome
        state.attempt_number = 1
        return state

    def test_caps_header_path_at_constant(self) -> None:
        """Header path: parser caps to _RETRY_AFTER_MAX_SECONDS, caller is no-op."""
        response = MagicMock(spec=requests.Response)
        response.headers = {"Retry-After": "999999999"}
        exc = requests.HTTPError(response=response)
        wait = _rate_limit_aware_wait(self._make_retry_state(exc))
        assert wait == _RETRY_AFTER_MAX_SECONDS

    def test_caps_iso_timestamp_path_at_constant(self) -> None:
        """ISO-timestamp path: parser returns uncapped value (e.g. 50 years
        in the future); caller's min() is the ONLY thing protecting the
        caller from a multi-decade sleep. If this regresses, change
        retry.py:_rate_limit_aware_wait or _parse_retry_after_seconds."""
        # 30 minutes in the future = 1800s + 5s buffer = 1805s, > 1200s cap.
        future = (datetime.now(UTC) + timedelta(minutes=30)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        exc = Exception(f"429 Too Many Requests. Retry after {future}")
        # First confirm the parser-level path returns > cap (uncapped).
        parsed = _parse_retry_after_seconds(exc)
        assert parsed is not None and parsed > _RETRY_AFTER_MAX_SECONDS
        # Then confirm the caller caps it.
        wait = _rate_limit_aware_wait(self._make_retry_state(exc))
        assert wait == _RETRY_AFTER_MAX_SECONDS


# ---------------------------------------------------------------------------
# Shovels retry integration test (DEPRECATED — legacy helper)
#
# The Shovels integration is no longer in normal DDR scope; the
# ``get_permit_history`` MCP tool is unregistered by default. This test
# still exercises the retry wiring on the legacy ``_call_shovels_search``
# helper so a future refactor of the retry config can't silently break
# the opt-in path (DDR_ENABLE_SHOVELS=true) without a test failure.
# ---------------------------------------------------------------------------


class TestShovelsRetry:
    """Test that the legacy Shovels API helper retries on transient errors."""

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
