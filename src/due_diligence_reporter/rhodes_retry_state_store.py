"""Rhodes registration retry state storage with optional Firestore durability."""

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

DEFAULT_FIRESTORE_COLLECTION = "ddrRhodesRegistrationRetryState"

RhodesRetryState = dict[str, dict[str, Any]]


class RhodesRetryStateStore(Protocol):
    """Storage boundary for Rhodes document-registration retry state."""

    def load(self) -> RhodesRetryState:
        """Return persisted retry state."""

    def save(self, state: RhodesRetryState) -> None:
        """Persist retry state."""


class JsonRhodesRetryStateStore:
    """Retry-state store backed by the existing local JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> RhodesRetryState:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - corrupt state should not block filing
            logger.warning("Ignoring unreadable Rhodes retry state at %s: %s", self.path, exc)
            return {}
        return _coerce_retry_state(payload)

    def save(self, state: RhodesRetryState) -> None:
        self.path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class FirestoreRhodesRetryStateStore:
    """Firestore-backed retry-state store with local JSON fallback.

    The scanner still mutates one in-memory dict during a run. This store makes
    that dict durable at run boundaries without changing the filing code path.
    """

    def __init__(
        self,
        *,
        project_id: str,
        fallback: JsonRhodesRetryStateStore,
        collection: str = DEFAULT_FIRESTORE_COLLECTION,
        database: str = DEFAULT_FIRESTORE_DATABASE,
        session: Any | None = None,
    ) -> None:
        self.project_id = project_id.strip()
        self.collection = collection.strip() or DEFAULT_FIRESTORE_COLLECTION
        self.database = database.strip() or DEFAULT_FIRESTORE_DATABASE
        self.fallback = fallback
        self.session = session or build_authorized_session()

    def load(self) -> RhodesRetryState:
        try:
            firestore_state = self._load_firestore_state()
        except Exception as exc:  # noqa: BLE001 - local JSON remains the safe fallback
            logger.warning("Failed to load Rhodes retry state from Firestore: %s", exc)
            alert_firestore_fallback("rhodes_retry_state", "load", exc)
            return self.fallback.load()
        if firestore_state:
            return firestore_state
        return self.fallback.load()

    def save(self, state: RhodesRetryState) -> None:
        try:
            self._save_firestore_state(state)
        except Exception as exc:  # noqa: BLE001 - preserve progress locally
            logger.warning("Failed to save Rhodes retry state to Firestore: %s", exc)
            alert_firestore_fallback("rhodes_retry_state", "save", exc)
            self.fallback.save(state)
            return
        self.fallback.save(state)

    def _load_firestore_state(self) -> RhodesRetryState:
        documents = self._list_documents()
        state: RhodesRetryState = {}
        for document in documents:
            fields = document.get("fields")
            if not isinstance(fields, dict):
                continue
            entry = decode_firestore_fields(fields)
            key = str(entry.pop("retry_key", "") or "").strip()
            if key:
                state[key] = entry
        return state

    def _save_firestore_state(self, state: RhodesRetryState) -> None:
        existing_ids = self._list_document_ids()
        desired_ids: set[str] = set()
        for retry_key, entry in state.items():
            document_id = _document_id_for_retry_key(retry_key)
            desired_ids.add(document_id)
            fields = dict(entry)
            fields["retry_key"] = retry_key
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


def build_rhodes_retry_state_store(
    path: Path,
) -> RhodesRetryStateStore:
    """Return the configured Rhodes retry-state store.

    Firestore is opt-in. Local development and tests keep the existing JSON
    behavior unless production explicitly enables the durable store.
    """
    fallback = JsonRhodesRetryStateStore(path)
    mode = os.getenv("RHODES_RETRY_STATE_STORE", "json").strip().lower()
    if mode != "firestore":
        return fallback

    project_id = os.getenv("RHODES_RETRY_STATE_FIRESTORE_PROJECT_ID", "").strip()
    if not project_id:
        logger.warning(
            "RHODES_RETRY_STATE_STORE=firestore but "
            "RHODES_RETRY_STATE_FIRESTORE_PROJECT_ID is unset"
        )
        return fallback

    try:
        return FirestoreRhodesRetryStateStore(
            project_id=project_id,
            fallback=fallback,
            collection=os.getenv(
                "RHODES_RETRY_STATE_FIRESTORE_COLLECTION",
                DEFAULT_FIRESTORE_COLLECTION,
            ),
            database=os.getenv(
                "RHODES_RETRY_STATE_FIRESTORE_DATABASE",
                DEFAULT_FIRESTORE_DATABASE,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - scanner must still run with JSON fallback
        logger.warning("Falling back to JSON Rhodes retry state store: %s", exc)
        return fallback


def _coerce_retry_state(payload: Any) -> RhodesRetryState:
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, dict)
    }


def _document_id_for_retry_key(retry_key: str) -> str:
    return hashlib.sha256(retry_key.strip().encode("utf-8")).hexdigest()
