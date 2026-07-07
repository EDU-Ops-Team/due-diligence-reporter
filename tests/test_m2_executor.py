from __future__ import annotations

from typing import Any

from due_diligence_reporter.m2_executor import (
    M2StepResult,
    execute_ready_m2_states,
)
from due_diligence_reporter.m2_pipeline import (
    JsonM2StateStore,
    consume_site_ready_event,
    watch_m2_sources,
)


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
        },
        "drive": {
            "site_folder_url": "https://drive.google.com/drive/folders/site",
            "m1_folder_url": "https://drive.google.com/drive/folders/m1",
        },
        "registered_documents": [
            _doc("sir", "SIR", "siteInvestigationReport"),
            _doc("school_approval_report", "School Approval", "regulatoryApproval"),
        ],
        "aadp_receipt": {},
        "remaining_work": [],
    }


def _doc(source_type: str, title: str, rhodes_doc_type: str) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "title": title,
        "rhodes_doc_type": rhodes_doc_type,
        "drive_url": f"https://drive.example/{source_type}",
        "drive_file_id": f"{source_type}-1",
        "registration_status": "registered",
        "readback_status": "verified",
        "readback_verified": True,
    }


def _capacity_ready_store(tmp_path) -> JsonM2StateStore:
    store = JsonM2StateStore(tmp_path / "state.json")
    consume_site_ready_event(_site_ready_event(), state_store=store)
    watch_m2_sources(
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
    )
    return store


class FakeAdapters:
    def __init__(
        self,
        *,
        capacity_write_status: str = "updated",
        packet_write_status: str = "updated",
        fail_source_step: str = "",
        note_status: str = "created",
        phase_source_type: str = "alpha_phasing_plan_report",
    ) -> None:
        self.capacity_write_status = capacity_write_status
        self.packet_write_status = packet_write_status
        self.fail_source_step = fail_source_step
        self.note_status = note_status
        self.phase_source_type = phase_source_type
        self.calls: list[str] = []
        self.write_calls: list[dict[str, Any]] = []
        self.write_field_sources: list[dict[str, str] | None] = []

    def run_alpha_capacity(self, state: dict[str, Any]) -> M2StepResult:
        del state
        self.calls.append("alpha")
        return M2StepResult(
            status="success",
            report_data_fields={
                "exec.fastest_open_capacity": "36",
                "exec.max_capacity_capacity": "54",
            },
            supporting_documents=[
                _doc(
                    "alpha_capacity_analysis",
                    "Alpha Capacity Analysis",
                    "capacityCalculation",
                )
            ],
        )

    def write_due_diligence(
        self,
        site_id: str,
        fields: dict[str, Any],
        field_sources: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        del site_id
        self.calls.append("write")
        self.write_calls.append(dict(fields))
        self.write_field_sources.append(dict(field_sources) if field_sources else None)
        status = (
            self.capacity_write_status
            if set(fields) <= {"foCapacity", "maxCapCapacity"}
            else self.packet_write_status
        )
        if status != "updated":
            if status == "proposal_submitted":
                return {
                    "status": "proposal_submitted",
                    "reason": "approval_queue",
                    "updated_fields": sorted(fields),
                    "rhodes_due_diligence_status": "proposal_submitted",
                    "human_followup_required": True,
                    "human_followup_type": "due_diligence_approval",
                    "remaining_work": [],
                    "approval": {
                        "pending_mutation_id": "MUT1",
                        "approval_session_id": "APPROVAL1",
                        "review_url": "https://locationos.example/review",
                    },
                    "due_diligence_update_handoff": {
                        "status": "created",
                        "reason": "handoff_note_created",
                        "note_readback_status": "verified",
                        "rhodes_note_id": "proposal-note-1",
                        "field_count": len(fields),
                        "fields": [
                            {"name": key, "value": str(fields[key]), "source": ""}
                            for key in sorted(fields)
                        ],
                    },
                }
            if status == "pending_user_action":
                return {
                    "status": "pending_user_action",
                    "reason": "handoff_note_created",
                    "error": "awaiting_browser_approval",
                    "error_summary": (
                        "LocationOS updateDueDiligence requires approval; "
                        "handoff note created."
                    ),
                    "updated_fields": sorted(fields),
                    "rhodes_due_diligence_status": "pending_user_action",
                    "human_followup_required": True,
                    "human_followup_type": "due_diligence_update",
                    "remaining_work": [],
                    "due_diligence_update_handoff": {
                        "status": "created",
                        "reason": "handoff_note_created",
                        "note_readback_status": "verified",
                        "rhodes_note_id": "handoff-note-1",
                        "field_count": len(fields),
                        "fields": [
                            {"name": key, "value": str(fields[key])}
                            for key in sorted(fields)
                        ],
                    },
                }
            return {
                "status": "failed",
                "reason": "readback_failed",
                "error": "readback failed",
                "updated_fields": sorted(fields),
            }
        return {"status": "updated", "reason": "ok", "updated_fields": sorted(fields)}

    def run_cost_timeline(self, state: dict[str, Any]) -> M2StepResult:
        del state
        self.calls.append("cost")
        return M2StepResult(
            status="success",
            report_data_fields={
                "exec.fastest_open_open_date": "08/12/26",
                "exec.max_capacity_open_date": "10/01/26",
                "exec.fastest_open_capex": "$125,000",
                "exec.max_capacity_capex": "$250,000",
            },
            supporting_documents=[
                _doc(
                    "cost_timeline_estimate",
                    "Cost Timeline Estimate",
                    "initialCostEstimate",
                )
            ],
        )

    def run_outdoor_play(self, state: dict[str, Any]) -> M2StepResult:
        del state
        self.calls.append("outdoor")
        if self.fail_source_step == "outdoor":
            return M2StepResult(
                status="blocked",
                reason="outdoor registration failed",
            )
        return M2StepResult(
            status="success",
            report_data_fields={
                "exec.play_area_score": "1",
                "exec.play_area_comment": "Outdoor play passes.",
            },
            supporting_documents=[
                _doc("outdoor_play_space_report", "Outdoor Play", "other")
            ],
        )

    def run_opening_plan(self, state: dict[str, Any]) -> M2StepResult:
        del state
        self.calls.append("opening")
        return M2StepResult(
            status="success",
            report_data_fields={
                "exec.regulatory_score": "2",
                "exec.regulatory_comment": "AHJ confirmation pending.",
                "fast_open_mode_confirmed": "Phase I fast-open mode.",
                "fast_open_occupancy_type_confirmed": "B occupancy.",
                "max_plan_occupancy_type_confirmed": "E occupancy.",
                "zoning_status_confirmed": "SIR and Opening Plan align.",
            },
            supporting_documents=[
                _doc("opening_plan_report", "Opening Plan", "other"),
                _doc("certificate_of_occupancy", "CO", "certificateOfOccupancy"),
            ],
        )

    def run_phase_1_phase_2(self, state: dict[str, Any]) -> M2StepResult:
        del state
        self.calls.append("phasing")
        return M2StepResult(
            status="success",
            report_data_fields={
                "exec.building_score": "2",
                "exec.building_comment": "Phase II scope tracked.",
                "exec.school_ops_score": "2",
                "exec.school_ops_comment": "Traffic plan needs review.",
                "current_occupancy_confirmed": "CO confirms occupancy.",
                "site_square_footage_confirmed": "Measured plan confirms SF.",
            },
            supporting_documents=[
                _doc(self.phase_source_type, "Phase 1 Phase 2 workbook", "phasing"),
                _doc("traffic_analysis", "Traffic Analysis", "other"),
                _doc("measured_floor_plan", "Measured Floor Plan", "floorPlan"),
            ],
        )

    def run_security_due_diligence(self, state: dict[str, Any]) -> M2StepResult:
        del state
        self.calls.append("security")
        if self.fail_source_step == "security":
            return M2StepResult(
                status="blocked",
                reason="security due diligence memo missing",
                raw={"resume_source_types": ["security_due_diligence_report"]},
            )
        return M2StepResult(
            status="success",
            supporting_documents=[
                _doc("security_due_diligence_report", "Security Due Diligence", "other")
            ],
        )

    def add_source_note(self, state: dict[str, Any], note_lines: list[str]) -> dict[str, Any]:
        del state
        self.calls.append("note")
        return {
            "status": self.note_status,
            "rhodes_note_id": "note-1" if self.note_status == "created" else "",
            "line_count": len(note_lines),
        }


def test_execute_ready_m2_state_completes_successfully(tmp_path) -> None:
    store = _capacity_ready_store(tmp_path)
    adapters = FakeAdapters()

    result = execute_ready_m2_states(
        state_store=store,
        apply=True,
        adapters=adapters,
    )

    state = store.load()["evt-1"]
    assert result["completed"] == 1
    assert state["m2_state"] == "complete"
    assert state["source_packet"]["m2_source_packet_complete"] is True
    assert state["source_note"]["rhodes_note_id"] == "note-1"
    assert adapters.calls == [
        "alpha",
        "write",
        "cost",
        "outdoor",
        "opening",
        "phasing",
        "security",
        "write",
        "note",
    ]
    assert adapters.write_calls[0] == {"foCapacity": 36, "maxCapCapacity": 54}
    assert "foCapEx" in adapters.write_calls[1]
    assert adapters.write_field_sources[0] == {
        "foCapacity": "Alpha Capacity Analysis",
        "maxCapCapacity": "Alpha Capacity Analysis",
    }
    packet_sources = adapters.write_field_sources[1]
    assert packet_sources is not None
    assert packet_sources["foCapacity"] == "Alpha Capacity Analysis"
    assert set(packet_sources) >= set(adapters.write_calls[1])


def test_execute_ready_dry_run_does_not_mutate_state(tmp_path) -> None:
    store = _capacity_ready_store(tmp_path)
    before = store.load()

    result = execute_ready_m2_states(state_store=store, apply=False)

    assert result["rows"][0]["status"] == "preview"
    assert result["rows"][0]["would_execute"] == ["run_alpha_capacity_analysis"]
    assert store.load() == before


def test_execute_ready_canary_filter_only_executes_matching_site(tmp_path) -> None:
    store = _capacity_ready_store(tmp_path)
    state = store.load()
    state["evt-2"] = {
        **state["evt-1"],
        "event_id": "evt-2",
        "site": {
            **state["evt-1"]["site"],
            "id": "SITE2",
            "name": "Alpha Other",
        },
    }
    store.save(state)
    adapters = FakeAdapters()

    result = execute_ready_m2_states(
        state_store=store,
        apply=True,
        adapters=adapters,
        site_id="SITE2",
    )

    state = store.load()
    assert result["states_checked"] == 1
    assert result["filters"] == {"site_id": "SITE2", "event_id": ""}
    assert result["rows"][0]["event_id"] == "evt-2"
    assert state["evt-1"]["m2_state"] == "capacity_ready"
    assert state["evt-2"]["m2_state"] == "complete"
    assert adapters.calls == [
        "alpha",
        "write",
        "cost",
        "outdoor",
        "opening",
        "phasing",
        "security",
        "write",
        "note",
    ]


def test_phase_1_phase_2_source_type_alias_completes_packet(tmp_path) -> None:
    store = _capacity_ready_store(tmp_path)
    adapters = FakeAdapters(phase_source_type="phase_1_phase_2_report")

    execute_ready_m2_states(state_store=store, apply=True, adapters=adapters)

    state = store.load()["evt-1"]
    docs = {
        doc["title"]: doc["source_type"]
        for doc in state["source_packet"]["supporting_documents"]
    }

    assert state["m2_state"] == "complete"
    assert docs["Phase 1 Phase 2 workbook"] == "alpha_phasing_plan_report"


def test_execute_ready_skips_unknown_blocked_next_action(tmp_path) -> None:
    store = _capacity_ready_store(tmp_path)
    state = store.load()
    state["evt-1"]["status"] = "blocked"
    state["evt-1"]["m2_state"] = "blocked"
    state["evt-1"]["open_blockers"] = [
        {
            "id": "manual_review",
            "m2_state": "blocked",
            "reason": "Manual review required.",
            "next_action": "manual_review",
        }
    ]
    store.save(state)
    adapters = FakeAdapters()

    result = execute_ready_m2_states(
        state_store=store,
        apply=True,
        adapters=adapters,
    )

    assert result["rows"] == []
    assert adapters.calls == []
    assert store.load() == state


def test_existing_alpha_capacity_artifact_is_reused(tmp_path) -> None:
    store = _capacity_ready_store(tmp_path)
    state = store.load()
    state["evt-1"]["registered_documents"].append(
        _doc("alpha_capacity_analysis", "Alpha Capacity Analysis", "capacityCalculation")
    )
    state["evt-1"]["report_data_fields"] = {
        "exec.fastest_open_capacity": "36",
        "exec.max_capacity_capacity": "54",
    }
    store.save(state)
    adapters = FakeAdapters()

    execute_ready_m2_states(state_store=store, apply=True, adapters=adapters)

    assert "alpha" not in adapters.calls
    assert adapters.calls[0] == "write"


def test_cost_timeline_waits_for_capacity_readback(tmp_path) -> None:
    store = _capacity_ready_store(tmp_path)
    adapters = FakeAdapters(capacity_write_status="failed")

    result = execute_ready_m2_states(
        state_store=store,
        apply=True,
        adapters=adapters,
    )

    state = store.load()["evt-1"]
    assert result["blocked"] == 1
    assert state["open_blockers"][0]["id"] == "capacity_write_readback_pending"
    assert "cost" not in adapters.calls
    assert "note" not in adapters.calls


def test_capacity_handoff_note_completes_manual_followup(tmp_path) -> None:
    store = _capacity_ready_store(tmp_path)
    adapters = FakeAdapters(capacity_write_status="pending_user_action")

    result = execute_ready_m2_states(
        state_store=store,
        apply=True,
        adapters=adapters,
    )

    state = store.load()["evt-1"]
    row = result["rows"][0]
    assert result["completed"] == 1
    assert result["blocked"] == 0
    assert row["status"] == "complete"
    assert row["completion_mode"] == "due_diligence_update_handoff"
    assert row["human_followup_required"] is True
    assert row["manual_handoff_note_id"] == "handoff-note-1"
    assert state["m2_state"] == "complete"
    assert state["status"] == "complete"
    assert state["open_blockers"] == []
    assert state["completion_mode"] == "due_diligence_update_handoff"
    assert state["human_followup_required"] is True
    assert state["manual_handoff"]["fields"] == [
        {"name": "foCapacity", "value": "36"},
        {"name": "maxCapCapacity", "value": "54"},
    ]
    assert state["capacity_write"]["status"] == "pending_user_action"
    assert state["capacity_write"]["reason"] == "handoff_note_created"
    assert adapters.calls == ["alpha", "write"]
    assert "cost" not in adapters.calls
    assert "note" not in adapters.calls
    assert adapters.write_field_sources[0] == {
        "foCapacity": "Alpha Capacity Analysis",
        "maxCapCapacity": "Alpha Capacity Analysis",
    }


def test_capacity_proposal_submission_completes_without_manual_update_ask(tmp_path) -> None:
    store = _capacity_ready_store(tmp_path)
    adapters = FakeAdapters(capacity_write_status="proposal_submitted")

    result = execute_ready_m2_states(
        state_store=store,
        apply=True,
        adapters=adapters,
    )

    state = store.load()["evt-1"]
    row = result["rows"][0]
    assert result["completed"] == 1
    assert row["status"] == "complete"
    assert state["m2_state"] == "complete"
    assert state["completion_mode"] == "due_diligence_proposal_submitted"
    assert state["human_followup_required"] is True
    assert state["human_followup_type"] == "due_diligence_approval"
    assert state["manual_handoff"]["type"] == "due_diligence_approval"
    assert state["manual_handoff"]["approval"] == {
        "pending_mutation_id": "MUT1",
        "approval_session_id": "APPROVAL1",
        "review_url": "https://locationos.example/review",
    }
    assert state["capacity_write"]["status"] == "proposal_submitted"
    assert adapters.calls == ["alpha", "write"]


def test_packet_proposal_submission_marks_fields_awaiting_approval(tmp_path) -> None:
    store = _capacity_ready_store(tmp_path)
    adapters = FakeAdapters(packet_write_status="proposal_submitted")

    result = execute_ready_m2_states(
        state_store=store,
        apply=True,
        adapters=adapters,
    )

    state = store.load()["evt-1"]
    assert result["completed"] == 1
    assert state["completion_mode"] == "due_diligence_proposal_submitted"
    packet = state["source_packet"]
    assert packet["status"] == "proposal_awaiting_approval"
    assert packet["m2_source_packet_complete"] is False
    assert packet["open_items"] == [
        "LocationOS due diligence fields await the approval-queue decision; "
        "site owner tracks the pending change."
    ]
    statuses = {
        row["locationos_key"]: row["write_status"]
        for row in packet["dd_field_updates"]
        if row.get("locationos_key") and row.get("write_status") != "written"
    }
    assert statuses
    assert set(statuses.values()) == {"proposal_submitted"}


def test_failed_source_registration_blocks_before_packet_write(tmp_path) -> None:
    store = _capacity_ready_store(tmp_path)
    adapters = FakeAdapters(fail_source_step="outdoor")

    execute_ready_m2_states(state_store=store, apply=True, adapters=adapters)

    state = store.load()["evt-1"]
    assert state["m2_state"] == "waiting_for_external_sources"
    assert state["open_blockers"][0]["id"] == "run_outdoor_play_space_failed"
    assert adapters.write_calls == [{"foCapacity": 36, "maxCapCapacity": 54}]
    assert "note" not in adapters.calls


def test_security_due_diligence_blocks_until_memo_is_registered(tmp_path) -> None:
    store = _capacity_ready_store(tmp_path)
    adapters = FakeAdapters(fail_source_step="security")

    execute_ready_m2_states(state_store=store, apply=True, adapters=adapters)

    state = store.load()["evt-1"]
    assert state["m2_state"] == "waiting_for_external_sources"
    assert state["open_blockers"][0]["id"] == "run_security_due_diligence_failed"
    assert state["open_blockers"][0]["next_action"] == "run_security_due_diligence"
    assert state["open_blockers"][0]["resume_source_types"] == [
        "security_due_diligence_report"
    ]
    assert adapters.write_calls == [{"foCapacity": 36, "maxCapCapacity": 54}]
    assert "note" not in adapters.calls


def test_failed_packet_readback_blocks_without_note(tmp_path) -> None:
    store = _capacity_ready_store(tmp_path)
    adapters = FakeAdapters(packet_write_status="failed")

    execute_ready_m2_states(state_store=store, apply=True, adapters=adapters)

    state = store.load()["evt-1"]
    assert state["m2_state"] == "dd_write_pending"
    assert state["open_blockers"][0]["id"] == "packet_write_readback_pending"
    assert "note" not in adapters.calls


def test_packet_handoff_note_completes_manual_followup(tmp_path) -> None:
    store = _capacity_ready_store(tmp_path)
    adapters = FakeAdapters(packet_write_status="pending_user_action")

    result = execute_ready_m2_states(
        state_store=store,
        apply=True,
        adapters=adapters,
    )

    state = store.load()["evt-1"]
    row = result["rows"][0]
    assert result["completed"] == 1
    assert result["blocked"] == 0
    assert row["status"] == "complete"
    assert row["source_packet_status"] == "handoff_pending_manual_update"
    assert row["completion_mode"] == "due_diligence_update_handoff"
    assert row["manual_handoff_note_id"] == "handoff-note-1"
    assert state["m2_state"] == "complete"
    assert state["open_blockers"] == []
    assert state["dd_write"]["status"] == "pending_user_action"
    assert state["source_packet"]["status"] == "handoff_pending_manual_update"
    assert state["source_packet"]["m2_source_packet_complete"] is False
    assert state["source_packet"]["manual_handoff_note_id"] == "handoff-note-1"
    assert any(
        update.get("write_status") == "pending_user_action"
        and update.get("handoff_note_id") == "handoff-note-1"
        for update in state["source_packet"]["dd_field_updates"]
    )
    assert adapters.calls == [
        "alpha",
        "write",
        "cost",
        "outdoor",
        "opening",
        "phasing",
        "security",
        "write",
    ]
    assert "note" not in adapters.calls
