"""Shared retry utilities for external API calls.

Provides a standard retry configuration using ``tenacity`` for all outbound
HTTP requests (Wrike, RayCon, Shovels.ai, OpenAI, Google APIs).
Retries on transient errors: connection errors, timeouts, and HTTP 429/5xx.

For 429 (rate limit) errors, the retry logic parses the ``Retry-After`` header
or error message and waits the requested duration before retrying.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import requests
from tenacity import (
    RetryCallState,
    before_sleep_log,
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger("[retry]")

# Max 5 attempts total (1 initial + 4 retries) — enough to survive a
# Gmail API 429 with a ~15-minute coolback window.
MAX_ATTEMPTS = 5
# Exponential backoff: 1s, 2s, 4s, ... capped at 30s (for non-429 errors)
BACKOFF_MIN = 1
BACKOFF_MAX = 30


_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _parse_retry_after_seconds(exc: BaseException) -> float | None:
    """Extract the number of seconds to wait from a 429 response.

    Looks for:
    1. A ``Retry after <ISO timestamp>`` pattern in the error message
       (Google API style)
    2. A ``Retry-After`` header with an integer (seconds) or HTTP-date

    Returns None if no retry-after information is found.
    """
    error_str = str(exc)

    # Google API embeds "Retry after 2026-04-15T22:48:00.602Z" in the message
    match = re.search(r"Retry after (\d{4}-\d{2}-\d{2}T[\d:.]+Z)", error_str)
    if match:
        try:
            retry_at = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
            now = datetime.now(UTC)
            wait_secs = (retry_at - now).total_seconds()
            # Add a 5-second buffer to avoid racing the edge
            return max(wait_secs + 5, 1)
        except (ValueError, OSError):
            pass

    # Standard Retry-After header (integer seconds).
    #
    # The header lives in different places depending on exception type:
    #   * googleapiclient.errors.HttpError exposes ``exc.headers`` directly.
    #   * requests.HTTPError carries the response on ``exc.response``;
    #     the headers are at ``exc.response.headers``.
    #
    # The original ``hasattr(exc, "headers")`` check returned False for
    # requests.HTTPError, which silently disabled rate-limit-aware waits
    # for every HTTP client (Rebl, dashboard publish, etc.). Probe both
    # locations so a 429 from a requests-based client is honored.
    candidate_headers: Any = None
    if hasattr(exc, "headers"):
        candidate_headers = getattr(exc, "headers", None)
    response = getattr(exc, "response", None)
    if response is not None and hasattr(response, "headers"):
        # When both are present, prefer the response (HTTP wire value) over
        # whatever the SDK set on the exception itself.
        candidate_headers = response.headers
    if candidate_headers:
        header = candidate_headers.get("Retry-After")
        if header and str(header).isdigit():
            return float(header)

    return None


def _is_retryable_http_error(exc: BaseException) -> bool:
    """Return True if the exception is a retryable HTTP error.

    Supports both ``requests.HTTPError`` and ``googleapiclient.errors.HttpError``.
    """
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        if response is not None and response.status_code in _RETRYABLE_STATUS_CODES:
            return True
    # Google API client errors — avoid hard import to keep retry.py lightweight
    exc_cls_name = type(exc).__name__
    if exc_cls_name == "HttpError" and hasattr(exc, "status_code"):
        if exc.status_code in _RETRYABLE_STATUS_CODES:
            return True
    return False


def _rate_limit_aware_wait(retry_state: RetryCallState) -> float:
    """Wait strategy that respects Retry-After for 429 errors.

    If the last exception was a 429 with a parseable Retry-After value,
    wait that long (capped at 20 minutes). Otherwise fall back to
    exponential backoff.
    """
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if exc is not None:
        wait_secs = _parse_retry_after_seconds(exc)
        if wait_secs is not None:
            # Cap at 20 minutes to avoid infinite waits
            capped = min(wait_secs, 1200)
            logger.info(
                "Rate limited — waiting %.0f seconds before retry (attempt %d/%d)",
                capped,
                retry_state.attempt_number + 1,
                MAX_ATTEMPTS,
            )
            return capped

    # Fallback: exponential backoff for non-429 errors
    exp = wait_exponential(multiplier=1, min=BACKOFF_MIN, max=BACKOFF_MAX)
    return exp(retry_state)


# Combined retry condition: network-level errors OR retryable HTTP status codes
RETRY_CONDITION = (
    retry_if_exception_type((requests.ConnectionError, requests.Timeout, ConnectionError))
    | retry_if_exception(_is_retryable_http_error)
)


def retry_config(**overrides: Any) -> dict[str, Any]:
    """Return standard tenacity retry kwargs, with optional overrides.

    Usage::

        @retry(**retry_config())
        def my_api_call():
            ...
    """
    defaults: dict[str, Any] = {
        "retry": RETRY_CONDITION,
        "stop": stop_after_attempt(MAX_ATTEMPTS),
        "wait": _rate_limit_aware_wait,
        "before_sleep": before_sleep_log(logger, logging.WARNING),
        "reraise": True,
    }
    defaults.update(overrides)
    return defaults


# Pre-built decorator for the common case
api_retry = retry(**retry_config())
