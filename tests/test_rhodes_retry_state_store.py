from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote

import pytest
import requests

from due_diligence_reporter.rhodes_retry_state_store import (
    FirestoreRhodesRetryStateStore,
    JsonRhodesRetryStateStore,
    build_rhodes_retry_state_store,
    decode_firestore_fields,
    encode_firestore_fields,
)


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


def test_json_store_loads_valid_dict_and_ignores_non_dict_values(tmp_path) -> None:
    path = tmp_path / ".rhodes_registration_retry_state.json"
    path.write_text(
        json.dumps({"SITE|sir|a.pdf": {"attempts": 1}, "bad": "not a dict"}),
        encoding="utf-8",
    )

    assert JsonRhodesRetryStateStore(path).load() == {
        "SITE|sir|a.pdf": {"attempts": 1}
    }


def test_json_store_ignores_corrupt_file(tmp_path) -> None:
    path = tmp_path / ".rhodes_registration_retry_state.json"
    path.write_text("{not-json", encoding="utf-8")

    assert JsonRhodesRetryStateStore(path).load() == {}


def test_firestore_field_encoding_round_trips_nested_state() -> None:
    payload = {
        "retry_key": "SITE|isp|file.pdf",
        "attempts": 3,
        "retry_exhausted": True,
        "last_error": None,
        "notes": {"owner": "P1", "scores": [1, 2.5]},
    }

    assert decode_firestore_fields(encode_firestore_fields(payload)) == payload


def test_firestore_load_uses_local_json_when_firestore_is_empty(tmp_path) -> None:
    fallback_path = tmp_path / ".rhodes_registration_retry_state.json"
    fallback_path.write_text(
        json.dumps({"SITE|sir|local.pdf": {"attempts": 1}}),
        encoding="utf-8",
    )
    session = FakeFirestoreSession()
    store = FirestoreRhodesRetryStateStore(
        project_id="project",
        fallback=JsonRhodesRetryStateStore(fallback_path),
        session=session,
    )

    assert store.load() == {"SITE|sir|local.pdf": {"attempts": 1}}


def test_firestore_save_writes_current_state_and_deletes_stale_documents(tmp_path) -> None:
    fallback_path = tmp_path / ".rhodes_registration_retry_state.json"
    session = FakeFirestoreSession()
    stale_key = "SITE|sir|stale.pdf"
    session.patch(
        f"https://example.test/{stale_key}",
        json={
            "fields": encode_firestore_fields(
                {"retry_key": stale_key, "attempts": 2}
            )
        },
        timeout=10,
    )
    store = FirestoreRhodesRetryStateStore(
        project_id="project",
        fallback=JsonRhodesRetryStateStore(fallback_path),
        session=session,
    )

    store.save({"SITE|isp|fresh.pdf": {"attempts": 1, "drive_file_id": "file-1"}})

    loaded = store.load()
    assert loaded == {"SITE|isp|fresh.pdf": {"attempts": 1, "drive_file_id": "file-1"}}
    assert JsonRhodesRetryStateStore(fallback_path).load() == loaded
    assert session.deleted


def test_firestore_save_falls_back_to_json_when_firestore_fails(tmp_path) -> None:
    fallback_path = tmp_path / ".rhodes_registration_retry_state.json"
    store = FirestoreRhodesRetryStateStore(
        project_id="project",
        fallback=JsonRhodesRetryStateStore(fallback_path),
        session=FailingFirestoreSession(),
    )

    store.save({"SITE|isp|file.pdf": {"attempts": 1}})

    assert JsonRhodesRetryStateStore(fallback_path).load() == {
        "SITE|isp|file.pdf": {"attempts": 1}
    }


def test_builder_keeps_json_store_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("RHODES_RETRY_STATE_STORE", raising=False)

    store = build_rhodes_retry_state_store(tmp_path / "state.json")

    assert isinstance(store, JsonRhodesRetryStateStore)


def test_builder_falls_back_to_json_without_firestore_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("RHODES_RETRY_STATE_STORE", "firestore")
    monkeypatch.delenv("RHODES_RETRY_STATE_FIRESTORE_PROJECT_ID", raising=False)

    store = build_rhodes_retry_state_store(tmp_path / "state.json")

    assert isinstance(store, JsonRhodesRetryStateStore)
