"""Executor for DDR-owned M2 source-packet completion."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from .alpha_capacity_analysis import generate_alpha_capacity_analysis_artifact
from .config import get_settings
from .google_client import GoogleClient
from .m1_lookup import _resolve_m1_folder
from .m2_pipeline import M2StateStore, m2_state_is_open, m2_state_matches_filters
from .rhodes import (
    RhodesClient,
    add_rhodes_site_note,
    register_rhodes_document_for_upload,
    update_rhodes_due_diligence,
)
from .source_packet import (
    SourceDocumentRef,
    build_m2_source_packet,
    mark_written_fields_from_update_result,
    source_packet_is_complete,
)
from .utils import extract_text_from_pdf_bytes

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ESTIMATOR_PATH = (
    PROJECT_ROOT / "docs" / "skills" / "cost-and-timeline-estimate" / "scripts" / "estimate.py"
)

EXECUTABLE_M2_STATES = frozenset(
    {
        "capacity_ready",
        "capacity_written",
        "waiting_for_external_sources",
        "source_packet_ready",
        "dd_write_pending",
        "blocked",
    }
)

KNOWN_EXECUTOR_ACTIONS = frozenset(
    {
        "run_alpha_capacity_analysis",
        "write_capacity_fields",
        "run_cost_timeline_estimate",
        "run_downstream_source_skills",
        "run_security_due_diligence",
        "build_m2_source_packet",
        "write_packet_approved_dd_fields",
        "add_source_note",
    }
)

REGISTERED_STATUSES = frozenset({"registered", "already_registered"})
SCORE_KEYS = frozenset(
    {
        "buildingScore",
        "playAreaScore",
        "regulatoryScore",
        "schoolOperationsScore",
    }
)
NUMERIC_KEYS = frozenset({"foCapacity", "maxCapCapacity", "foCapEx", "maxCapCapEx"})


@dataclass(frozen=True)
class M2StepResult:
    """Normalized result from one M2 executor adapter step."""

    status: str
    report_data_fields: dict[str, Any] = field(default_factory=dict)
    supporting_documents: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class M2ExecutorAdapters(Protocol):
    """Side-effect boundary for live M2 execution."""

    def run_alpha_capacity(self, state: dict[str, Any]) -> M2StepResult:
        """Run or reuse Alpha Capacity Analysis and return capacity fields."""

    def write_due_diligence(self, site_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Write LocationOS/Rhodes due diligence fields with readback."""

    def run_cost_timeline(self, state: dict[str, Any]) -> M2StepResult:
        """Run or reuse the cost/timeline estimate source."""

    def run_outdoor_play(self, state: dict[str, Any]) -> M2StepResult:
        """Run or reuse the Outdoor Play Space source."""

    def run_opening_plan(self, state: dict[str, Any]) -> M2StepResult:
        """Run or reuse the Opening Plan source."""

    def run_phase_1_phase_2(self, state: dict[str, Any]) -> M2StepResult:
        """Run or reuse the Phase 1 / Phase 2 source."""

    def run_security_due_diligence(self, state: dict[str, Any]) -> M2StepResult:
        """Run or reuse the Security Due Diligence source."""

    def add_source_note(self, state: dict[str, Any], note_lines: Sequence[str]) -> dict[str, Any]:
        """Add and verify the concise Rhodes M2 source note."""


def execute_ready_m2_states(
    *,
    state_store: M2StateStore,
    apply: bool = False,
    limit: int = 10,
    adapters: M2ExecutorAdapters | None = None,
    now: str | None = None,
    site_id: str = "",
    event_id: str = "",
) -> dict[str, Any]:
    """Execute open M2 states that are ready for DDR-owned automation."""

    timestamp = now or _utc_now_iso()
    state = state_store.load()
    rows: list[dict[str, Any]] = []
    changed = False
    remaining = max(limit, 0)

    for state_event_id, entry in state.items():
        if remaining <= 0:
            break
        if not m2_state_matches_filters(
            state_event_id,
            entry,
            site_id=site_id,
            event_id=event_id,
        ):
            continue
        if not _is_executor_ready(entry):
            continue
        remaining -= 1
        if not apply:
            rows.append(_preview_row(state_event_id, entry))
            continue
        runner = adapters or LiveM2ExecutorAdapters()
        updated, row = execute_m2_state(entry, adapters=runner, now=timestamp)
        row["event_id"] = state_event_id
        rows.append(row)
        if row.get("changed"):
            state[state_event_id] = updated
            changed = True

    if apply and changed:
        state_store.save(state)

    return {
        "status": "success",
        "apply": apply,
        "states_checked": len(rows),
        "executed": sum(1 for row in rows if row.get("status") != "preview"),
        "completed": sum(1 for row in rows if row.get("m2_state") == "complete"),
        "blocked": sum(1 for row in rows if row.get("status") == "blocked"),
        "rows": rows,
    }


def execute_m2_state(
    state: dict[str, Any],
    *,
    adapters: M2ExecutorAdapters,
    now: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute one M2 state through the deterministic post-capacity chain."""

    timestamp = now or _utc_now_iso()
    updated = _json_clone(state)
    steps: list[dict[str, Any]] = []

    if _needs_alpha_capacity(updated):
        result = adapters.run_alpha_capacity(updated)
        steps.append(_step_row("run_alpha_capacity_analysis", result))
        if not _step_succeeded(result):
            _set_blocker(
                updated,
                blocker_id="run_alpha_capacity_analysis_failed",
                m2_state="capacity_ready",
                reason=_step_reason(result, "Alpha Capacity Analysis did not complete."),
                next_action="run_alpha_capacity_analysis",
            )
            return _finish(updated, steps, timestamp)
        _merge_step_result(updated, result)

    if _needs_capacity_write(updated):
        capacity_fields = _capacity_locationos_fields(updated)
        if set(capacity_fields) != {"foCapacity", "maxCapCapacity"}:
            _set_blocker(
                updated,
                blocker_id="capacity_fields_missing",
                m2_state="capacity_ready",
                reason="Alpha Capacity Analysis did not provide foCapacity and maxCapCapacity.",
                next_action="write_capacity_fields",
            )
            return _finish(updated, steps, timestamp)
        write_result = adapters.write_due_diligence(_site_id(updated), capacity_fields)
        updated["capacity_write"] = write_result
        steps.append(_dict_step_row("write_capacity_fields", write_result))
        if write_result.get("status") != "updated":
            _set_blocker(
                updated,
                blocker_id="capacity_write_readback_pending",
                m2_state="capacity_ready",
                reason=_write_reason(write_result, "Capacity write/readback failed."),
                next_action="write_capacity_fields",
            )
            return _finish(updated, steps, timestamp)
        _set_blocker(
            updated,
            blocker_id="run_cost_timeline_estimate",
            m2_state="capacity_written",
            reason="Capacity fields are written/readback; run cost/timeline estimate.",
            next_action="run_cost_timeline_estimate",
        )

    if _needs_cost_timeline(updated):
        result = adapters.run_cost_timeline(updated)
        steps.append(_step_row("run_cost_timeline_estimate", result))
        if not _step_succeeded(result):
            _set_blocker(
                updated,
                blocker_id="cost_timeline_estimate_failed",
                m2_state="capacity_written",
                reason=_step_reason(result, "Cost/timeline estimate did not complete."),
                next_action="run_cost_timeline_estimate",
            )
            return _finish(updated, steps, timestamp)
        _merge_step_result(updated, result)
        _set_blocker(
            updated,
            blocker_id="run_downstream_source_skills",
            m2_state="waiting_for_external_sources",
            reason="Cost/timeline source is registered; run downstream M2 source skills.",
            next_action="run_downstream_source_skills",
        )

    if _needs_downstream_sources(updated):
        for step_name, source_type, runner in (
            ("run_outdoor_play_space", "outdoor_play_space_report", adapters.run_outdoor_play),
            ("run_opening_plan_v2", "opening_plan_report", adapters.run_opening_plan),
            ("run_phase_1_phase_2", "alpha_phasing_plan_report", adapters.run_phase_1_phase_2),
            (
                "run_security_due_diligence",
                "security_due_diligence_report",
                adapters.run_security_due_diligence,
            ),
        ):
            if source_type == "security_due_diligence_report" and not _security_due_diligence_is_due(updated):
                continue
            if _has_doc(updated, source_type):
                continue
            result = runner(updated)
            steps.append(_step_row(step_name, result))
            if not _step_succeeded(result):
                next_action = (
                    "run_security_due_diligence"
                    if step_name == "run_security_due_diligence"
                    else "run_downstream_source_skills"
                )
                _set_blocker(
                    updated,
                    blocker_id=f"{step_name}_failed",
                    m2_state="waiting_for_external_sources",
                    reason=_step_reason(result, f"{step_name} did not complete."),
                    next_action=next_action,
                    resume_source_types=_resume_source_types(result),
                )
                return _finish(updated, steps, timestamp)
            _merge_step_result(updated, result)
        _set_blocker(
            updated,
            blocker_id="build_m2_source_packet",
            m2_state="source_packet_ready",
            reason="Required downstream source skills completed; build source packet.",
            next_action="build_m2_source_packet",
        )

    packet = _build_packet(updated)
    updated["source_packet"] = packet
    steps.append(
        {
            "step": "build_m2_source_packet",
            "status": "success" if _packet_ready_for_write(packet) else "blocked",
            "open_items": packet.get("open_items", []),
        }
    )
    if not _packet_ready_for_write(packet):
        _set_blocker(
            updated,
            blocker_id="source_packet_blocked",
            m2_state="source_packet_ready",
            reason=_packet_blocker_reason(packet),
            next_action="build_m2_source_packet",
        )
        return _finish(updated, steps, timestamp)

    write_fields = _pending_locationos_fields(packet)
    if not write_fields:
        _set_blocker(
            updated,
            blocker_id="packet_write_fields_missing",
            m2_state="dd_write_pending",
            reason="M2 source packet has no writable LocationOS fields.",
            next_action="write_packet_approved_dd_fields",
        )
        return _finish(updated, steps, timestamp)
    write_result = adapters.write_due_diligence(_site_id(updated), write_fields)
    steps.append(_dict_step_row("write_packet_approved_dd_fields", write_result))
    packet = mark_written_fields_from_update_result(
        source_packet=packet,
        update_result=write_result,
    )
    updated["source_packet"] = packet
    updated["dd_write"] = write_result
    if not source_packet_is_complete(packet):
        _set_blocker(
            updated,
            blocker_id="packet_write_readback_pending",
            m2_state="dd_write_pending",
            reason=_write_reason(write_result, _packet_blocker_reason(packet)),
            next_action="write_packet_approved_dd_fields",
        )
        return _finish(updated, steps, timestamp)

    note_result = adapters.add_source_note(
        updated,
        cast(Sequence[str], packet.get("source_note_lines") or []),
    )
    steps.append(_dict_step_row("add_source_note", note_result))
    updated["source_note"] = note_result
    if note_result.get("status") != "created":
        _set_blocker(
            updated,
            blocker_id="source_note_readback_pending",
            m2_state="dd_write_pending",
            reason=_write_reason(note_result, "Rhodes source note was not created/readback."),
            next_action="add_source_note",
        )
        return _finish(updated, steps, timestamp)

    updated["m2_state"] = "complete"
    updated["status"] = "complete"
    updated["open_blockers"] = []
    return _finish(updated, steps, timestamp)


class LiveM2ExecutorAdapters:
    """Live side-effect adapters for the repo-owned M2 executor."""

    def __init__(
        self,
        *,
        gc: GoogleClient | None = None,
        rhodes: RhodesClient | None = None,
    ) -> None:
        self._settings = get_settings()
        self._gc = gc
        self._rhodes = rhodes

    @property
    def gc(self) -> GoogleClient:
        if self._gc is None:
            self._gc = GoogleClient.from_oauth_config(
                client_config_path=str(self._settings.get_client_config_path()),
                token_file_path=str(self._settings.get_token_file_path()),
                oauth_port=self._settings.oauth_port,
                scopes=self._settings.google_scopes,
            )
        return self._gc

    @property
    def rhodes(self) -> RhodesClient:
        if self._rhodes is None:
            self._rhodes = RhodesClient()
        return self._rhodes

    def run_alpha_capacity(self, state: dict[str, Any]) -> M2StepResult:
        existing = _registered_doc(state, "alpha_capacity_analysis")
        if existing and _doc_drive_file_id(existing):
            payload = self._read_json_file(_doc_drive_file_id(existing))
            fields = _dict(payload.get("report_data_fields"))
            return M2StepResult(
                status="success" if fields else "blocked",
                report_data_fields=fields,
                supporting_documents=[existing],
                artifacts={"reused_existing": True, "payload": payload},
                reason="" if fields else "Existing Alpha Capacity Analysis lacks report_data_fields.",
            )

        block_plan = _registered_doc(state, "block_plan") or _event_doc(state, "block_plan")
        block_plan_file_id = _doc_drive_file_id(block_plan)
        if not block_plan_file_id:
            return M2StepResult(
                status="blocked",
                reason="No registered Block Plan source with a Drive file ID.",
            )
        site = _dict(state.get("site"))
        m1_folder_id = self._m1_folder_id(state)
        block_bytes = self.gc.download_file_bytes(block_plan_file_id)
        block_text = _text_from_document_bytes(block_bytes, _doc_title(block_plan))
        result = generate_alpha_capacity_analysis_artifact(
            self.gc,
            m1_folder_id=m1_folder_id,
            site_name=_text(site.get("name")),
            site_address=_text(site.get("address")),
            block_plan_content=block_text,
            block_plan_file_id=block_plan_file_id,
            block_plan_file_bytes=block_bytes,
            block_plan_file_name=_doc_title(block_plan) or "Block Plan.pdf",
        )
        if result.get("status") != "success":
            return M2StepResult(
                status=str(result.get("status") or "blocked"),
                reason=_text(result.get("message") or result.get("error")),
                raw=result,
            )
        registration = register_rhodes_document_for_upload(
            site_id=_site_id(state),
            ddr_doc_type="alpha_capacity_analysis",
            title=_text(result.get("artifact_name")) or f"Alpha Capacity Analysis - {_text(site.get('name'))}.json",
            drive_file_id=_text(result.get("capacity_analysis_file_id")),
            drive_url=_text(result.get("capacity_analysis_url")),
            mime_type="application/json",
            original_filename=_text(result.get("artifact_name")),
            source="m2_executor",
        )
        doc = _source_doc(
            source_type="alpha_capacity_analysis",
            title=_text(result.get("artifact_name")) or "Alpha Capacity Analysis",
            drive_file_id=_text(result.get("capacity_analysis_file_id")),
            drive_url=_text(result.get("capacity_analysis_url")),
            rhodes_doc_type=_text(registration.get("rhodes_doc_type")) or "capacityCalculation",
            registration_status=_text(registration.get("status")),
            fields_supported=("fast_open_capacity", "max_plan_capacity"),
        )
        return M2StepResult(
            status="success" if _doc_registered(doc) else "blocked",
            report_data_fields=_dict(result.get("report_data_fields")),
            supporting_documents=[doc],
            artifacts={"rhodes_registration": registration, "capacity_analysis": result},
            reason="" if _doc_registered(doc) else _text(registration.get("reason")),
            raw=result,
        )

    def write_due_diligence(self, site_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        return update_rhodes_due_diligence(site_id=site_id, fields=fields)

    def run_cost_timeline(self, state: dict[str, Any]) -> M2StepResult:
        existing = _registered_doc(state, "cost_timeline_estimate")
        if existing and _doc_drive_file_id(existing):
            payload = self._read_json_file(_doc_drive_file_id(existing))
            return M2StepResult(
                status="success" if _dict(payload.get("report_data_fields")) else "blocked",
                report_data_fields=_dict(payload.get("report_data_fields")),
                supporting_documents=[existing],
                artifacts={"reused_existing": True, "payload": payload},
                reason="" if _dict(payload.get("report_data_fields")) else "Existing Cost/Timeline Estimate lacks report_data_fields.",
            )

        site = self.rhodes.get_site(site_id=_site_id(state))
        estimator = _load_cost_timeline_estimator()
        payload = {
            "rhodes_site": site,
            "start_date": _today_iso(),
        }
        estimate = estimator(payload)
        report_fields = _dict(estimate.get("report_data_fields"))
        if not report_fields:
            return M2StepResult(
                status="blocked",
                reason="Cost/timeline estimate did not produce report_data_fields.",
                raw=estimate,
            )
        site_name = _text(_dict(state.get("site")).get("name")) or _text(site.get("name"))
        file_name = f"Cost Timeline Estimate - {_safe_file_part(site_name)}.json"
        uploaded = self.gc.upload_file_to_folder(
            self._m1_folder_id(state),
            file_name,
            json.dumps(estimate, indent=2, sort_keys=True).encode("utf-8"),
            mime_type="application/json",
        )
        registration = register_rhodes_document_for_upload(
            site_id=_site_id(state),
            ddr_doc_type="cost_timeline_estimate",
            title=file_name,
            drive_file_id=_text(uploaded.get("id")),
            drive_url=_text(uploaded.get("webViewLink")),
            mime_type="application/json",
            original_filename=file_name,
            source="m2_executor",
        )
        doc = _source_doc(
            source_type="cost_timeline_estimate",
            title=file_name,
            drive_file_id=_text(uploaded.get("id")),
            drive_url=_text(uploaded.get("webViewLink")),
            rhodes_doc_type=_text(registration.get("rhodes_doc_type")) or "initialCostEstimate",
            registration_status=_text(registration.get("status")),
            fields_supported=(
                "fast_open_date",
                "max_plan_date",
                "fast_open_capex",
                "max_plan_capex",
            ),
        )
        return M2StepResult(
            status="success" if _doc_registered(doc) else "blocked",
            report_data_fields=report_fields,
            supporting_documents=[doc],
            artifacts={"estimate": estimate, "rhodes_registration": registration},
            reason="" if _doc_registered(doc) else _text(registration.get("reason")),
            raw=estimate,
        )

    def run_outdoor_play(self, state: dict[str, Any]) -> M2StepResult:
        from . import server

        if _registered_doc(state, "outdoor_play_space_report"):
            return M2StepResult(
                status="success",
                supporting_documents=[cast(dict[str, Any], _registered_doc(state, "outdoor_play_space_report"))],
                artifacts={"reused_existing": True},
            )
        max_capacity = _int_from_text(_report_data(state).get("exec.max_capacity_capacity"))
        if max_capacity is None:
            return M2StepResult(status="blocked", reason="Max Capacity is required for outdoor play.")
        site = _dict(state.get("site"))
        result = _run_async(
            server.apply_outdoor_play_space_skill(
                site_name=_text(site.get("name")),
                site_id=_site_id(state),
                address=_text(site.get("address")),
                drive_folder_url=_drive_folder_url(state),
                student_count=max_capacity,
                student_count_source="max_plan_capacity",
            )
        )
        return _step_result_from_tool(result)

    def run_opening_plan(self, state: dict[str, Any]) -> M2StepResult:
        from . import server

        if _registered_doc(state, "opening_plan_report"):
            return M2StepResult(
                status="success",
                supporting_documents=[cast(dict[str, Any], _registered_doc(state, "opening_plan_report"))],
                artifacts={"reused_existing": True},
            )
        sir = _registered_doc(state, "sir")
        sir_file_id = _doc_drive_file_id(sir)
        if not sir_file_id:
            return M2StepResult(status="blocked", reason="SIR Drive file ID is required for Opening Plan.")
        sir_read = _run_async(server.read_drive_document(sir_file_id, _doc_title(sir) or "SIR.pdf"))
        sir_content = _text(sir_read.get("content") or sir_read.get("text_content") or sir_read.get("text"))
        if not sir_content:
            return M2StepResult(status="blocked", reason="SIR content could not be read for Opening Plan.")
        site = _dict(state.get("site"))
        result = _run_async(
            server.apply_opening_plan_skill(
                site_name=_text(site.get("name")),
                site_address=_text(site.get("address")),
                sir_content=sir_content,
                drive_folder_url=_drive_folder_url(state),
                site_id=_site_id(state),
                target_open_date=_text(_report_data(state).get("exec.fastest_open_open_date")),
            )
        )
        return _step_result_from_tool(result)

    def run_phase_1_phase_2(self, state: dict[str, Any]) -> M2StepResult:
        from . import server

        if _registered_doc(state, "alpha_phasing_plan_report"):
            return M2StepResult(
                status="success",
                supporting_documents=[cast(dict[str, Any], _registered_doc(state, "alpha_phasing_plan_report"))],
                artifacts={"reused_existing": True},
            )
        report_data = _report_data(state)
        site = _dict(state.get("site"))
        fastest_capacity = _text(report_data.get("exec.fastest_open_capacity"))
        max_capacity = _text(report_data.get("exec.max_capacity_capacity"))
        fastest_open_date = _text(report_data.get("exec.fastest_open_open_date"))
        result = _run_async(
            server.apply_alpha_phasing_plan_skill(
                site_name=_text(site.get("name")),
                drive_folder_url=_drive_folder_url(state),
                site_address=_text(site.get("address")),
                site_id=_site_id(state),
                source_of_truth="Cost/Timeline Estimate and Alpha Capacity Analysis",
                quality_bar_target="M2 Diligence source packet",
                opening_target_date=fastest_open_date,
                must_complete_before_opening=(
                    f"Phase I buildout for Fastest Open capacity ({fastest_capacity} students)."
                ),
                deferred_scopes=[
                    f"Max Capacity expansion from {fastest_capacity} to {max_capacity} students."
                ],
                phase_i_scope_summary=(
                    f"Open with the minimum scope needed for {fastest_capacity} students."
                ),
                phase_ii_total_allowance=_text(report_data.get("exec.max_capacity_capex")),
                recommended_timing="Defer Phase II until Max Capacity operating need is confirmed.",
                source_notes=[
                    "Generated from registered M2 Cost/Timeline Estimate and Alpha Capacity Analysis."
                ],
            )
        )
        return _step_result_from_tool(result)

    def run_security_due_diligence(self, state: dict[str, Any]) -> M2StepResult:
        existing = _registered_doc(state, "security_due_diligence_report")
        if existing:
            return M2StepResult(
                status="success",
                supporting_documents=[existing],
                artifacts={"reused_existing": True},
            )
        if not _security_due_diligence_is_due(state):
            return M2StepResult(
                status="success",
                message=(
                    "Security Due Diligence is not due until a block/floor plan "
                    "and Alpha Capacity Analysis are present."
                ),
            )
        return M2StepResult(
            status="blocked",
            reason=(
                "Security Due Diligence is ready but no registered memo is present. "
                "Run ops-skills:security-due-diligence with the site address, "
                "block/floor plan context, and Alpha Capacity Analysis context, "
                "then save/register the memo as security_due_diligence_report."
            ),
            raw={"resume_source_types": ["security_due_diligence_report"]},
        )

    def add_source_note(self, state: dict[str, Any], note_lines: Sequence[str]) -> dict[str, Any]:
        body = _source_note_body(state, note_lines)
        return add_rhodes_site_note(site_id=_site_id(state), body=body)

    def _m1_folder_id(self, state: dict[str, Any]) -> str:
        m1_folder_id, _m1_url = _resolve_m1_folder(
            self.gc,
            _drive_folder_url(state),
            create_if_missing=True,
            allow_legacy_fallback=False,
        )
        if not m1_folder_id:
            raise RuntimeError("Could not resolve M1 folder for M2 execution.")
        return m1_folder_id

    def _read_json_file(self, file_id: str) -> dict[str, Any]:
        raw = self.gc.download_file_bytes(file_id)
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, dict) else {}


def _is_executor_ready(state: dict[str, Any]) -> bool:
    if not m2_state_is_open(state):
        return False
    m2_state = _text(state.get("m2_state"))
    next_actions = set(_next_actions(state))
    if next_actions:
        return bool(next_actions & KNOWN_EXECUTOR_ACTIONS)
    return m2_state in (EXECUTABLE_M2_STATES - {"blocked"})


def _preview_row(event_id: str, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "site_id": _site_id(state),
        "site_name": _text(_dict(state.get("site")).get("name")),
        "status": "preview",
        "m2_state": _text(state.get("m2_state")),
        "would_execute": _planned_actions(state),
    }


def _planned_actions(state: dict[str, Any]) -> list[str]:
    actions = [action for action in _next_actions(state) if action in KNOWN_EXECUTOR_ACTIONS]
    if actions:
        return actions
    m2_state = _text(state.get("m2_state"))
    if m2_state == "capacity_ready":
        return ["run_alpha_capacity_analysis", "write_capacity_fields"]
    if m2_state == "capacity_written":
        return ["run_cost_timeline_estimate"]
    if m2_state == "waiting_for_external_sources":
        return ["run_downstream_source_skills"]
    if m2_state == "source_packet_ready":
        return ["build_m2_source_packet", "write_packet_approved_dd_fields"]
    if m2_state == "dd_write_pending":
        return ["write_packet_approved_dd_fields", "add_source_note"]
    if m2_state == "blocked":
        return [action for action in _next_actions(state) if action]
    return []


def _needs_alpha_capacity(state: dict[str, Any]) -> bool:
    return not _has_doc(state, "alpha_capacity_analysis") and (
        _text(state.get("m2_state")) == "capacity_ready"
        or "run_alpha_capacity_analysis" in _next_actions(state)
    )


def _needs_capacity_write(state: dict[str, Any]) -> bool:
    if _text(state.get("m2_state")) not in {"capacity_ready", "blocked"}:
        return False
    return bool(_has_doc(state, "alpha_capacity_analysis")) or _has_capacity_report_fields(state)


def _needs_cost_timeline(state: dict[str, Any]) -> bool:
    return not _has_doc(state, "cost_timeline_estimate") and (
        _text(state.get("m2_state")) == "capacity_written"
        or "run_cost_timeline_estimate" in _next_actions(state)
    )


def _needs_downstream_sources(state: dict[str, Any]) -> bool:
    required = _required_downstream_source_types(state)
    return not all(_has_doc(state, source_type) for source_type in required) and (
        _text(state.get("m2_state")) == "waiting_for_external_sources"
        or "run_downstream_source_skills" in _next_actions(state)
        or "run_security_due_diligence" in _next_actions(state)
    )


def _required_downstream_source_types(state: dict[str, Any]) -> set[str]:
    required = {
        "outdoor_play_space_report",
        "opening_plan_report",
        "alpha_phasing_plan_report",
    }
    if _security_due_diligence_is_due(state):
        required.add("security_due_diligence_report")
    return required


def _finish(
    state: dict[str, Any],
    steps: list[dict[str, Any]],
    timestamp: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    state["updated_at"] = timestamp
    history = _list_of_dicts(state.get("m2_execution_steps"))
    state["m2_execution_steps"] = [*history, *steps]
    return state, {
        "status": _text(state.get("status")) or "blocked",
        "changed": True,
        "site_id": _site_id(state),
        "site_name": _text(_dict(state.get("site")).get("name")),
        "m2_state": _text(state.get("m2_state")),
        "open_blockers": _list_of_dicts(state.get("open_blockers")),
        "steps": steps,
        "source_packet_status": _text(_dict(state.get("source_packet")).get("status")),
    }


def _set_blocker(
    state: dict[str, Any],
    *,
    blocker_id: str,
    m2_state: str,
    reason: str,
    next_action: str,
    resume_source_types: Sequence[str] = (),
) -> None:
    state["status"] = "blocked"
    state["m2_state"] = m2_state
    state["open_blockers"] = [
        {
            "id": blocker_id,
            "m2_state": m2_state,
            "reason": reason,
            "resume_source_types": sorted({_canonical_source_type(value) for value in resume_source_types}),
            "next_action": next_action,
        }
    ]


def _merge_step_result(state: dict[str, Any], result: M2StepResult) -> None:
    report_data = _report_data(state)
    report_data.update(result.report_data_fields)
    state["report_data_fields"] = report_data
    docs = [
        *_supporting_documents(state),
        *result.supporting_documents,
    ]
    state["supporting_documents"] = _dedupe_docs(docs)
    registered = [
        *_list_of_dicts(state.get("registered_documents")),
        *result.supporting_documents,
    ]
    state["registered_documents"] = _dedupe_docs(registered)


def _build_packet(state: dict[str, Any]) -> dict[str, Any]:
    return build_m2_source_packet(
        values=_report_data(state),
        supporting_documents=_supporting_documents(state),
    )


def _packet_ready_for_write(packet: dict[str, Any]) -> bool:
    for doc in _list_of_dicts(packet.get("supporting_documents")):
        if _text(doc.get("registration_status")) not in REGISTERED_STATUSES:
            return False
        if not _text(doc.get("rhodes_doc_type")):
            return False
    for update in _list_of_dicts(packet.get("dd_field_updates")):
        status = _text(update.get("write_status"))
        locationos_key = _text(update.get("locationos_key"))
        if status == "blocked":
            return False
        if locationos_key and status not in {"pending", "written", "updated"}:
            return False
        if not locationos_key and _text(update.get("hold_reason")) not in {
            "locationos_schema_gap",
            "",
        }:
            return False
    return True


def _packet_blocker_reason(packet: dict[str, Any]) -> str:
    open_items = [str(item) for item in packet.get("open_items", []) if str(item).strip()]
    actionable = [
        item
        for item in open_items
        if "write not completed" not in item and "readback not verified" not in item
    ]
    return "; ".join(actionable[:5] or open_items[:5] or ["M2 source packet is blocked."])


def _pending_locationos_fields(packet: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for update in _list_of_dicts(packet.get("dd_field_updates")):
        if _text(update.get("write_status")) != "pending":
            continue
        key = _text(update.get("locationos_key"))
        if not key:
            continue
        fields[key] = _normalize_locationos_value(key, update.get("value"))
    return fields


def _capacity_locationos_fields(state: dict[str, Any]) -> dict[str, Any]:
    report_data = _report_data(state)
    fields: dict[str, Any] = {}
    if value := _normalize_locationos_value(
        "foCapacity",
        report_data.get("exec.fastest_open_capacity"),
    ):
        fields["foCapacity"] = value
    if value := _normalize_locationos_value(
        "maxCapCapacity",
        report_data.get("exec.max_capacity_capacity"),
    ):
        fields["maxCapCapacity"] = value
    return fields


def _normalize_locationos_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key in NUMERIC_KEYS or key in SCORE_KEYS:
        number = _number_from_text(value)
        if number is not None:
            return number
    return value


def _source_note_body(state: dict[str, Any], note_lines: Sequence[str]) -> str:
    site_name = _text(_dict(state.get("site")).get("name")) or "site"
    lines = [line for line in note_lines if str(line).strip()]
    bullets = "\n".join(f"- {line}" for line in lines[:14])
    return f"M2 source packet completed for {site_name}.\n{bullets}".strip()


def _step_row(step: str, result: M2StepResult) -> dict[str, Any]:
    row = {
        "step": step,
        "status": result.status,
        "message": result.message,
        "reason": result.reason,
        "report_data_fields": sorted(result.report_data_fields),
        "supporting_documents": [
            _text(doc.get("source_type")) for doc in result.supporting_documents
        ],
    }
    if result.artifacts:
        row["artifacts"] = _json_safe(result.artifacts)
    return row


def _dict_step_row(step: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "step": step,
        "status": _text(result.get("status")),
        "reason": _text(result.get("reason")),
        "error": _text(result.get("error") or result.get("error_summary")),
        "updated_fields": result.get("updated_fields", []),
    }


def _step_result_from_tool(result: dict[str, Any]) -> M2StepResult:
    docs = _list_of_dicts(result.get("supporting_documents"))
    registration = result.get("rhodes_registration")
    if not docs and isinstance(registration, dict):
        source_type = _text(result.get("source_type") or result.get("doc_type"))
        if source_type:
            docs = [
                _source_doc(
                    source_type=source_type,
                    title=_text(
                        result.get("doc_name")
                        or result.get("workbook_name")
                        or result.get("artifact_name")
                        or source_type.replace("_", " ").title()
                    ),
                    drive_file_id=_text(
                        result.get("doc_id")
                        or result.get("workbook_id")
                        or result.get("drive_file_id")
                    ),
                    drive_url=_text(
                        result.get("doc_url")
                        or result.get("workbook_url")
                        or result.get("drive_url")
                    ),
                    rhodes_doc_type=_text(registration.get("rhodes_doc_type")) or "other",
                    registration_status=_text(registration.get("status")),
                )
            ]
    status = _text(result.get("status")) or "blocked"
    if status == "success" and docs and not any(_doc_registered(doc) for doc in docs):
        status = "blocked"
    return M2StepResult(
        status=status,
        report_data_fields=_dict(result.get("report_data_fields")),
        supporting_documents=docs,
        artifacts={"tool_result": result},
        message=_text(result.get("message")),
        reason=_text(result.get("reason") or result.get("error")),
        raw=result,
    )


def _step_succeeded(result: M2StepResult) -> bool:
    return result.status == "success"


def _step_reason(result: M2StepResult, fallback: str) -> str:
    return result.reason or result.message or fallback


def _resume_source_types(result: M2StepResult) -> list[str]:
    return [
        value
        for value in _string_list(_dict(result.raw).get("resume_source_types"))
        if value
    ]


def _write_reason(result: dict[str, Any], fallback: str) -> str:
    return _text(
        result.get("error_summary")
        or result.get("error")
        or result.get("reason")
        or result.get("message")
    ) or fallback


def _has_capacity_report_fields(state: dict[str, Any]) -> bool:
    report_data = _report_data(state)
    return bool(
        _text(report_data.get("exec.fastest_open_capacity"))
        and _text(report_data.get("exec.max_capacity_capacity"))
    )


def _security_due_diligence_is_due(state: dict[str, Any]) -> bool:
    return _has_doc(state, "alpha_capacity_analysis") and _has_security_plan_source(state)


def _has_security_plan_source(state: dict[str, Any]) -> bool:
    source_types = {
        "block_plan",
        "floor_plan",
        "measured_floor_plan",
        "fastest_open_block_plan",
        "max_capacity_block_plan",
    }
    return any(_has_doc(state, source_type) or _event_doc(state, source_type) for source_type in source_types)


def _has_doc(state: dict[str, Any], source_type: str) -> bool:
    return _registered_doc(state, source_type) is not None


def _registered_doc(state: dict[str, Any], source_type: str) -> dict[str, Any] | None:
    for doc in _supporting_documents(state):
        if _canonical_source_type(_text(doc.get("source_type"))) != source_type:
            continue
        if _doc_registered(doc):
            return doc
    return None


def _event_doc(state: dict[str, Any], source_type: str) -> dict[str, Any] | None:
    for event in _list_of_dicts(state.get("source_events")):
        if _canonical_source_type(_text(event.get("source_type") or event.get("doc_type"))) == source_type:
            return {
                "source_type": source_type,
                "title": _text(event.get("file_name") or event.get("name")),
                "drive_file_id": _text(event.get("drive_file_id") or event.get("id")),
                "drive_url": _text(event.get("drive_url") or event.get("webViewLink")),
                "registration_status": "registered",
                "readback_status": "verified",
            }
    return None


def _supporting_documents(state: dict[str, Any]) -> list[dict[str, Any]]:
    return _dedupe_docs(
        [
            *_list_of_dicts(state.get("registered_documents")),
            *_list_of_dicts(state.get("supporting_documents")),
        ]
    )


def _dedupe_docs(docs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for doc in docs:
        source_type = _canonical_source_type(_text(doc.get("source_type") or doc.get("doc_type")))
        if not source_type:
            continue
        row = dict(doc)
        row["source_type"] = source_type
        key = (
            source_type,
            _doc_drive_file_id(row),
            _text(row.get("title") or row.get("name")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _source_doc(
    *,
    source_type: str,
    title: str,
    drive_file_id: str,
    drive_url: str,
    rhodes_doc_type: str,
    registration_status: str,
    fields_supported: Sequence[str] = (),
) -> dict[str, Any]:
    return SourceDocumentRef(
        source_type=source_type,
        title=title,
        drive_url=drive_url,
        drive_file_id=drive_file_id,
        rhodes_doc_type=rhodes_doc_type,
        registration_status=registration_status,
        fields_supported=tuple(fields_supported),
    ).to_dict()


def _doc_registered(doc: dict[str, Any]) -> bool:
    return _text(doc.get("registration_status") or doc.get("status")) in REGISTERED_STATUSES


def _doc_drive_file_id(doc: dict[str, Any] | None) -> str:
    if not isinstance(doc, dict):
        return ""
    return _text(doc.get("drive_file_id") or doc.get("driveFileId") or doc.get("file_id") or doc.get("id"))


def _doc_title(doc: dict[str, Any] | None) -> str:
    if not isinstance(doc, dict):
        return ""
    return _text(doc.get("title") or doc.get("name") or doc.get("file_name"))


def _report_data(state: dict[str, Any]) -> dict[str, Any]:
    raw = state.get("report_data_fields")
    if isinstance(raw, dict):
        return dict(raw)
    raw = state.get("report_data")
    return dict(raw) if isinstance(raw, dict) else {}


def _site_id(state: dict[str, Any]) -> str:
    return _text(_dict(state.get("site")).get("id"))


def _drive_folder_url(state: dict[str, Any]) -> str:
    return _text(_dict(state.get("drive")).get("site_folder_url"))


def _next_actions(state: dict[str, Any]) -> list[str]:
    return [
        _text(blocker.get("next_action"))
        for blocker in _list_of_dicts(state.get("open_blockers"))
        if _text(blocker.get("next_action"))
    ]


def _load_cost_timeline_estimator() -> Any:
    spec = importlib.util.spec_from_file_location(
        "cost_and_timeline_estimate_runtime",
        ESTIMATOR_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load estimator at {ESTIMATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    estimate = getattr(module, "estimate", None)
    if not callable(estimate):
        raise RuntimeError("Cost/timeline estimator does not expose estimate(payload).")
    return estimate


def _text_from_document_bytes(document_bytes: bytes, file_name: str) -> str:
    if file_name.lower().endswith(".pdf"):
        return extract_text_from_pdf_bytes(document_bytes)
    return document_bytes.decode("utf-8", errors="replace")


def _run_async(awaitable: Any) -> dict[str, Any]:
    result = asyncio.run(awaitable)
    return result if isinstance(result, dict) else {}


def _number_from_text(value: Any) -> int | float | None:
    text = _text(value)
    if not text:
        return None
    cleaned = re.sub(r"[$,\s]", "", text)
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _int_from_text(value: Any) -> int | None:
    number = _number_from_text(value)
    return int(number) if number is not None else None


def _canonical_source_type(value: str) -> str:
    normalized = value.strip().replace("-", "_")
    aliases = {
        "initial_cost_estimate": "cost_timeline_estimate",
        "initialcostestimate": "cost_timeline_estimate",
        "cost_and_timeline_estimate": "cost_timeline_estimate",
        "security_due_diligence": "security_due_diligence_report",
        "securityduediligence": "security_due_diligence_report",
        "securityduediligencereport": "security_due_diligence_report",
    }
    return aliases.get(normalized, aliases.get(normalized.casefold(), normalized))


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _json_clone(value: Mapping[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(json.dumps(value)))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _safe_file_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "", value).strip() or "Site"


def _today_iso() -> str:
    return datetime.now(UTC).date().isoformat()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
