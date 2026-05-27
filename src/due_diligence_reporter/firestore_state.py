"""Shared Firestore REST helpers for durable automation state stores."""

from __future__ import annotations

from typing import Any

import google.auth
from google.auth.transport.requests import AuthorizedSession

FIRESTORE_SCOPE = "https://www.googleapis.com/auth/datastore"
DEFAULT_FIRESTORE_DATABASE = "(default)"


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
