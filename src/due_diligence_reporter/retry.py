"""Shared retry utilities for external API calls.

Provides a standard retry configuration using ``tenacity`` for all outbound
HTTP requests (Wrike, RayCon, Shovels.ai, OpenAI, Google APIs).
Retries on transient errors: connection errors, timeouts, and HTTP 429/5xx.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger("[retry]")

# Max 3 attempts total (1 initial + 2 retries)
MAX_ATTEMPTS = 3
# Exponential backoff: 1s, 2s, 4s, ... capped at 30s
BACKOFF_MIN = 1
BACKOFF_MAX = 30


_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


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
        "wait": wait_exponential(multiplier=1, min=BACKOFF_MIN, max=BACKOFF_MAX),
        "before_sleep": before_sleep_log(logger, logging.WARNING),
        "reraise": True,
    }
    defaults.update(overrides)
    return defaults


# Pre-built decorator for the common case
api_retry = retry(**retry_config())
