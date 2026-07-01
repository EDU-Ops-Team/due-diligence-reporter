"""Repo-owned M2 event intake and resume state for AADP handoffs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from .firestore_state import (
    DEFAULT_FIRESTORE_DATABASE,
    build_authorized_session,
    decode_firestore_fields,
    encode_firestore_fields,
)
from .source_packet import (
    REGISTERED_DOCUMENT_STATUSES,
    SourceDocumentRef,
    build_m2_source_packet,
)
from .source_types import canonical_source_type

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

M2_EVENT_SCHEMA_VERSION = "aadp.site_ready_for_ddr.v1"
SOURCE_AVAILABLE_SCHEMA_VERSION = "ddr.source_available.v1"
M2_STATE_SCHEMA_VERSION = "ddr.m2_state.v1"
DEFAULT_M2_STATE_PATH = PROJECT_ROOT / ".m2_direct_dd_state.json"
DEFAULT_M2_STATE_FIRESTORE_COLLECTION = "ddrM2DirectDdState"
DEFAULT_M2_EVENT_FIRESTORE_COLLECTION = "m2DirectDdEvents"

EVENT_STATUSES = frozenset({"pending", "processing", "completed", "blocked", "failed"})
OPEN_M2_STATES = frozenset(
    {
        "waiting_for_capacity_source",
        "capacity_ready",
        "capacity_written",
        "waiting_for_external_sources",
        "source_packet_ready",
        "dd_write_pending",
        "blocked",
    }
)
CAPACITY_SOURCE_TYPES = frozenset(
    {
        "alpha_capacity_analysis",
        "block_plan",
        "capacity_calculation",
        "fastest_open_block_plan",
        "floor_plan",
        "lidar",
        "max_capacity_block_plan",
        "measured_floor_plan",
        "bim",
    }
)
OCCUPANCY_SOURCE_TYPES = frozenset({"certificate_of_occupancy", "permit_of_record", "permit"})
SQUARE_FOOTAGE_SOURCE_TYPES = frozenset({"floor_plan", "measured_floor_plan", "bim", "lidar"})
TRAFFIC_SOURCE_TYPES = frozenset({"traffic_analysis"})
PHASING_BUILD_CONTEXT_SOURCE_TYPES = frozenset(
    {
        "alpha_phasing_plan_report",
        "bid_cost_estimate",
        "construction_budget",
        "cost_timeline_estimate",
        "initial_cost_estimate",
        "phasing_plan",
    }
)
SECURITY_DUE_DILIGENCE_SOURCE_TYPES = frozenset({"security_due_diligence_report"})

DocumentLister = Callable[[str, str], list[dict[str, Any]]]


class M2EventValidationError(ValueError):
    """Raised when an AADP M2 handoff event is incomplete or unverified."""


class M2EventQueueError(RuntimeError):
    """Raised when the Firestore event queue is not configured."""


class M2StateStore(Protocol):
    """Storage boundary for DDR-owned M2 handoff state."""

    def load(self) -> dict[str, dict[str, Any]]:
        """Return persisted M2 state keyed by event ID."""

    def save(self, state: dict[str, dict[str, Any]]) -> None:
        """Persist M2 state keyed by event ID."""


class JsonM2StateStore:
    """M2 state store backed by a local JSON file."""

    def __init__(self, path: Path = DEFAULT_M2_STATE_PATH) -> None:
        self.path = path

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - corrupt local state should not block runs
            logger.warning("Ignoring unreadable M2 state at %s: %s", self.path, exc)
            return {}
        return _coerce_state_map(payload)

    def save(self, state: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(_coerce_state_map(state), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class FirestoreM2StateStore:
    """Firestore-backed M2 state store with local JSON fallback."""

    def __init__(
        self,
        *,
        project_id: str,
        fallback: JsonM2StateStore,
        collection: str = DEFAULT_M2_STATE_FIRESTORE_COLLECTION,
        database: str = DEFAULT_FIRESTORE_DATABASE,
        session: Any | None = None,
    ) -> None:
        self.project_id = project_id.strip()
        self.collection = collection.strip() or DEFAULT_M2_STATE_FIRESTORE_COLLECTION
        self.database = database.strip() or DEFAULT_FIRESTORE_DATABASE
        self.fallback = fallback
        self.session = session or build_authorized_session()

    def load(self) -> dict[str, dict[str, Any]]:
        try:
            firestore_state = self._load_firestore_state()
        except Exception as exc:  # noqa: BLE001 - local JSON remains the safe fallback
            logger.warning("Failed to load M2 state from Firestore: %s", exc)
            return self.fallback.load()
        if firestore_state:
            return firestore_state
        return self.fallback.load()

    def save(self, state: dict[str, dict[str, Any]]) -> None:
        clean_state = _coerce_state_map(state)
        try:
            self._save_firestore_state(clean_state)
        except Exception as exc:  # noqa: BLE001 - preserve progress locally
            logger.warning("Failed to save M2 state to Firestore: %s", exc)
            self.fallback.save(clean_state)
            return
        self.fallback.save(clean_state)

    def _load_firestore_state(self) -> dict[str, dict[str, Any]]:
        documents = self._list_documents()
        state: dict[str, dict[str, Any]] = {}
        for document in documents:
            fields = document.get("fields")
            if not isinstance(fields, dict):
                continue
            entry = decode_firestore_fields(fields)
            event_id = _text(entry.get("event_id"))
            if event_id:
                state[event_id] = entry
        return state

    def _save_firestore_state(self, state: dict[str, dict[str, Any]]) -> None:
        existing_ids = self._list_document_ids()
        desired_ids: set[str] = set()
        for event_id, entry in state.items():
            document_id = _document_id_for_key(event_id)
            desired_ids.add(document_id)
            fields = dict(entry)
            fields["event_id"] = event_id
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
            document_id = _document_id_from_document(document)
            if document_id:
                ids.add(document_id)
        return ids

    def _collection_url(self) -> str:
        return _firestore_collection_url(
            project_id=self.project_id,
            database=self.database,
            collection=self.collection,
        )

    def _document_url(self, document_id: str) -> str:
        return f"{self._collection_url()}/{quote(document_id, safe='')}"


class FirestoreM2EventQueue:
    """Firestore queue reader/writer for ``m2DirectDdEvents``."""

    def __init__(
        self,
        *,
        project_id: str,
        collection: str = DEFAULT_M2_EVENT_FIRESTORE_COLLECTION,
        database: str = DEFAULT_FIRESTORE_DATABASE,
        session: Any | None = None,
    ) -> None:
        self.project_id = project_id.strip()
        self.collection = collection.strip() or DEFAULT_M2_EVENT_FIRESTORE_COLLECTION
        self.database = database.strip() or DEFAULT_FIRESTORE_DATABASE
        self.session = session or build_authorized_session()

    def pending_events(
        self,
        *,
        limit: int = 10,
        site_id: str = "",
        event_id: str = "",
        schema_version: str = M2_EVENT_SCHEMA_VERSION,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        target_site_id = _text(site_id)
        target_event_id = _text(event_id)
        target_schema_version = _text(schema_version)
        for document in self._list_documents():
            fields = document.get("fields")
            if not isinstance(fields, dict):
                continue
            event = decode_firestore_fields(fields)
            if target_schema_version and _text(event.get("schema_version")) != target_schema_version:
                continue
            if _text(event.get("status")) != "pending":
                continue
            if target_event_id and _text(event.get("event_id")) != target_event_id:
                continue
            site = event.get("site")
            event_site_id = _text(site.get("id")) if isinstance(site, dict) else ""
            if target_site_id and event_site_id != target_site_id:
                continue
            document_id = _document_id_from_document(document)
            if document_id:
                event["_firestore_document_id"] = document_id
            events.append(event)
        return sorted(events, key=lambda item: _text(item.get("event_id")))[: max(limit, 0)]

    def pending_source_events(
        self,
        *,
        limit: int = 50,
        site_id: str = "",
    ) -> list[dict[str, Any]]:
        return self.pending_events(
            limit=limit,
            site_id=site_id,
            schema_version=SOURCE_AVAILABLE_SCHEMA_VERSION,
        )

    def write_event(self, event: dict[str, Any]) -> None:
        event_id = _text(event.get("event_id"))
        if not event_id:
            raise M2EventQueueError("Cannot write Firestore event without event_id")
        payload = {
            key: value
            for key, value in event.items()
            if isinstance(key, str) and not key.startswith("_")
        }
        response = self.session.patch(
            self._document_url(event_id),
            json={"fields": encode_firestore_fields(payload)},
            timeout=10,
        )
        response.raise_for_status()

    def update_event_status(
        self,
        event: dict[str, Any],
        status: str,
        result: dict[str, Any],
    ) -> None:
        if status not in EVENT_STATUSES:
            raise ValueError(f"Unsupported M2 event status: {status}")
        document_id = _text(event.get("_firestore_document_id")) or _text(event.get("event_id"))
        if not document_id:
            raise M2EventQueueError("Cannot update Firestore event without a document ID")
        updated = {
            key: value
            for key, value in event.items()
            if isinstance(key, str) and not key.startswith("_")
        }
        updated["status"] = status
        updated["ddr_result"] = result
        updated["ddr_updated_at"] = _utc_now_iso()
        response = self.session.patch(
            self._document_url(document_id),
            json={"fields": encode_firestore_fields(updated)},
            timeout=10,
        )
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

    def _collection_url(self) -> str:
        return _firestore_collection_url(
            project_id=self.project_id,
            database=self.database,
            collection=self.collection,
        )

    def _document_url(self, document_id: str) -> str:
        return f"{self._collection_url()}/{quote(document_id, safe='')}"


def build_m2_state_store(path: Path = DEFAULT_M2_STATE_PATH) -> M2StateStore:
    """Return the configured M2 state store."""

    fallback = JsonM2StateStore(path)
    mode = os.getenv("M2_DD_STATE_STORE", "json").strip().lower()
    if mode != "firestore":
        return fallback

    project_id = os.getenv("M2_DD_STATE_FIRESTORE_PROJECT_ID", "").strip()
    if not project_id:
        logger.warning(
            "M2_DD_STATE_STORE=firestore but M2_DD_STATE_FIRESTORE_PROJECT_ID is unset"
        )
        return fallback

    try:
        return FirestoreM2StateStore(
            project_id=project_id,
            fallback=fallback,
            collection=os.getenv(
                "M2_DD_STATE_FIRESTORE_COLLECTION",
                DEFAULT_M2_STATE_FIRESTORE_COLLECTION,
            ),
            database=os.getenv(
                "M2_DD_STATE_FIRESTORE_DATABASE",
                DEFAULT_FIRESTORE_DATABASE,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - scheduled runs must keep JSON fallback
        logger.warning("Falling back to JSON M2 state store: %s", exc)
        return fallback


def build_m2_event_queue_from_env() -> FirestoreM2EventQueue:
    """Build the Firestore queue configured for AADP -> DDR M2 events."""

    project_id = (
        os.getenv("M2_DD_EVENT_FIRESTORE_PROJECT_ID", "").strip()
        or os.getenv("M2_DD_STATE_FIRESTORE_PROJECT_ID", "").strip()
        or os.getenv("DD_REPUBLISH_STATE_FIRESTORE_PROJECT_ID", "").strip()
    )
    if not project_id:
        raise M2EventQueueError("M2_DD_EVENT_FIRESTORE_PROJECT_ID is required")
    return FirestoreM2EventQueue(
        project_id=project_id,
        collection=os.getenv(
            "M2_DD_EVENT_FIRESTORE_COLLECTION",
            DEFAULT_M2_EVENT_FIRESTORE_COLLECTION,
        ),
        database=os.getenv(
            "M2_DD_EVENT_FIRESTORE_DATABASE",
            os.getenv("M2_DD_STATE_FIRESTORE_DATABASE", DEFAULT_FIRESTORE_DATABASE),
        ),
    )


def validate_site_ready_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one ``aadp.site_ready_for_ddr.v1`` event."""

    if not isinstance(payload, dict):
        raise M2EventValidationError("event payload must be an object")
    schema = _text(payload.get("schema_version"))
    if schema != M2_EVENT_SCHEMA_VERSION:
        raise M2EventValidationError(
            f"schema_version must be {M2_EVENT_SCHEMA_VERSION}"
        )
    event_id = _text(payload.get("event_id"))
    if not event_id:
        raise M2EventValidationError("event_id is required")
    status = _text(payload.get("status"))
    if status not in EVENT_STATUSES:
        raise M2EventValidationError("status must be one of pending, processing, completed, blocked, failed")
    if payload.get("ready_for_ddr") is not True:
        raise M2EventValidationError("ready_for_ddr must be true")

    site = payload.get("site")
    if not isinstance(site, dict):
        raise M2EventValidationError("site must be an object")
    site_id = _text(site.get("id") or site.get("site_id") or site.get("siteId"))
    site_name = _text(site.get("name") or site.get("title") or site.get("site_name"))
    if not site_id:
        raise M2EventValidationError("site.id is required")
    if not site_name:
        raise M2EventValidationError("site.name is required")

    drive = payload.get("drive")
    if not isinstance(drive, dict):
        raise M2EventValidationError("drive must be an object")
    site_folder_url = _text(
        drive.get("site_folder_url")
        or drive.get("siteFolderUrl")
        or drive.get("site_folder")
    )
    m1_folder_url = _text(
        drive.get("m1_folder_url")
        or drive.get("m1FolderUrl")
        or drive.get("m1_folder")
    )
    if not site_folder_url:
        raise M2EventValidationError("drive.site_folder_url is required")
    if not m1_folder_url:
        raise M2EventValidationError("drive.m1_folder_url is required")

    docs = payload.get("registered_documents")
    if not isinstance(docs, list):
        raise M2EventValidationError("registered_documents must be an array")
    normalized_docs = [_normalize_registered_document(doc) for doc in docs if isinstance(doc, dict)]
    if len(normalized_docs) != len(docs):
        raise M2EventValidationError("registered_documents entries must be objects")
    _validate_required_handoff_docs(normalized_docs)

    event = dict(payload)
    event["event_id"] = event_id
    event["status"] = status
    event["site"] = {
        "id": site_id,
        "name": site_name,
        "address": _text(site.get("address") or site.get("site_address")),
        "site_record_url": _text(site.get("site_record_url") or site.get("url")),
    }
    event["drive"] = {
        "site_folder_url": site_folder_url,
        "m1_folder_url": m1_folder_url,
    }
    event["registered_documents"] = normalized_docs
    event["remaining_work"] = _list_of_dicts_or_strings(payload.get("remaining_work"))
    event["aadp_receipt"] = payload.get("aadp_receipt") if isinstance(payload.get("aadp_receipt"), dict) else {}
    return event


def build_source_available_event(
    *,
    site: Mapping[str, Any],
    source_type: str,
    document: Mapping[str, Any],
    producer: Mapping[str, Any],
    fingerprint: str = "",
    event_id: str = "",
    status: str = "pending",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build the canonical event emitted when DDR source evidence becomes available."""

    clean_site_id = _first_text(site.get("id"), site.get("site_id"), site.get("siteId"))
    clean_source_type = _canonical_source_type(source_type)
    clean_fingerprint = _text(fingerprint) or _source_event_fingerprint(
        site_id=clean_site_id,
        source_type=clean_source_type,
        document=document,
    )
    event = {
        "schema_version": SOURCE_AVAILABLE_SCHEMA_VERSION,
        "event_id": _text(event_id)
        or _source_available_event_id(
            site_id=clean_site_id,
            source_type=clean_source_type,
            fingerprint=clean_fingerprint,
        ),
        "status": _text(status) or "pending",
        "site": dict(site),
        "source_type": clean_source_type,
        "document": dict(document),
        "producer": dict(producer),
        "fingerprint": clean_fingerprint,
        "created_at": created_at or _utc_now_iso(),
    }
    return validate_source_available_event(event)


def emit_source_available_event(
    event_queue: FirestoreM2EventQueue,
    *,
    site: Mapping[str, Any],
    source_type: str,
    document: Mapping[str, Any],
    producer: Mapping[str, Any],
    fingerprint: str = "",
    event_id: str = "",
    status: str = "pending",
    created_at: str | None = None,
    apply: bool = True,
) -> dict[str, Any]:
    """Validate and optionally enqueue a canonical DDR source-available event."""

    event = build_source_available_event(
        site=site,
        source_type=source_type,
        document=document,
        producer=producer,
        fingerprint=fingerprint,
        event_id=event_id,
        status=status,
        created_at=created_at,
    )
    if apply:
        event_queue.write_event(event)
    return event


def source_available_event_from_observation(
    *,
    site: Mapping[str, Any],
    observation: Mapping[str, Any],
    producer: Mapping[str, Any],
    registration_status: str = "pending_user_action",
    readback_status: str = "not_verified",
    status: str = "pending",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Convert a Drive/source observation into the canonical source-event contract."""

    source_type = _event_source_type(dict(observation))
    document = {
        "title": _first_text(
            observation.get("file_name"),
            observation.get("name"),
            source_type.replace("_", " ").title(),
        ),
        "drive_url": _first_text(observation.get("drive_url"), observation.get("webViewLink")),
        "drive_file_id": _first_text(
            observation.get("drive_file_id"),
            observation.get("file_id"),
            observation.get("id"),
        ),
        "rhodes_doc_type": _rhodes_doc_type_for_source(source_type),
        "registration_status": registration_status,
        "readback_status": readback_status,
        "readback_verified": readback_status in {"verified", "readback_verified", "found"},
    }
    normalized_site = {
        "id": _first_text(site.get("id"), site.get("site_id"), site.get("siteId")),
        "name": _first_text(site.get("name"), site.get("title"), site.get("site_name")),
        "address": _first_text(site.get("address"), site.get("site_address")),
        "site_record_url": _first_text(site.get("site_record_url"), site.get("url")),
    }
    return build_source_available_event(
        site=normalized_site,
        source_type=source_type,
        document=document,
        producer=producer,
        fingerprint=_text(observation.get("fingerprint")),
        event_id=_text(observation.get("event_id")),
        status=status,
        created_at=created_at,
    )


def validate_source_available_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one ``ddr.source_available.v1`` event."""

    if not isinstance(payload, dict):
        raise M2EventValidationError("source event payload must be an object")
    schema = _text(payload.get("schema_version"))
    if schema != SOURCE_AVAILABLE_SCHEMA_VERSION:
        raise M2EventValidationError(
            f"schema_version must be {SOURCE_AVAILABLE_SCHEMA_VERSION}"
        )
    event_id = _text(payload.get("event_id"))
    if not event_id:
        raise M2EventValidationError("event_id is required")
    status = _text(payload.get("status"))
    if status not in EVENT_STATUSES:
        raise M2EventValidationError("status must be one of pending, processing, completed, blocked, failed")
    source_type = _canonical_source_type(_text(payload.get("source_type") or payload.get("sourceType")))
    if not source_type:
        raise M2EventValidationError("source_type is required")
    fingerprint = _text(payload.get("fingerprint"))
    if not fingerprint:
        raise M2EventValidationError("fingerprint is required")
    created_at = _text(payload.get("created_at") or payload.get("createdAt"))
    if not created_at:
        raise M2EventValidationError("created_at is required")

    site = payload.get("site")
    if not isinstance(site, dict):
        raise M2EventValidationError("site must be an object")
    site_id = _first_text(site.get("id"), site.get("site_id"), site.get("siteId"))
    site_name = _first_text(site.get("name"), site.get("title"), site.get("site_name"))
    if not site_id:
        raise M2EventValidationError("site.id is required")
    if not site_name:
        raise M2EventValidationError("site.name is required")

    document = payload.get("document")
    if not isinstance(document, dict):
        raise M2EventValidationError("document must be an object")
    document_title = _first_text(document.get("title"), document.get("name"), payload.get("file_name"))
    drive_file_id = _first_text(
        document.get("drive_file_id"),
        document.get("driveFileId"),
        document.get("file_id"),
        document.get("fileId"),
        payload.get("drive_file_id"),
        payload.get("id"),
    )
    drive_url = _first_text(
        document.get("drive_url"),
        document.get("driveUrl"),
        document.get("url"),
        document.get("link"),
        payload.get("drive_url"),
        payload.get("webViewLink"),
    )
    if not document_title:
        raise M2EventValidationError("document.title is required")
    if not (drive_file_id or drive_url):
        raise M2EventValidationError("document.drive_file_id or document.drive_url is required")

    registration_status = _first_text(
        document.get("registration_status"),
        document.get("registrationStatus"),
        payload.get("registration_status"),
    )
    if not registration_status:
        raise M2EventValidationError("document.registration_status is required")
    readback_status = _first_text(
        document.get("readback_status"),
        document.get("readbackStatus"),
        payload.get("readback_status"),
    )
    producer = payload.get("producer")
    if not isinstance(producer, dict):
        raise M2EventValidationError("producer must be an object")
    if not _first_text(producer.get("workflow"), producer.get("run_id"), producer.get("runId")):
        raise M2EventValidationError("producer.workflow or producer.run_id is required")

    normalized = dict(payload)
    normalized["event_id"] = event_id
    normalized["status"] = status
    normalized["site"] = {
        "id": site_id,
        "name": site_name,
        "address": _first_text(site.get("address"), site.get("site_address")),
        "site_record_url": _first_text(site.get("site_record_url"), site.get("url")),
    }
    normalized["source_type"] = source_type
    normalized["document"] = {
        "title": document_title,
        "drive_url": drive_url,
        "drive_file_id": drive_file_id,
        "rhodes_doc_type": _first_text(
            document.get("rhodes_doc_type"),
            document.get("rhodesDocType"),
            document.get("doc_type"),
            document.get("docType"),
        ),
        "rhodes_milestone": _first_text(
            document.get("rhodes_milestone"),
            document.get("rhodesMilestone"),
            document.get("milestone"),
        ),
        "registration_status": registration_status,
        "readback_status": readback_status,
        "readback_verified": bool(document.get("readback_verified") or document.get("readbackVerified")),
    }
    normalized["producer"] = {
        "workflow": _text(producer.get("workflow")),
        "run_id": _first_text(producer.get("run_id"), producer.get("runId")),
        "artifact_type": _first_text(producer.get("artifact_type"), producer.get("artifactType")),
    }
    normalized["fingerprint"] = fingerprint
    normalized["created_at"] = created_at
    normalized["doc_type"] = _first_text(payload.get("doc_type"), normalized["document"]["rhodes_doc_type"])
    normalized["drive_file_id"] = drive_file_id
    normalized["drive_url"] = drive_url
    normalized["file_name"] = document_title
    normalized["registration_status"] = registration_status
    normalized["readback_status"] = readback_status
    normalized["readback_verified"] = normalized["document"]["readback_verified"]
    return normalized


def consume_site_ready_event(
    payload: dict[str, Any],
    *,
    state_store: M2StateStore | None = None,
    apply: bool = True,
    verify_rhodes_readback: bool = False,
    document_lister: DocumentLister | None = None,
) -> dict[str, Any]:
    """Validate an AADP handoff event, initialize M2 state, and optionally persist it."""

    event = validate_site_ready_event(payload)
    rhodes_readback = {"status": "not_checked"}
    if verify_rhodes_readback:
        if document_lister is None:
            raise M2EventValidationError(
                "document_lister is required when verify_rhodes_readback is true"
            )
        rhodes_readback = verify_required_documents_from_rhodes(event, document_lister)
        if rhodes_readback["status"] != "verified":
            raise M2EventValidationError(
                f"required Rhodes documents not verified: {', '.join(rhodes_readback['missing'])}"
            )

    m2_state = initialize_m2_state(event, rhodes_readback=rhodes_readback)
    if apply and state_store is not None:
        state = state_store.load()
        state[event["event_id"]] = m2_state
        state_store.save(state)
    return m2_state_summary(m2_state)


def initialize_m2_state(
    event: dict[str, Any],
    *,
    rhodes_readback: dict[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Build DDR-owned M2 state from a validated AADP event."""

    validated = validate_site_ready_event(event)
    timestamp = now or _utc_now_iso()
    supporting_documents = [
        _source_document_ref_from_event_doc(doc).to_dict()
        for doc in validated["registered_documents"]
    ]
    source_packet = build_m2_source_packet(
        values={},
        supporting_documents=supporting_documents,
    )
    open_blockers = _initial_blockers(validated["registered_documents"])
    m2_state = _state_name_from_blockers(open_blockers)
    return {
        "schema_version": M2_STATE_SCHEMA_VERSION,
        "event_id": validated["event_id"],
        "aadp_schema_version": validated["schema_version"],
        "aadp_receipt": validated.get("aadp_receipt", {}),
        "site": validated["site"],
        "drive": validated["drive"],
        "registered_documents": validated["registered_documents"],
        "rhodes_readback": rhodes_readback or {"status": "not_checked"},
        "m2_state": m2_state,
        "status": "complete" if m2_state == "complete" else "blocked",
        "open_blockers": open_blockers,
        "remaining_work": validated.get("remaining_work", []),
        "source_packet": source_packet,
        "source_events": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def verify_required_documents_from_rhodes(
    event: dict[str, Any],
    document_lister: DocumentLister,
) -> dict[str, Any]:
    """Confirm SIR and School Approval registrations from Rhodes readback."""

    validated = validate_site_ready_event(event)
    site_id = validated["site"]["id"]
    docs_by_source = {
        _normalized_source_type(doc): doc for doc in validated["registered_documents"]
    }
    checks = (
        ("sir", "siteInvestigationReport"),
        ("school_approval_report", "regulatoryApproval"),
    )
    missing: list[str] = []
    verified: list[dict[str, str]] = []
    for source_type, rhodes_doc_type in checks:
        event_doc = docs_by_source[source_type]
        documents = document_lister(site_id, rhodes_doc_type)
        matched_document = _matching_rhodes_document(event_doc, documents)
        if matched_document is None:
            missing.append(source_type)
            continue
        verified.append(
            {
                "source_type": source_type,
                "rhodes_doc_type": rhodes_doc_type,
                "title": _text(matched_document.get("title") or matched_document.get("name")),
                "drive_file_id": _first_text(
                    matched_document.get("driveFileId"),
                    matched_document.get("drive_file_id"),
                    matched_document.get("fileId"),
                    matched_document.get("file_id"),
                ),
            }
        )
    return {
        "status": "verified" if not missing else "missing",
        "site_id": site_id,
        "verified": verified,
        "missing": missing,
    }


def _matching_rhodes_document(
    event_doc: dict[str, Any],
    rhodes_documents: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    target_file_id = _text(event_doc.get("drive_file_id"))
    target_url = _text(event_doc.get("drive_url"))
    target_title = _text(event_doc.get("title")).casefold()
    for document in rhodes_documents:
        file_id = _first_text(
            document.get("driveFileId"),
            document.get("drive_file_id"),
            document.get("fileId"),
            document.get("file_id"),
        )
        if target_file_id and file_id == target_file_id:
            return document
        drive_url = _first_text(
            document.get("driveUrl"),
            document.get("drive_url"),
            document.get("url"),
        )
        if target_url and drive_url == target_url:
            return document
        title = _text(document.get("title") or document.get("name")).casefold()
        if target_title and title == target_title:
            return document
    return None


def poll_m2_events(
    *,
    event_queue: FirestoreM2EventQueue,
    state_store: M2StateStore,
    apply: bool = False,
    limit: int = 10,
    verify_rhodes_readback: bool = False,
    document_lister: DocumentLister | None = None,
    site_id: str = "",
    event_id: str = "",
) -> dict[str, Any]:
    """Consume pending Firestore M2 events and update event/state status."""

    target_event_id = event_id
    events = event_queue.pending_events(
        limit=limit,
        site_id=site_id,
        event_id=target_event_id,
    )
    rows: list[dict[str, Any]] = []
    for event in events:
        pending_event_id = _text(event.get("event_id"))
        if apply:
            event_queue.update_event_status(
                event,
                "processing",
                {"event_id": pending_event_id, "started_at": _utc_now_iso()},
            )
        try:
            result = consume_site_ready_event(
                event,
                state_store=state_store,
                apply=apply,
                verify_rhodes_readback=verify_rhodes_readback,
                document_lister=document_lister,
            )
        except M2EventValidationError as exc:
            result = {
                "event_id": pending_event_id,
                "status": "failed",
                "error": str(exc),
            }
            if apply:
                event_queue.update_event_status(event, "failed", result)
            rows.append(result)
            continue

        event_status = "completed" if result["m2_state"] == "complete" else "blocked"
        result["event_status"] = event_status
        if apply:
            event_queue.update_event_status(event, event_status, result)
        rows.append(result)

    return {
        "status": "success",
        "apply": apply,
        "filters": m2_filter_summary(site_id=site_id, event_id=target_event_id),
        "events_found": len(events),
        "events_processed": len(rows),
        "blocked": sum(1 for row in rows if row.get("event_status") == "blocked"),
        "failed": sum(1 for row in rows if row.get("status") == "failed"),
        "rows": rows,
    }


def watch_m2_sources(
    *,
    state_store: M2StateStore,
    source_events_by_site: Mapping[str, Sequence[dict[str, Any]]],
    apply: bool = False,
    now: str | None = None,
    site_id: str = "",
    event_id: str = "",
) -> dict[str, Any]:
    """Resume only open M2 states whose current blockers match new source events."""

    timestamp = now or _utc_now_iso()
    state = state_store.load()
    rows: list[dict[str, Any]] = []
    changed = False
    for state_event_id, entry in state.items():
        if not m2_state_is_open(entry):
            continue
        if not m2_state_matches_filters(
            state_event_id,
            entry,
            site_id=site_id,
            event_id=event_id,
        ):
            continue
        raw_site = entry.get("site")
        site = raw_site if isinstance(raw_site, dict) else {}
        entry_site_id = _text(site.get("id"))
        events = list(source_events_by_site.get(entry_site_id, ()))
        updated, row = advance_m2_state_with_source_events(
            entry,
            events,
            now=timestamp,
        )
        row["event_id"] = state_event_id
        row["site_id"] = entry_site_id
        rows.append(row)
        if row.get("state_changed", row["resumed"]):
            changed = True
            if apply:
                state[state_event_id] = updated

    if apply and changed:
        state_store.save(state)

    return {
        "status": "success",
        "apply": apply,
        "filters": m2_filter_summary(site_id=site_id, event_id=event_id),
        "open_states_checked": len(rows),
        "resumed": sum(1 for row in rows if row["resumed"]),
        "source_events_consumed": sum(len(row.get("matched_source_event_ids", [])) for row in rows),
        "source_events_blocked": sum(len(row.get("blocked_source_event_ids", [])) for row in rows),
        "rows": rows,
    }


def watch_m2_source_event_queue(
    *,
    event_queue: FirestoreM2EventQueue,
    state_store: M2StateStore,
    fallback_source_events_by_site: Mapping[str, Sequence[dict[str, Any]]] | None = None,
    apply: bool = False,
    limit: int = 50,
    now: str | None = None,
    site_id: str = "",
    event_id: str = "",
) -> dict[str, Any]:
    """Consume canonical source events, update M2 state, and acknowledge handled events."""

    pending_events = event_queue.pending_source_events(limit=limit, site_id=site_id)
    valid_events: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []
    for event in pending_events:
        try:
            valid_events.append(validate_source_available_event(event))
        except M2EventValidationError as exc:
            result = {
                "event_id": _text(event.get("event_id")),
                "status": "failed",
                "error": str(exc),
            }
            invalid_rows.append(result)
            if apply:
                event_queue.update_event_status(event, "failed", result)

    events_by_site: dict[str, list[dict[str, Any]]] = {}
    for site, events in (fallback_source_events_by_site or {}).items():
        events_by_site[_text(site)] = [dict(event) for event in events if isinstance(event, dict)]
    for event in valid_events:
        raw_site = event.get("site")
        event_site_id = _text(raw_site.get("id")) if isinstance(raw_site, dict) else ""
        if not event_site_id:
            continue
        events_by_site.setdefault(event_site_id, []).append(event)

    watch_result = watch_m2_sources(
        state_store=state_store,
        source_events_by_site=events_by_site,
        apply=apply,
        now=now,
        site_id=site_id,
        event_id=event_id,
    )
    completed_ids = {
        event_id
        for row in watch_result.get("rows", [])
        for event_id in _string_list(row.get("matched_source_event_ids"))
    }
    blocked_ids = {
        event_id
        for row in watch_result.get("rows", [])
        for event_id in _string_list(row.get("blocked_source_event_ids"))
    }
    valid_by_id = {_text(event.get("event_id")): event for event in valid_events}
    if apply:
        for source_event_id in sorted(completed_ids):
            source_event = valid_by_id.get(source_event_id)
            if source_event is not None:
                event_queue.update_event_status(
                    source_event,
                    "completed",
                    {"event_id": source_event_id, "status": "completed"},
                )
        for source_event_id in sorted(blocked_ids):
            source_event = valid_by_id.get(source_event_id)
            if source_event is not None:
                event_queue.update_event_status(
                    source_event,
                    "blocked",
                    {
                        "event_id": source_event_id,
                        "status": "blocked",
                        "next_action": "document_registration_handoff",
                    },
                )

    pending_ids = set(valid_by_id) - completed_ids - blocked_ids
    return {
        **watch_result,
        "source_event_queue": {
            "status": "success",
            "schema_version": SOURCE_AVAILABLE_SCHEMA_VERSION,
            "events_found": len(pending_events),
            "events_valid": len(valid_events),
            "events_invalid": len(invalid_rows),
            "events_completed": len(completed_ids),
            "events_blocked": len(blocked_ids),
            "events_pending": len(pending_ids),
            "invalid_rows": invalid_rows,
        },
    }


def advance_m2_state_with_source_events(
    state: dict[str, Any],
    source_events: Sequence[dict[str, Any]],
    *,
    now: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Advance a single M2 state when a matching resume source has arrived."""

    updated = dict(state)
    blockers = _list_of_dicts(updated.get("open_blockers"))
    matched_events: list[dict[str, Any]] = []
    blocked_events: list[dict[str, Any]] = []
    remaining_blockers: list[dict[str, Any]] = []
    for blocker in blockers:
        matching = _matching_resume_events(blocker, source_events)
        verified_matching = [event for event in matching if _source_event_is_verified(event)]
        blocked_matching = [event for event in matching if not _source_event_is_verified(event)]
        if verified_matching:
            matched_events.extend(verified_matching)
            continue
        if blocked_matching:
            blocked_events.extend(blocked_matching)
            continue
        remaining_blockers.append(blocker)

    if blocked_events:
        prior_events = _list_of_dicts(updated.get("source_events"))
        updated["source_events"] = _dedupe_source_events([*prior_events, *blocked_events])
        handoff_blockers = [
            _document_registration_handoff_blocker(event)
            for event in blocked_events
            if _event_source_type(event)
        ]
        next_blockers = _dedupe_blockers([*remaining_blockers, *handoff_blockers])
        updated["open_blockers"] = next_blockers
        updated["m2_state"] = _state_name_from_blockers(next_blockers)
        updated["status"] = "blocked"
        updated["updated_at"] = now or _utc_now_iso()
        return updated, {
            "resumed": False,
            "state_changed": True,
            "m2_state": updated["m2_state"],
            "matched_source_types": [],
            "matched_source_event_ids": [],
            "blocked_source_event_ids": _source_event_ids(blocked_events),
            "next_actions": _next_actions_for_blockers(next_blockers),
        }

    if not matched_events:
        return updated, {
            "resumed": False,
            "state_changed": False,
            "m2_state": _text(updated.get("m2_state")),
            "matched_source_types": [],
            "matched_source_event_ids": [],
            "blocked_source_event_ids": [],
            "next_actions": _next_actions_for_blockers(blockers),
        }

    prior_events = _list_of_dicts(updated.get("source_events"))
    updated["source_events"] = _dedupe_source_events([*prior_events, *matched_events])
    for event in matched_events:
        source_type = _event_source_type(event)
        if source_type:
            updated["registered_documents"] = _merge_registered_document_event(
                _list_of_dicts(updated.get("registered_documents")),
                event,
            )

    next_blockers = _followup_blockers_after_resume(remaining_blockers, matched_events)
    updated["open_blockers"] = next_blockers
    updated["m2_state"] = _state_name_from_blockers(next_blockers)
    updated["status"] = "complete" if updated["m2_state"] == "complete" else "blocked"
    updated["updated_at"] = now or _utc_now_iso()
    return updated, {
        "resumed": True,
        "state_changed": True,
        "m2_state": updated["m2_state"],
        "matched_source_types": sorted({_event_source_type(event) for event in matched_events}),
        "matched_source_event_ids": _source_event_ids(matched_events),
        "blocked_source_event_ids": [],
        "next_actions": _next_actions_for_blockers(next_blockers),
    }


def m2_state_is_open(state: dict[str, Any]) -> bool:
    """Return True when a persisted M2 state should be watched for source arrivals."""

    return _text(state.get("m2_state")) in OPEN_M2_STATES


def open_m2_site_ids(
    state: dict[str, dict[str, Any]],
    *,
    site_id: str = "",
    event_id: str = "",
) -> list[str]:
    """Return unique Rhodes site IDs with open M2 state."""

    site_ids: list[str] = []
    seen: set[str] = set()
    for state_event_id, entry in state.items():
        if not m2_state_is_open(entry):
            continue
        if not m2_state_matches_filters(
            state_event_id,
            entry,
            site_id=site_id,
            event_id=event_id,
        ):
            continue
        site = entry.get("site")
        entry_site_id = _text(site.get("id")) if isinstance(site, dict) else ""
        if not entry_site_id or entry_site_id in seen:
            continue
        seen.add(entry_site_id)
        site_ids.append(entry_site_id)
    return site_ids


def m2_state_matches_filters(
    state_event_id: str,
    state: dict[str, Any],
    *,
    site_id: str = "",
    event_id: str = "",
) -> bool:
    """Return True when an M2 state matches optional canary selectors."""

    target_event_id = _text(event_id)
    if target_event_id and _text(state_event_id) != target_event_id:
        return False
    target_site_id = _text(site_id)
    if target_site_id:
        site = state.get("site")
        entry_site_id = _text(site.get("id")) if isinstance(site, dict) else ""
        if entry_site_id != target_site_id:
            return False
    return True


def m2_filter_summary(*, site_id: str = "", event_id: str = "") -> dict[str, str]:
    """Return normalized M2 canary selector metadata for operator artifacts."""

    return {
        "site_id": _text(site_id),
        "event_id": _text(event_id),
    }


def m2_state_summary(state: dict[str, Any]) -> dict[str, Any]:
    """Return an operator-safe summary for CLI and event status updates."""

    raw_site = state.get("site")
    site = raw_site if isinstance(raw_site, dict) else {}
    raw_source_packet = state.get("source_packet")
    source_packet = raw_source_packet if isinstance(raw_source_packet, dict) else {}
    blockers = _list_of_dicts(state.get("open_blockers"))
    return {
        "status": _text(state.get("status")) or "blocked",
        "event_id": _text(state.get("event_id")),
        "site": {
            "id": _text(site.get("id")),
            "name": _text(site.get("name")),
            "address": _text(site.get("address")),
        },
        "m2_state": _text(state.get("m2_state")),
        "open_blockers": blockers,
        "next_actions": _next_actions_for_blockers(blockers),
        "source_packet_status": _text(source_packet.get("status")),
        "source_packet_open_items": source_packet.get("open_items")
        if isinstance(source_packet.get("open_items"), list)
        else [],
    }


def _validate_required_handoff_docs(docs: Sequence[dict[str, Any]]) -> None:
    missing: list[str] = []
    for source_type in ("sir", "school_approval_report"):
        candidates = [doc for doc in docs if _normalized_source_type(doc) == source_type]
        if not candidates:
            missing.append(source_type)
            continue
        if not any(_document_is_verified(doc) for doc in candidates):
            missing.append(f"{source_type}: unverified")
    if missing:
        raise M2EventValidationError(
            f"required source documents are missing or unverified: {', '.join(missing)}"
        )


def _normalize_registered_document(doc: dict[str, Any]) -> dict[str, Any]:
    source_type = _normalized_source_type(doc)
    registration_status = _text(
        doc.get("registration_status")
        or doc.get("registrationStatus")
        or doc.get("registration")
        or doc.get("status")
    )
    readback_status = _text(
        doc.get("readback_status")
        or doc.get("readbackStatus")
        or doc.get("registration_readback_status")
        or doc.get("registrationReadbackStatus")
    )
    return {
        "source_type": source_type,
        "title": _text(doc.get("title") or doc.get("name")) or source_type.replace("_", " ").title(),
        "rhodes_doc_type": _text(
            doc.get("rhodes_doc_type")
            or doc.get("rhodesDocType")
            or doc.get("doc_type")
            or doc.get("docType")
        ),
        "drive_url": _text(
            doc.get("drive_url")
            or doc.get("driveUrl")
            or doc.get("url")
            or doc.get("link")
        ),
        "drive_file_id": _text(
            doc.get("drive_file_id")
            or doc.get("driveFileId")
            or doc.get("file_id")
            or doc.get("fileId")
        ),
        "registration_status": registration_status,
        "readback_status": readback_status,
        "readback_verified": bool(doc.get("readback_verified") or doc.get("readbackVerified")),
        "fields_supported": _string_list(
            doc.get("fields_supported") or doc.get("fieldsSupported")
        ),
    }


def _normalized_source_type(doc: dict[str, Any]) -> str:
    explicit = _text(doc.get("source_type") or doc.get("sourceType"))
    if explicit:
        return _canonical_source_type(explicit)
    rhodes_doc_type = _text(doc.get("rhodes_doc_type") or doc.get("rhodesDocType") or doc.get("doc_type") or doc.get("docType"))
    title = _text(doc.get("title") or doc.get("name")).casefold()
    if rhodes_doc_type == "siteInvestigationReport":
        return "sir"
    if rhodes_doc_type == "regulatoryApproval" and "school approval" in title:
        return "school_approval_report"
    if rhodes_doc_type == "capacityCalculation":
        return "alpha_capacity_analysis"
    if rhodes_doc_type == "floorPlan":
        return "floor_plan"
    if rhodes_doc_type == "other" and "security due diligence" in title:
        return "security_due_diligence_report"
    if rhodes_doc_type == "lidar":
        return "lidar"
    if rhodes_doc_type == "certificateOfOccupancy":
        return "certificate_of_occupancy"
    if rhodes_doc_type == "permit":
        return "permit_of_record"
    return _canonical_source_type(rhodes_doc_type)


def _canonical_source_type(value: str) -> str:
    return canonical_source_type(value)


def _document_is_verified(doc: dict[str, Any]) -> bool:
    registration_status = _text(doc.get("registration_status"))
    readback_status = _text(doc.get("readback_status"))
    return registration_status in REGISTERED_DOCUMENT_STATUSES and (
        readback_status in {"verified", "readback_verified", "found"}
        or doc.get("readback_verified") is True
    )


def _source_document_ref_from_event_doc(doc: dict[str, Any]) -> SourceDocumentRef:
    normalized = _normalize_registered_document(doc)
    return SourceDocumentRef(
        source_type=normalized["source_type"],
        title=normalized["title"],
        drive_url=normalized["drive_url"],
        drive_file_id=normalized["drive_file_id"],
        rhodes_doc_type=normalized["rhodes_doc_type"],
        registration_status=normalized["registration_status"],
        fields_supported=tuple(normalized["fields_supported"]),
    )


def _initial_blockers(docs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    source_types = {_normalized_source_type(doc) for doc in docs if _document_is_verified(doc)}
    if not (source_types & CAPACITY_SOURCE_TYPES):
        return [
            _blocker(
                "missing_capacity_source",
                "waiting_for_capacity_source",
                "Waiting for block plan, measured floor plan, BIM, LiDAR, or capacity document.",
                CAPACITY_SOURCE_TYPES,
                "run_alpha_capacity_analysis",
            )
        ]
    if "alpha_capacity_analysis" not in source_types:
        return [
            _blocker(
                "run_alpha_capacity_analysis",
                "capacity_ready",
                "Capacity source is present; run alpha-capacity-analysis before DD writes.",
                {"alpha_capacity_analysis"},
                "write_capacity_fields",
            )
        ]
    return [
        _blocker(
            "capacity_write_readback_pending",
            "capacity_ready",
            "Alpha Capacity Analysis is present; write and read back FO/Max capacity fields.",
            {"alpha_capacity_analysis"},
            "write_capacity_fields",
        )
    ]


def _state_name_from_blockers(blockers: Sequence[dict[str, Any]]) -> str:
    if not blockers:
        return "complete"
    for preferred in (
        "waiting_for_capacity_source",
        "capacity_ready",
        "capacity_written",
        "waiting_for_external_sources",
        "source_packet_ready",
        "dd_write_pending",
    ):
        if any(_text(blocker.get("m2_state")) == preferred for blocker in blockers):
            return preferred
    return "blocked"


def _blocker(
    blocker_id: str,
    m2_state: str,
    reason: str,
    resume_source_types: Iterable[str],
    next_action: str,
) -> dict[str, Any]:
    return {
        "id": blocker_id,
        "m2_state": m2_state,
        "reason": reason,
        "resume_source_types": sorted({_canonical_source_type(value) for value in resume_source_types}),
        "next_action": next_action,
    }


def _document_registration_handoff_blocker(event: dict[str, Any]) -> dict[str, Any]:
    source_type = _event_source_type(event)
    document = event.get("document")
    event_doc = document if isinstance(document, dict) else {}
    title = _first_text(event_doc.get("title"), event.get("file_name"), source_type)
    registration_status = _first_text(
        event_doc.get("registration_status"),
        event.get("registration_status"),
        "unknown",
    )
    readback_status = _first_text(
        event_doc.get("readback_status"),
        event.get("readback_status"),
        "not_verified",
    )
    return _blocker(
        f"document_registration_handoff:{source_type}",
        "blocked",
        (
            f"{title} arrived as {source_type}, but Rhodes registration/readback "
            f"is not verified ({registration_status}/{readback_status})."
        ),
        {source_type},
        "document_registration_handoff",
    )


def _matching_resume_events(
    blocker: dict[str, Any],
    source_events: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    resume_types = set(_string_list(blocker.get("resume_source_types")))
    if not resume_types:
        return []
    return [
        event
        for event in source_events
        if _event_source_type(event) in resume_types
        or _canonical_source_type(_text(event.get("doc_type"))) in resume_types
    ]


def _followup_blockers_after_resume(
    remaining_blockers: list[dict[str, Any]],
    matched_events: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    matched_types = {_event_source_type(event) for event in matched_events}
    next_blockers = list(remaining_blockers)
    if matched_types & (CAPACITY_SOURCE_TYPES - {"alpha_capacity_analysis"}):
        next_blockers.append(
            _blocker(
                "run_alpha_capacity_analysis",
                "capacity_ready",
                "Capacity source arrived; run alpha-capacity-analysis.",
                {"alpha_capacity_analysis"},
                "run_alpha_capacity_analysis",
            )
        )
    if "alpha_capacity_analysis" in matched_types and not any(
        _text(blocker.get("id")) == "capacity_write_readback_pending"
        for blocker in next_blockers
    ):
        next_blockers.append(
            _blocker(
                "capacity_write_readback_pending",
                "capacity_ready",
                "Alpha Capacity Analysis arrived; write and read back FO/Max capacity fields.",
                {"alpha_capacity_analysis"},
                "write_capacity_fields",
            )
        )
    if matched_types & SECURITY_DUE_DILIGENCE_SOURCE_TYPES:
        next_blockers.append(
            _blocker(
                "build_m2_source_packet",
                "source_packet_ready",
                "Security Due Diligence memo arrived; build source packet.",
                set(),
                "build_m2_source_packet",
            )
        )
    return _dedupe_blockers(next_blockers)


def _next_actions_for_blockers(blockers: Sequence[dict[str, Any]]) -> list[str]:
    return _dedupe([_text(blocker.get("next_action")) for blocker in blockers if _text(blocker.get("next_action"))])


def _merge_registered_document_event(
    docs: list[dict[str, Any]],
    event: dict[str, Any],
) -> list[dict[str, Any]]:
    source_type = _event_source_type(event)
    if not source_type:
        return docs
    raw_document = event.get("document")
    event_doc = raw_document if isinstance(raw_document, dict) else {}
    document = {
        "source_type": source_type,
        "title": _first_text(
            event_doc.get("title"),
            event.get("file_name"),
            event.get("name"),
            source_type.replace("_", " ").title(),
        ),
        "rhodes_doc_type": _first_text(
            event_doc.get("rhodes_doc_type"),
            event.get("rhodes_doc_type"),
            _rhodes_doc_type_for_source(source_type),
        ),
        "drive_url": _first_text(event_doc.get("drive_url"), event.get("drive_url"), event.get("webViewLink")),
        "drive_file_id": _first_text(event_doc.get("drive_file_id"), event.get("drive_file_id"), event.get("id")),
        "registration_status": _first_text(
            event_doc.get("registration_status"),
            event.get("registration_status"),
            "registered",
        ),
        "readback_status": _first_text(
            event_doc.get("readback_status"),
            event.get("readback_status"),
            "verified",
        ),
        "readback_verified": bool(
            event_doc.get("readback_verified")
            or event_doc.get("readbackVerified")
            or event.get("readback_verified")
            or event.get("readbackVerified")
        ),
        "fields_supported": [],
    }
    filtered = [doc for doc in docs if _normalized_source_type(doc) != source_type]
    filtered.append(document)
    return filtered


def _rhodes_doc_type_for_source(source_type: str) -> str:
    mapping = {
        "sir": "siteInvestigationReport",
        "school_approval_report": "regulatoryApproval",
        "alpha_capacity_analysis": "capacityCalculation",
        "block_plan": "fastestOpenBlockPlan",
        "cost_timeline_estimate": "initialCostEstimate",
        "floor_plan": "floorPlan",
        "security_due_diligence_report": "other",
        "lidar": "lidar",
        "certificate_of_occupancy": "certificateOfOccupancy",
        "permit": "permit",
        "permit_of_record": "permit",
        "traffic_analysis": "other",
    }
    return mapping.get(source_type, "other")


def _event_source_type(event: dict[str, Any]) -> str:
    return _canonical_source_type(
        _text(event.get("source_type") or event.get("sourceType") or event.get("doc_type"))
    )


def _source_event_is_verified(event: dict[str, Any]) -> bool:
    if _text(event.get("schema_version")) != SOURCE_AVAILABLE_SCHEMA_VERSION:
        return True
    raw_document = event.get("document")
    document = raw_document if isinstance(raw_document, dict) else {}
    registration_status = _first_text(
        document.get("registration_status"),
        event.get("registration_status"),
    )
    readback_status = _first_text(
        document.get("readback_status"),
        event.get("readback_status"),
    )
    readback_verified = bool(
        document.get("readback_verified")
        or document.get("readbackVerified")
        or event.get("readback_verified")
        or event.get("readbackVerified")
    )
    return registration_status in REGISTERED_DOCUMENT_STATUSES and (
        readback_status in {"verified", "readback_verified", "found"}
        or readback_verified
    )


def _source_event_ids(events: Sequence[dict[str, Any]]) -> list[str]:
    return _dedupe(
        [
            _text(event.get("event_id"))
            or _source_available_event_id(
                site_id=_source_event_site_id(event),
                source_type=_event_source_type(event),
                fingerprint=_text(event.get("fingerprint")),
            )
            for event in events
        ]
    )


def _source_event_site_id(event: dict[str, Any]) -> str:
    site = event.get("site")
    return _text(site.get("id")) if isinstance(site, dict) else ""


def _dedupe_source_events(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for event in events:
        key = "|".join([
            _text(event.get("event_id")),
            _event_source_type(event),
            _text(event.get("fingerprint")),
            _text(event.get("drive_file_id") or event.get("id")),
        ])
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(event))
    return result


def _dedupe_blockers(blockers: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for blocker in blockers:
        blocker_id = _text(blocker.get("id"))
        if blocker_id in seen:
            continue
        seen.add(blocker_id)
        result.append(blocker)
    return result


def _coerce_state_map(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    state: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        event_id = _text(value.get("event_id")) or key
        state[event_id] = dict(value)
    return state


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _list_of_dicts_or_strings(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, (dict, str))]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _source_available_event_id(*, site_id: str, source_type: str, fingerprint: str) -> str:
    raw_key = "|".join([_text(site_id), _canonical_source_type(source_type), _text(fingerprint)])
    return f"ddr-source-{hashlib.sha256(raw_key.encode('utf-8')).hexdigest()[:24]}"


def _source_event_fingerprint(
    *,
    site_id: str,
    source_type: str,
    document: Mapping[str, Any],
) -> str:
    return "|".join(
        [
            _text(site_id),
            _canonical_source_type(source_type),
            _first_text(
                document.get("drive_file_id"),
                document.get("driveFileId"),
                document.get("file_id"),
                document.get("fileId"),
            ),
            _first_text(document.get("drive_modified_time"), document.get("modifiedTime")),
            _first_text(document.get("drive_url"), document.get("driveUrl"), document.get("url")),
        ]
    )


def _document_id_for_key(key: str) -> str:
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()


def _document_id_from_document(document: dict[str, Any]) -> str:
    name = _text(document.get("name"))
    return name.rsplit("/", maxsplit=1)[-1].strip() if name else ""


def _firestore_collection_url(
    *,
    project_id: str,
    database: str,
    collection: str,
) -> str:
    return (
        "https://firestore.googleapis.com/v1/"
        f"projects/{quote(project_id, safe='')}/"
        f"databases/{quote(database, safe='')}/"
        f"documents/{quote(collection, safe='')}"
    )
