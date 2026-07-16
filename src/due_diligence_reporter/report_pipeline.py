"""Shared report pipeline — readiness check, Claude agent loop, and notifications.

Extracted from ``scripts/daily_dd_check.py`` so that both the daily sweep
and the 15-minute inbox scanner can trigger report generation for a single site.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import anthropic

from .automation_event import (
    build_dd_report_republish_candidate_event,
    build_dd_report_summary_event,
    build_source_review_required_event,
    build_vendor_gate_review_required_event,
)
from .classifier import (
    AI_GENERATED_DOC_TYPES,
    SITE_FOLDER_DOC_TYPES,
    classify_document_type,
    is_site_folder_scan_candidate,
    match_file_to_site_llm,
)
from .completeness import raycon_token_paths
from .config import Settings, get_settings
from .google_client import GoogleClient
from .m1_lookup import _list_m1_documents_by_type, _resolve_m1_folder
from .open_questions import (
    close_open_questions,
    extract_open_questions_from_report_data,
    serialize_open_questions,
)
from .pipeline_contracts import (
    ArtifactRef,
    PipelineError,
    PipelineRun,
    StepResult,
    StepStatus,
    failed_step_name,
    make_run_id,
    next_operator_action,
    utc_now_iso,
)
from .pipeline_manifest import (
    RUN_MANIFEST_DIR,
    load_run_manifest,
    manifest_has_secret_like_value,
    persist_run_manifest,
)
from .pipeline_quality import evaluate_run_quality
from .provenance import classify_provenance
from .raycon_client import (
    RAYCON_FAILED_STATUSES,
    RayConSchemaError,
    raycon_payload_failed,
    raycon_payload_status,
    raycon_scenario_to_report_fields,
    read_raycon_scenario_from_m1,
)
from .report_schema import AGENT_KEY_ALIASES
from .rhodes import (
    add_rhodes_site_note,
    lookup_rhodes_site_owner,
    update_rhodes_due_diligence,
    verify_rhodes_due_diligence_fields,
)
from .rhodes_events import (
    post_google_chat_to_configured_webhooks,
    record_rhodes_automation_event,
    should_alert_google_chat,
)
from .sir_learning import build_sir_learning_review
from .source_packet import (
    dd_field_update_sources,
    locationos_fields_allowed_by_source_packet,
    mark_written_fields_from_update_result,
    source_packet_is_complete,
)
from .utils import (
    escape_html_text,
    extract_folder_id_from_url,
    flatten_report_data_for_replacement,
    post_google_chat_message,
    sanitize_http_url,
    score_site_match_strength,
    send_email,
)

logger = logging.getLogger("report_pipeline")

_RAYCON_REPORT_FIELD_PATHS: frozenset[str] = frozenset(raycon_token_paths())
_COST_TIMELINE_REQUIRED_REPORT_FIELDS: frozenset[str] = frozenset(
    {
        "exec.fastest_open_open_date",
        "exec.max_capacity_open_date",
        "exec.fastest_open_capex",
        "exec.max_capacity_capex",
    }
)
DD_REPORT_EVENT_FREQUENCY_CAP_BUSINESS_DAYS = 2
DUE_DILIGENCE_UPDATE_STEP = "rhodes.due_diligence_update"
LOCATIONOS_MCP_WRITE_REQUIRED_STATUS = "locationos_mcp_write_required"
LOCATIONOS_MCP_WRITE_REQUEST_KEY = "locationos_mcp_write_request"
LOCATIONOS_MCP_RESUME_SCHEMA_VERSION = "locationos_mcp_resume.v1"
LOCATIONOS_MCP_WRITE_MODES = frozenset({"api", "mcp_assisted"})
DD_REPORT_CREATED_SOR_PENDING_WARNING = (
    "DD Report Google Doc created; Rhodes/LocationOS dueDiligence write or "
    "readback is pending manual verification."
)

_DUE_DILIGENCE_REPORT_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("exec.fastest_open_capacity", "foCapacity"),
    ("exec.fastest_open_capex", "foCapEx"),
    ("exec.fastest_open_open_date", "foDate"),
    ("exec.max_capacity_capacity", "maxCapCapacity"),
    ("exec.max_capacity_capex", "maxCapCapEx"),
    ("exec.max_capacity_open_date", "maxCapProjOpenDate"),
    ("exec.regulatory_score", "regulatoryScore"),
    ("exec.regulatory_comment", "regulatoryComment"),
    ("exec.building_score", "buildingScore"),
    ("exec.building_comment", "buildingComment"),
    ("exec.play_area_score", "playAreaScore"),
    ("exec.play_area_comment", "playAreaComment"),
    ("exec.school_ops_score", "schoolOperationsScore"),
    ("exec.school_ops_comment", "schoolOperationsComment"),
)

_DUE_DILIGENCE_SCORE_FIELD_KEYS: frozenset[str] = frozenset(
    {
        "regulatoryScore",
        "buildingScore",
        "playAreaScore",
        "schoolOperationsScore",
    }
)
_DUE_DILIGENCE_NUMERIC_FIELD_KEYS: frozenset[str] = frozenset(
    {
        "foCapEx",
        "foCapacity",
        "maxCapCapEx",
        "maxCapCapacity",
    }
)
_DUE_DILIGENCE_GREEN_SCORE_COMMENT_PAIRS: tuple[tuple[str, str], ...] = (
    ("regulatoryScore", "regulatoryComment"),
    ("buildingScore", "buildingComment"),
    ("playAreaScore", "playAreaComment"),
    ("schoolOperationsScore", "schoolOperationsComment"),
)
_DUE_DILIGENCE_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
_DUE_DILIGENCE_SCORE_LABELS: dict[str, int] = {
    "green": 1,
    "yellow": 2,
    "red": 3,
}

_DUE_DILIGENCE_RECOMMENDATION_KEYS: tuple[str, ...] = (
    "due_diligence.recommendation",
    "dueDiligence.recommendation",
    "recommendation",
)

# ─────────────────────────────────────────────────────────────────────────────
# Tool definitions for the Claude API call (mirrors the MCP tools)
# ─────────────────────────────────────────────────────────────────────────────

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_drive_documents",
        "description": "List matched shared DD source reports plus site-folder artifacts found in the site folder or its M1 subfolder. Results may include Block Plan PDFs and derived reports such as Capacity Brainlift, RayCon Scenario, Opening Plan, Phase 1 Phase 2 workbook, and DD reports. Each file includes a doc_type field. If drive_folder_url is not already known, pass site_name and site_address so the tool can resolve the linked site folder from Rhodes instead of asking the user for a folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drive_folder_url": {"type": "string", "description": "Google Drive folder URL, when supplied by the request or returned by Rhodes"},
                "site_name": {"type": "string", "description": "Site name used to match docs in shared folders"},
                "site_address": {"type": "string", "description": "Optional full property address used to strengthen shared-folder matching"},
            },
            "required": [],
        },
    },
    {
        "name": "read_drive_document",
        "description": "Read and return the text content of a Google Drive file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "file_name": {"type": "string"},
            },
            "required": ["file_id", "file_name"],
        },
    },
    {
        "name": "lookup_rhodes_site_owner",
        "description": (
            "Read the Rhodes/LocationOS site record for the supplied site and return "
            "the current P1 DRI / site owner and linked Google Drive folder URL. "
            "Call this before list_drive_documents or prepare_due_diligence_data when the "
            "request does not include a Drive folder URL. "
            "Use returned report_data_fields for meta.prepared_by and include the "
            "owner email in send_dd_report_email additional_recipients."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name": {"type": "string", "description": "Site display name"},
                "site_address": {"type": "string", "description": "Full site address"},
                "site_id": {"type": "string", "description": "Optional Rhodes site ID"},
                "slug": {"type": "string", "description": "Optional Rhodes site slug"},
            },
        },
    },
    {
        "name": "apply_e_occupancy_skill",
        "description": "Apply E-Occupancy scoring to a building using the hosted Ops-Skills ease-of-conversion rating contract. Pass site_name and drive_folder_url to auto-publish the assessment as a Google Doc in the M1 subfolder - the returned doc_url can be used as sources.e_occupancy_link.",
        "input_schema": {
            "type": "object",
            "properties": {
                "building_type_description": {"type": "string"},
                "stories": {"type": "integer"},
                "floor_level": {"type": "integer", "default": 1},
                "shared_hvac": {"type": "boolean", "default": False},
                "shared_egress": {"type": "boolean", "default": False},
                "building_management_approval_required": {"type": "boolean", "default": False},
                "no_dedicated_entrance": {"type": "boolean", "default": False},
                "no_outdoor_space": {"type": "boolean", "default": False},
                "shared_parking": {"type": "boolean", "default": False},
                "incompatible_tenants": {"type": "boolean", "default": False},
                "site_id": {"type": "string", "default": "", "description": "Rhodes site ID when available; enables Rhodes document registration"},
                "site_name": {"type": "string", "default": "", "description": "Site name — pass to auto-publish assessment to Drive"},
                "drive_folder_url": {"type": "string", "default": "", "description": "Site Drive folder URL — pass to auto-publish"},
            },
            "required": ["building_type_description", "stories"],
        },
    },
    {
        "name": "apply_school_approval_skill",
        "description": "Determine school registration requirements for a US state. Pass site_name and drive_folder_url to auto-publish the assessment as a Google Doc in the M1 subfolder — the returned doc_url can be used as sources.school_approval_link.",
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "Two-letter US state abbreviation"},
                "address": {"type": "string", "default": "", "description": "Full site address; preferred when available"},
                "site_id": {"type": "string", "default": "", "description": "Rhodes site ID when available; enables Rhodes document registration"},
                "site_name": {"type": "string", "default": "", "description": "Site name — pass to auto-publish assessment to Drive"},
                "drive_folder_url": {"type": "string", "default": "", "description": "Site Drive folder URL — pass to auto-publish"},
            },
            "required": [],
        },
    },
    {
        "name": "apply_opening_plan_skill",
        "description": "Create or reuse the Opening Plan Google Doc after source reads and School Approval context are available, before Alpha Phasing and prepare_due_diligence_data. Pass the full SIR text as sir_content plus optional School Approval and Building Inspection text. On success, copy returned report_data_fields into report_data, especially sources.opening_plan_link.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name": {"type": "string"},
                "site_id": {"type": "string", "default": "", "description": "Rhodes site ID when available; enables Rhodes document registration"},
                "site_address": {"type": "string"},
                "sir_content": {"type": "string", "description": "Full text of the SIR / AI SIR baseline read from Drive"},
                "drive_folder_url": {"type": "string", "default": ""},
                "school_approval_data": {"type": "string", "default": "", "description": "Optional School Approval report text or compact JSON"},
                "building_inspection_content": {"type": "string", "default": "", "description": "Optional Building Inspection report text"},
                "target_open_date": {"type": "string", "default": ""},
            },
            "required": ["site_name", "site_address", "sir_content"],
        },
    },
    {
        "name": "apply_alpha_capacity_analysis_skill",
        "description": "Run hosted Ops-Skills alpha-capacity-analysis from Block Plan text. Pass the full extracted Block Plan text from read_drive_document, plus site_name, site_address, drive_folder_url, block_plan_file_id, and total_building_sf when known. On success, DDR saves a machine-readable Alpha Capacity Analysis JSON artifact in M1 and returns report_data_fields for exec.fastest_open_capacity and exec.max_capacity_capacity. The Cost/Timeline Estimate runs after Rhodes capacity readback; do not use this tool for construction cost.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name": {"type": "string"},
                "site_id": {"type": "string", "default": "", "description": "Rhodes site ID when available; enables Rhodes document registration"},
                "site_address": {"type": "string", "default": ""},
                "block_plan_content": {"type": "string"},
                "drive_folder_url": {"type": "string", "default": ""},
                "block_plan_file_id": {"type": "string", "default": ""},
                "total_building_sf": {"type": "integer", "default": 0},
            },
            "required": ["site_name", "block_plan_content"],
        },
    },
    {
        "name": "apply_outdoor_play_space_skill",
        "description": "Run Ops-Skills outdoor-play-space after Alpha Capacity Analysis has produced Max Plan capacity. Pass site_name, site_id, address, drive_folder_url, and student_count=max_plan_capacity. The tool runs with --skip-drive-upload, then DDR uploads/registers Markdown, JSON, PNG, and HTML artifacts to the site folder. On success, copy exec.play_area_score and exec.play_area_comment into report_data and include returned supporting_documents in prepare_due_diligence_data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name": {"type": "string"},
                "site_id": {"type": "string"},
                "address": {"type": "string"},
                "drive_folder_url": {"type": "string"},
                "student_count": {"type": "integer", "description": "Use max_plan_capacity when available; fast_open_capacity is interim only"},
                "max_walk_minutes": {"type": "number", "default": 5},
                "on_site_green_sf": {"type": "number"},
                "on_site_playscape": {"type": "string", "default": "unknown"},
                "marked_outdoor_sf": {"type": "number"},
                "marked_outdoor_source": {"type": "string", "default": ""},
                "marked_outdoor_note": {"type": "string", "default": ""},
                "student_count_source": {"type": "string", "default": "max_plan_capacity"},
                "max_plan_capacity_not_applicable": {"type": "boolean", "default": False},
            },
            "required": ["site_name", "site_id", "address", "drive_folder_url", "student_count"],
        },
    },
    {
        "name": "apply_alpha_phasing_plan_skill",
        "description": "Create and publish the Phase 1 Phase 2 workbook after source reads, E-Occupancy, School Approval, and Cost/Timeline Estimate context are available. Only call this tool when you intend to publish: missing judgment inputs (source of truth, quality bar target, opening target date, Phase I scope, Phase II deferred scope) are auto-accepted from skill recommendations, recorded in auto_accepted_inputs, and the P2 DRI is notified for after-the-fact review - do not probe-call it to discover missing inputs. If a workbook already exists in M1 it is reused without a new notification. On success, copy returned report_data_fields into report_data before prepare_due_diligence_data, including sources.alpha_phasing_plan_link and exec.alpha_phasing_* summary fields.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name": {"type": "string"},
                "site_id": {"type": "string", "default": "", "description": "Rhodes site ID when available; enables Rhodes document registration"},
                "site_address": {"type": "string", "default": ""},
                "drive_folder_url": {"type": "string"},
                "source_of_truth": {"type": "string", "description": "Confirmed phasing source, live sheet, budget tracker, or source document name/link"},
                "quality_bar_target": {"type": "string", "description": "Quality bar target, e.g. Q1 / Standard"},
                "opening_target_date": {"type": "string"},
                "must_complete_before_opening": {"type": "string", "description": "Confirmed Phase I opening scope"},
                "deferred_scopes": {"type": "array", "items": {"type": "string"}, "description": "Confirmed Phase II deferred scopes only"},
                "phase_i_scope_summary": {"type": "string", "default": ""},
                "phase_i_budget_items": {"type": "array", "items": {"type": "object"}, "default": []},
                "phase_ii_budget_items": {"type": "array", "items": {"type": "object"}, "default": []},
                "phase_ii_total_allowance": {"type": "string", "default": ""},
                "recommended_timing": {"type": "string", "default": ""},
                "render_deck_inputs": {"type": "array", "items": {"type": "object"}, "default": []},
                "source_notes": {"type": "array", "items": {"type": "string"}, "default": []},
                "budget_tracker_url": {"type": "string", "default": ""},
            },
            "required": [
                "site_name",
                "drive_folder_url",
                "source_of_truth",
                "quality_bar_target",
                "opening_target_date",
                "must_complete_before_opening",
                "deferred_scopes",
            ],
        },
    },
    {
        "name": "prepare_due_diligence_data",
        "description": "Normalize due-diligence report_data without creating a Google Doc. Call this after source reads and enrichment tools, before create_dd_report, so the pipeline can publish structured DD fields to Rhodes first. The report_data dict must use exact current template token keys. Copy report_data_fields from skill tools directly into report_data. Pass token_evidence for source traceability.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name": {"type": "string"},
                "drive_folder_url": {"type": "string"},
                "site_address": {"type": "string", "description": "Optional full property address used for deterministic REBL site ID resolution"},
                "report_data": {"type": "object"},
                "token_evidence": {"type": "object", "description": "Optional dict mapping token names to raw source excerpts for local diagnostics"},
                "supporting_documents": {"type": "array", "items": {"type": "object"}, "description": "M2 source-packet supporting document refs returned by source-reading and skill tools"},
            },
            "required": ["site_name", "drive_folder_url", "report_data"],
        },
    },
    {
        "name": "create_dd_report",
        "description": "Create a completed DD report Google Doc. The report_data dict must use exact current template token keys (e.g. 'exec.c_zoning', 'exec.fastest_open_capex', 'sources.sir_link'). Copy report_data_fields from skill tools directly into report_data. Pass token_evidence for source traceability.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name": {"type": "string"},
                "drive_folder_url": {"type": "string"},
                "site_address": {"type": "string", "description": "Optional full property address used for deterministic REBL site ID resolution"},
                "report_data": {"type": "object"},
                "token_evidence": {"type": "object", "description": "Optional dict mapping token names to raw source excerpts for local diagnostics"},
            },
            "required": ["site_name", "drive_folder_url", "report_data"],
        },
    },
    {
        "name": "check_report_completeness",
        "description": "Check a generated DD report for unresolved placeholders.",
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string"},
            },
            "required": ["doc_id"],
        },
    },
    {
        "name": "save_skill_report",
        "description": "Save a skill assessment (E-Occupancy, School Approval, Capacity Brainlift, or RayCon Scenario) as a standalone Google Doc in the site's M1 subfolder. The Phase 1 Phase 2 workbook uses apply_alpha_phasing_plan_skill because its required output is an Excel workbook. Pass the FULL result dict from the skill tool as skill_data — the tool formats it into a readable document. Returns doc_url for inclusion in sources.* tokens.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Skill name, e.g. 'E-Occupancy', 'School Approval', 'Capacity Brainlift', or 'RayCon Scenario'"},
                "site_name": {"type": "string", "description": "Site name for the document title"},
                "drive_folder_url": {"type": "string", "description": "Google Drive folder URL for the site"},
                "skill_data": {"type": "object", "description": "Full result dict from the skill tool (pass the entire response)"},
                "site_id": {"type": "string", "default": "", "description": "Rhodes site ID when available; enables Rhodes document registration"},
                "ddr_doc_type": {"type": "string", "default": "", "description": "DDR doc type to map to Rhodes when registering the generated report"},
            },
            "required": ["skill_name", "site_name", "drive_folder_url", "skill_data"],
        },
    },
    {
        "name": "send_dd_report_email",
        "description": "Send the completed DD report by email to configured recipients plus optional additional recipients (e.g. P1 Assignee).",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name": {"type": "string", "description": "Site name for the email subject line"},
                "report_url": {"type": "string", "description": "URL of the generated DD report Google Doc"},
                "key_findings": {"type": "string", "description": "Short summary of key findings for the email body"},
                "additional_recipients": {"type": "string", "default": "", "description": "Comma-separated email addresses to add"},
            },
            "required": ["site_name", "report_url", "key_findings"],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Tool router — calls the actual Python functions from the MCP server
# ─────────────────────────────────────────────────────────────────────────────


def routed_tool_map() -> dict[str, Any]:
    """Name → server function map for agent tool routing (shared with tests)."""
    from . import server as srv

    return {
        "list_drive_documents": srv.list_drive_documents,
        "read_drive_document": srv.read_drive_document,
        "lookup_rhodes_site_owner": srv.lookup_rhodes_site_owner,
        "apply_e_occupancy_skill": srv.apply_e_occupancy_skill,
        "apply_school_approval_skill": srv.apply_school_approval_skill,
        "apply_opening_plan_skill": srv.apply_opening_plan_skill,
        "apply_alpha_capacity_analysis_skill": srv.apply_alpha_capacity_analysis_skill,
        "apply_outdoor_play_space_skill": srv.apply_outdoor_play_space_skill,
        "apply_alpha_phasing_plan_skill": srv.apply_alpha_phasing_plan_skill,
        "prepare_due_diligence_data": srv.prepare_due_diligence_data,
        "create_dd_report": srv.create_dd_report,
        "check_report_completeness": srv.check_report_completeness,
        "save_skill_report": srv.save_skill_report,
        "send_dd_report_email": srv.send_dd_report_email,
    }


async def route_tool_call(tool_name: str, tool_input: dict[str, Any]) -> Any:
    """Route a Claude API tool call to the corresponding Python function."""
    fn = routed_tool_map().get(tool_name)
    if fn is None:
        return {"status": "error", "message": f"Unknown tool: {tool_name}"}

    return await fn(**tool_input)


def route_tool_call_sync(tool_name: str, tool_input: dict[str, Any]) -> Any:
    """Synchronous wrapper for route_tool_call."""
    import asyncio

    return asyncio.run(route_tool_call(tool_name, tool_input))


def _canonicalize_site_tool_input(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    site_title: str,
    drive_folder_url: str | None,
    site_address: str | None,
    site_id: str | None = None,
) -> dict[str, Any]:
    """Keep agent tool calls anchored to the pipeline's canonical site context."""
    canonical = dict(tool_input)
    if tool_name in {
        "lookup_rhodes_site_owner",
        "list_drive_documents",
        "apply_e_occupancy_skill",
        "apply_school_approval_skill",
        "apply_opening_plan_skill",
        "apply_alpha_capacity_analysis_skill",
        "apply_outdoor_play_space_skill",
        "apply_alpha_phasing_plan_skill",
        "prepare_due_diligence_data",
        "create_dd_report",
        "save_skill_report",
        "send_dd_report_email",
    }:
        canonical["site_name"] = site_title
    if site_id and tool_name in {
        "lookup_rhodes_site_owner",
        "apply_e_occupancy_skill",
        "apply_school_approval_skill",
        "apply_opening_plan_skill",
        "apply_alpha_capacity_analysis_skill",
        "apply_outdoor_play_space_skill",
        "apply_alpha_phasing_plan_skill",
        "save_skill_report",
    }:
        canonical["site_id"] = site_id

    if drive_folder_url and tool_name in {
        "list_drive_documents",
        "apply_e_occupancy_skill",
        "apply_school_approval_skill",
        "apply_opening_plan_skill",
        "apply_alpha_capacity_analysis_skill",
        "apply_outdoor_play_space_skill",
        "apply_alpha_phasing_plan_skill",
        "prepare_due_diligence_data",
        "create_dd_report",
        "save_skill_report",
    }:
        canonical["drive_folder_url"] = drive_folder_url

    if site_address and tool_name in {
        "lookup_rhodes_site_owner",
        "list_drive_documents",
        "apply_school_approval_skill",
        "apply_opening_plan_skill",
        "apply_alpha_capacity_analysis_skill",
        "apply_alpha_phasing_plan_skill",
        "prepare_due_diligence_data",
        "create_dd_report",
    }:
        canonical["site_address"] = site_address
        if tool_name == "apply_school_approval_skill" and not str(canonical.get("address") or "").strip():
            canonical["address"] = site_address

    if tool_name == "apply_outdoor_play_space_skill":
        # The play-space server tool only accepts ``address``; a site_address
        # kwarg (from canonicalization or the agent) makes every routed call
        # raise TypeError and silently degrades the play-area lane.
        agent_site_address = str(canonical.pop("site_address", "") or "").strip()
        if not str(canonical.get("address") or "").strip():
            resolved_address = str(site_address or "").strip() or agent_site_address
            if resolved_address:
                canonical["address"] = resolved_address

    return canonical


# ─────────────────────────────────────────────────────────────────────────────
# Shared folder cache helpers
# ─────────────────────────────────────────────────────────────────────────────


def list_shared_folders_once(
    gc: GoogleClient,
) -> dict[str, list[dict[str, Any]]]:
    """List files in the three shared Drive folders once (cached per run).

    Returns {"sir": [...], "isp": [...], "building_inspection": [...]}.
    """
    settings = get_settings()
    folder_map = {
        "sir": settings.sir_folder_id,
        "isp": settings.isp_folder_id,
        "building_inspection": settings.building_inspection_folder_id,
    }
    result: dict[str, list[dict[str, Any]]] = {}
    for doc_type, folder_id in folder_map.items():
        if not folder_id:
            result[doc_type] = []
            continue
        try:
            result[doc_type] = gc.list_files_in_folder(folder_id)
        except Exception as e:
            logger.warning("Failed to list shared %s folder (%s): %s", doc_type, folder_id, e)
            result[doc_type] = []
    return result


def match_site_in_shared_cache(
    match_terms: list[str],
    shared_cache: dict[str, list[dict[str, Any]]],
    *,
    site_title: str | None = None,
    site_address: str | None = None,
) -> dict[str, dict[str, Any] | None]:
    """Find docs matching any of *match_terms* in the pre-fetched shared folder file lists.

    Pass 1: substring match (free).
    Pass 2: LLM site-match for missing doc types (when *site_title* is provided).
    """
    needles = [t.lower() for t in match_terms if t]
    result: dict[str, dict[str, Any] | None] = {
        "sir": None,
        "isp": None,
        "building_inspection": None,
    }
    min_match_score = 40 if site_title else 20

    # Pass 1: substring match
    for doc_type, files in shared_cache.items():
        matches = [
            f for f in files
            if any(needle in f.get("name", "").lower() for needle in needles)
        ]
        if not matches:
            continue
        if not site_title:
            result[doc_type] = {**matches[0], "doc_type": doc_type}
            continue

        scored: list[tuple[int, dict[str, Any]]] = []
        for file_info in matches:
            score = score_site_match_strength(
                file_info.get("name", ""),
                site_title,
                site_address,
            )
            scored.append((score, file_info))

        best_score, best_file = max(scored, key=lambda item: item[0])
        if best_score >= min_match_score:
            result[doc_type] = {**best_file, "doc_type": doc_type}

    # Pass 2: LLM fallback for missing doc types
    if site_title:
        for doc_type in ["sir", "isp", "building_inspection"]:
            if result[doc_type] is not None:
                continue
            files = shared_cache.get(doc_type, [])
            if not files:
                continue
            filenames = [f.get("name", "") for f in files if f.get("name")]
            llm_matches = match_file_to_site_llm(filenames, site_title, site_address)
            if llm_matches:
                best_fn = max(llm_matches, key=llm_matches.get)  # type: ignore[arg-type]
                for f in files:
                    if f.get("name") == best_fn:
                        score = score_site_match_strength(
                            f.get("name", ""),
                            site_title,
                            site_address,
                        )
                        if score < min_match_score:
                            logger.warning(
                                "Ignoring weak shared-cache LLM match for '%s': %s (score=%d)",
                                site_title,
                                best_fn,
                                score,
                            )
                            break
                        result[doc_type] = {**f, "doc_type": doc_type}
                        logger.info(
                            "LLM cache-match: '%s' -> '%s' for %s (conf=%.2f)",
                            best_fn, site_title, doc_type, llm_matches[best_fn],
                        )
                        break

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Direct readiness check (bypasses MCP layer)
# ─────────────────────────────────────────────────────────────────────────────


def _raycon_readiness_metadata(
    gc: GoogleClient,
    drive_folder_url: str,
    m1_folder_id: str | None,
    m1_files_by_type: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return readiness fields for the M1 RayCon scenario JSON."""
    raycon_scenario_file = m1_files_by_type.get("raycon_scenario_json")
    if not raycon_scenario_file:
        return {
            "raycon_scenario_found": False,
            "raycon_scenario_usable": False,
            "raycon_scenario_status": "missing",
            "raycon_scenario_failed": False,
            "raycon_scenario_failure_reason": "",
            "raycon_scenario_run_id": "",
            "raycon_report_data_fields": {},
        }

    base: dict[str, Any] = {
        "raycon_scenario_found": True,
        "raycon_scenario_usable": False,
        "raycon_scenario_status": "read_error",
        "raycon_scenario_failed": True,
        "raycon_scenario_failure_reason": "",
        "raycon_scenario_run_id": "",
        "raycon_scenario_file_id": str(raycon_scenario_file.get("id") or ""),
        "raycon_scenario_modified_time": str(
            raycon_scenario_file.get("modifiedTime") or ""
        ),
        "raycon_report_data_fields": {},
    }

    try:
        scenario = read_raycon_scenario_from_m1(
            gc,
            drive_folder_url,
            m1_folder_id=m1_folder_id,
            m1_files=[f for f in m1_files_by_type.values() if f],
        )
    except RayConSchemaError as e:
        return {
            **base,
            "raycon_scenario_status": "invalid",
            "raycon_scenario_failure_reason": str(e),
        }
    except Exception as e:  # pragma: no cover
        logger.warning("RayCon scenario read failed: %s", e)
        return {
            **base,
            "raycon_scenario_failure_reason": str(e),
        }

    if scenario is None:
        return {
            **base,
            "raycon_scenario_status": "missing",
            "raycon_scenario_failure_reason": "raycon_scenario.json could not be read",
        }

    report_fields = raycon_scenario_to_report_fields(scenario)
    failed = raycon_payload_failed(scenario)
    status = raycon_payload_status(scenario) or "completed"
    failure_reason = str(
        report_fields.get("exec.raycon_failure_reason") or ""
    ).strip()
    if failed and not failure_reason:
        failure_reason = f"RayCon status: {status or 'failed'}"

    return {
        **base,
        "raycon_scenario_usable": not failed,
        "raycon_scenario_status": "failed_validation" if failed else status,
        "raycon_scenario_failed": failed,
        "raycon_scenario_failure_reason": failure_reason,
        "raycon_scenario_run_id": report_fields.get("exec.raycon_run_id", ""),
        "raycon_report_data_fields": report_fields,
    }


def _cost_timeline_readiness_metadata(
    gc: GoogleClient,
    m1_files_by_type: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return readiness fields for the M1 Cost/Timeline Estimate JSON."""

    estimate_file = m1_files_by_type.get("cost_timeline_estimate")
    if not estimate_file:
        return {
            "cost_timeline_estimate_found": False,
            "cost_timeline_estimate_usable": False,
            "cost_timeline_estimate_status": "missing",
            "cost_timeline_estimate_failure_reason": "",
            "cost_timeline_report_data_fields": {},
        }

    file_id = str(
        estimate_file.get("id")
        or estimate_file.get("drive_file_id")
        or estimate_file.get("driveFileId")
        or ""
    ).strip()
    base: dict[str, Any] = {
        "cost_timeline_estimate_found": True,
        "cost_timeline_estimate_usable": False,
        "cost_timeline_estimate_status": "read_error",
        "cost_timeline_estimate_failure_reason": "",
        "cost_timeline_estimate_file_id": file_id,
        "cost_timeline_estimate_modified_time": str(
            estimate_file.get("modifiedTime") or ""
        ),
        "cost_timeline_report_data_fields": {},
    }
    if not file_id:
        return {
            **base,
            "cost_timeline_estimate_status": "invalid",
            "cost_timeline_estimate_failure_reason": (
                "Cost/Timeline Estimate has no Drive file ID."
            ),
        }

    try:
        payload = json.loads(gc.download_file_bytes(file_id).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - one bad estimate should block cleanly
        logger.warning("Cost/Timeline Estimate read failed: %s", exc)
        return {
            **base,
            "cost_timeline_estimate_failure_reason": str(exc),
        }
    if not isinstance(payload, dict):
        return {
            **base,
            "cost_timeline_estimate_status": "invalid",
            "cost_timeline_estimate_failure_reason": (
                "Cost/Timeline Estimate payload is not a JSON object."
            ),
        }

    report_fields_raw = payload.get("report_data_fields")
    report_fields = dict(report_fields_raw) if isinstance(report_fields_raw, dict) else {}
    missing_fields = sorted(
        field
        for field in _COST_TIMELINE_REQUIRED_REPORT_FIELDS
        if not str(report_fields.get(field) or "").strip()
    )
    if missing_fields:
        return {
            **base,
            "cost_timeline_estimate_status": "invalid",
            "cost_timeline_estimate_failure_reason": (
                "Cost/Timeline Estimate missing report_data_fields: "
                + ", ".join(missing_fields)
            ),
            "cost_timeline_report_data_fields": report_fields,
        }

    return {
        **base,
        "cost_timeline_estimate_usable": True,
        "cost_timeline_estimate_status": "completed",
        "cost_timeline_report_data_fields": report_fields,
    }


def check_site_readiness_direct(
    gc: GoogleClient,
    drive_folder_url: str,
    match_terms: list[str],
    shared_cache: dict[str, list[dict[str, Any]]],
    *,
    site_title: str | None = None,
    site_address: str | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    """Check site document readiness directly without going through MCP.

    Pass ``read_only=True`` from read-only callers (e.g. the diagnose
    tool) to suppress two write side effects: creating the per-site M1
    folder when it's missing and writing the provenance cache to Drive
    on a Tier 2 miss. Defaults to ``False`` to preserve existing
    cron-path behavior for ``check_site_readiness`` / ``create_dd_report``.
    """
    folder_id = extract_folder_id_from_url(drive_folder_url)
    if not folder_id:
        return {
            "sir_found": False, "isp_found": False, "inspection_found": False,
            "report_exists": False,
            "e_occupancy_report_found": False, "school_approval_report_found": False,
            "opening_plan_report_found": False,
            "alpha_phasing_plan_report_found": False,
            "security_due_diligence_report_found": False,
            "error": "bad_url",
        }

    # 1. Match docs from pre-fetched shared folder cache (substring + LLM fallback)
    shared_docs = match_site_in_shared_cache(
        match_terms, shared_cache,
        site_title=site_title, site_address=site_address,
    )

    # 2. Recursively list + classify files in the site's own folder (all subfolders).
    # `max_depth=2` covers the per-site `M1` subfolder where the inbox scanner
    # files net-new SIR/BI/ISP uploads, so we can pick those up alongside the
    # AI-generated report artifacts saved at the site folder root.
    all_site_files = [
        {**f, "doc_type": classify_document_type(f.get("name", ""))}
        for f in gc.list_files_recursive(folder_id, max_depth=2)
        if is_site_folder_scan_candidate(f)
    ]
    keep_doc_types = SITE_FOLDER_DOC_TYPES
    site_folder_files = [
        f for f in all_site_files if f.get("doc_type") in keep_doc_types
    ]

    # 3. Merge — site folder (incl. M1) wins over shared cache for SIR/ISP/BI
    # so the freshly-uploaded copy in M1 takes precedence over any legacy
    # match from the shared SIR/ISP/Building Inspection folders.
    files_by_type: dict[str, dict[str, Any] | None] = {
        "sir": None,
        "isp": None,
        "building_inspection": None,
        "dd_report": None,
        "e_occupancy_report": None,
        "school_approval_report": None,
        "opening_plan_report": None,
        "alpha_capacity_analysis": None,
        "outdoor_play_space_report": None,
        "security_due_diligence_report": None,
        "alpha_phasing_plan_report": None,
        "traffic_analysis": None,
        "certificate_of_occupancy": None,
        "permit_of_record": None,
        "measured_floor_plan": None,
        "floor_plan": None,
        "lidar": None,
    }
    for f in site_folder_files:
        dt = f.get("doc_type", "unknown")
        if dt in files_by_type and files_by_type[dt] is None:
            files_by_type[dt] = f
    # Fall back to shared-folder matches for any source doc type still missing.
    for dt in ("sir", "isp", "building_inspection"):
        if files_by_type[dt] is None:
            files_by_type[dt] = shared_docs.get(dt)

    # `all_files` holds only derived artifacts; preserve
    # the contract that source docs are exposed via the per-type flags below.
    ai_generated_site_files = [
        f for f in site_folder_files if f.get("doc_type") in AI_GENERATED_DOC_TYPES
    ]

    # ── Vendor-vs-AI provenance ─────────────────────────────────────────────
    # The presence of a file classified as ``sir`` / ``building_inspection``
    # is no longer enough; our own pipeline also drops AI-generated SIRs and
    # CDS overlays into the M1 folder. Run a per-file provenance check (cheap
    # filename heuristic + cached LLM content fallback) so the readiness gate
    # only opens on *vendor-sourced* SIR + BI.
    m1_folder_id, _ = _resolve_m1_folder(
        gc, drive_folder_url, create_if_missing=not read_only
    )

    def _vendor_check(doc_type: str) -> bool:
        f = files_by_type.get(doc_type)
        if not f:
            return False
        try:
            verdict = classify_provenance(
                f,
                gc,
                m1_folder_id=m1_folder_id,
                doc_type=doc_type,
                read_only=read_only,
            )
        except Exception as e:  # pragma: no cover
            logger.warning(
                "provenance check failed for %s in %s: %s", doc_type, site_title, e
            )
            return True  # default to vendor; gate will retry on next run
        return verdict.is_vendor

    sir_is_vendor = _vendor_check("sir")
    inspection_is_vendor = _vendor_check("building_inspection")

    # ── Cost/Timeline Estimate plus legacy RayCon metadata ──────────────────
    # Cost/Timeline Estimate is the active third gating input. Legacy RayCon
    # metadata remains in the readiness payload for old report placeholders,
    # but it no longer opens the full-report gate.
    raycon_metadata: dict[str, Any] = _raycon_readiness_metadata(
        gc, drive_folder_url, m1_folder_id, {}
    )
    cost_timeline_metadata: dict[str, Any] = _cost_timeline_readiness_metadata(gc, {})
    if m1_folder_id:
        try:
            m1_files_by_type = _list_m1_documents_by_type(gc, m1_folder_id)
            cost_timeline_metadata = _cost_timeline_readiness_metadata(
                gc, m1_files_by_type
            )
        except Exception as e:  # pragma: no cover
            logger.warning(
                "M1 lookup failed for cost_timeline_estimate in %s: %s", site_title, e
            )

    sir_review_files = [f for f in site_folder_files if f.get("doc_type") == "sir"]
    shared_sir = shared_docs.get("sir")
    if shared_sir:
        sir_review_files.append({**shared_sir, "doc_type": "sir"})
    sir_learning_review = build_sir_learning_review(
        sir_review_files,
        gc,
        m1_folder_id=m1_folder_id,
        read_only=read_only,
    )

    return {
        "sir_found": files_by_type["sir"] is not None,
        "isp_found": files_by_type["isp"] is not None,
        "inspection_found": files_by_type["building_inspection"] is not None,
        # Vendor-confirmed flags. The new gate (see ``_missing_required_docs``)
        # reads these instead of the bare ``*_found`` bools so AI-generated
        # SIRs in M1 don't open the gate.
        "sir_vendor": files_by_type["sir"] is not None and sir_is_vendor,
        "inspection_vendor": files_by_type["building_inspection"] is not None
            and inspection_is_vendor,
        **raycon_metadata,
        **cost_timeline_metadata,
        "report_exists": files_by_type["dd_report"] is not None,
        "e_occupancy_report_found": files_by_type["e_occupancy_report"] is not None,
        "school_approval_report_found": files_by_type["school_approval_report"] is not None,
        "opening_plan_report_found": files_by_type["opening_plan_report"] is not None,
        "alpha_phasing_plan_report_found": (
            files_by_type["alpha_phasing_plan_report"] is not None
        ),
        "security_due_diligence_report_found": (
            files_by_type["security_due_diligence_report"] is not None
        ),
        "all_files": ai_generated_site_files,
        "sir_learning_review": sir_learning_review.to_dict(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Claude agentic loop — generates one DD report
# ─────────────────────────────────────────────────────────────────────────────


def run_dd_report_agent(
    site_title: str,
    system_prompt: str,
    model_id: str,
    *,
    drive_folder_url: str | None = None,
    site_address: str | None = None,
    site_id: str | None = None,
    initial_report_fields: dict[str, Any] | None = None,
    rhodes_owner_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run Claude as a tool-calling agent to generate one DD report.

    Args:
        site_title: Site name to generate the report for.
        system_prompt: Full system prompt text.
        model_id: Anthropic model ID to use.

    Returns a dict with keys: success, doc_id, doc_url, error.
    """
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not anthropic_api_key:
        return {"success": False, "error": "ANTHROPIC_API_KEY not set"}

    client = anthropic.Anthropic(api_key=anthropic_api_key, max_retries=2)

    # Initialize provenance trace
    trace = ReportTrace(
        site_name=site_title,
        started_at=datetime.now(UTC).isoformat(),
        prompt_version=4,
    )
    run_start = time.monotonic()

    request_lines = [f"Generate a DD Report for: {site_title}"]
    effective_site_id = (site_id or "").strip()
    effective_site_address = site_address
    if effective_site_address:
        request_lines.append(f"Site address: {effective_site_address}")
    effective_drive_folder_url = drive_folder_url
    if drive_folder_url:
        request_lines.append(f"Drive folder URL: {drive_folder_url}")
        request_lines.append("Use the provided Drive folder directly.")
    else:
        request_lines.append(
            "Drive folder URL: not supplied. Use lookup_rhodes_site_owner first; "
            "if it returns drive_folder_url, use that exact URL for Drive tools."
        )
    if rhodes_owner_context:
        owner_name = str(rhodes_owner_context.get("p1_assignee_name") or "").strip()
        owner_email = str(rhodes_owner_context.get("p1_assignee_email") or "").strip()
        owner_status = str(rhodes_owner_context.get("status") or "").strip()
        rhodes_drive_url = str(rhodes_owner_context.get("drive_folder_url") or "").strip()
        rhodes_site_address = str(rhodes_owner_context.get("site_address") or "").strip()
        rhodes_site_id = str(rhodes_owner_context.get("site_id") or "").strip()
        if rhodes_site_id and not effective_site_id:
            effective_site_id = rhodes_site_id
        if rhodes_site_address and not effective_site_address:
            effective_site_address = rhodes_site_address
            request_lines.append(f"Rhodes site address: {rhodes_site_address}")
        if rhodes_drive_url and not effective_drive_folder_url:
            effective_drive_folder_url = rhodes_drive_url
            request_lines.append(f"Rhodes Drive folder URL: {rhodes_drive_url}")
        if owner_name or owner_email:
            owner_email_suffix = f" <{owner_email}>" if owner_email else ""
            request_lines.append(
                f"Rhodes P1 DRI / site owner: {owner_name or '[name missing]'}"
                f"{owner_email_suffix}"
            )
        elif owner_status:
            request_lines.append(f"Rhodes P1 DRI / site owner lookup status: {owner_status}")
    if effective_site_id:
        request_lines.append(f"Rhodes site ID: {effective_site_id}")

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "\n".join(request_lines)},
    ]

    doc_id: str | None = None
    doc_url: str | None = None
    document_role = "active"
    republish_guard: dict[str, Any] | None = None
    prepared_render_input: dict[str, Any] | None = None
    prepared_report_metadata: dict[str, Any] | None = None
    prepared_source_packet: dict[str, Any] | None = None
    cached_report_fields: dict[str, Any] = dict(initial_report_fields or {})
    if effective_site_address:
        cached_report_fields.setdefault("site.address", effective_site_address)
        cached_report_fields.setdefault("site.site_address", effective_site_address)
    max_iterations = 40  # Safety limit

    for iteration in range(max_iterations):
        logger.info("Agent iteration %d for site: %s", iteration + 1, site_title)

        response = client.messages.create(
            model=model_id,
            max_tokens=8192,
            system=system_prompt,
            tools=TOOL_DEFINITIONS,  # type: ignore[arg-type]
            messages=messages,  # type: ignore[arg-type]
        )

        # Collect assistant message
        assistant_content: list[Any] = []
        tool_uses: list[Any] = []

        for block in response.content:
            assistant_content.append(block)
            if block.type == "tool_use":
                tool_uses.append(block)

        messages.append({"role": "assistant", "content": assistant_content})

        # If no tool calls, agent is done
        if not tool_uses:
            logger.info("Agent finished (no more tool calls) after %d iterations", iteration + 1)
            break

        # Execute tool calls and collect results
        tool_results: list[dict[str, Any]] = []
        for tool_use in tool_uses:
            logger.info("Executing tool: %s", tool_use.name)
            tool_input = _canonicalize_site_tool_input(
                tool_use.name,
                dict(tool_use.input),
                site_title=site_title,
                site_id=effective_site_id,
                drive_folder_url=effective_drive_folder_url,
                site_address=effective_site_address,
            )
            if tool_use.name in {"prepare_due_diligence_data", "create_dd_report"}:
                tool_input = _merge_cached_report_fields(tool_input, cached_report_fields)

            t0 = time.monotonic()
            tool_error: str | None = None
            try:
                result = route_tool_call_sync(tool_use.name, tool_input)
            except Exception as e:
                logger.error("Tool %s failed: %s", tool_use.name, e)
                result = {"status": "error", "message": str(e)}
                tool_error = str(e)
            elapsed_ms = int((time.monotonic() - t0) * 1000)

            # Record in provenance trace
            trace.add_event(TraceEvent(
                timestamp=datetime.now(UTC).isoformat(),
                event_type="tool_call",
                tool_name=tool_use.name,
                input_summary=_sanitize_input(tool_input),
                output_summary=_summarize_tool_output(result),
                duration_ms=elapsed_ms,
                error=tool_error,
            ))

            # Capture normalized DD data before rendering so the pipeline can
            # write the SOR first, then create the DDR as a view.
            if tool_use.name == "prepare_due_diligence_data" and isinstance(result, dict):
                if result.get("status") == "success":
                    rd = tool_input.get("report_data")
                    normalized = result.get("normalized_report_data")
                    if isinstance(rd, dict):
                        trace.final_report_data = dict(rd)
                    if isinstance(normalized, dict):
                        trace.final_report_data.update(normalized)
                    prepared_render_input = {
                        "site_name": tool_input.get("site_name", site_title),
                        "drive_folder_url": tool_input.get("drive_folder_url", ""),
                        "report_data": dict(trace.final_report_data),
                        "site_address": tool_input.get("site_address", ""),
                    }
                    token_evidence = tool_input.get("token_evidence")
                    if isinstance(token_evidence, dict):
                        prepared_render_input["token_evidence"] = dict(token_evidence)
                    metadata = result.get("report_metadata")
                    prepared_report_metadata = metadata if isinstance(metadata, dict) else None
                    packet = result.get("source_packet")
                    prepared_source_packet = packet if isinstance(packet, dict) else None
                    logger.info("Prepared DD data for SOR-first publish")
            # Capture doc_id from create_dd_report
            elif tool_use.name == "create_dd_report" and isinstance(result, dict):
                doc_data = result.get("document", {})
                if doc_data.get("id"):
                    doc_id = doc_data["id"]
                    doc_url = doc_data.get("url")
                    document_role = str(doc_data.get("role") or "active")
                    guard = result.get("republish_guard")
                    republish_guard = guard if isinstance(guard, dict) else None
                    logger.info("Created DD report: %s", doc_url)
                    trace.doc_id = doc_id
                    trace.tokens_filled = result.get("replacements_applied", 0)
                    trace.tokens_unfilled = result.get("unfilled_template_tokens", 0)
                    # Stash the final, fully-merged report_data for local
                    # run diagnostics.
                    rd = tool_input.get("report_data")
                    normalized = result.get("normalized_report_data")
                    if isinstance(rd, dict):
                        trace.final_report_data = dict(rd)
                    if isinstance(normalized, dict):
                        trace.final_report_data.update(normalized)
            elif isinstance(result, dict):
                report_fields = result.get("report_data_fields")
                if isinstance(report_fields, dict):
                    cached_report_fields.update(report_fields)
                rhodes_drive_url = str(result.get("drive_folder_url") or "").strip()
                rhodes_site_address = str(result.get("site_address") or "").strip()
                if not rhodes_drive_url and isinstance(report_fields, dict):
                    rhodes_drive_url = str(
                        report_fields.get("meta.drive_folder_url")
                        or report_fields.get("site.drive_folder_url")
                        or ""
                    ).strip()
                if not rhodes_site_address and isinstance(report_fields, dict):
                    rhodes_site_address = str(
                        report_fields.get("site.address")
                        or report_fields.get("site.site_address")
                        or ""
                    ).strip()
                if rhodes_drive_url and not effective_drive_folder_url:
                    effective_drive_folder_url = rhodes_drive_url
                if rhodes_site_address and not effective_site_address:
                    effective_site_address = rhodes_site_address
                    cached_report_fields.setdefault("site.address", rhodes_site_address)
                    cached_report_fields.setdefault("site.site_address", rhodes_site_address)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": json.dumps(result),
            })

            if doc_id or prepared_render_input is not None:
                logger.info("DD report handoff complete during tool batch, skipping remaining tool calls")
                break

        messages.append({"role": "user", "content": tool_results})

        # Stop as soon as data is prepared or a report exists. Pipeline handles
        # SOR publish, rendering, validation, and notifications.
        if doc_id or prepared_render_input is not None:
            logger.info("Agent handoff complete after %d iterations", iteration + 1)
            break

    # Finalize trace
    trace.ended_at = datetime.now(UTC).isoformat()
    trace.total_duration_ms = int((time.monotonic() - run_start) * 1000)
    trace.final_status = "success" if doc_id or prepared_render_input is not None else "no_report"

    if prepared_render_input is not None and doc_id is None:
        return {
            "success": True,
            "prepared": True,
            "trace": trace,
            "render_input": prepared_render_input,
            "prepared_report_data": trace.final_report_data,
            "report_metadata": prepared_report_metadata or {},
            "source_packet": prepared_source_packet or {},
        }

    if doc_id:
        return {
            "success": True,
            "doc_id": doc_id,
            "doc_url": doc_url,
            "trace": trace,
            "document_role": document_role,
            "republish_guard": republish_guard,
        }
    return {"success": False, "error": "Agent completed without creating a report", "trace": trace}


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline result dataclass
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PipelineResult:
    """Structured result from a single-site pipeline run."""

    site_title: str
    status: str  # waiting_on_docs | report_exists | report_data_prepared | locationos_mcp_write_required | report_created | republish_candidate_created | report_incomplete | generation_failed | error
    missing_docs: list[str] = field(default_factory=list)
    doc_id: str | None = None
    doc_url: str | None = None
    unresolved_tokens: list[str] = field(default_factory=list)
    pending_count: int = 0
    error: str | None = None
    trace_url: str | None = None
    trace: ReportTrace | None = None
    run_id: str | None = None
    failed_step: str | None = None
    quality_score: int | None = None
    quality_band: str | None = None
    manifest_path: str | None = None
    sir_review_status: str | None = None
    sir_learning_review: dict[str, Any] | None = None
    source_event: dict[str, Any] | None = None
    open_questions: list[dict[str, Any]] = field(default_factory=list)
    closed_open_questions: list[dict[str, Any]] = field(default_factory=list)
    republish_summary: dict[str, Any] | None = None
    source_packet: dict[str, Any] | None = None
    rhodes_due_diligence_update: dict[str, Any] | None = None
    rhodes_report_event: dict[str, Any] | None = None
    locationos_mcp_resume: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    steps: list[StepResult] = field(default_factory=list)


class _RunRecorder:
    """Collect step results for one pipeline run."""

    def __init__(
        self,
        site_title: str,
        site_id: str | None = None,
        *,
        launch_context: dict[str, Any] | None = None,
    ) -> None:
        self.run_id = make_run_id(site_title)
        self.site_title = site_title
        self.site_id = site_id
        self.launch_context = launch_context
        self.started_at = utc_now_iso()
        self.steps: list[StepResult] = []
        self.sir_learning_review: dict[str, Any] | None = None
        self.p1_dri_missing = False

    def start(self) -> tuple[str, float]:
        return utc_now_iso(), time.monotonic()

    def record(
        self,
        step: str,
        started_at: str,
        started_monotonic: float,
        status: str,
        *,
        error: PipelineError | None = None,
        artifacts: list[ArtifactRef] | None = None,
        skipped_reason: str | None = None,
    ) -> StepResult:
        rerun = None
        if status in {"failed", "blocked"}:
            rerun = _rerun_command(self.run_id, step)
        result = StepResult(
            run_id=self.run_id,
            step=step,
            status=cast(StepStatus, status),
            started_at=started_at,
            ended_at=utc_now_iso(),
            duration_ms=int((time.monotonic() - started_monotonic) * 1000),
            error=error,
            artifacts=artifacts or [],
            rerun_command=rerun,
            skipped_reason=skipped_reason,
        )
        self.steps.append(result)
        return result

    def to_run(self, final_status: str) -> PipelineRun:
        return PipelineRun(
            run_id=self.run_id,
            site_title=self.site_title,
            site_id=self.site_id,
            started_at=self.started_at,
            ended_at=utc_now_iso(),
            final_status=final_status,
            steps=list(self.steps),
            launch_context=self.launch_context,
            sir_learning_review=self.sir_learning_review,
            p1_dri_missing=self.p1_dri_missing,
        )


def _rerun_command(run_id: str, step: str) -> str:
    return f"ddr rerun --run-id {run_id} --step {step}"


def _pipeline_error(
    run_id: str,
    step: str,
    code: str,
    message: str,
    *,
    retryable: bool = True,
    cause: str | None = None,
) -> PipelineError:
    return PipelineError(
        code=code,
        message=message,
        retryable=retryable,
        operator_action=_rerun_command(run_id, step),
        cause=cause,
    )


def _finalize_pipeline_result(
    result: PipelineResult,
    recorder: _RunRecorder,
    *,
    gc: GoogleClient | None = None,
    drive_folder_url: str = "",
) -> PipelineResult:
    if result.sir_learning_review is None:
        result.sir_learning_review = recorder.sir_learning_review
    if result.sir_learning_review:
        result.sir_review_status = str(result.sir_learning_review.get("status") or "")
    run = recorder.to_run(result.status)
    _attach_result_metadata(run, result)
    manifest_persisted = False
    secret_detected = manifest_has_secret_like_value(run.to_dict())
    started_at, started_monotonic = recorder.start()
    if secret_detected:
        recorder.record(
            "manifest.save",
            started_at,
            started_monotonic,
            "failed",
            error=_pipeline_error(
                recorder.run_id,
                "manifest.save",
                "manifest_secret_detected",
                "Run manifest contains secret-like material and was not persisted",
                retryable=False,
            ),
        )
    else:
        try:
            path = persist_run_manifest(run)
            manifest_persisted = True
            result.manifest_path = str(path)
            recorder.record(
                "manifest.save",
                started_at,
                started_monotonic,
                "succeeded",
                artifacts=[ArtifactRef(kind="manifest", name=path.name, uri=str(path))],
            )
        except Exception as e:  # pragma: no cover - defensive path
            recorder.record(
                "manifest.save",
                started_at,
                started_monotonic,
                "failed",
                error=_pipeline_error(
                    recorder.run_id,
                    "manifest.save",
                    "manifest_save_failed",
                    str(e),
                ),
            )
    run = recorder.to_run(result.status)
    _attach_result_metadata(run, result)
    run.manifest_path = result.manifest_path
    run.quality = evaluate_run_quality(
        run,
        manifest_persisted=manifest_persisted,
        secret_detected=secret_detected,
    )
    if manifest_persisted:
        path = persist_run_manifest(run)
        result.manifest_path = str(path)
        run = recorder.to_run(result.status)
        _attach_result_metadata(run, result)
        run.manifest_path = result.manifest_path
        run.quality = evaluate_run_quality(
            run,
            manifest_persisted=manifest_persisted,
            secret_detected=secret_detected,
        )
        persist_run_manifest(run)
    result.run_id = run.run_id
    result.steps = list(run.steps)
    result.failed_step = failed_step_name(run.steps)
    quality = run.quality
    assert quality is not None
    result.quality_score = quality.score
    result.quality_band = quality.band
    result.manifest_path = run.manifest_path
    return result


def _attach_result_metadata(run: PipelineRun, result: PipelineResult) -> None:
    run.source_event = result.source_event
    run.open_questions = result.open_questions
    run.closed_open_questions = result.closed_open_questions
    run.republish_summary = result.republish_summary
    run.source_packet = result.source_packet
    run.rhodes_due_diligence_update = result.rhodes_due_diligence_update
    run.rhodes_report_event = result.rhodes_report_event
    run.locationos_mcp_resume = result.locationos_mcp_resume
    run.warnings = list(result.warnings)


def _get_payload_error(result: dict[str, Any]) -> str | None:
    """Return a normalized error string for tool-style payloads."""
    error = result.get("error")
    if not error and result.get("status") != "error":
        return None
    message = result.get("message")
    if message and message != error:
        return f"{error}: {message}" if error else str(message)
    if error:
        return str(error)
    return "Unknown tool error"


# ─────────────────────────────────────────────────────────────────────────────
# Report generation trace — provenance log
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TraceEvent:
    """A single event in the report generation trace."""

    timestamp: str
    event_type: str  # "tool_call" | "run_start" | "run_end"
    tool_name: str = ""
    input_summary: dict[str, Any] = field(default_factory=dict)
    output_summary: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    error: str | None = None


@dataclass
class ReportTrace:
    """Accumulated trace of a report generation run."""

    site_name: str
    started_at: str
    prompt_version: int = 2
    events: list[TraceEvent] = field(default_factory=list)
    ended_at: str = ""
    total_duration_ms: int = 0
    final_status: str = ""
    doc_id: str | None = None
    tokens_filled: int = 0
    tokens_unfilled: int = 0
    # Full normalized report_data prepared for SOR publish and DDR rendering.
    # Kept on the in-memory trace for validation and local run diagnostics.
    final_report_data: dict[str, Any] = field(default_factory=dict)

    def add_event(self, event: TraceEvent) -> None:
        self.events.append(event)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "site_name": self.site_name,
            "started_at": self.started_at,
            "prompt_version": self.prompt_version,
            "ended_at": self.ended_at,
            "total_duration_ms": self.total_duration_ms,
            "final_status": self.final_status,
            "doc_id": self.doc_id,
            "tokens_filled": self.tokens_filled,
            "tokens_unfilled": self.tokens_unfilled,
            "event_count": len(self.events),
            "events": [
                {
                    "timestamp": e.timestamp,
                    "event_type": e.event_type,
                    "tool_name": e.tool_name,
                    "input_summary": e.input_summary,
                    "output_summary": e.output_summary,
                    "duration_ms": e.duration_ms,
                    "error": e.error,
                }
                for e in self.events
            ],
        }


def _sanitize_input(tool_input: dict[str, Any]) -> dict[str, Any]:
    """Remove or truncate large input values for trace logging."""
    sanitized: dict[str, Any] = {}
    for k, v in tool_input.items():
        if k == "report_data" and isinstance(v, dict):
            sanitized[k] = f"<{len(v)} top-level keys>"
        elif k == "content" and isinstance(v, str) and len(v) > 200:
            sanitized[k] = v[:200] + f"... ({len(v)} chars)"
        elif isinstance(v, str) and len(v) > 500:
            sanitized[k] = v[:500] + "..."
        else:
            sanitized[k] = v
    return sanitized


def _summarize_tool_output(result: Any) -> dict[str, Any]:
    """Create a compact summary of a tool result for the trace."""
    if not isinstance(result, dict):
        text = str(result)
        return {"text": text[:500]}

    summary: dict[str, Any] = {"status": result.get("status", "unknown")}

    if "document" in result:
        summary["document"] = result["document"]
    if "files" in result and isinstance(result["files"], list):
        summary["file_count"] = len(result["files"])
    content = result.get("content")
    if not isinstance(content, str):
        content = result.get("text")
    if isinstance(content, str):
        summary["content_length"] = len(content)
        preview = content[:200].strip()
        if preview.startswith("[") or len(content) <= 200:
            summary["content_preview"] = preview
    if "message" in result:
        msg = str(result["message"])
        summary["message"] = msg[:300] if len(msg) > 300 else msg
    if "error" in result:
        summary["error"] = str(result["error"])[:200]
    if "replacements_applied" in result:
        summary["replacements_applied"] = result["replacements_applied"]
    if "unfilled_template_tokens" in result:
        summary["unfilled_template_tokens"] = result["unfilled_template_tokens"]
    if "source_usable" in result:
        summary["source_usable"] = result["source_usable"]
    if "source_quality_warnings" in result:
        summary["source_quality_warnings"] = result["source_quality_warnings"]

    return summary


def _merge_cached_report_fields(
    tool_input: dict[str, Any],
    cached_report_fields: dict[str, Any],
) -> dict[str, Any]:
    """Merge cached tool report_data_fields into a report-data tool input."""
    if not cached_report_fields:
        return tool_input

    merged = dict(tool_input)
    report_data = merged.get("report_data")
    if not isinstance(report_data, dict):
        report_data = {}
    else:
        report_data = dict(report_data)

    for key, value in cached_report_fields.items():
        report_data.setdefault(key, value)

    cached_raycon_status = str(
        cached_report_fields.get("exec.raycon_status") or ""
    ).strip().lower()
    if cached_raycon_status in RAYCON_FAILED_STATUSES:
        for key, value in cached_report_fields.items():
            if key.startswith("exec.raycon_") or key in _RAYCON_REPORT_FIELD_PATHS:
                report_data[key] = value

    merged["report_data"] = report_data
    return merged


def _vendor_gate_enabled() -> bool:
    """Feature flag for the vendor-only readiness gate.

    Default OFF during soak so the legacy ``*_found`` behavior keeps
    running for sites that already have AI-only artifacts. Flip
    ``VENDOR_GATE_ENABLED=1`` once we've confirmed vendor detection
    classifies cleanly across the live portfolio.
    """
    return os.environ.get("VENDOR_GATE_ENABLED", "0").strip() not in {"", "0", "false", "False"}


def _missing_required_docs(readiness: dict[str, Any]) -> list[str]:
    """Return human-readable names for missing full-report DD inputs.

    With ``VENDOR_GATE_ENABLED=1`` the gate requires:
      * Vendor-sourced SIR
      * Vendor-sourced Building Inspection
      * Cost/Timeline Estimate

    Without the flag (legacy default), only SIR + Building Inspection presence
    is checked, regardless of provenance — matches pre-cutover behavior.
    """
    missing: list[str] = []
    if _vendor_gate_enabled():
        if not readiness.get("sir_vendor", False):
            missing.append(
                "Vendor SIR" if readiness.get("sir_found") else "SIR"
            )
        if not readiness.get("inspection_vendor", False):
            missing.append("Vendor Building Inspection")
        cost_timeline_found = bool(
            readiness.get("cost_timeline_estimate_found", False)
        )
        cost_timeline_usable = bool(
            readiness.get("cost_timeline_estimate_usable", cost_timeline_found)
        )
        if not cost_timeline_found:
            missing.append("Cost/Timeline Estimate")
        elif not cost_timeline_usable:
            missing.append("Usable Cost/Timeline Estimate")
        return missing

    if not readiness.get("sir_found", False):
        missing.append("SIR")
    if not readiness.get("inspection_found", False):
        missing.append("Building Inspection")
    return missing


def _missing_first_round_docs(readiness: dict[str, Any]) -> list[str]:
    """Return blocking inputs for first-round direct DD publishing.

    First-round reports are allowed to publish from the AI SIR / research
    output before all vendor documents come back. Full-report readiness stays
    modeled by ``_missing_required_docs`` for diagnostics and escalation.
    """
    if readiness.get("sir_found", False):
        return []
    return ["SIR"]


def _source_doc_type_for_alert(file_name: str) -> str | None:
    """Return the monitored source-doc label for a filename."""
    name = file_name.lower()
    if "sir" in name:
        return "SIR"
    if "building inspection" in name or "inspection report" in name:
        return "Building Inspection"
    return None


def _extract_source_read_issues(trace: ReportTrace | None) -> list[dict[str, str]]:
    """Return unreadable SIR / Building Inspection events from a run trace."""
    if trace is None:
        return []

    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for event in trace.events:
        if event.tool_name != "read_drive_document":
            continue
        file_name = str(event.input_summary.get("file_name", "")).strip()
        doc_type = _source_doc_type_for_alert(file_name)
        if not doc_type:
            continue

        output = event.output_summary
        preview = str(output.get("content_preview", ""))
        explicit_problem = event.error or str(output.get("error", "")).strip()
        raw_content_length = output.get("content_length")
        try:
            empty_content = raw_content_length is not None and int(raw_content_length) == 0
        except (TypeError, ValueError):
            empty_content = False
        has_issue = (
            bool(explicit_problem)
            or output.get("status") == "error"
            or empty_content
            or "returned no text" in preview.lower()
            or "requires ocr" in preview.lower()
        )
        if not has_issue:
            continue

        key = (doc_type, file_name)
        if key in seen:
            continue
        seen.add(key)
        issues.append({
            "doc_type": doc_type,
            "file_name": file_name or doc_type,
            "problem": (
                explicit_problem
                or str(output.get("message", "")).strip()
                or "Document could not be read cleanly"
            ),
        })

    return issues


def _notify_vendor_gate_extraction_failure(
    webhook_url: str,
    site_title: str,
    *,
    drive_folder_url: str = "",
    failure_reason: str = "",
    trace_url: str = "",
) -> None:
    """Alert humans when all vendor inputs are present but extraction fails.

    The vendor gate guarantees a vendor SIR + vendor Building Inspection +
    Cost/Timeline Estimate were all on hand when generation was attempted. If
    the agent still couldn't produce a complete report, the inputs almost
    certainly need a human to disambiguate — OCR failure, malformed
    Cost/Timeline Estimate payload, conflicting permit narratives, etc. We
    escalate to Google Chat rather than silently leaving the row in
    ``report_incomplete``.

    Idempotency: this alert is keyed on the failure_reason text so multiple
    runs of the same site with the same root cause don't spam the channel.
    Per-site dedup is the responsibility of the alert sink (current
    Google Chat webhook does not natively dedupe; we accept duplicates here
    rather than build a side-state file).
    """
    if not webhook_url:
        return
    lines = [
        f"DD Vendor Gate — Human Intervention Needed: {site_title}",
        "All three required inputs are present (vendor SIR, vendor Building "
        "Inspection, Cost/Timeline Estimate) but the report could not be "
        "completed. A human reviewer should inspect the inputs.",
    ]
    if failure_reason:
        lines.append(f"Reason: {failure_reason[:300]}")
    if drive_folder_url:
        lines.append(f"Drive: {drive_folder_url}")
    if trace_url:
        lines.append(f"Trace: {trace_url}")
    msg = "\n".join(lines)
    for url in [u.strip() for u in webhook_url.split(",") if u.strip()]:
        try:
            post_google_chat_message(url, msg)
        except Exception as e:
            logger.error(
                "Failed to post vendor-gate alert for '%s' to %s: %s",
                site_title,
                url[:60],
                e,
            )


def _record_vendor_gate_alert_step(
    recorder: _RunRecorder,
    settings: Settings,
    site_title: str,
    *,
    drive_folder_url: str = "",
    failure_reason: str = "",
    trace_url: str = "",
    site_id: str,
    owner_user_id: str,
    owner_email: str,
    mutation_status: str,
) -> None:
    """Record a review event when complete vendor inputs fail to produce a report."""

    started_at, started_monotonic = recorder.start()
    event = build_vendor_gate_review_required_event(
        site_id=site_id,
        site_name=site_title,
        run_id=recorder.run_id,
        failure_reason=failure_reason,
        mutation_status=mutation_status,
        drive_folder_url=drive_folder_url,
        trace_url=trace_url,
    )
    event_status, body = record_rhodes_automation_event(
        event,
        owner_user_id=owner_user_id,
        owner_email=owner_email,
        add_note=add_rhodes_site_note,
    )
    should_alert_chat = should_alert_google_chat(event_status)
    chat_result: dict[str, Any] | None = None
    if should_alert_chat:
        chat_result = _post_google_chat_to_configured_webhooks(
            settings.google_chat_webhook_url,
            body,
        )
        event_status["google_chat"] = chat_result

    artifact = ArtifactRef(
        kind="rhodes_note",
        name="DDR vendor gate AutomationEvent",
        metadata=event_status,
    )
    note_status = str(event_status.get("status") or "")
    chat_status = str((chat_result or {}).get("status") or "")
    if note_status == "created" and chat_status not in {"failed", "skipped"}:
        recorder.record(
            "vendor_gate.alert",
            started_at,
            started_monotonic,
            "succeeded",
            artifacts=[artifact],
        )
        return
    if note_status == "created" and not should_alert_chat:
        recorder.record(
            "vendor_gate.alert",
            started_at,
            started_monotonic,
            "succeeded",
            artifacts=[artifact],
        )
        return

    message = str(event_status.get("error") or event_status.get("reason") or "unknown")
    if should_alert_chat and chat_status in {"failed", "skipped"}:
        message = f"{message}; Google Chat fallback {chat_status}"
    recorder.record(
        "vendor_gate.alert",
        started_at,
        started_monotonic,
        "failed",
        error=_pipeline_error(
            recorder.run_id,
            "vendor_gate.alert",
            "vendor_gate_alert_failed",
            message,
        ),
        artifacts=[artifact],
    )


def _resolve_readiness_result(
    site_title: str,
    readiness: dict[str, Any],
    *,
    force_regenerate: bool = False,
) -> PipelineResult | None:
    """Convert readiness payload into an early pipeline result when applicable.

    ``force_regenerate=True`` bypasses ONLY the ``report_exists`` short-circuit;
    error and missing-docs gates still apply.
    """
    readiness_error = _get_payload_error(readiness)
    if readiness_error:
        logger.error("Readiness check failed for '%s': %s", site_title, readiness_error)
        return PipelineResult(site_title=site_title, status="error", error=readiness_error)

    missing_docs = _missing_first_round_docs(readiness)
    if missing_docs:
        return PipelineResult(
            site_title=site_title,
            status="waiting_on_docs",
            missing_docs=missing_docs,
        )

    if readiness.get("report_exists", False):
        if force_regenerate:
            logger.info(
                "force_regenerate=True — bypassing report_exists check for site=%s",
                site_title,
            )
        else:
            logger.info("'%s' - report already exists, skipping", site_title)
            return PipelineResult(site_title=site_title, status="report_exists")

    return None


def _run_pipeline_agent(
    site_title: str,
    system_prompt: str,
    settings: Settings,
    *,
    drive_folder_url: str | None = None,
    site_address: str | None = None,
    site_id: str | None = None,
    initial_report_fields: dict[str, Any] | None = None,
    rhodes_owner_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, PipelineResult | None]:
    """Run report generation and map failures into a PipelineResult."""
    # M2 DD execution is repo-owned. DD_REPORT_OWNER is retained as a tolerated
    # legacy env var but no longer delegates this workflow to another pipeline.
    owner = os.environ.get("DD_REPORT_OWNER", "reporter").strip().lower()
    if owner == "pipeline":
        logger.info(
            "Ignoring legacy DD_REPORT_OWNER=pipeline for %s; "
            "M2 execution remains in due-diligence-reporter.",
            site_title,
        )

    logger.info("'%s' - all docs present, generating report...", site_title)
    try:
        agent_result = run_dd_report_agent(
            site_title,
            system_prompt,
            settings.anthropic_report_model,
            drive_folder_url=drive_folder_url,
            site_address=site_address,
            site_id=site_id,
            initial_report_fields=initial_report_fields,
            rhodes_owner_context=rhodes_owner_context,
        )
    except Exception as e:
        logger.error("Report generation crashed for '%s': %s", site_title, e)
        return None, PipelineResult(
            site_title=site_title,
            status="generation_failed",
            error=str(e),
        )

    if agent_result.get("success"):
        return agent_result, None

    err = agent_result.get("error", "unknown error")
    logger.error("Report generation failed for '%s': %s", site_title, err)
    return None, PipelineResult(
        site_title=site_title,
        status="generation_failed",
        error=err,
        trace=agent_result.get("trace"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def _render_prepared_dd_report(agent_result: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Render a DDR from a prior prepare_due_diligence_data handoff."""
    render_input = agent_result.get("render_input")
    if not isinstance(render_input, dict):
        return None, "Prepared DD data result did not include render_input"
    try:
        result = route_tool_call_sync("create_dd_report", render_input)
    except Exception as exc:  # noqa: BLE001 - pipeline records a clean failure
        return None, str(exc)
    if not isinstance(result, dict):
        return None, f"create_dd_report returned {type(result).__name__}, expected object"
    if result.get("status") != "success":
        message = str(result.get("message") or result.get("error") or "create_dd_report failed")
        return None, message
    document = result.get("document")
    if not isinstance(document, dict) or not str(document.get("id") or "").strip():
        return None, "create_dd_report did not return a valid document"
    return result, ""


def _check_generated_report(
    site_title: str,
    doc_id: str,
    doc_url: str,
) -> tuple[dict[str, Any] | None, PipelineResult | None]:
    """Run completeness check and convert failures into a PipelineResult."""
    import asyncio

    from . import server as srv

    completeness = asyncio.run(srv.check_report_completeness(doc_id))
    completeness_error = _get_payload_error(completeness)
    if completeness_error:
        logger.error("Completeness check failed for '%s': %s", site_title, completeness_error)
        return None, PipelineResult(
            site_title=site_title,
            status="error",
            doc_id=doc_id,
            doc_url=doc_url,
            error=completeness_error,
        )

    if completeness.get("ready_to_send", False):
        return completeness, None

    return None, PipelineResult(
        site_title=site_title,
        status="report_incomplete",
        doc_id=doc_id,
        doc_url=doc_url,
        unresolved_tokens=completeness.get("unresolved_tokens", []),
        error=str(completeness.get("summary") or completeness.get("message") or ""),
    )


def _email_pipeline_report(
    settings: Settings,
    site_title: str,
    doc_url: str,
    p1_email: str | None,
    *,
    is_update: bool = False,
    open_question_count: int = 0,
) -> str | None:
    """Send the completed DD report email when email settings are configured."""
    if not settings.email_sender or not settings.email_app_password:
        return "email settings not configured"

    recipients = [
        r.strip()
        for r in settings.dd_report_email_recipients.split(",")
        if r.strip()
    ] if settings.dd_report_email_recipients else []

    if p1_email and p1_email.lower() not in {r.lower() for r in recipients}:
        recipients.append(p1_email)

    safe_site_name = escape_html_text(site_title)
    safe_report_url = sanitize_http_url(doc_url)
    report_link_html = "<p>Report link unavailable.</p>"
    if safe_report_url:
        report_link_html = (
            f'<p><a href="{safe_report_url}" '
            'style="font-size:16px;font-weight:bold;">'
            "View Report in Google Docs</a></p>"
        )

    html_body = f"""
<html><body>
<h2>Due Diligence Report - {safe_site_name}</h2>
<p>The Due Diligence report has been {"updated" if is_update else "generated"} for <strong>{safe_site_name}</strong>.</p>
{"<p><strong>Status:</strong> DDR Published (Partial). Open verification items remain.</p>" if open_question_count else ""}
{report_link_html}
</body></html>
"""
    try:
        send_email(
            sender=settings.email_sender,
            app_password=settings.email_app_password,
            recipients=recipients,
            subject=f"DD Report {'Updated' if is_update else 'Ready'} - {site_title}",
            html_body=html_body,
            global_cc=settings.global_email_cc,
        )
        logger.info("Email sent for '%s' to %s", site_title, recipients)
        return None
    except Exception as e:
        logger.error("Failed to send email for '%s': %s", site_title, e)
        return str(e)


def _resolve_rhodes_owner_for_pipeline(
    site_title: str,
    site_address: str | None,
    site_id: str | None = None,
) -> dict[str, Any]:
    """Best-effort Rhodes site lookup for owner and Drive-folder context."""
    try:
        result = lookup_rhodes_site_owner(
            site_name=site_title,
            site_address=site_address or "",
            site_id=site_id or "",
        )
    except Exception as exc:  # noqa: BLE001 - non-blocking lookup
        logger.warning("Rhodes owner lookup failed for %s: %s", site_title, exc)
        return {
            "status": "error",
            "message": str(exc),
            "report_data_fields": {},
        }
    return result


def _owner_lookup_report_fields(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    fields = result.get("report_data_fields")
    return fields if isinstance(fields, dict) else {}


def _mark_missing_p1_dri_if_needed(
    recorder: _RunRecorder,
    result: dict[str, Any] | None,
) -> None:
    if isinstance(result, dict) and result.get("status") == "owner_missing":
        recorder.p1_dri_missing = True


def _drive_folder_from_rhodes(result: dict[str, Any] | None) -> str | None:
    if not isinstance(result, dict):
        return None
    direct = result.get("drive_folder_url")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    fields = _owner_lookup_report_fields(result)
    for key in ("meta.drive_folder_url", "site.drive_folder_url"):
        value = fields.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _owner_user_id_from_context(context: dict[str, Any] | None) -> str:
    if not isinstance(context, dict):
        return ""
    for key in ("p1_assignee_user_id", "owner_user_id", "p1_user_id"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    owner = context.get("p1_dri")
    if isinstance(owner, dict):
        for key in ("userId", "user_id", "_id", "id"):
            value = owner.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _post_google_chat_to_configured_webhooks(webhook_urls: str, text: str) -> dict[str, Any]:
    return post_google_chat_to_configured_webhooks(
        webhook_urls,
        text,
        post_message=post_google_chat_message,
    )


def _report_data_from_trace(trace: ReportTrace | None) -> dict[str, Any]:
    if trace is None:
        return {}
    return trace.final_report_data if isinstance(trace.final_report_data, dict) else {}


def _set_open_question_state(
    result: PipelineResult,
    *,
    trace: ReportTrace | None,
    run_id: str,
    source_event: dict[str, Any] | None,
    open_questions_before: list[dict[str, Any]] | None,
    validated: bool,
) -> None:
    questions = extract_open_questions_from_report_data(
        _report_data_from_trace(trace),
        created_run=run_id,
    )
    result.open_questions = serialize_open_questions(questions)
    result.source_event = source_event
    if validated:
        closures = close_open_questions(
            open_questions_before or [],
            result.open_questions,
            source_event=source_event,
            closed_run=run_id,
        )
        result.closed_open_questions = [closure.to_dict() for closure in closures]
    if source_event:
        result.republish_summary = {
            "trigger_source": source_event.get("source_type", ""),
            "closed_open_item_count": len(result.closed_open_questions),
            "still_open_item_count": len(result.open_questions),
            "outstanding_vendor_docs": result.missing_docs,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Full single-site pipeline
# ─────────────────────────────────────────────────────────────────────────────


def _record_source_alert_step(
    recorder: _RunRecorder,
    settings: Settings,
    site_title: str,
    trace: ReportTrace | None,
    *,
    drive_folder_url: str,
    trace_url: str,
    site_id: str,
    owner_user_id: str,
    owner_email: str,
) -> None:
    started_at, started_monotonic = recorder.start()
    issues = _extract_source_read_issues(trace)
    if issues:
        event = build_source_review_required_event(
            site_id=site_id,
            site_name=site_title,
            run_id=recorder.run_id,
            issues=issues,
            drive_folder_url=drive_folder_url,
            trace_url=trace_url,
        )
        event_status, body = record_rhodes_automation_event(
            event,
            owner_user_id=owner_user_id,
            owner_email=owner_email,
            add_note=add_rhodes_site_note,
        )
        if should_alert_google_chat(event_status):
            event_status["google_chat"] = _post_google_chat_to_configured_webhooks(
                settings.google_chat_webhook_url,
                body,
            )
        artifact = ArtifactRef(
            kind="rhodes_note",
            name="DDR source review AutomationEvent",
            metadata=event_status,
        )
        recorder.record(
            "source.alert",
            started_at,
            started_monotonic,
            "failed",
            error=_pipeline_error(
                recorder.run_id,
                "source.alert",
                "source_read_issue",
                f"{len(issues)} required source document read issue(s)",
            ),
            artifacts=[artifact],
        )
    else:
        recorder.record("source.alert", started_at, started_monotonic, "succeeded")


def _record_sir_learning_review_step(
    recorder: _RunRecorder,
    review: dict[str, Any] | None,
) -> None:
    started_at, started_monotonic = recorder.start()
    recorder.sir_learning_review = review
    if not review:
        recorder.record(
            "sir.learning_review",
            started_at,
            started_monotonic,
            "skipped",
            skipped_reason="no SIR review metadata",
        )
        return

    status = str(review.get("status") or "not_applicable")
    if status == "ready_for_review":
        recorder.record(
            "sir.learning_review",
            started_at,
            started_monotonic,
            "succeeded",
            artifacts=[
                ArtifactRef(
                    kind="sir_learning_review",
                    name="AI/CDS SIR comparison candidate",
                    metadata=review,
                )
            ],
        )
        return

    recorder.record(
        "sir.learning_review",
        started_at,
        started_monotonic,
        "skipped",
        skipped_reason=str(review.get("reason") or status),
    )


def _record_email_step(
    recorder: _RunRecorder,
    settings: Settings,
    site_title: str,
    doc_url: str,
    p1_email: str | None,
    *,
    is_update: bool = False,
    open_question_count: int = 0,
    full_report_inputs_present: bool = False,
) -> None:
    started_at, started_monotonic = recorder.start()
    skip_reason = _dd_report_email_skip_reason(
        is_update=is_update,
        full_report_inputs_present=full_report_inputs_present,
        open_question_count=open_question_count,
    )
    if skip_reason:
        recorder.record(
            "notify.email",
            started_at,
            started_monotonic,
            "skipped",
            skipped_reason=skip_reason,
        )
        return

    error = _email_pipeline_report(
        settings,
        site_title,
        doc_url,
        p1_email,
        is_update=is_update,
        open_question_count=open_question_count,
    )
    if error == "email settings not configured":
        recorder.record(
            "notify.email",
            started_at,
            started_monotonic,
            "skipped",
            skipped_reason=error,
        )
    elif error:
        recorder.record(
            "notify.email",
            started_at,
            started_monotonic,
            "failed",
            error=_pipeline_error(recorder.run_id, "notify.email", "email_failed", error),
        )
    else:
        recorder.record("notify.email", started_at, started_monotonic, "succeeded")


def _dd_report_email_skip_reason(
    *,
    is_update: bool,
    full_report_inputs_present: bool,
    open_question_count: int,
) -> str | None:
    """Return why a DD report email should be skipped, or None to send it."""
    if not is_update:
        return None
    if not full_report_inputs_present:
        return "interim DDR update; full vendor input set not present"
    if open_question_count > 0:
        return "interim DDR update; open verification items remain"
    return None


def _record_rhodes_due_diligence_update_step(
    recorder: _RunRecorder,
    result: PipelineResult,
    *,
    site_id: str,
    report_data: dict[str, Any],
    due_diligence_write_mode: str = "api",
    locationos_mcp_write_completed: bool = False,
) -> None:
    started_at, started_monotonic = recorder.start()
    fields = _build_due_diligence_update_fields(
        report_data,
        result,
        completed_at=recorder.started_at,
    )
    source_packet = result.source_packet if isinstance(result.source_packet, dict) else None
    if source_packet is not None:
        fields = locationos_fields_allowed_by_source_packet(fields, source_packet)
    if due_diligence_write_mode == "mcp_assisted" and locationos_mcp_write_completed:
        update_status = _verify_locationos_mcp_due_diligence_update(
            site_id=site_id,
            fields=fields,
        )
    else:
        field_sources = (
            dd_field_update_sources(source_packet.get("dd_field_updates") or [])
            if source_packet is not None
            else None
        )
        update_status = update_rhodes_due_diligence(
            site_id=site_id,
            fields=fields,
            field_sources=field_sources,
        )
        if (
            due_diligence_write_mode == "mcp_assisted"
            and _due_diligence_update_needs_locationos_mcp(update_status)
        ):
            update_status = {
                **update_status,
                LOCATIONOS_MCP_WRITE_REQUEST_KEY: _build_locationos_mcp_write_request(
                    run_id=recorder.run_id,
                    site_title=result.site_title,
                    site_id=site_id,
                    fields=fields,
                    field_sources=field_sources,
                    error=str(update_status.get("error") or ""),
                    document_already_rendered=bool(result.doc_url),
                    doc_url=result.doc_url or "",
                ),
            }
    if source_packet is not None:
        source_packet = mark_written_fields_from_update_result(
            source_packet=source_packet,
            update_result=update_status,
        )
        result.source_packet = source_packet
        update_status = {
            **update_status,
            "source_packet_status": source_packet.get("status"),
            "m2_source_packet_complete": source_packet.get("m2_source_packet_complete"),
            "source_note_lines": source_packet.get("source_note_lines", []),
            "source_packet_open_items": source_packet.get("open_items", []),
        }
    result.rhodes_due_diligence_update = update_status
    artifact = ArtifactRef(
        kind="rhodes_due_diligence",
        name="Rhodes due diligence update",
        metadata=update_status,
    )
    status = str(update_status.get("status") or "")
    if status in {"updated", "proposal_submitted"}:
        # proposal_submitted means the write entered the LocationOS approval
        # queue and the owner note was verified: the action executed and is
        # logged; the site owner tracks the pending change from here.
        recorder.record(
            DUE_DILIGENCE_UPDATE_STEP,
            started_at,
            started_monotonic,
            "succeeded",
            artifacts=[artifact],
        )
        return
    if status == "skipped":
        recorder.record(
            DUE_DILIGENCE_UPDATE_STEP,
            started_at,
            started_monotonic,
            "skipped",
            skipped_reason=str(update_status.get("reason") or "skipped"),
            artifacts=[artifact],
        )
        return

    message = str(
        update_status.get("error_summary")
        or update_status.get("error")
        or update_status.get("reason")
        or "Rhodes due diligence update failed"
    )
    recorder.record(
        DUE_DILIGENCE_UPDATE_STEP,
        started_at,
        started_monotonic,
        "failed",
        error=_pipeline_error(
            recorder.run_id,
            DUE_DILIGENCE_UPDATE_STEP,
            "rhodes_due_diligence_update_failed",
            message,
        ),
        artifacts=[artifact],
    )


def _verify_locationos_mcp_due_diligence_update(
    *,
    site_id: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    readback_status = verify_rhodes_due_diligence_fields(site_id=site_id, fields=fields)
    base = {
        "rhodes_site_id": site_id.strip(),
        "updated_fields": sorted(fields),
        "write_mode": "locationos_mcp",
        "mcp_write_completed": True,
    }
    if readback_status.get("status") == "verified":
        return {
            **base,
            "status": "updated",
            "reason": "locationos_mcp_readback_verified",
            "readback": readback_status.get("readback"),
        }
    return {
        **base,
        "status": "failed",
        "reason": "locationos_mcp_readback_failed",
        "error": (
            str(readback_status.get("error") or readback_status.get("reason") or "")
            + " Note: an operator-approved updateDueDiligence can land in the "
            "LocationOS approval queue, in which case values will not read "
            "back until the pending field-change request is approved - check "
            "the approval queue before retrying the write."
        ).strip(),
        "readback": readback_status,
    }


def _due_diligence_update_needs_locationos_mcp(update_status: dict[str, Any]) -> bool:
    if update_status.get("status") != "failed":
        return False
    return "elicitation_unsupported" in json.dumps(update_status, default=str).lower()


def _build_locationos_mcp_write_request(
    *,
    run_id: str,
    site_title: str,
    site_id: str,
    fields: dict[str, Any],
    error: str,
    field_sources: Mapping[str, str] | None = None,
    document_already_rendered: bool = False,
    doc_url: str = "",
) -> dict[str, Any]:
    arguments: dict[str, Any] = {"siteId": site_id.strip()}
    for key in sorted(fields):
        arguments[key] = fields[key]
    resume_condition = (
        "Run updateDueDiligence through the user's OAuth-backed "
        "locationos MCP, approve the Aerie card if prompted, then rerun "
        "the emitted manifest-bound resume command."
    )
    if document_already_rendered:
        resume_condition = (
            "Run updateDueDiligence through the user's OAuth-backed "
            "locationos MCP, approve the Aerie card if prompted, then verify "
            "the fields with getSite. No DDR resume is required because the "
            "DD Report Google Doc already exists."
        )
    return {
        "status": "pending",
        "server": "locationos",
        "tool": "updateDueDiligence",
        "reason": "updateDueDiligence requires OAuth-backed LocationOS MCP elicitation",
        "error": error,
        "site_id": site_id.strip(),
        "site_title": site_title,
        "run_id": run_id,
        "arguments": arguments,
        "field_sources": dict(field_sources or {}),
        "readback": {
            "server": "locationos",
            "tool": "getSite",
            "arguments": {"siteId": site_id.strip()},
            "verify_fields": sorted(fields),
        },
        "resume": {
            "required": not document_already_rendered,
            "condition": resume_condition,
        },
        "dd_report_doc_url": doc_url.strip() if document_already_rendered else "",
    }


def _locationos_mcp_resume_condition(resume_required: bool) -> str:
    if resume_required:
        return (
            "Run updateDueDiligence through the user's OAuth-backed locationos MCP, "
            "approve the Aerie card if prompted, then rerun the emitted "
            "manifest-bound resume command."
        )
    return (
        "Run updateDueDiligence through the user's OAuth-backed locationos MCP, "
        "approve the Aerie card if prompted, then verify live getSite readback. "
        "No DDR resume is required because the DD Report Doc already exists."
    )


def _locationos_mcp_write_request_from_result(
    result: PipelineResult,
) -> dict[str, Any] | None:
    update_status = result.rhodes_due_diligence_update
    if not isinstance(update_status, dict):
        return None
    request = update_status.get(LOCATIONOS_MCP_WRITE_REQUEST_KEY)
    return request if isinstance(request, dict) else None


def _build_locationos_mcp_resume_payload(
    *,
    recorder: _RunRecorder,
    result: PipelineResult,
    agent_result: dict[str, Any],
    site_id: str,
    drive_folder_url: str,
    owner_user_id: str,
    owner_email: str,
    p1_name: str | None,
) -> dict[str, Any] | None:
    request = _locationos_mcp_write_request_from_result(result)
    render_input = agent_result.get("render_input")
    if request is None or not isinstance(render_input, dict):
        return None
    prepared_report_data = agent_result.get("prepared_report_data")
    if not isinstance(prepared_report_data, dict):
        prepared_report_data = _report_data_from_trace(result.trace)
    report_metadata = agent_result.get("report_metadata")
    return {
        "schema_version": LOCATIONOS_MCP_RESUME_SCHEMA_VERSION,
        "source_run_id": recorder.run_id,
        "site_id": site_id.strip(),
        "site_title": result.site_title,
        "drive_folder_url": drive_folder_url,
        "owner_user_id": owner_user_id,
        "owner_email": owner_email,
        "p1_name": p1_name or "",
        "locationos_mcp_write_request": request,
        "render_input": render_input,
        "prepared_report_data": prepared_report_data,
        "report_metadata": report_metadata if isinstance(report_metadata, dict) else {},
        "missing_docs": list(result.missing_docs),
        "source_event": result.source_event,
        "open_questions": list(result.open_questions),
        "closed_open_questions": list(result.closed_open_questions),
        "trace_url": result.trace_url or "",
    }


def _locationos_mcp_resume_fields(
    resume_payload: dict[str, Any],
) -> tuple[str, dict[str, Any], str | None]:
    request = resume_payload.get("locationos_mcp_write_request")
    if not isinstance(request, dict):
        return "", {}, "Manifest resume payload is missing locationos_mcp_write_request"
    arguments = request.get("arguments")
    if not isinstance(arguments, dict):
        return "", {}, "LocationOS MCP write request is missing arguments"
    site_id = str(arguments.get("siteId") or resume_payload.get("site_id") or "").strip()
    if not site_id:
        return "", {}, "LocationOS MCP write request is missing siteId"
    fields = {str(key): value for key, value in arguments.items() if key != "siteId"}
    if not fields:
        return site_id, {}, "LocationOS MCP write request has no due diligence fields"
    return site_id, fields, None


def _record_locationos_mcp_resume_readback_step(
    recorder: _RunRecorder,
    result: PipelineResult,
    *,
    site_id: str,
    fields: dict[str, Any],
) -> None:
    started_at, started_monotonic = recorder.start()
    update_status = _verify_locationos_mcp_due_diligence_update(
        site_id=site_id,
        fields=fields,
    )
    result.rhodes_due_diligence_update = update_status
    artifact = ArtifactRef(
        kind="rhodes_due_diligence",
        name="Rhodes due diligence MCP readback",
        metadata=update_status,
    )
    if update_status.get("status") == "updated":
        recorder.record(
            DUE_DILIGENCE_UPDATE_STEP,
            started_at,
            started_monotonic,
            "succeeded",
            artifacts=[artifact],
        )
        return
    message = str(
        update_status.get("error")
        or update_status.get("reason")
        or "LocationOS MCP due diligence readback failed"
    )
    recorder.record(
        DUE_DILIGENCE_UPDATE_STEP,
        started_at,
        started_monotonic,
        "failed",
        error=_pipeline_error(
            recorder.run_id,
            DUE_DILIGENCE_UPDATE_STEP,
            "locationos_mcp_readback_failed",
            message,
        ),
        artifacts=[artifact],
    )


def _build_due_diligence_update_fields(
    report_data: dict[str, Any],
    result: PipelineResult,
    *,
    completed_at: str,
) -> dict[str, Any]:
    flat = _aliased_report_data(report_data)
    final_ready = _due_diligence_result_is_final_ready(result)
    fields: dict[str, Any] = {
        "status": _due_diligence_status_for_result(result),
    }
    if final_ready:
        fields["dateCompleted"] = _date_completed_value(completed_at)
        doc_url = _clean_due_diligence_value(result.doc_url)
        if doc_url is not None:
            fields["ddReportLink"] = doc_url

    for source_key, rhodes_key in _DUE_DILIGENCE_REPORT_FIELD_MAP:
        value = _clean_due_diligence_field_value(rhodes_key, flat.get(source_key))
        if value is not None:
            fields[rhodes_key] = value

    recommendation = _explicit_due_diligence_recommendation(flat)
    if recommendation:
        fields["recommendation"] = recommendation

    _drop_green_due_diligence_comments(fields)

    return fields


def _drop_green_due_diligence_comments(fields: dict[str, Any]) -> None:
    for score_key, comment_key in _DUE_DILIGENCE_GREEN_SCORE_COMMENT_PAIRS:
        if fields.get(score_key) == 1:
            fields.pop(comment_key, None)


def _clean_due_diligence_field_value(rhodes_key: str, value: Any) -> Any:
    cleaned = _clean_due_diligence_value(value)
    if cleaned is None:
        return None
    if rhodes_key in _DUE_DILIGENCE_SCORE_FIELD_KEYS:
        return _normalize_due_diligence_score_value(cleaned)
    if rhodes_key in _DUE_DILIGENCE_NUMERIC_FIELD_KEYS:
        return _normalize_due_diligence_numeric_value(cleaned)
    return cleaned


def _aliased_report_data(report_data: dict[str, Any]) -> dict[str, Any]:
    flat = flatten_report_data_for_replacement(report_data)
    for alias, canonical in AGENT_KEY_ALIASES.items():
        if alias in flat and canonical not in flat:
            flat[canonical] = flat[alias]
    return flat


def _due_diligence_status_for_result(result: PipelineResult) -> str:
    if _due_diligence_result_is_final_ready(result):
        return "complete"
    if _due_diligence_result_is_follow_up(result):
        return "follow-up"
    return "data-gathering"


def _due_diligence_result_is_final_ready(result: PipelineResult) -> bool:
    return (
        not result.open_questions
        and not result.missing_docs
        and _source_packet_allows_completion(result)
    )


def _source_packet_allows_completion(result: PipelineResult) -> bool:
    packet = result.source_packet if isinstance(result.source_packet, dict) else None
    if not packet:
        return True
    return source_packet_is_complete(packet)


def _due_diligence_result_is_follow_up(result: PipelineResult) -> bool:
    return bool(result.source_event) and bool(result.open_questions) and not result.missing_docs


def _date_completed_value(completed_at: str) -> str:
    parsed = _parse_iso_datetime(completed_at)
    if parsed is None:
        parsed = datetime.now(UTC)
    return parsed.date().isoformat()


def _clean_due_diligence_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        lowered = text.lower()
        if lowered.startswith("[not found") or text.startswith("{{"):
            return None
        return text
    if isinstance(value, bool | int | float):
        return value
    return None


def _normalize_due_diligence_score_value(value: Any) -> int | None:
    """Normalize report-facing score text to LocationOS score enum values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value in {1, 2, 3} else None
    if isinstance(value, float):
        if value.is_integer():
            numeric = int(value)
            return numeric if numeric in {1, 2, 3} else None
        return None
    if not isinstance(value, str):
        return None

    text = value.strip().lower()
    if not text:
        return None
    if text in {"1", "2", "3"}:
        return int(text)
    try:
        parsed_score = float(text)
    except ValueError:
        parsed_score = None
    if parsed_score is not None and parsed_score.is_integer():
        score = int(parsed_score)
        return score if score in {1, 2, 3} else None
    for label, score in _DUE_DILIGENCE_SCORE_LABELS.items():
        if label in text:
            return score
    return None


def _normalize_due_diligence_numeric_value(value: Any) -> int | float | None:
    """Normalize LocationOS numeric fields without guessing at ranges or gaps."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    lowered = text.lower()
    if (
        any(marker in lowered for marker in ("not found", "pending", "unknown", "tbd"))
        or "-" in text
        or " to " in lowered
    ):
        return None

    compact = text.replace("$", "").replace(",", "").strip()
    if compact.upper().endswith("USD"):
        compact = compact[:-3].strip()
    if not _DUE_DILIGENCE_NUMBER_RE.fullmatch(compact):
        return None

    parsed = float(compact)
    return int(parsed) if parsed.is_integer() else parsed


def _explicit_due_diligence_recommendation(flat_report_data: dict[str, Any]) -> str | None:
    for key in _DUE_DILIGENCE_RECOMMENDATION_KEYS:
        value = _clean_due_diligence_value(flat_report_data.get(key))
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower().replace("_", "-")
        if normalized == "go":
            return "go"
        if normalized in {"no-go", "no go", "nogo"}:
            return "no-go"
    return None


def _due_diligence_update_failed(result: PipelineResult) -> bool:
    update_status = result.rhodes_due_diligence_update
    return isinstance(update_status, dict) and update_status.get("status") == "failed"


def _due_diligence_update_is_document_first_blocker(result: PipelineResult) -> bool:
    update_status = result.rhodes_due_diligence_update
    if not isinstance(update_status, dict) or update_status.get("status") != "failed":
        return False

    if _locationos_mcp_write_request_from_result(result) is not None:
        return True

    reason = str(update_status.get("reason") or "").strip().lower()
    readback = update_status.get("readback")
    readback_reason = ""
    if isinstance(readback, dict):
        if readback.get("mismatches"):
            return False
        readback_reason = str(readback.get("reason") or "").strip().lower()
        if readback_reason == "field_mismatch":
            return False

    if reason in {"readback_failed", "locationos_mcp_readback_failed"}:
        return True

    return readback_reason in {"get_site_failed", "readback_failed"}


def _due_diligence_update_was_written(update_status: dict[str, Any] | None) -> bool:
    return isinstance(update_status, dict) and update_status.get("status") == "updated"


def _record_rhodes_report_event_step(
    recorder: _RunRecorder,
    settings: Settings,
    result: PipelineResult,
    *,
    site_id: str,
    owner_user_id: str,
    owner_email: str,
) -> None:
    started_at, started_monotonic = recorder.start()
    event = build_dd_report_summary_event(
        site_id=site_id,
        site_name=result.site_title,
        run_id=recorder.run_id,
        doc_id=result.doc_id,
        doc_url=result.doc_url,
        source_event=result.source_event,
        open_questions=result.open_questions,
        closed_open_questions=result.closed_open_questions,
        missing_vendor_docs=result.missing_docs,
        due_diligence_update=result.rhodes_due_diligence_update,
    )
    event_status, body = record_rhodes_automation_event(
        event,
        owner_user_id=owner_user_id,
        owner_email=owner_email,
        add_note=add_rhodes_site_note,
    )
    should_alert_chat = should_alert_google_chat(
        event_status,
        decision_required=event.decision_required,
    )
    chat_result: dict[str, Any] | None = None
    if should_alert_chat:
        chat_result = _post_google_chat_to_configured_webhooks(
            settings.google_chat_webhook_url,
            body,
        )
        event_status["google_chat"] = chat_result
    if _report_event_needs_wtc_review(event_status):
        _mark_report_event_wtc_review(
            event_status,
            body=body,
            owner_user_id=owner_user_id,
            owner_email=owner_email,
        )
    if _report_event_notification_was_sent(event_status):
        event_status["sent_at"] = recorder.started_at

    result.rhodes_report_event = event_status
    artifact = ArtifactRef(
        kind="rhodes_note",
        name="DDR report AutomationEvent",
        metadata=event_status,
    )
    note_status = str(event_status.get("status") or "")
    chat_status = str((chat_result or {}).get("status") or "")
    if note_status == "created" and chat_status not in {"failed", "skipped"}:
        recorder.record(
            "rhodes.report_event",
            started_at,
            started_monotonic,
            "succeeded",
            artifacts=[artifact],
        )
        return
    if note_status == "created" and not should_alert_chat:
        recorder.record(
            "rhodes.report_event",
            started_at,
            started_monotonic,
            "succeeded",
            artifacts=[artifact],
        )
        return
    if note_status == "skipped" and not event.decision_required:
        recorder.record(
            "rhodes.report_event",
            started_at,
            started_monotonic,
            "skipped",
            skipped_reason=str(event_status.get("reason") or "skipped"),
        )
        return
    if event_status.get("wtc_review_required"):
        message = str(event_status.get("error") or event_status.get("reason") or "unknown")
        if should_alert_chat and chat_status in {"failed", "skipped"}:
            message = f"{message}; Google Chat fallback {chat_status}"
        warning = (
            "Rhodes event note was not verified; manually confirm the DD Report "
            "Google Doc was produced and LocationOS/Rhodes dueDiligence fields "
            "were completed."
        )
        event_status["severity"] = "warning"
        event_status["warning"] = warning
        event_status["manual_check"] = {
            "dd_report_doc": result.doc_url or "",
            "due_diligence_update": result.rhodes_due_diligence_update or {},
            "reason": message,
        }
        artifact.metadata = event_status
        recorder.record(
            "rhodes.report_event",
            started_at,
            started_monotonic,
            "succeeded",
            artifacts=[artifact],
        )
        return

    message = str(event_status.get("error") or event_status.get("reason") or "unknown")
    if should_alert_chat and chat_status in {"failed", "skipped"}:
        message = f"{message}; Google Chat fallback {chat_status}"
    warning = (
        "Rhodes event note was not verified; manually confirm the DD Report "
        "Google Doc was produced and LocationOS/Rhodes dueDiligence fields "
        "were completed."
    )
    event_status["severity"] = "warning"
    event_status["warning"] = warning
    event_status["manual_check"] = {
        "dd_report_doc": result.doc_url or "",
        "due_diligence_update": result.rhodes_due_diligence_update or {},
        "reason": message,
    }
    artifact.metadata = event_status
    recorder.record(
        "rhodes.report_event",
        started_at,
        started_monotonic,
        "skipped",
        skipped_reason=warning,
        artifacts=[artifact],
    )


def _record_republish_candidate_event_step(
    recorder: _RunRecorder,
    settings: Settings,
    result: PipelineResult,
    *,
    site_id: str,
    owner_user_id: str,
    owner_email: str,
) -> None:
    started_at, started_monotonic = recorder.start()
    guard: dict[str, Any] = {}
    if isinstance(result.republish_summary, dict):
        raw_guard = result.republish_summary.get("overwrite_guard")
        if isinstance(raw_guard, dict):
            guard = raw_guard
    event = build_dd_report_republish_candidate_event(
        site_id=site_id,
        site_name=result.site_title,
        run_id=recorder.run_id,
        candidate_doc_id=result.doc_id,
        candidate_doc_url=result.doc_url,
        source_event=result.source_event,
        missing_vendor_docs=result.missing_docs,
        overwrite_guard=guard,
        due_diligence_update=result.rhodes_due_diligence_update,
    )
    event_status, body = record_rhodes_automation_event(
        event,
        owner_user_id=owner_user_id,
        owner_email=owner_email,
        add_note=add_rhodes_site_note,
    )
    should_alert_chat = should_alert_google_chat(
        event_status,
        decision_required=event.decision_required,
    )
    chat_result: dict[str, Any] | None = None
    if should_alert_chat:
        chat_result = _post_google_chat_to_configured_webhooks(
            settings.google_chat_webhook_url,
            body,
        )
        event_status["google_chat"] = chat_result
    if _report_event_needs_wtc_review(event_status):
        _mark_report_event_wtc_review(
            event_status,
            body=body,
            owner_user_id=owner_user_id,
            owner_email=owner_email,
        )
    result.rhodes_report_event = event_status
    artifact = ArtifactRef(
        kind="rhodes_note",
        name="DDR republish candidate AutomationEvent",
        metadata=event_status,
    )
    note_status = str(event_status.get("status") or "")
    chat_status = str((chat_result or {}).get("status") or "")
    if note_status == "created" and chat_status not in {"failed", "skipped"}:
        recorder.record(
            "rhodes.report_event",
            started_at,
            started_monotonic,
            "succeeded",
            artifacts=[artifact],
        )
        return
    if note_status == "created" and not should_alert_chat:
        recorder.record(
            "rhodes.report_event",
            started_at,
            started_monotonic,
            "succeeded",
            artifacts=[artifact],
        )
        return
    if event_status.get("wtc_review_required"):
        recorder.record(
            "rhodes.report_event",
            started_at,
            started_monotonic,
            "succeeded",
            artifacts=[artifact],
        )
        return
    message = str(event_status.get("error") or event_status.get("reason") or "unknown")
    if should_alert_chat and chat_status in {"failed", "skipped"}:
        message = f"{message}; Google Chat fallback {chat_status}"
    recorder.record(
        "rhodes.report_event",
        started_at,
        started_monotonic,
        "failed",
        error=_pipeline_error(
            recorder.run_id,
            "rhodes.report_event",
            "republish_candidate_event_failed",
            message,
        ),
        artifacts=[artifact],
    )


def resume_locationos_mcp_write_from_manifest(
    run_id: str,
    *,
    settings: Settings | None = None,
    gc: GoogleClient | None = None,
) -> PipelineResult:
    """Resume a blocked MCP-assisted SOR handoff without regenerating DD data."""

    try:
        manifest = load_run_manifest(run_id)
    except Exception as exc:  # noqa: BLE001 - CLI should return structured failure
        return PipelineResult(
            site_title="",
            status="error",
            error=f"Unable to load run manifest {run_id}: {exc}",
        )

    resume_payload = manifest.get("locationos_mcp_resume")
    site_title_source = (
        resume_payload.get("site_title")
        if isinstance(resume_payload, dict)
        else manifest.get("site_title")
    )
    site_title = str(site_title_source or "").strip() or str(
        manifest.get("site_title") or run_id
    )
    site_id_source = (
        resume_payload.get("site_id")
        if isinstance(resume_payload, dict)
        else manifest.get("site_id")
    )
    site_id = str(site_id_source or "").strip()
    recorder = _RunRecorder(site_title, site_id=site_id or None)
    base_result = PipelineResult(site_title=site_title, status="report_data_prepared")

    if not isinstance(resume_payload, dict):
        started_at, started_monotonic = recorder.start()
        recorder.record(
            "locationos_mcp.resume_load",
            started_at,
            started_monotonic,
            "failed",
            error=_pipeline_error(
                recorder.run_id,
                "locationos_mcp.resume_load",
                "locationos_mcp_resume_missing",
                f"Run manifest {run_id} does not contain a LocationOS MCP resume payload",
                retryable=False,
            ),
        )
        base_result.status = "error"
        base_result.error = (
            f"Run manifest {run_id} does not contain a LocationOS MCP resume payload"
        )
        return _finalize_pipeline_result(base_result, recorder, gc=gc)

    base_result.locationos_mcp_resume = resume_payload
    base_result.missing_docs = _string_list(resume_payload.get("missing_docs"))
    base_result.source_event = (
        resume_payload.get("source_event")
        if isinstance(resume_payload.get("source_event"), dict)
        else None
    )
    base_result.open_questions = _dict_list(resume_payload.get("open_questions"))
    base_result.closed_open_questions = _dict_list(
        resume_payload.get("closed_open_questions")
    )
    base_result.trace_url = str(resume_payload.get("trace_url") or "")

    source_run_id = str(resume_payload.get("source_run_id") or run_id)
    started_at, started_monotonic = recorder.start()
    if resume_payload.get("schema_version") != LOCATIONOS_MCP_RESUME_SCHEMA_VERSION:
        recorder.record(
            "locationos_mcp.resume_load",
            started_at,
            started_monotonic,
            "failed",
            error=_pipeline_error(
                recorder.run_id,
                "locationos_mcp.resume_load",
                "locationos_mcp_resume_schema_unsupported",
                "Run manifest LocationOS MCP resume payload has an unsupported schema",
                retryable=False,
            ),
        )
        base_result.status = "error"
        base_result.error = "Unsupported LocationOS MCP resume payload schema"
        return _finalize_pipeline_result(base_result, recorder, gc=gc)

    render_input = resume_payload.get("render_input")
    if not isinstance(render_input, dict):
        recorder.record(
            "locationos_mcp.resume_load",
            started_at,
            started_monotonic,
            "failed",
            error=_pipeline_error(
                recorder.run_id,
                "locationos_mcp.resume_load",
                "locationos_mcp_resume_render_input_missing",
                "Run manifest LocationOS MCP resume payload is missing render_input",
                retryable=False,
            ),
        )
        base_result.status = "error"
        base_result.error = "LocationOS MCP resume payload is missing render_input"
        return _finalize_pipeline_result(base_result, recorder, gc=gc)

    readback_site_id, fields, fields_error = _locationos_mcp_resume_fields(
        resume_payload
    )
    if fields_error:
        recorder.record(
            "locationos_mcp.resume_load",
            started_at,
            started_monotonic,
            "failed",
            error=_pipeline_error(
                recorder.run_id,
                "locationos_mcp.resume_load",
                "locationos_mcp_resume_arguments_invalid",
                fields_error,
                retryable=False,
            ),
        )
        base_result.status = "error"
        base_result.error = fields_error
        return _finalize_pipeline_result(base_result, recorder, gc=gc)

    if readback_site_id and readback_site_id != recorder.site_id:
        recorder.site_id = readback_site_id

    recorder.record(
        "locationos_mcp.resume_load",
        started_at,
        started_monotonic,
        "succeeded",
        artifacts=[
            ArtifactRef(
                kind="manifest",
                name=f"{source_run_id}.json",
                metadata={
                    "source_run_id": source_run_id,
                    "resume_schema": LOCATIONOS_MCP_RESUME_SCHEMA_VERSION,
                },
            )
        ],
    )

    _record_locationos_mcp_resume_readback_step(
        recorder,
        base_result,
        site_id=readback_site_id,
        fields=fields,
    )
    if _due_diligence_update_failed(base_result):
        base_result.error = str(
            (base_result.rhodes_due_diligence_update or {}).get("error")
            or "LocationOS MCP readback failed"
        )
        return _finalize_pipeline_result(
            base_result,
            recorder,
            gc=gc,
            drive_folder_url=str(resume_payload.get("drive_folder_url") or ""),
        )

    agent_result: dict[str, Any] = {
        "render_input": render_input,
        "prepared_report_data": resume_payload.get("prepared_report_data")
        if isinstance(resume_payload.get("prepared_report_data"), dict)
        else {},
        "report_metadata": resume_payload.get("report_metadata")
        if isinstance(resume_payload.get("report_metadata"), dict)
        else {},
        "trace": None,
    }
    started_at, started_monotonic = recorder.start()
    render_result, render_error = _render_prepared_dd_report(agent_result)
    if render_result is None:
        recorder.record(
            "report.render",
            started_at,
            started_monotonic,
            "failed",
            error=_pipeline_error(
                recorder.run_id,
                "report.render",
                "report_render_failed",
                render_error or "DD report render failed",
            ),
        )
        base_result.status = "generation_failed"
        base_result.error = render_error or "DD report render failed"
        return _finalize_pipeline_result(
            base_result,
            recorder,
            gc=gc,
            drive_folder_url=str(resume_payload.get("drive_folder_url") or ""),
        )

    document = render_result["document"]
    doc_id = str(document.get("id") or "")
    doc_url = str(document.get("url") or "")
    document_role = str(document.get("role") or "active")
    recorder.record(
        "report.render",
        started_at,
        started_monotonic,
        "succeeded",
        artifacts=[
            ArtifactRef(
                kind="google_doc",
                name="DD report",
                uri=doc_url,
                drive_file_id=doc_id,
            )
        ],
    )

    effective_settings = settings or get_settings()
    owner_user_id = str(resume_payload.get("owner_user_id") or "")
    owner_email = str(resume_payload.get("owner_email") or "")
    drive_folder_url = str(resume_payload.get("drive_folder_url") or "")

    if document_role == "candidate":
        candidate_result = PipelineResult(
            site_title=site_title,
            status="republish_candidate_created",
            missing_docs=list(base_result.missing_docs),
            doc_id=doc_id,
            doc_url=doc_url,
            source_event=base_result.source_event,
            open_questions=list(base_result.open_questions),
            closed_open_questions=list(base_result.closed_open_questions),
            rhodes_due_diligence_update=base_result.rhodes_due_diligence_update,
            locationos_mcp_resume=resume_payload,
        )
        guard = render_result.get("republish_guard")
        if isinstance(guard, dict):
            candidate_result.republish_summary = {
                "overwrite_guard": guard,
                "outstanding_vendor_docs": candidate_result.missing_docs,
            }
        _record_republish_candidate_event_step(
            recorder,
            effective_settings,
            candidate_result,
            site_id=readback_site_id,
            owner_user_id=owner_user_id,
            owner_email=owner_email,
        )
        return _finalize_pipeline_result(
            candidate_result,
            recorder,
            gc=gc,
            drive_folder_url=drive_folder_url,
        )

    started_at, started_monotonic = recorder.start()
    completeness, completeness_result = _check_generated_report(site_title, doc_id, doc_url)
    if completeness_result is not None:
        validation_code = "report_validation_error"
        if completeness_result.status != "error":
            validation_code = "report_validation_failed"
        recorder.record(
            "report.validate",
            started_at,
            started_monotonic,
            "failed",
            error=_pipeline_error(
                recorder.run_id,
                "report.validate",
                validation_code,
                completeness_result.error
                or f"{len(completeness_result.unresolved_tokens)} unresolved token(s)",
            ),
            artifacts=[
                ArtifactRef(
                    kind="google_doc",
                    name="DD report",
                    uri=doc_url,
                    drive_file_id=doc_id,
                )
            ],
        )
        completeness_result.missing_docs = list(base_result.missing_docs)
        completeness_result.source_event = base_result.source_event
        completeness_result.open_questions = list(base_result.open_questions)
        completeness_result.closed_open_questions = list(base_result.closed_open_questions)
        completeness_result.rhodes_due_diligence_update = (
            base_result.rhodes_due_diligence_update
        )
        completeness_result.locationos_mcp_resume = resume_payload
        return _finalize_pipeline_result(
            completeness_result,
            recorder,
            gc=gc,
            drive_folder_url=drive_folder_url,
        )
    assert completeness is not None
    recorder.record(
        "report.validate",
        started_at,
        started_monotonic,
        "succeeded",
        artifacts=[
            ArtifactRef(
                kind="google_doc",
                name="DD report",
                uri=doc_url,
                drive_file_id=doc_id,
            )
        ],
    )

    final_result = PipelineResult(
        site_title=site_title,
        status="report_created",
        missing_docs=list(base_result.missing_docs),
        doc_id=doc_id,
        doc_url=doc_url,
        pending_count=completeness.get("pending_section_count", 0),
        source_event=base_result.source_event,
        open_questions=list(base_result.open_questions),
        closed_open_questions=list(base_result.closed_open_questions),
        rhodes_due_diligence_update=base_result.rhodes_due_diligence_update,
        locationos_mcp_resume=resume_payload,
    )
    _record_rhodes_report_event_step(
        recorder,
        effective_settings,
        final_result,
        site_id=readback_site_id,
        owner_user_id=owner_user_id,
        owner_email=owner_email,
    )
    _record_email_step(
        recorder,
        effective_settings,
        site_title,
        doc_url,
        owner_email or None,
        is_update=final_result.source_event is not None,
        open_question_count=len(final_result.open_questions),
        full_report_inputs_present=not final_result.missing_docs,
    )
    return _finalize_pipeline_result(
        final_result,
        recorder,
        gc=gc,
        drive_folder_url=drive_folder_url,
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dd_report_event_frequency_cap(
    event: Any,
    *,
    site_title: str,
    current_run_id: str,
    now: datetime,
    manifest_root: Path | None = None,
) -> dict[str, Any] | None:
    if not getattr(event, "decision_required", False):
        return None
    details = getattr(event, "details", {})
    if isinstance(details, dict) and str(details.get("Trigger source") or "").strip():
        return None
    if _detail_open_count(event) <= 0:
        return None
    prior = _latest_prior_dd_report_notification(
        site_id=str(getattr(event, "site_id", "") or ""),
        site_title=site_title,
        current_run_id=current_run_id,
        manifest_root=manifest_root or RUN_MANIFEST_DIR,
    )
    if prior is None:
        return None

    last_sent_at = prior["sent_at"]
    next_allowed_at = _add_business_days(
        last_sent_at,
        DD_REPORT_EVENT_FREQUENCY_CAP_BUSINESS_DAYS,
    )
    if now >= next_allowed_at:
        return None

    return {
        "event_type": getattr(event, "event_type", "dd_report_event"),
        "source_id": getattr(event, "source_id", current_run_id),
        "decision_required": True,
        "status": "skipped",
        "reason": "frequency_cap",
        "business_day_cap": DD_REPORT_EVENT_FREQUENCY_CAP_BUSINESS_DAYS,
        "last_sent_at": last_sent_at.isoformat(),
        "next_allowed_at": next_allowed_at.isoformat(),
        "message": (
            "Skipped DD report open-ask notification because this site was "
            f"already notified within the last "
            f"{DD_REPORT_EVENT_FREQUENCY_CAP_BUSINESS_DAYS} business days."
        ),
    }


def _latest_prior_dd_report_notification(
    *,
    site_id: str,
    site_title: str,
    current_run_id: str,
    manifest_root: Path,
) -> dict[str, Any] | None:
    if not manifest_root.exists():
        return None
    matches: list[dict[str, Any]] = []
    for path in manifest_root.glob("*.json"):
        payload = _read_manifest(path)
        if not payload:
            continue
        if str(payload.get("run_id") or "") == current_run_id:
            continue
        if not _manifest_matches_report_site(payload, site_id=site_id, site_title=site_title):
            continue
        event_status = payload.get("rhodes_report_event")
        if not isinstance(event_status, dict):
            continue
        if event_status.get("event_type") not in {"dd_report_created", "dd_report_updated"}:
            continue
        if not event_status.get("decision_required"):
            continue
        if not _report_event_notification_was_sent(event_status):
            continue
        sent_at = _sent_at_from_manifest(payload, event_status)
        if sent_at is None:
            continue
        matches.append({"sent_at": sent_at, "run_id": payload.get("run_id")})
    if not matches:
        return None
    return max(matches, key=lambda item: item["sent_at"])


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - stale/corrupt run manifests should not block pipeline work
        return {}
    return payload if isinstance(payload, dict) else {}


def _manifest_matches_report_site(
    payload: dict[str, Any],
    *,
    site_id: str,
    site_title: str,
) -> bool:
    payload_site_id = str(payload.get("site_id") or "").strip()
    if site_id.strip() and payload_site_id and payload_site_id == site_id.strip():
        return True
    return _normalize_site_title(str(payload.get("site_title") or "")) == _normalize_site_title(site_title)


def _normalize_site_title(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _report_event_notification_was_sent(event_status: dict[str, Any]) -> bool:
    if event_status.get("status") == "created":
        return True
    google_chat = event_status.get("google_chat")
    if isinstance(google_chat, dict) and google_chat.get("status") in {"sent", "partial"}:
        return True
    return False


def _report_event_needs_wtc_review(event_status: dict[str, Any]) -> bool:
    if not bool(event_status.get("decision_required", True)):
        return False
    if event_status.get("reason") == "frequency_cap":
        return False
    note_id = str(event_status.get("rhodes_note_id") or "").strip()
    owner_mentioned = (
        event_status.get("status") == "created"
        and event_status.get("owner_notification") == "mentioned"
        and bool(note_id)
    )
    return not owner_mentioned


def _mark_report_event_wtc_review(
    event_status: dict[str, Any],
    *,
    body: str,
    owner_user_id: str,
    owner_email: str,
) -> None:
    if not str(event_status.get("reason") or event_status.get("error") or "").strip():
        event_status["reason"] = "p1_owner_review_note_not_verified"
    event_status["wtc_review_required"] = True
    event_status["intended_note_body"] = body
    event_status["intended_owner_user_id"] = owner_user_id.strip()
    event_status["intended_owner_email"] = owner_email.strip()


def _sent_at_from_manifest(
    payload: dict[str, Any],
    event_status: dict[str, Any],
) -> datetime | None:
    for key in ("sent_at", "created_at", "ended_at", "started_at"):
        source = event_status if key in event_status else payload
        parsed = _parse_iso_datetime(str(source.get(key) or ""))
        if parsed is not None:
            return parsed
    return None


def _parse_iso_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _add_business_days(value: datetime, days: int) -> datetime:
    current = value.astimezone(UTC)
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def _detail_open_count(event: Any) -> int:
    details = getattr(event, "details", {})
    if not isinstance(details, dict):
        return 0
    try:
        return int(details.get("Open item count") or 0)
    except (TypeError, ValueError):
        return 0


def process_site_pipeline(
    gc: GoogleClient,
    site_title: str,
    drive_folder_url: str,
    match_terms: list[str],
    shared_cache: dict[str, list[dict[str, Any]]],
    system_prompt: str,
    settings: Settings,
    p1_email: str | None = None,
    site_address: str | None = None,
    p1_name: str | None = None,
    # Deprecated provenance fields accepted for caller compatibility.
    school_feasibility: str | None = None,
    timeline_confidence: str | None = None,
    # Optional ISO 8601 source created date.
    site_created_at: str | None = None,
    site_id: str | None = None,
    rhodes_owner_context: dict[str, Any] | None = None,
    source_event: dict[str, Any] | None = None,
    open_questions_before: list[dict[str, Any]] | None = None,
    # When True, bypass the ``report_exists`` short-circuit so a fresh
    # report is generated on top of an existing DD Report Doc. Used by the
    # event-driven republish path when authoritative source inputs have just
    # landed. All other gates
    # — vendor gate, missing required docs — still apply.
    force_regenerate: bool = False,
    due_diligence_write_mode: str = "api",
    locationos_mcp_write_completed: bool = False,
    document_first_on_sor_blocker: bool = True,
    launch_context: dict[str, Any] | None = None,
) -> PipelineResult:
    """Full single-site pipeline: readiness -> report generation -> completeness -> email gate.

    Returns a PipelineResult describing what happened.
    """
    if due_diligence_write_mode not in LOCATIONOS_MCP_WRITE_MODES:
        raise ValueError(f"Unsupported due diligence write mode: {due_diligence_write_mode}")
    if locationos_mcp_write_completed and due_diligence_write_mode != "mcp_assisted":
        raise ValueError("--mcp-write-completed requires due_diligence_write_mode=mcp_assisted")
    recorder = _RunRecorder(site_title, site_id=site_id, launch_context=launch_context)
    initial_report_fields: dict[str, Any] = {}

    if not drive_folder_url.strip():
        started_at, started_monotonic = recorder.start()
        rhodes_owner_context = _resolve_rhodes_owner_for_pipeline(
            site_title,
            site_address,
            site_id,
        )
        rhodes_status = str(rhodes_owner_context.get("status") or "")
        if rhodes_status == "not_configured":
            recorder.record(
                "rhodes.owner_lookup",
                started_at,
                started_monotonic,
                "skipped",
                skipped_reason="LocationOS MCP auth not configured",
            )
        elif rhodes_status == "error":
            recorder.record(
                "rhodes.owner_lookup",
                started_at,
                started_monotonic,
                "skipped",
                skipped_reason=str(rhodes_owner_context.get("message") or "lookup failed"),
            )
        else:
            recorder.record("rhodes.owner_lookup", started_at, started_monotonic, "succeeded")

        resolved_site_id = str(rhodes_owner_context.get("site_id") or "").strip()
        if resolved_site_id and not recorder.site_id:
            recorder.site_id = resolved_site_id
        _mark_missing_p1_dri_if_needed(recorder, rhodes_owner_context)
        initial_report_fields.update(_owner_lookup_report_fields(rhodes_owner_context))
        p1_name = p1_name or _first_text(
            rhodes_owner_context.get("p1_assignee_name"),
            initial_report_fields.get("p1_assignee_name"),
            initial_report_fields.get("site.p1_assignee_name"),
        )
        p1_email = p1_email or _first_text(
            rhodes_owner_context.get("p1_assignee_email"),
            initial_report_fields.get("p1_assignee_email"),
            initial_report_fields.get("site.p1_assignee_email"),
        )
        site_created_at = site_created_at or _first_text(
            initial_report_fields.get("site_created_at")
        )
        rhodes_drive_folder_url = _drive_folder_from_rhodes(rhodes_owner_context)
        if rhodes_drive_folder_url and not drive_folder_url.strip():
            drive_folder_url = rhodes_drive_folder_url

    if not drive_folder_url.strip():
        if str((rhodes_owner_context or {}).get("status") or "") == "not_configured":
            message = (
                "No Drive folder URL was supplied and LocationOS MCP auth is not "
                "configured, so DDR could not resolve the linked site Drive folder. "
                "Refresh/configure LocationOS MCP auth or provide a site-linked "
                "Drive folder, then rerun."
            )
        else:
            message = (
                "No Drive folder URL was supplied and Rhodes did not return a linked "
                "Google Drive folder for this site. Link/provision the site folder in "
                "Rhodes and rerun."
            )
        started_at, started_monotonic = recorder.start()
        recorder.record(
            "readiness.check",
            started_at,
            started_monotonic,
            "blocked",
            error=_pipeline_error(
                recorder.run_id,
                "readiness.check",
                "missing_drive_folder_url",
                message,
                retryable=False,
                cause=str(
                    (rhodes_owner_context or {}).get("drive_folder_message") or ""
                ) or None,
            ),
        )
        return _finalize_pipeline_result(
            PipelineResult(site_title=site_title, status="error", error=message),
            recorder,
            gc=gc,
            drive_folder_url=drive_folder_url,
        )

    started_at, started_monotonic = recorder.start()
    try:
        readiness = check_site_readiness_direct(
            gc,
            drive_folder_url,
            match_terms,
            shared_cache,
            site_title=site_title,
            site_address=site_address,
        )
    except Exception as e:
        logger.error("Failed to check readiness for '%s': %s", site_title, e)
        recorder.record(
            "readiness.check",
            started_at,
            started_monotonic,
            "failed",
            error=_pipeline_error(
                recorder.run_id,
                "readiness.check",
                "readiness_check_failed",
                str(e),
            ),
        )
        return _finalize_pipeline_result(
            PipelineResult(site_title=site_title, status="error", error=str(e)),
            recorder,
            gc=gc,
            drive_folder_url=drive_folder_url,
        )

    cost_timeline_report_fields = readiness.get("cost_timeline_report_data_fields")
    if isinstance(cost_timeline_report_fields, dict):
        initial_report_fields.update(cost_timeline_report_fields)

    readiness_result = _resolve_readiness_result(
        site_title, readiness, force_regenerate=force_regenerate
    )
    sir_learning_review = readiness.get("sir_learning_review")
    if readiness_result is not None:
        if source_event:
            readiness_result.source_event = source_event
            readiness_result.republish_summary = {
                "trigger_source": source_event.get("source_type", ""),
                "closed_open_item_count": 0,
                "still_open_item_count": 0,
            }
        if readiness_result.status == "waiting_on_docs":
            recorder.record(
                "readiness.check",
                started_at,
                started_monotonic,
                "blocked",
                error=_pipeline_error(
                    recorder.run_id,
                    "readiness.check",
                    "missing_required_documents",
                    ", ".join(readiness_result.missing_docs),
                    retryable=False,
                ),
            )
        elif readiness_result.status == "error":
            recorder.record(
                "readiness.check",
                started_at,
                started_monotonic,
                "failed",
                error=_pipeline_error(
                    recorder.run_id,
                    "readiness.check",
                    "readiness_payload_error",
                    readiness_result.error or "Readiness check failed",
                ),
            )
        else:
            recorder.record("readiness.check", started_at, started_monotonic, "succeeded")
            recorder.record(
                "report.generate",
                *recorder.start(),
                "skipped",
                skipped_reason=readiness_result.status,
            )
        _record_sir_learning_review_step(recorder, sir_learning_review)
        return _finalize_pipeline_result(
            readiness_result,
            recorder,
            gc=gc,
            drive_folder_url=drive_folder_url,
        )
    missing_full_report_docs = _missing_required_docs(readiness)
    full_report_inputs_present = not missing_full_report_docs
    recorder.record("readiness.check", started_at, started_monotonic, "succeeded")
    _record_sir_learning_review_step(recorder, sir_learning_review)

    if source_event and force_regenerate and missing_full_report_docs:
        waiting_result = PipelineResult(
            site_title=site_title,
            status="waiting_on_docs",
            missing_docs=missing_full_report_docs,
        )
        _set_open_question_state(
            waiting_result,
            trace=None,
            run_id=recorder.run_id,
            source_event=source_event,
            open_questions_before=open_questions_before,
            validated=False,
        )
        recorder.record(
            "report.generate",
            *recorder.start(),
            "skipped",
            skipped_reason="source_triggered_republish_waiting_on_docs",
        )
        return _finalize_pipeline_result(
            waiting_result,
            recorder,
            gc=gc,
            drive_folder_url=drive_folder_url,
        )

    if rhodes_owner_context is None and not (p1_name and p1_email):
        started_at, started_monotonic = recorder.start()
        rhodes_owner_context = _resolve_rhodes_owner_for_pipeline(
            site_title,
            site_address,
            site_id,
        )
        rhodes_status = str(rhodes_owner_context.get("status") or "")
        if rhodes_status == "not_configured":
            recorder.record(
                "rhodes.owner_lookup",
                started_at,
                started_monotonic,
                "skipped",
                skipped_reason="LocationOS MCP auth not configured",
            )
        elif rhodes_status == "error":
            recorder.record(
                "rhodes.owner_lookup",
                started_at,
                started_monotonic,
                "skipped",
                skipped_reason=str(rhodes_owner_context.get("message") or "lookup failed"),
            )
        else:
            recorder.record("rhodes.owner_lookup", started_at, started_monotonic, "succeeded")

        resolved_site_id = str(rhodes_owner_context.get("site_id") or "").strip()
        if resolved_site_id and not recorder.site_id:
            recorder.site_id = resolved_site_id
        _mark_missing_p1_dri_if_needed(recorder, rhodes_owner_context)
        initial_report_fields.update(_owner_lookup_report_fields(rhodes_owner_context))
        p1_name = p1_name or _first_text(
            rhodes_owner_context.get("p1_assignee_name"),
            initial_report_fields.get("p1_assignee_name"),
            initial_report_fields.get("site.p1_assignee_name"),
        )
        p1_email = p1_email or _first_text(
            rhodes_owner_context.get("p1_assignee_email"),
            initial_report_fields.get("p1_assignee_email"),
            initial_report_fields.get("site.p1_assignee_email"),
        )
        site_created_at = site_created_at or _first_text(
            initial_report_fields.get("site_created_at")
        )

    started_at, started_monotonic = recorder.start()
    agent_result, generation_result = _run_pipeline_agent(
        site_title,
        system_prompt,
        settings,
        drive_folder_url=drive_folder_url,
        site_address=site_address,
        site_id=recorder.site_id or site_id,
        initial_report_fields=initial_report_fields,
        rhodes_owner_context=rhodes_owner_context,
    )
    if generation_result is not None:
        gen_status = "failed"
        gen_error = _pipeline_error(
            recorder.run_id,
            "report.generate",
            "report_generation_failed",
            generation_result.error or generation_result.status,
        )
        recorder.record(
            "report.generate",
            started_at,
            started_monotonic,
            gen_status,
            error=gen_error,
        )
        generation_result.trace_url = None
        _record_source_alert_step(
            recorder,
            settings,
            site_title,
            generation_result.trace,
            drive_folder_url=drive_folder_url,
            trace_url=generation_result.trace_url or "",
            site_id=recorder.site_id or site_id or "",
            owner_user_id=_owner_user_id_from_context(rhodes_owner_context),
            owner_email=p1_email or "",
        )
        # First-round publishing can proceed before every full-report
        # vendor/cost-timeline input is present. Escalate only when the full input
        # set was actually present and generation still failed.
        if (
            _vendor_gate_enabled()
            and full_report_inputs_present
            and generation_result.status == "generation_failed"
        ):
            _record_vendor_gate_alert_step(
                recorder,
                settings,
                site_title,
                drive_folder_url=drive_folder_url,
                failure_reason=generation_result.error or "",
                trace_url=generation_result.trace_url or "",
                site_id=recorder.site_id or site_id or "",
                owner_user_id=_owner_user_id_from_context(rhodes_owner_context),
                owner_email=p1_email or "",
                mutation_status=generation_result.status,
            )
        _set_open_question_state(
            generation_result,
            trace=generation_result.trace,
            run_id=recorder.run_id,
            source_event=source_event,
            open_questions_before=open_questions_before,
            validated=False,
        )
        return _finalize_pipeline_result(
            generation_result,
            recorder,
            gc=gc,
            drive_folder_url=drive_folder_url,
        )
    assert agent_result is not None
    recorder.record("report.generate", started_at, started_monotonic, "succeeded")

    trace = agent_result.get("trace")
    final_report_data = getattr(trace, "final_report_data", None)
    if isinstance(final_report_data, dict):
        p1_name = p1_name or _first_text(
            final_report_data.get("p1_assignee_name"),
            final_report_data.get("site.p1_assignee_name"),
            final_report_data.get("meta.prepared_by"),
        )
        p1_email = p1_email or _first_text(
            final_report_data.get("p1_assignee_email"),
            final_report_data.get("site.p1_assignee_email"),
        )
        site_created_at = site_created_at or _first_text(
            final_report_data.get("site_created_at")
        )
    trace_url = ""
    _record_source_alert_step(
        recorder,
        settings,
        site_title,
        trace,
        drive_folder_url=drive_folder_url,
        trace_url=trace_url or "",
        site_id=recorder.site_id or site_id or "",
        owner_user_id=_owner_user_id_from_context(rhodes_owner_context),
        owner_email=p1_email or "",
    )

    pre_render_due_diligence_update: dict[str, Any] | None = None
    pre_render_locationos_mcp_resume: dict[str, Any] | None = None
    if agent_result.get("prepared"):
        prepared_result = PipelineResult(
            site_title=site_title,
            status="report_data_prepared",
            missing_docs=missing_full_report_docs,
            trace_url=trace_url,
            trace=trace,
            source_packet=(
                agent_result.get("source_packet")
                if isinstance(agent_result.get("source_packet"), dict)
                else None
            ),
        )
        _set_open_question_state(
            prepared_result,
            trace=trace,
            run_id=recorder.run_id,
            source_event=source_event,
            open_questions_before=open_questions_before,
            validated=False,
        )
        prepare_metadata = agent_result.get("report_metadata")
        recorder.record(
            "due_diligence.prepare",
            *recorder.start(),
            "succeeded",
            artifacts=[
                ArtifactRef(
                    kind="due_diligence_data",
                    name="Normalized DD data",
                    metadata=prepare_metadata if isinstance(prepare_metadata, dict) else {},
                )
            ],
        )
        if not document_first_on_sor_blocker:
            _record_rhodes_due_diligence_update_step(
                recorder,
                prepared_result,
                site_id=recorder.site_id or site_id or "",
                report_data=_report_data_from_trace(trace),
                due_diligence_write_mode=due_diligence_write_mode,
                locationos_mcp_write_completed=locationos_mcp_write_completed,
            )
            pre_render_due_diligence_update = prepared_result.rhodes_due_diligence_update
            if _due_diligence_update_failed(prepared_result):
                if _locationos_mcp_write_request_from_result(prepared_result) is not None:
                    prepared_result.status = LOCATIONOS_MCP_WRITE_REQUIRED_STATUS
                    prepared_result.locationos_mcp_resume = (
                        _build_locationos_mcp_resume_payload(
                            recorder=recorder,
                            result=prepared_result,
                            agent_result=agent_result,
                            site_id=recorder.site_id or site_id or "",
                            drive_folder_url=drive_folder_url,
                            owner_user_id=_owner_user_id_from_context(rhodes_owner_context),
                            owner_email=p1_email or "",
                            p1_name=p1_name,
                        )
                    )
                else:
                    _record_rhodes_report_event_step(
                        recorder,
                        settings,
                        prepared_result,
                        site_id=recorder.site_id or site_id or "",
                        owner_user_id=_owner_user_id_from_context(rhodes_owner_context),
                        owner_email=p1_email or "",
                    )
                return _finalize_pipeline_result(
                    prepared_result,
                    recorder,
                    gc=gc,
                    drive_folder_url=drive_folder_url,
                )
            if pre_render_locationos_mcp_resume is None:
                pre_render_locationos_mcp_resume = prepared_result.locationos_mcp_resume

        started_at, started_monotonic = recorder.start()
        render_result, render_error = _render_prepared_dd_report(agent_result)
        if render_result is None:
            recorder.record(
                "report.render",
                started_at,
                started_monotonic,
                "failed",
                error=_pipeline_error(
                    recorder.run_id,
                    "report.render",
                    "report_render_failed",
                    render_error or "DD report render failed",
                ),
            )
            prepared_result.status = "generation_failed"
            prepared_result.error = render_error or "DD report render failed"
            _record_rhodes_report_event_step(
                recorder,
                settings,
                prepared_result,
                site_id=recorder.site_id or site_id or "",
                owner_user_id=_owner_user_id_from_context(rhodes_owner_context),
                owner_email=p1_email or "",
            )
            return _finalize_pipeline_result(
                prepared_result,
                recorder,
                gc=gc,
                drive_folder_url=drive_folder_url,
            )
        document = render_result["document"]
        doc_id = str(document.get("id") or "")
        doc_url = str(document.get("url") or "")
        document_role = str(document.get("role") or "active")
        guard = render_result.get("republish_guard")
        if isinstance(guard, dict):
            agent_result["republish_guard"] = guard
        normalized = render_result.get("normalized_report_data")
        if isinstance(trace, ReportTrace) and isinstance(normalized, dict):
            trace.final_report_data.update(normalized)
        recorder.record(
            "report.render",
            started_at,
            started_monotonic,
            "succeeded",
            artifacts=[
                ArtifactRef(
                    kind="google_doc",
                    name="DD report",
                    uri=doc_url,
                    drive_file_id=doc_id,
                )
            ],
        )
    else:
        doc_id = agent_result["doc_id"]
        doc_url = agent_result.get("doc_url", "")
        document_role = str(agent_result.get("document_role") or "active")

    if document_role == "candidate":
        candidate_result = PipelineResult(
            site_title=site_title,
            status="republish_candidate_created",
            missing_docs=missing_full_report_docs,
            doc_id=doc_id,
            doc_url=doc_url,
            trace_url=trace_url,
            trace=agent_result.get("trace"),
            source_packet=(
                agent_result.get("source_packet")
                if isinstance(agent_result.get("source_packet"), dict)
                else None
            ),
        )
        _set_open_question_state(
            candidate_result,
            trace=agent_result.get("trace"),
            run_id=recorder.run_id,
            source_event=source_event,
            open_questions_before=open_questions_before,
            validated=False,
        )
        guard = agent_result.get("republish_guard")
        if isinstance(guard, dict):
            candidate_result.republish_summary = {
                **(candidate_result.republish_summary or {}),
                "overwrite_guard": guard,
                "outstanding_vendor_docs": missing_full_report_docs,
            }
        if pre_render_due_diligence_update is None:
            _record_rhodes_due_diligence_update_step(
                recorder,
                candidate_result,
                site_id=recorder.site_id or site_id or "",
                report_data=_report_data_from_trace(trace),
                due_diligence_write_mode=due_diligence_write_mode,
                locationos_mcp_write_completed=locationos_mcp_write_completed,
            )
        else:
            candidate_result.rhodes_due_diligence_update = pre_render_due_diligence_update
        candidate_result.locationos_mcp_resume = pre_render_locationos_mcp_resume
        if _due_diligence_update_failed(candidate_result):
            candidate_result.warnings.append(DD_REPORT_CREATED_SOR_PENDING_WARNING)
        _record_republish_candidate_event_step(
            recorder,
            settings,
            candidate_result,
            site_id=recorder.site_id or site_id or "",
            owner_user_id=_owner_user_id_from_context(rhodes_owner_context),
            owner_email=p1_email or "",
        )
        return _finalize_pipeline_result(
            candidate_result,
            recorder,
            gc=gc,
            drive_folder_url=drive_folder_url,
        )

    started_at, started_monotonic = recorder.start()
    completeness, completeness_result = _check_generated_report(site_title, doc_id, doc_url)
    if completeness_result is not None:
        validation_status = "failed"
        validation_code = "report_validation_failed"
        if completeness_result.status == "error":
            validation_code = "report_validation_error"
        recorder.record(
            "report.validate",
            started_at,
            started_monotonic,
            validation_status,
            error=_pipeline_error(
                recorder.run_id,
                "report.validate",
                validation_code,
                completeness_result.error
                or f"{len(completeness_result.unresolved_tokens)} unresolved token(s)",
            ),
            artifacts=[ArtifactRef(kind="google_doc", name="DD report", uri=doc_url, drive_file_id=doc_id)],
        )
        completeness_result.trace_url = trace_url
        completeness_result.trace = agent_result.get("trace")
        # Same escalation as the agent-failure branch above: when the full
        # vendor/cost-timeline input set was present but the resulting report is
        # incomplete, humans need to look at the inputs.
        if (
            _vendor_gate_enabled()
            and full_report_inputs_present
            and completeness_result.status == "report_incomplete"
        ):
            failure_reason = completeness_result.error or (
                "Report generated but "
                f"{len(completeness_result.unresolved_tokens)} tokens "
                "unresolved"
            )
            _record_vendor_gate_alert_step(
                recorder,
                settings,
                site_title,
                drive_folder_url=drive_folder_url,
                failure_reason=failure_reason,
                trace_url=trace_url or "",
                site_id=recorder.site_id or site_id or "",
                owner_user_id=_owner_user_id_from_context(rhodes_owner_context),
                owner_email=p1_email or "",
                mutation_status=completeness_result.status,
            )
        _set_open_question_state(
            completeness_result,
            trace=completeness_result.trace,
            run_id=recorder.run_id,
            source_event=source_event,
            open_questions_before=open_questions_before,
            validated=False,
        )
        completeness_result.rhodes_due_diligence_update = pre_render_due_diligence_update
        completeness_result.source_packet = (
            agent_result.get("source_packet")
            if isinstance(agent_result.get("source_packet"), dict)
            else None
        )
        completeness_result.locationos_mcp_resume = pre_render_locationos_mcp_resume
        if _due_diligence_update_failed(completeness_result):
            completeness_result.warnings.append(DD_REPORT_CREATED_SOR_PENDING_WARNING)
        return _finalize_pipeline_result(
            completeness_result,
            recorder,
            gc=gc,
            drive_folder_url=drive_folder_url,
        )
    assert completeness is not None
    recorder.record(
        "report.validate",
        started_at,
        started_monotonic,
        "succeeded",
        artifacts=[ArtifactRef(kind="google_doc", name="DD report", uri=doc_url, drive_file_id=doc_id)],
    )

    final_result = PipelineResult(
        site_title=site_title,
        status="report_created",
        missing_docs=missing_full_report_docs,
        doc_id=doc_id,
        doc_url=doc_url,
        pending_count=completeness.get("pending_section_count", 0),
        trace_url=trace_url,
        trace=agent_result.get("trace"),
        source_packet=(
            agent_result.get("source_packet")
            if isinstance(agent_result.get("source_packet"), dict)
            else None
        ),
    )
    _set_open_question_state(
        final_result,
        trace=agent_result.get("trace"),
        run_id=recorder.run_id,
        source_event=source_event,
        open_questions_before=open_questions_before,
        validated=True,
    )
    if pre_render_due_diligence_update is None:
        _record_rhodes_due_diligence_update_step(
            recorder,
            final_result,
            site_id=recorder.site_id or site_id or "",
            report_data=_report_data_from_trace(trace),
            due_diligence_write_mode=due_diligence_write_mode,
            locationos_mcp_write_completed=locationos_mcp_write_completed,
        )
    else:
        final_result.rhodes_due_diligence_update = pre_render_due_diligence_update
    final_result.locationos_mcp_resume = pre_render_locationos_mcp_resume
    due_diligence_update_failed = _due_diligence_update_failed(final_result)

    _record_rhodes_report_event_step(
        recorder,
        settings,
        final_result,
        site_id=recorder.site_id or site_id or "",
        owner_user_id=_owner_user_id_from_context(rhodes_owner_context),
        owner_email=p1_email or "",
    )
    if due_diligence_update_failed:
        final_result.warnings.append(DD_REPORT_CREATED_SOR_PENDING_WARNING)
        return _finalize_pipeline_result(
            final_result,
            recorder,
            gc=gc,
            drive_folder_url=drive_folder_url,
        )
    _record_email_step(
        recorder,
        settings,
        site_title,
        doc_url,
        p1_email,
        is_update=source_event is not None,
        open_question_count=len(final_result.open_questions),
        full_report_inputs_present=full_report_inputs_present,
    )

    return _finalize_pipeline_result(
        final_result,
        recorder,
        gc=gc,
        drive_folder_url=drive_folder_url,
    )


# Google Chat notification per pipeline result
# ─────────────────────────────────────────────────────────────────────────────


def post_pipeline_result(
    webhook_url: str,
    result: PipelineResult,
    drive_folder_url: str = "",
) -> None:
    """Post a Google Chat message summarizing a single PipelineResult.

    webhook_url can be a single URL or comma-separated URLs for multiple spaces.
    """
    if not webhook_url:
        return

    urls = [u.strip() for u in webhook_url.split(",") if u.strip()]
    if not urls:
        return

    if result.status == "waiting_on_docs":
        sir = "SIR" not in result.missing_docs
        insp = not {
            "Building Inspection",
            "Vendor Building Inspection",
        }.intersection(result.missing_docs)
        lines = [
            f"DD Check -- {result.site_title}",
            "Status: WAITING ON DOCUMENTS",
            f"  {'[OK]' if sir else '[  ]'} SIR {'found' if sir else 'not found'}",
            f"  {'[OK]' if insp else '[  ]'} Building Inspection {'found' if insp else 'not found'}",
        ]
        if drive_folder_url:
            lines.append(f"Drive: {drive_folder_url}")
        lines.extend(_pipeline_observability_lines(result))
        msg = "\n".join(lines)

    elif result.status == "report_exists":
        msg = "\n".join([
            f"DD Check -- {result.site_title}",
            "Report already exists, skipping.",
            *_pipeline_observability_lines(result),
        ])

    elif result.status == "report_created":
        if result.source_event:
            headline = "DDR Updated"
        elif result.open_questions:
            headline = "DDR Published (Partial)"
        else:
            headline = "DD Report CREATED"
        msg = (
            f"{headline} -- {result.site_title}\n"
            f"Report: {result.doc_url or '(no URL)'}"
        )
        republish_lines = _republish_observability_lines(result)
        if republish_lines:
            msg += "\n" + "\n".join(republish_lines)
        if result.trace_url:
            msg += f"\nTrace: {result.trace_url}"
        if result.pending_count:
            msg += f"\nPending fields: {result.pending_count}"
        msg += "\n" + "\n".join(_pipeline_observability_lines(result))

    elif result.status == "republish_candidate_created":
        msg = (
            f"DDR candidate created -- {result.site_title}\n"
            "Active DDR was not overwritten because manual edits may be present.\n"
            f"Candidate: {result.doc_url or '(no URL)'}"
        )
        republish_lines = _republish_observability_lines(result)
        if republish_lines:
            msg += "\n" + "\n".join(republish_lines)
        msg += "\n" + "\n".join(_pipeline_observability_lines(result))

    elif result.status == "report_incomplete":
        count = len(result.unresolved_tokens)
        tokens = ", ".join(result.unresolved_tokens[:10])
        if result.error:
            msg = (
                f"DD Report for {result.site_title} is incomplete.\n"
                f"Reason: {result.error}\n"
                f"Report: {result.doc_url or '(no URL)'}"
            )
        else:
            msg = (
                f"DD Report for {result.site_title} has {count} unfilled placeholder(s).\n"
                f"Tokens: {tokens}\n"
                f"Report: {result.doc_url or '(no URL)'}"
            )
        msg += "\n" + "\n".join(_pipeline_observability_lines(result))

    elif result.status == "generation_failed":
        msg = (
            f"DD Report generation FAILED for {result.site_title}\n"
            f"Error: {result.error or 'unknown'}"
        )
        msg += "\n" + "\n".join(_pipeline_observability_lines(result))

    elif result.status == "error":
        msg = (
            f"DD Check ERROR for {result.site_title}\n"
            f"Error: {result.error or 'unknown'}"
        )
        msg += "\n" + "\n".join(_pipeline_observability_lines(result))

    else:
        msg = f"DD Check -- {result.site_title}\nStatus: {result.status}"
        msg += "\n" + "\n".join(_pipeline_observability_lines(result))

    for url in urls:
        try:
            post_google_chat_message(url, msg)
        except Exception as e:
            logger.error("Failed to post Chat message for '%s' to %s: %s", result.site_title, url[:60], e)


def post_completed_report_bundle_summary(
    webhook_url: str,
    results: list[PipelineResult],
) -> None:
    """Post one end-of-run summary for sites that already have reports."""
    if not webhook_url:
        return

    report_exists = [result for result in results if result.status == "report_exists"]
    if not report_exists:
        return

    urls = [u.strip() for u in webhook_url.split(",") if u.strip()]
    if not urls:
        return

    lines = [
        "Daily DDR scan -- completed report bundles already present",
        f"Sites skipped because a completed DD Report already exists: {len(report_exists)}",
    ]
    lines.extend(f"- {result.site_title}" for result in report_exists)
    msg = "\n".join(lines)

    for url in urls:
        try:
            post_google_chat_message(url, msg)
        except Exception as e:
            logger.error("Failed to post completed report bundle summary to %s: %s", url[:60], e)


def _pipeline_observability_lines(result: PipelineResult) -> list[str]:
    lines: list[str] = []
    if result.run_id:
        lines.append(f"Run ID: {result.run_id}")
    if result.failed_step:
        lines.append(f"Failed step: {result.failed_step}")
    if result.quality_score is not None and result.quality_band:
        lines.append(f"Run quality: {result.quality_score} ({result.quality_band})")
    if result.manifest_path:
        lines.append(f"Manifest: {result.manifest_path}")
    if result.sir_review_status:
        lines.append(f"SIR review: {result.sir_review_status}")
    report_event_warning = _rhodes_report_event_warning(result)
    if report_event_warning:
        lines.append(f"Warning: {report_event_warning}")
    action = next_operator_action(result.steps)
    if action:
        lines.append(f"Next action: {action}")
    return lines


def _rhodes_report_event_warning(result: PipelineResult) -> str:
    event_status = result.rhodes_report_event
    if not isinstance(event_status, dict):
        return ""
    if event_status.get("severity") == "warning":
        return str(event_status.get("warning") or "").strip()
    status = str(event_status.get("status") or "").strip().lower()
    if status in {"failed", "error"}:
        return (
            "Rhodes event note was not verified; manually confirm the DD Report "
            "Google Doc was produced and LocationOS/Rhodes dueDiligence fields "
            "were completed."
        )
    return ""


def _republish_observability_lines(result: PipelineResult) -> list[str]:
    lines: list[str] = []
    source_event = result.source_event or {}
    trigger_source = str(source_event.get("source_type") or "").strip()
    if trigger_source:
        lines.append(f"Trigger source: {trigger_source}")
    if result.missing_docs:
        lines.append(f"Outstanding vendor docs: {', '.join(result.missing_docs)}")
    elif trigger_source:
        lines.append("Outstanding vendor docs: None")
    if result.closed_open_questions:
        lines.append(f"Closed open items: {len(result.closed_open_questions)}")
        for item in result.closed_open_questions[:5]:
            text = str(item.get("display_text") or "").strip()
            if text:
                lines.append(f"- {text}")
    if result.open_questions:
        lines.append(f"Still-open items: {len(result.open_questions)}")
        for item in result.open_questions[:5]:
            text = str(item.get("display_text") or "").strip()
            if text:
                lines.append(f"- {text}")
    return lines
