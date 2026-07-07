"""RayCon follow-up runtime state storage with optional Firestore durability."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from .firestore_state import (
    DEFAULT_FIRESTORE_DATABASE,
    alert_firestore_fallback,
    build_authorized_session,
    decode_firestore_fields,
    encode_firestore_fields,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DISPATCH_STATE_PATH = PROJECT_ROOT / ".raycon_dispatch_state.json"
DEFAULT_ALERT_STATE_PATH = PROJECT_ROOT / ".raycon_followup_alerts.json"
DEFAULT_DISPATCH_FIRESTORE_COLLECTION = "ddrRayconDispatchState"
DEFAULT_ALERT_FIRESTORE_COLLECTION = "ddrRayconAlertState"

RayConDispatchState = dict[str, dict[str, Any]]
RayConAlertState = dict[str, str]


class RayConDispatchStateStore(Protocol):
    """Storage boundary for RayCon dispatch dedupe state."""

    def load(self) -> RayConDispatchState:
        """Return persisted dispatch state."""

    def save(self, state: RayConDispatchState) -> None:
        """Persist dispatch state."""


class RayConAlertStateStore(Protocol):
    """Storage boundary for RayCon stuck-site alert dedupe state."""

    def load(self) -> RayConAlertState:
        """Return persisted alert state."""

    def save(self, state: RayConAlertState) -> None:
        """Persist alert state."""


class JsonRayConDispatchStateStore:
    """Dispatch-state store backed by the existing local JSON file."""

    def __init__(self, path: Path = DEFAULT_DISPATCH_STATE_PATH) -> None:
        self.path = path

    def load(self) -> RayConDispatchState:
        try:
            if not self.path.exists():
                return {}
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            return {str(key): value for key, value in data.items() if isinstance(value, dict)}
        except Exception as exc:  # noqa: BLE001 - corrupt state should not block RayCon runs
            logger.warning("Failed to read RayCon dispatch state at %s: %s", self.path, exc)
            return {}

    def save(self, state: RayConDispatchState) -> None:
        self.path.write_text(
            json.dumps(state, sort_keys=True, indent=2),
            encoding="utf-8",
        )


class JsonRayConAlertStateStore:
    """Alert-state store backed by the existing local JSON file."""

    def __init__(self, path: Path = DEFAULT_ALERT_STATE_PATH) -> None:
        self.path = path

    def load(self) -> RayConAlertState:
        try:
            if not self.path.exists():
                return {}
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            return {str(key): str(value) for key, value in data.items()}
        except Exception as exc:  # noqa: BLE001 - corrupt state should not block alerts
            logger.warning("Failed to read RayCon alert state at %s: %s", self.path, exc)
            return {}

    def save(self, state: RayConAlertState) -> None:
        self.path.write_text(
            json.dumps(state, sort_keys=True, indent=2),
            encoding="utf-8",
        )


class FirestoreRayConDispatchStateStore:
    """Firestore-backed RayCon dispatch-state store with local JSON fallback."""

    def __init__(
        self,
        *,
        project_id: str,
        fallback: JsonRayConDispatchStateStore,
        collection: str = DEFAULT_DISPATCH_FIRESTORE_COLLECTION,
        database: str = DEFAULT_FIRESTORE_DATABASE,
        session: Any | None = None,
    ) -> None:
        self.project_id = project_id.strip()
        self.collection = collection.strip() or DEFAULT_DISPATCH_FIRESTORE_COLLECTION
        self.database = database.strip() or DEFAULT_FIRESTORE_DATABASE
        self.fallback = fallback
        self.session = session or build_authorized_session()

    def load(self) -> RayConDispatchState:
        try:
            firestore_state = self._load_firestore_state()
        except Exception as exc:  # noqa: BLE001 - local JSON remains the safe fallback
            logger.warning("Failed to load RayCon dispatch state from Firestore: %s", exc)
            alert_firestore_fallback("raycon_dispatch_state", "load", exc)
            return self.fallback.load()
        if firestore_state:
            return firestore_state
        return self.fallback.load()

    def save(self, state: RayConDispatchState) -> None:
        try:
            self._save_firestore_state(state)
        except Exception as exc:  # noqa: BLE001 - preserve progress locally
            logger.warning("Failed to save RayCon dispatch state to Firestore: %s", exc)
            alert_firestore_fallback("raycon_dispatch_state", "save", exc)
            self.fallback.save(state)
            return
        self.fallback.save(state)

    def _load_firestore_state(self) -> RayConDispatchState:
        state: RayConDispatchState = {}
        for fields in self._iter_document_fields():
            entry = decode_firestore_fields(fields)
            key = str(entry.pop("state_key", "") or "").strip()
            if key:
                state[key] = entry
        return state

    def _save_firestore_state(self, state: RayConDispatchState) -> None:
        existing_ids = self._list_document_ids()
        desired_ids: set[str] = set()
        for state_key, entry in state.items():
            document_id = _document_id_for_state_key(state_key)
            desired_ids.add(document_id)
            fields = dict(entry)
            fields["state_key"] = state_key
            self._patch_document(document_id, fields)
        self._delete_stale_documents(existing_ids - desired_ids)

    def _iter_document_fields(self) -> list[dict[str, Any]]:
        return _list_document_fields(
            self.session,
            self._collection_url(),
        )

    def _list_document_ids(self) -> set[str]:
        return _list_document_ids(self.session, self._collection_url())

    def _patch_document(self, document_id: str, fields: dict[str, Any]) -> None:
        response = self.session.patch(
            self._document_url(document_id),
            json={"fields": encode_firestore_fields(fields)},
            timeout=10,
        )
        response.raise_for_status()

    def _delete_stale_documents(self, document_ids: set[str]) -> None:
        for document_id in document_ids:
            response = self.session.delete(self._document_url(document_id), timeout=10)
            if response.status_code != 404:
                response.raise_for_status()

    def _collection_url(self) -> str:
        return _collection_url(self.project_id, self.database, self.collection)

    def _document_url(self, document_id: str) -> str:
        return f"{self._collection_url()}/{quote(document_id, safe='')}"


class FirestoreRayConAlertStateStore:
    """Firestore-backed RayCon alert-state store with local JSON fallback."""

    def __init__(
        self,
        *,
        project_id: str,
        fallback: JsonRayConAlertStateStore,
        collection: str = DEFAULT_ALERT_FIRESTORE_COLLECTION,
        database: str = DEFAULT_FIRESTORE_DATABASE,
        session: Any | None = None,
    ) -> None:
        self.project_id = project_id.strip()
        self.collection = collection.strip() or DEFAULT_ALERT_FIRESTORE_COLLECTION
        self.database = database.strip() or DEFAULT_FIRESTORE_DATABASE
        self.fallback = fallback
        self.session = session or build_authorized_session()

    def load(self) -> RayConAlertState:
        try:
            firestore_state = self._load_firestore_state()
        except Exception as exc:  # noqa: BLE001 - local JSON remains the safe fallback
            logger.warning("Failed to load RayCon alert state from Firestore: %s", exc)
            alert_firestore_fallback("raycon_alert_state", "load", exc)
            return self.fallback.load()
        if firestore_state:
            return firestore_state
        return self.fallback.load()

    def save(self, state: RayConAlertState) -> None:
        try:
            self._save_firestore_state(state)
        except Exception as exc:  # noqa: BLE001 - preserve progress locally
            logger.warning("Failed to save RayCon alert state to Firestore: %s", exc)
            alert_firestore_fallback("raycon_alert_state", "save", exc)
            self.fallback.save(state)
            return
        self.fallback.save(state)

    def _load_firestore_state(self) -> RayConAlertState:
        state: RayConAlertState = {}
        for fields in self._iter_document_fields():
            entry = decode_firestore_fields(fields)
            key = str(entry.get("state_key") or "").strip()
            last_alert = str(entry.get("last_alert") or "").strip()
            if key and last_alert:
                state[key] = last_alert
        return state

    def _save_firestore_state(self, state: RayConAlertState) -> None:
        existing_ids = self._list_document_ids()
        desired_ids: set[str] = set()
        for state_key, last_alert in state.items():
            document_id = _document_id_for_state_key(state_key)
            desired_ids.add(document_id)
            self._patch_document(
                document_id,
                {
                    "state_key": state_key,
                    "last_alert": last_alert,
                },
            )
        self._delete_stale_documents(existing_ids - desired_ids)

    def _iter_document_fields(self) -> list[dict[str, Any]]:
        return _list_document_fields(
            self.session,
            self._collection_url(),
        )

    def _list_document_ids(self) -> set[str]:
        return _list_document_ids(self.session, self._collection_url())

    def _patch_document(self, document_id: str, fields: dict[str, Any]) -> None:
        response = self.session.patch(
            self._document_url(document_id),
            json={"fields": encode_firestore_fields(fields)},
            timeout=10,
        )
        response.raise_for_status()

    def _delete_stale_documents(self, document_ids: set[str]) -> None:
        for document_id in document_ids:
            response = self.session.delete(self._document_url(document_id), timeout=10)
            if response.status_code != 404:
                response.raise_for_status()

    def _collection_url(self) -> str:
        return _collection_url(self.project_id, self.database, self.collection)

    def _document_url(self, document_id: str) -> str:
        return f"{self._collection_url()}/{quote(document_id, safe='')}"


def build_raycon_dispatch_state_store(
    path: Path = DEFAULT_DISPATCH_STATE_PATH,
) -> RayConDispatchStateStore:
    """Return the configured RayCon dispatch-state store."""
    fallback = JsonRayConDispatchStateStore(path)
    mode = os.getenv("RAYCON_RUNTIME_STATE_STORE", "json").strip().lower()
    if mode != "firestore":
        return fallback

    project_id = os.getenv("RAYCON_RUNTIME_STATE_FIRESTORE_PROJECT_ID", "").strip()
    if not project_id:
        logger.warning(
            "RAYCON_RUNTIME_STATE_STORE=firestore but "
            "RAYCON_RUNTIME_STATE_FIRESTORE_PROJECT_ID is unset"
        )
        return fallback

    try:
        return FirestoreRayConDispatchStateStore(
            project_id=project_id,
            fallback=fallback,
            collection=os.getenv(
                "RAYCON_RUNTIME_STATE_DISPATCH_FIRESTORE_COLLECTION",
                DEFAULT_DISPATCH_FIRESTORE_COLLECTION,
            ),
            database=os.getenv(
                "RAYCON_RUNTIME_STATE_FIRESTORE_DATABASE",
                DEFAULT_FIRESTORE_DATABASE,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - scheduled runs must keep JSON fallback
        logger.warning("Falling back to JSON RayCon dispatch state store: %s", exc)
        return fallback


def build_raycon_alert_state_store(
    path: Path = DEFAULT_ALERT_STATE_PATH,
) -> RayConAlertStateStore:
    """Return the configured RayCon alert-state store."""
    fallback = JsonRayConAlertStateStore(path)
    mode = os.getenv("RAYCON_RUNTIME_STATE_STORE", "json").strip().lower()
    if mode != "firestore":
        return fallback

    project_id = os.getenv("RAYCON_RUNTIME_STATE_FIRESTORE_PROJECT_ID", "").strip()
    if not project_id:
        logger.warning(
            "RAYCON_RUNTIME_STATE_STORE=firestore but "
            "RAYCON_RUNTIME_STATE_FIRESTORE_PROJECT_ID is unset"
        )
        return fallback

    try:
        return FirestoreRayConAlertStateStore(
            project_id=project_id,
            fallback=fallback,
            collection=os.getenv(
                "RAYCON_RUNTIME_STATE_ALERT_FIRESTORE_COLLECTION",
                DEFAULT_ALERT_FIRESTORE_COLLECTION,
            ),
            database=os.getenv(
                "RAYCON_RUNTIME_STATE_FIRESTORE_DATABASE",
                DEFAULT_FIRESTORE_DATABASE,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - scheduled runs must keep JSON fallback
        logger.warning("Falling back to JSON RayCon alert state store: %s", exc)
        return fallback


def _list_document_fields(session: Any, collection_url: str) -> list[dict[str, Any]]:
    response = session.get(collection_url, timeout=10)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        return []
    documents = data.get("documents")
    if not isinstance(documents, list):
        return []
    fields: list[dict[str, Any]] = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        document_fields = document.get("fields")
        if isinstance(document_fields, dict):
            fields.append(document_fields)
    return fields


def _list_document_ids(session: Any, collection_url: str) -> set[str]:
    response = session.get(collection_url, timeout=10)
    if response.status_code == 404:
        return set()
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        return set()
    documents = data.get("documents")
    if not isinstance(documents, list):
        return set()
    ids: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            continue
        name = str(document.get("name") or "").strip()
        document_id = name.rsplit("/", maxsplit=1)[-1].strip()
        if document_id:
            ids.add(document_id)
    return ids


def _collection_url(project_id: str, database: str, collection: str) -> str:
    return (
        "https://firestore.googleapis.com/v1/"
        f"projects/{quote(project_id, safe='')}/"
        f"databases/{quote(database, safe='')}/"
        f"documents/{quote(collection, safe='')}"
    )


def _document_id_for_state_key(state_key: str) -> str:
    return hashlib.sha256(state_key.strip().encode("utf-8")).hexdigest()
