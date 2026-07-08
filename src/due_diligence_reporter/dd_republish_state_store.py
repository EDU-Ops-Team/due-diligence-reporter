"""DD Report republish dedupe state storage with optional Firestore durability."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from .dd_republish import (
    DD_REPUBLISH_STATE_PATH,
    LEGACY_DD_REPUBLISH_STATE_PATH,
    load_state,
    save_state,
)
from .firestore_state import (
    DEFAULT_FIRESTORE_DATABASE,
    alert_firestore_fallback,
    build_authorized_session,
    decode_firestore_fields,
    encode_firestore_fields,
)

logger = logging.getLogger(__name__)

DEFAULT_FIRESTORE_COLLECTION = "ddrDDRepublishState"

DDRepublishState = dict[str, str]


class DDRepublishStateStore(Protocol):
    """Storage boundary for DD Report republish dedupe state."""

    def load(self) -> DDRepublishState:
        """Return persisted dedupe state."""

    def save(self, state: DDRepublishState) -> None:
        """Persist dedupe state."""


class JsonDDRepublishStateStore:
    """Republish-state store backed by the existing local JSON file."""

    def __init__(
        self,
        path: Path = DD_REPUBLISH_STATE_PATH,
        *,
        legacy_path: Path = LEGACY_DD_REPUBLISH_STATE_PATH,
    ) -> None:
        self.path = path
        self.legacy_path = legacy_path

    def load(self) -> DDRepublishState:
        return load_state(self.path, legacy_path=self.legacy_path)

    def save(self, state: DDRepublishState) -> None:
        save_state(state, self.path)


class FirestoreDDRepublishStateStore:
    """Firestore-backed republish-state store with local JSON fallback."""

    def __init__(
        self,
        *,
        project_id: str,
        fallback: JsonDDRepublishStateStore,
        collection: str = DEFAULT_FIRESTORE_COLLECTION,
        database: str = DEFAULT_FIRESTORE_DATABASE,
        session: Any | None = None,
    ) -> None:
        self.project_id = project_id.strip()
        self.collection = collection.strip() or DEFAULT_FIRESTORE_COLLECTION
        self.database = database.strip() or DEFAULT_FIRESTORE_DATABASE
        self.fallback = fallback
        self.session = session or build_authorized_session()

    def load(self) -> DDRepublishState:
        try:
            firestore_state = self._load_firestore_state()
        except Exception as exc:  # noqa: BLE001 - local JSON remains the safe fallback
            logger.warning("Failed to load DD republish state from Firestore: %s", exc)
            alert_firestore_fallback("dd_republish_state", "load", exc)
            return self.fallback.load()
        if firestore_state:
            return firestore_state
        return self.fallback.load()

    def save(self, state: DDRepublishState) -> None:
        try:
            self._save_firestore_state(state)
        except Exception as exc:  # noqa: BLE001 - preserve progress locally
            logger.warning("Failed to save DD republish state to Firestore: %s", exc)
            alert_firestore_fallback("dd_republish_state", "save", exc)
            self.fallback.save(state)
            return
        self.fallback.save(state)

    def _load_firestore_state(self) -> DDRepublishState:
        documents = self._list_documents()
        state: DDRepublishState = {}
        for document in documents:
            fields = document.get("fields")
            if not isinstance(fields, dict):
                continue
            entry = decode_firestore_fields(fields)
            key = str(entry.get("state_key") or "").strip()
            last_republish = str(entry.get("last_republish") or "").strip()
            if key and last_republish:
                state[key] = last_republish
        return state

    def _save_firestore_state(self, state: DDRepublishState) -> None:
        existing_ids = self._list_document_ids()
        desired_ids: set[str] = set()
        for state_key, last_republish in state.items():
            document_id = _document_id_for_state_key(state_key)
            desired_ids.add(document_id)
            fields = {
                "state_key": state_key,
                "last_republish": last_republish,
            }
            response = self.session.patch(
                self._document_url(document_id),
                json={"fields": encode_firestore_fields(fields)},
                timeout=10,
            )
            response.raise_for_status()
        for document_id in existing_ids - desired_ids:
            response = self.session.delete(self._document_url(document_id), timeout=10)
            if response.status_code != 404:
                response.raise_for_status()

    def _list_documents(self) -> list[dict[str, Any]]:
        response = self.session.get(self._collection_url(), timeout=10)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return []
        documents = data.get("documents")
        return documents if isinstance(documents, list) else []

    def _list_document_ids(self) -> set[str]:
        ids: set[str] = set()
        for document in self._list_documents():
            name = str(document.get("name") or "").strip()
            document_id = name.rsplit("/", maxsplit=1)[-1].strip()
            if document_id:
                ids.add(document_id)
        return ids

    def _collection_url(self) -> str:
        return (
            "https://firestore.googleapis.com/v1/"
            f"projects/{quote(self.project_id, safe='')}/"
            f"databases/{quote(self.database, safe='')}/"
            f"documents/{quote(self.collection, safe='')}"
        )

    def _document_url(self, document_id: str) -> str:
        return f"{self._collection_url()}/{quote(document_id, safe='')}"


def build_dd_republish_state_store(
    path: Path = DD_REPUBLISH_STATE_PATH,
    *,
    legacy_path: Path = LEGACY_DD_REPUBLISH_STATE_PATH,
) -> DDRepublishStateStore:
    """Return the configured DD republish-state store."""
    fallback = JsonDDRepublishStateStore(path, legacy_path=legacy_path)
    mode = os.getenv("DD_REPUBLISH_STATE_STORE", "json").strip().lower()
    if mode != "firestore":
        return fallback

    project_id = os.getenv("DD_REPUBLISH_STATE_FIRESTORE_PROJECT_ID", "").strip()
    if not project_id:
        logger.warning(
            "DD_REPUBLISH_STATE_STORE=firestore but "
            "DD_REPUBLISH_STATE_FIRESTORE_PROJECT_ID is unset"
        )
        return fallback

    try:
        return FirestoreDDRepublishStateStore(
            project_id=project_id,
            fallback=fallback,
            collection=os.getenv(
                "DD_REPUBLISH_STATE_FIRESTORE_COLLECTION",
                DEFAULT_FIRESTORE_COLLECTION,
            ),
            database=os.getenv(
                "DD_REPUBLISH_STATE_FIRESTORE_DATABASE",
                DEFAULT_FIRESTORE_DATABASE,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - scheduled runs must keep JSON fallback
        logger.warning("Falling back to JSON DD republish state store: %s", exc)
        return fallback


def _document_id_for_state_key(state_key: str) -> str:
    return hashlib.sha256(state_key.strip().encode("utf-8")).hexdigest()
