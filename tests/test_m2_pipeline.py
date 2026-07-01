from __future__ import annotations

from typing import Any
from urllib.parse import unquote

import pytest
import requests

from due_diligence_reporter.firestore_state import encode_firestore_fields
from due_diligence_reporter.m2_pipeline import (
    SOURCE_AVAILABLE_SCHEMA_VERSION,
    FirestoreM2EventQueue,
    JsonM2StateStore,
    M2EventValidationError,
    build_source_available_event,
    consume_site_ready_event,
    emit_source_available_event,
    poll_m2_events,
    source_available_event_from_observation,
    validate_site_ready_event,
    validate_source_available_event,
    watch_m2_source_event_queue,
    watch_m2_sources,
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
        self.patches: list[tuple[str, dict[str, Any]]] = []

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
        self.patches.append((document_id, json["fields"]))
        return FakeResponse()


def _site_ready_event() -> dict[str, Any]:
    return {
        "schema_version": "aadp.site_ready_for_ddr.v1",
        "event_id": "evt-1",
        "status": "pending",
        "ready_for_ddr": True,
        "site": {
            "id": "SITE1",
            "name": "Alpha Test",
            "address": "123 Main St, Austin, TX",
            "site_record_url": "https://rhodes.example/sites/SITE1",
        },
        "drive": {
            "site_folder_url": "https://drive.google.com/drive/folders/site",
            "m1_folder_url": "https://drive.google.com/drive/folders/m1",
        },
        "registered_documents": [
            {
                "source_type": "sir",
                "title": "Alpha Test SIR",
                "rhodes_doc_type": "siteInvestigationReport",
                "drive_url": "https://drive.example/sir",
                "drive_file_id": "sir-1",
                "registration_status": "registered",
                "readback_status": "verified",
            },
            {
                "source_type": "school_approval_report",
                "title": "Alpha Test School Approval Report",
                "rhodes_doc_type": "regulatoryApproval",
                "drive_url": "https://drive.example/school",
                "drive_file_id": "school-1",
                "registration_status": "registered",
                "readback_status": "verified",
            },
        ],
        "aadp_receipt": {
            "standard_intake_run_id": "aadp-run-1",
            "task_receipt_status": "completed",
            "vendor_email_context": {"cds": "queued"},
        },
        "remaining_work": [],
    }


def _source_available_event(
    *,
    event_id: str = "src-1",
    site_id: str = "SITE1",
    source_type: str = "block_plan",
    registration_status: str = "registered",
    readback_status: str = "verified",
) -> dict[str, Any]:
    return {
        "schema_version": SOURCE_AVAILABLE_SCHEMA_VERSION,
        "event_id": event_id,
        "status": "pending",
        "site": {
            "id": site_id,
            "name": "Alpha Test",
            "address": "123 Main St, Austin, TX",
        },
        "source_type": source_type,
        "document": {
            "title": "Block Plan - Alpha Test.pdf",
            "drive_file_id": f"{event_id}-file",
            "drive_url": f"https://drive.example/{event_id}",
            "rhodes_doc_type": "floorPlan",
            "registration_status": registration_status,
            "readback_status": readback_status,
            "readback_verified": readback_status == "verified",
        },
        "producer": {
            "workflow": "unit-test",
            "run_id": "run-1",
            "artifact_type": "source_document",
        },
        "fingerprint": f"{event_id}-file:2026-07-01T12:00:00Z",
        "created_at": "2026-07-01T12:00:00Z",
    }


def test_source_available_event_validates_and_normalizes_contract() -> None:
    event = validate_source_available_event(_source_available_event())

    assert event["schema_version"] == SOURCE_AVAILABLE_SCHEMA_VERSION
    assert event["source_type"] == "block_plan"
    assert event["site"] == {
        "id": "SITE1",
        "name": "Alpha Test",
        "address": "123 Main St, Austin, TX",
        "site_record_url": "",
    }
    assert event["drive_file_id"] == "src-1-file"
    assert event["registration_status"] == "registered"
    assert event["readback_status"] == "verified"


def test_source_available_event_rejects_missing_registration_status() -> None:
    event = _source_available_event()
    event["document"]["registration_status"] = ""

    with pytest.raises(M2EventValidationError, match="document.registration_status is required"):
        validate_source_available_event(event)


def test_build_and_emit_source_available_event_uses_stable_event_id() -> None:
    session = FakeFirestoreSession()
    queue = FirestoreM2EventQueue(project_id="project", session=session)
    site = {"id": "SITE1", "name": "Alpha Test"}
    document = {
        "title": "Capacity.pdf",
        "drive_file_id": "capacity-1",
        "registration_status": "registered",
        "readback_status": "verified",
    }
    producer = {"workflow": "unit-test"}

    built = build_source_available_event(
        site=site,
        source_type="alpha_capacity_analysis",
        document=document,
        producer=producer,
        created_at="2026-07-01T12:00:00Z",
    )
    emitted = emit_source_available_event(
        queue,
        site=site,
        source_type="alpha_capacity_analysis",
        document=document,
        producer=producer,
        created_at="2026-07-01T12:00:00Z",
    )

    assert emitted["event_id"] == built["event_id"]
    assert emitted["event_id"].startswith("ddr-source-")
    assert emitted["event_id"] in session.documents


def test_source_available_event_from_observation_defaults_to_handoff_status() -> None:
    event = source_available_event_from_observation(
        site={"id": "SITE1", "name": "Alpha Test"},
        observation={
            "source_type": "block_plan",
            "fingerprint": "block-1:2026-07-01T12:00:00Z",
            "drive_file_id": "block-1",
            "drive_url": "https://drive.example/block-1",
            "file_name": "Block Plan.pdf",
        },
        producer={"workflow": "unit-test"},
        created_at="2026-07-01T12:00:00Z",
    )

    assert event["schema_version"] == SOURCE_AVAILABLE_SCHEMA_VERSION
    assert event["document"]["registration_status"] == "pending_user_action"
    assert event["document"]["readback_status"] == "not_verified"


def test_site_ready_event_initializes_waiting_for_capacity_state(tmp_path) -> None:
    store = JsonM2StateStore(tmp_path / ".m2_direct_dd_state.json")

    result = consume_site_ready_event(
        _site_ready_event(),
        state_store=store,
        apply=True,
    )

    state = store.load()["evt-1"]
    assert result["status"] == "blocked"
    assert result["m2_state"] == "waiting_for_capacity_source"
    assert result["next_actions"] == ["run_alpha_capacity_analysis"]
    assert result["source_packet_status"] == "blocked"
    assert state["site"]["name"] == "Alpha Test"
    assert state["registered_documents"][0]["source_type"] == "sir"
    assert state["open_blockers"][0]["id"] == "missing_capacity_source"


def test_site_ready_event_rejects_unverified_school_approval() -> None:
    event = _site_ready_event()
    event["registered_documents"][1]["readback_status"] = "pending"

    with pytest.raises(M2EventValidationError, match="school_approval_report: unverified"):
        validate_site_ready_event(event)


def test_consume_event_can_require_live_rhodes_document_readback(tmp_path) -> None:
    seen: list[tuple[str, str]] = []

    def document_lister(site_id: str, doc_type: str) -> list[dict[str, Any]]:
        seen.append((site_id, doc_type))
        drive_file_id = "sir-1" if doc_type == "siteInvestigationReport" else "school-1"
        return [{"title": f"{doc_type} doc", "driveFileId": drive_file_id}]

    result = consume_site_ready_event(
        _site_ready_event(),
        state_store=JsonM2StateStore(tmp_path / "state.json"),
        verify_rhodes_readback=True,
        document_lister=document_lister,
    )

    assert result["m2_state"] == "waiting_for_capacity_source"
    assert seen == [
        ("SITE1", "siteInvestigationReport"),
        ("SITE1", "regulatoryApproval"),
    ]


def test_source_watch_resumes_only_matching_open_site(tmp_path) -> None:
    store = JsonM2StateStore(tmp_path / "state.json")
    consume_site_ready_event(_site_ready_event(), state_store=store)
    second = _site_ready_event()
    second["event_id"] = "evt-2"
    second["site"]["id"] = "SITE2"
    second["site"]["name"] = "Alpha Other"
    consume_site_ready_event(second, state_store=store)

    result = watch_m2_sources(
        state_store=store,
        source_events_by_site={
            "SITE1": [
                {
                    "source_type": "block_plan",
                    "doc_type": "block_plan",
                    "fingerprint": "block-1:2026-06-30T12:00:00Z",
                    "drive_file_id": "block-1",
                    "drive_url": "https://drive.example/block-1",
                    "file_name": "Block Plan - Alpha Test.pdf",
                }
            ]
        },
        apply=True,
        now="2026-06-30T12:00:00Z",
    )

    state = store.load()
    assert result["open_states_checked"] == 2
    assert result["resumed"] == 1
    assert state["evt-1"]["m2_state"] == "capacity_ready"
    assert state["evt-1"]["open_blockers"][0]["id"] == "run_alpha_capacity_analysis"
    assert state["evt-2"]["m2_state"] == "waiting_for_capacity_source"


def test_source_event_queue_resumes_verified_source_and_completes_event(tmp_path) -> None:
    store = JsonM2StateStore(tmp_path / "state.json")
    consume_site_ready_event(_site_ready_event(), state_store=store)
    session = FakeFirestoreSession()
    session.documents["src-1"] = encode_firestore_fields(_source_available_event())
    queue = FirestoreM2EventQueue(project_id="project", session=session)

    result = watch_m2_source_event_queue(
        event_queue=queue,
        state_store=store,
        apply=True,
        now="2026-07-01T12:00:00Z",
    )

    state = store.load()["evt-1"]
    assert result["resumed"] == 1
    assert result["source_event_queue"]["events_found"] == 1
    assert result["source_event_queue"]["events_completed"] == 1
    assert result["source_event_queue"]["events_blocked"] == 0
    assert result["rows"][0]["matched_source_event_ids"] == ["src-1"]
    assert state["m2_state"] == "capacity_ready"
    patched_statuses = [
        fields["status"]["stringValue"]
        for document_id, fields in session.patches
        if document_id == "src-1"
    ]
    assert patched_statuses == ["completed"]


def test_source_event_queue_blocks_unverified_registration_without_resuming(tmp_path) -> None:
    store = JsonM2StateStore(tmp_path / "state.json")
    consume_site_ready_event(_site_ready_event(), state_store=store)
    session = FakeFirestoreSession()
    session.documents["src-1"] = encode_firestore_fields(
        _source_available_event(
            registration_status="pending_user_action",
            readback_status="not_verified",
        )
    )
    queue = FirestoreM2EventQueue(project_id="project", session=session)

    result = watch_m2_source_event_queue(
        event_queue=queue,
        state_store=store,
        apply=True,
        now="2026-07-01T12:00:00Z",
    )

    state = store.load()["evt-1"]
    assert result["resumed"] == 0
    assert result["source_events_blocked"] == 1
    assert result["source_event_queue"]["events_blocked"] == 1
    assert result["rows"][0]["blocked_source_event_ids"] == ["src-1"]
    assert state["m2_state"] == "blocked"
    assert state["open_blockers"][0]["id"] == "document_registration_handoff:block_plan"
    assert state["open_blockers"][0]["next_action"] == "document_registration_handoff"
    patched_statuses = [
        fields["status"]["stringValue"]
        for document_id, fields in session.patches
        if document_id == "src-1"
    ]
    assert patched_statuses == ["blocked"]


def test_source_event_queue_leaves_unmatched_source_event_pending(tmp_path) -> None:
    store = JsonM2StateStore(tmp_path / "state.json")
    consume_site_ready_event(_site_ready_event(), state_store=store)
    session = FakeFirestoreSession()
    session.documents["src-other"] = encode_firestore_fields(
        _source_available_event(
            event_id="src-other",
            site_id="SITE2",
        )
    )
    queue = FirestoreM2EventQueue(project_id="project", session=session)

    result = watch_m2_source_event_queue(
        event_queue=queue,
        state_store=store,
        apply=True,
        now="2026-07-01T12:00:00Z",
    )

    assert result["resumed"] == 0
    assert result["source_event_queue"]["events_pending"] == 1
    assert session.patches == []
    assert store.load()["evt-1"]["m2_state"] == "waiting_for_capacity_source"


def test_source_watch_canary_filters_open_states(tmp_path) -> None:
    store = JsonM2StateStore(tmp_path / "state.json")
    consume_site_ready_event(_site_ready_event(), state_store=store)
    second = _site_ready_event()
    second["event_id"] = "evt-2"
    second["site"]["id"] = "SITE2"
    second["site"]["name"] = "Alpha Other"
    consume_site_ready_event(second, state_store=store)

    result = watch_m2_sources(
        state_store=store,
        source_events_by_site={
            "SITE1": [
                {
                    "source_type": "block_plan",
                    "doc_type": "block_plan",
                    "fingerprint": "block-1:2026-06-30T12:00:00Z",
                    "drive_file_id": "block-1",
                    "drive_url": "https://drive.example/block-1",
                    "file_name": "Block Plan - Alpha Test.pdf",
                }
            ]
        },
        apply=True,
        site_id="SITE2",
        now="2026-06-30T12:00:00Z",
    )

    state = store.load()
    assert result["open_states_checked"] == 1
    assert result["filters"] == {"site_id": "SITE2", "event_id": ""}
    assert result["resumed"] == 0
    assert result["rows"][0]["event_id"] == "evt-2"
    assert state["evt-1"]["m2_state"] == "waiting_for_capacity_source"
    assert state["evt-2"]["m2_state"] == "waiting_for_capacity_source"


def test_source_watch_ignores_raycon_events(tmp_path) -> None:
    store = JsonM2StateStore(tmp_path / "state.json")
    consume_site_ready_event(_site_ready_event(), state_store=store)

    result = watch_m2_sources(
        state_store=store,
        source_events_by_site={
            "SITE1": [
                {
                    "source_type": "raycon_scenario",
                    "doc_type": "raycon_scenario_json",
                    "fingerprint": "raycon-1:2026-06-30T12:00:00Z",
                    "drive_file_id": "raycon-1",
                    "drive_url": "https://drive.example/raycon-1",
                    "file_name": "raycon_scenario.json",
                }
            ]
        },
        apply=True,
        now="2026-06-30T12:00:00Z",
    )

    state = store.load()["evt-1"]
    assert result["resumed"] == 0
    assert state["m2_state"] == "waiting_for_capacity_source"
    assert state["open_blockers"][0]["id"] == "missing_capacity_source"


def test_source_watch_resumes_security_due_diligence_memo(tmp_path) -> None:
    store = JsonM2StateStore(tmp_path / "state.json")
    consume_site_ready_event(_site_ready_event(), state_store=store)
    state = store.load()
    state["evt-1"]["m2_state"] = "waiting_for_external_sources"
    state["evt-1"]["open_blockers"] = [
        {
            "id": "run_security_due_diligence_failed",
            "m2_state": "waiting_for_external_sources",
            "reason": "security due diligence memo missing",
            "resume_source_types": ["security_due_diligence_report"],
            "next_action": "run_security_due_diligence",
        }
    ]
    store.save(state)

    result = watch_m2_sources(
        state_store=store,
        source_events_by_site={
            "SITE1": [
                {
                    "source_type": "security_due_diligence_report",
                    "doc_type": "security_due_diligence_report",
                    "fingerprint": "security-1:2026-06-30T12:00:00Z",
                    "drive_file_id": "security-1",
                    "drive_url": "https://drive.example/security-1",
                    "file_name": "Security Due Diligence - Alpha Test.md",
                }
            ]
        },
        apply=True,
        now="2026-06-30T12:00:00Z",
    )

    resumed = store.load()["evt-1"]
    assert result["resumed"] == 1
    assert resumed["m2_state"] == "source_packet_ready"
    assert resumed["open_blockers"][0]["id"] == "build_m2_source_packet"
    assert {
        doc["source_type"] for doc in resumed["registered_documents"]
    } >= {"security_due_diligence_report"}


def test_firestore_event_queue_polls_pending_events_and_updates_status(tmp_path) -> None:
    session = FakeFirestoreSession()
    session.documents["evt-1"] = encode_firestore_fields(_site_ready_event())
    store = JsonM2StateStore(tmp_path / "state.json")
    queue = FirestoreM2EventQueue(project_id="project", session=session)

    result = poll_m2_events(
        event_queue=queue,
        state_store=store,
        apply=True,
        limit=5,
    )

    assert result["events_found"] == 1
    assert result["blocked"] == 1
    assert store.load()["evt-1"]["m2_state"] == "waiting_for_capacity_source"
    patched_statuses = [
        fields["status"]["stringValue"]
        for document_id, fields in session.patches
        if document_id == "evt-1"
    ]
    assert patched_statuses == ["processing", "blocked"]


def test_poll_m2_events_canary_filters_before_limit(tmp_path) -> None:
    session = FakeFirestoreSession()
    session.documents["evt-1"] = encode_firestore_fields(_site_ready_event())
    second = _site_ready_event()
    second["event_id"] = "evt-2"
    second["site"]["id"] = "SITE2"
    second["site"]["name"] = "Alpha Other"
    session.documents["evt-2"] = encode_firestore_fields(second)
    store = JsonM2StateStore(tmp_path / "state.json")
    queue = FirestoreM2EventQueue(project_id="project", session=session)

    result = poll_m2_events(
        event_queue=queue,
        state_store=store,
        apply=False,
        limit=1,
        site_id="SITE2",
    )

    assert result["events_found"] == 1
    assert result["filters"] == {"site_id": "SITE2", "event_id": ""}
    assert result["rows"][0]["event_id"] == "evt-2"
    assert store.load() == {}


def test_json_state_store_ignores_corrupt_state(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not-json", encoding="utf-8")

    assert JsonM2StateStore(path).load() == {}
