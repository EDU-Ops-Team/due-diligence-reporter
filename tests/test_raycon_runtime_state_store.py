from __future__ import annotations

from typing import Any
from urllib.parse import unquote

import pytest
import requests

from due_diligence_reporter.firestore_state import encode_firestore_fields
from due_diligence_reporter.raycon_runtime_state_store import (
    FirestoreRayConAlertStateStore,
    FirestoreRayConDispatchStateStore,
    JsonRayConAlertStateStore,
    JsonRayConDispatchStateStore,
    build_raycon_alert_state_store,
    build_raycon_dispatch_state_store,
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


def test_json_dispatch_store_loads_valid_dict_and_ignores_non_dict_values(tmp_path) -> None:
    path = tmp_path / ".raycon_dispatch_state.json"
    path.write_text(
        '{"bp-1": {"count": 1}, "bad": "not a dict"}',
        encoding="utf-8",
    )

    assert JsonRayConDispatchStateStore(path).load() == {"bp-1": {"count": 1}}


def test_json_alert_store_loads_stringified_values(tmp_path) -> None:
    path = tmp_path / ".raycon_followup_alerts.json"
    path.write_text('{"Alpha Keller": "2026-05-05T10:00:00Z"}', encoding="utf-8")

    assert JsonRayConAlertStateStore(path).load() == {
        "Alpha Keller": "2026-05-05T10:00:00Z"
    }


def test_firestore_dispatch_save_writes_current_state_and_deletes_stale_documents(
    tmp_path,
) -> None:
    fallback_path = tmp_path / ".raycon_dispatch_state.json"
    session = FakeFirestoreSession()
    stale_key = "stale-bp"
    session.patch(
        f"https://example.test/{stale_key}",
        json={
            "fields": encode_firestore_fields(
                {"state_key": stale_key, "count": 1, "site": "Old Site"}
            )
        },
        timeout=10,
    )
    store = FirestoreRayConDispatchStateStore(
        project_id="project",
        fallback=JsonRayConDispatchStateStore(fallback_path),
        session=session,
    )

    state = {
        "bp-1": {
            "last_dispatch": "2026-05-05T10:00:00Z",
            "count": 2,
            "site": "Alpha Keller",
            "raycon_run_id": "run-1",
        }
    }
    store.save(state)

    assert store.load() == state
    assert JsonRayConDispatchStateStore(fallback_path).load() == state
    assert session.deleted


def test_firestore_alert_save_writes_current_state_and_deletes_stale_documents(
    tmp_path,
) -> None:
    fallback_path = tmp_path / ".raycon_followup_alerts.json"
    session = FakeFirestoreSession()
    stale_key = "Old Site"
    session.patch(
        f"https://example.test/{stale_key}",
        json={
            "fields": encode_firestore_fields(
                {"state_key": stale_key, "last_alert": "2026-05-01T10:00:00Z"}
            )
        },
        timeout=10,
    )
    store = FirestoreRayConAlertStateStore(
        project_id="project",
        fallback=JsonRayConAlertStateStore(fallback_path),
        session=session,
    )

    state = {"Alpha Keller": "2026-05-05T10:00:00Z"}
    store.save(state)

    assert store.load() == state
    assert JsonRayConAlertStateStore(fallback_path).load() == state
    assert session.deleted


def test_firestore_dispatch_save_falls_back_to_json_when_firestore_fails(tmp_path) -> None:
    fallback_path = tmp_path / ".raycon_dispatch_state.json"
    store = FirestoreRayConDispatchStateStore(
        project_id="project",
        fallback=JsonRayConDispatchStateStore(fallback_path),
        session=FailingFirestoreSession(),
    )

    state = {"bp-1": {"count": 1, "site": "Alpha Keller"}}
    store.save(state)

    assert JsonRayConDispatchStateStore(fallback_path).load() == state


def test_firestore_alert_save_falls_back_to_json_when_firestore_fails(tmp_path) -> None:
    fallback_path = tmp_path / ".raycon_followup_alerts.json"
    store = FirestoreRayConAlertStateStore(
        project_id="project",
        fallback=JsonRayConAlertStateStore(fallback_path),
        session=FailingFirestoreSession(),
    )

    state = {"Alpha Keller": "2026-05-05T10:00:00Z"}
    store.save(state)

    assert JsonRayConAlertStateStore(fallback_path).load() == state


def test_builders_keep_json_stores_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("RAYCON_RUNTIME_STATE_STORE", raising=False)

    dispatch_store = build_raycon_dispatch_state_store(tmp_path / "dispatch.json")
    alert_store = build_raycon_alert_state_store(tmp_path / "alerts.json")

    assert isinstance(dispatch_store, JsonRayConDispatchStateStore)
    assert isinstance(alert_store, JsonRayConAlertStateStore)


def test_builders_fall_back_to_json_without_firestore_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("RAYCON_RUNTIME_STATE_STORE", "firestore")
    monkeypatch.delenv("RAYCON_RUNTIME_STATE_FIRESTORE_PROJECT_ID", raising=False)

    dispatch_store = build_raycon_dispatch_state_store(tmp_path / "dispatch.json")
    alert_store = build_raycon_alert_state_store(tmp_path / "alerts.json")

    assert isinstance(dispatch_store, JsonRayConDispatchStateStore)
    assert isinstance(alert_store, JsonRayConAlertStateStore)
