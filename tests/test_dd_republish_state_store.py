from __future__ import annotations

from typing import Any
from urllib.parse import unquote

import pytest
import requests

from due_diligence_reporter.dd_republish_state_store import (
    FirestoreDDRepublishStateStore,
    JsonDDRepublishStateStore,
    build_dd_republish_state_store,
)
from due_diligence_reporter.firestore_state import encode_firestore_fields


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeFirestoreSession:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []

    def get(self, url: str, timeout: int) -> FakeResponse:
        del timeout
        documents = []
        for document_id, fields in self.documents.items():
            documents.append({"name": f"{url}/{document_id}", "fields": fields})
        return FakeResponse(payload={"documents": documents})

    def patch(self, url: str, json: dict[str, Any], timeout: int) -> FakeResponse:
        del timeout
        document_id = unquote(url.rsplit("/", maxsplit=1)[-1])
        self.documents[document_id] = json["fields"]
        return FakeResponse()

    def delete(self, url: str, timeout: int) -> FakeResponse:
        del timeout
        document_id = unquote(url.rsplit("/", maxsplit=1)[-1])
        self.deleted.append(document_id)
        self.documents.pop(document_id, None)
        return FakeResponse()


class FailingFirestoreSession(FakeFirestoreSession):
    def get(self, url: str, timeout: int) -> FakeResponse:
        del url, timeout
        raise requests.ConnectionError("network down")


def test_json_store_preserves_legacy_migration(tmp_path) -> None:
    legacy_path = tmp_path / ".raycon_dd_republish_state.json"
    state_path = tmp_path / ".dd_republish_state.json"
    legacy_path.write_text(
        '{"site-123:rc_run_abc": "2026-05-05T10:00:00+00:00"}',
        encoding="utf-8",
    )

    store = JsonDDRepublishStateStore(state_path, legacy_path=legacy_path)

    assert store.load() == {
        "site-123:raycon_scenario:rc_run_abc": "2026-05-05T10:00:00+00:00"
    }


def test_firestore_load_uses_local_json_when_firestore_is_empty(tmp_path) -> None:
    fallback_path = tmp_path / ".dd_republish_state.json"
    fallback_path.write_text('{"site-1:vendor_sir:f1": "2026-05-05T11:00:00Z"}')
    session = FakeFirestoreSession()
    store = FirestoreDDRepublishStateStore(
        project_id="project",
        fallback=JsonDDRepublishStateStore(fallback_path),
        session=session,
    )

    assert store.load() == {"site-1:vendor_sir:f1": "2026-05-05T11:00:00Z"}


def test_firestore_save_writes_current_state_and_deletes_stale_documents(tmp_path) -> None:
    fallback_path = tmp_path / ".dd_republish_state.json"
    session = FakeFirestoreSession()
    stale_key = "site-1:vendor_sir:stale"
    session.patch(
        f"https://example.test/{stale_key}",
        json={
            "fields": encode_firestore_fields(
                {
                    "state_key": stale_key,
                    "last_republish": "2026-05-01T10:00:00Z",
                }
            )
        },
        timeout=10,
    )
    store = FirestoreDDRepublishStateStore(
        project_id="project",
        fallback=JsonDDRepublishStateStore(fallback_path),
        session=session,
    )

    store.save({"site-2:raycon_scenario:fresh": "2026-05-05T12:00:00Z"})

    loaded = store.load()
    assert loaded == {"site-2:raycon_scenario:fresh": "2026-05-05T12:00:00Z"}
    assert JsonDDRepublishStateStore(fallback_path).load() == loaded
    assert session.deleted


def test_firestore_save_falls_back_to_json_when_firestore_fails(tmp_path) -> None:
    fallback_path = tmp_path / ".dd_republish_state.json"
    store = FirestoreDDRepublishStateStore(
        project_id="project",
        fallback=JsonDDRepublishStateStore(fallback_path),
        session=FailingFirestoreSession(),
    )

    store.save({"site-1:school_approval_report:f1": "2026-05-05T11:00:00Z"})

    assert JsonDDRepublishStateStore(fallback_path).load() == {
        "site-1:school_approval_report:f1": "2026-05-05T11:00:00Z"
    }


def test_builder_keeps_json_store_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("DD_REPUBLISH_STATE_STORE", raising=False)

    store = build_dd_republish_state_store(tmp_path / "state.json")

    assert isinstance(store, JsonDDRepublishStateStore)


def test_builder_falls_back_to_json_without_firestore_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DD_REPUBLISH_STATE_STORE", "firestore")
    monkeypatch.delenv("DD_REPUBLISH_STATE_FIRESTORE_PROJECT_ID", raising=False)

    store = build_dd_republish_state_store(tmp_path / "state.json")

    assert isinstance(store, JsonDDRepublishStateStore)
