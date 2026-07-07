"""Shared Firestore REST helpers for durable automation state stores."""

from __future__ import annotations

import logging
import os
from typing import Any

import google.auth
from google.auth.transport.requests import AuthorizedSession

FIRESTORE_SCOPE = "https://www.googleapis.com/auth/datastore"
DEFAULT_FIRESTORE_DATABASE = "(default)"

logger = logging.getLogger(__name__)

_FALLBACK_ALERTS_SENT: set[str] = set()


def alert_firestore_fallback(store_name: str, operation: str, error: Exception | str) -> None:
    """Post a Google Chat alert when a Firestore state store degrades to local fallback.

    Fallback degradation is otherwise silent: runs keep succeeding on the
    JSON/cache fallback while durable dedupe and progress state quietly stops
    persisting (this ran unnoticed for six weeks before 2026-07-07). One alert
    per store+operation per process keeps scheduled runs from spamming the
    webhook; the alert must never break the run it fires from.
    """

    key = f"{store_name}:{operation}"
    if key in _FALLBACK_ALERTS_SENT:
        return
    _FALLBACK_ALERTS_SENT.add(key)
    webhook_url = os.environ.get("GOOGLE_CHAT_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return
    message = (
        f"DDR state-store degradation: {store_name} failed to {operation} "
        f"Firestore state and is running on the local JSON/cache fallback. "
        f"Durable dedupe/progress state stops persisting until Firestore "
        f"access is repaired. Error: {str(error)[:300]}"
    )
    try:
        from .utils import post_google_chat_message

        post_google_chat_message(webhook_url, message)
    except Exception:  # noqa: BLE001 - alerting must never break the run
        logger.warning("Failed to post Firestore fallback alert for %s", key)


def encode_firestore_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {key: encode_firestore_value(value) for key, value in data.items()}


def encode_firestore_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, dict):
        return {"mapValue": {"fields": encode_firestore_fields(value)}}
    if isinstance(value, list):
        return {"arrayValue": {"values": [encode_firestore_value(item) for item in value]}}
    return {"stringValue": str(value)}


def decode_firestore_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: decode_firestore_value(value) for key, value in fields.items()}


def decode_firestore_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    if "nullValue" in value:
        return None
    if "booleanValue" in value:
        return bool(value["booleanValue"])
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "stringValue" in value:
        return str(value["stringValue"])
    if "mapValue" in value:
        map_value = value.get("mapValue")
        fields = map_value.get("fields", {}) if isinstance(map_value, dict) else {}
        return decode_firestore_fields(fields) if isinstance(fields, dict) else {}
    if "arrayValue" in value:
        array_value = value.get("arrayValue")
        values = array_value.get("values", []) if isinstance(array_value, dict) else []
        return [decode_firestore_value(item) for item in values if isinstance(item, dict)]
    return None


def build_authorized_session() -> AuthorizedSession:
    credentials, _project_id = google.auth.default(scopes=[FIRESTORE_SCOPE])
    return AuthorizedSession(credentials)
