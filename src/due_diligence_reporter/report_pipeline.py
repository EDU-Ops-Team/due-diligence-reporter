"""Shared report pipeline — readiness check, Claude agent loop, and notifications.

Extracted from ``scripts/daily_dd_check.py`` so that both the daily sweep
and the 15-minute inbox scanner can trigger report generation for a single site.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anthropic

from .classifier import AI_GENERATED_DOC_TYPES, classify_document_type, match_file_to_site_llm
from .config import Settings, get_settings
from .dashboard_publisher import publish_to_dashboard
from .google_client import GoogleClient
from .utils import (
    escape_html_text,
    extract_folder_id_from_url,
    post_google_chat_message,
    sanitize_http_url,
    score_site_match_strength,
    send_email,
)

logger = logging.getLogger("report_pipeline")

# ─────────────────────────────────────────────────────────────────────────────
# Tool definitions for the Claude API call (mirrors the MCP tools)
# ─────────────────────────────────────────────────────────────────────────────

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_site_record",
        "description": "Fetch a Wrike Site Record by name or ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name_or_id": {"type": "string", "description": "Site name or Wrike ID"},
            },
            "required": ["site_name_or_id"],
        },
    },
    {
        "name": "list_drive_documents",
        "description": "List matched shared DD source reports plus site-folder artifacts found in the site folder or its M1 subfolder. Results may include Block Plan PDFs and derived reports such as Capacity Brainlift, RayCon Scenario, Opening Plan, and DD reports. Each file includes a doc_type field. Always pass site_name to match shared-folder docs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drive_folder_url": {"type": "string", "description": "Google Drive folder URL"},
                "site_name": {"type": "string", "description": "Site name from Wrike (used to match docs in shared folders)"},
            },
            "required": ["drive_folder_url"],
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
        "name": "apply_e_occupancy_skill",
        "description": "Apply E-Occupancy scoring to a building. Pass site_name and drive_folder_url to auto-publish the assessment as a Google Doc in the M1 subfolder — the returned doc_url can be used as sources.e_occupancy_link.",
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
                "site_name": {"type": "string", "default": "", "description": "Site name — pass to auto-publish assessment to Drive"},
                "drive_folder_url": {"type": "string", "default": "", "description": "Site Drive folder URL — pass to auto-publish"},
            },
            "required": ["state"],
        },
    },
    {
        "name": "apply_capacity_brainlift_skill",
        "description": "Extract Fastest Open and Max Capacity scenario capacities from a Block Plan. Pass site_name, site_address, block_plan_content, and total_building_sf. Pass drive_folder_url to auto-publish the Capacity Brainlift report to the site's M1 subfolder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name": {"type": "string"},
                "site_address": {"type": "string"},
                "block_plan_content": {"type": "string"},
                "total_building_sf": {"type": "integer"},
                "drive_folder_url": {"type": "string", "default": "", "description": "Site Drive folder URL â€” pass to auto-publish"},
                "block_plan_url": {"type": "string", "default": "", "description": "Drive URL for the source Block Plan PDF"},
            },
            "required": ["site_name", "site_address", "block_plan_content", "total_building_sf"],
        },
    },
    {
        "name": "get_cost_estimate",
        "description": "Estimate renovation costs using the Building Optimizer API. For Block Plan flows, pass the full SIR, Building Inspection, and Block Plan text when available so RayCon can use the complete source context. Returns report_data_fields with DD report tokens — copy these directly into report_data as flat keys.",
        "input_schema": {
            "type": "object",
            "properties": {
                "total_building_sf": {"type": "integer"},
                "region": {"type": "string", "default": "default"},
                "rooms": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "sqft": {"type": "integer"},
                        },
                    },
                },
                "classroom_count": {"type": "integer", "default": 0},
                "site_name": {"type": "string", "default": ""},
                "address": {"type": "string", "default": ""},
                "inspection_content": {"type": "string", "default": ""},
                "sir_content": {"type": "string", "default": ""},
                "block_plan_content": {"type": "string", "default": ""},
                "inspection_summary": {"type": "string", "default": ""},
                "sir_summary": {"type": "string", "default": ""},
            },
            "required": ["total_building_sf"],
        },
    },
    {
        "name": "create_dd_report",
        "description": "Create a completed DD report Google Doc. The report_data dict must use exact V3 template token keys (e.g. 'exec.c_zoning', 'exec.fastest_open_capex', 'sources.sir_link'). Copy report_data_fields from skill tools directly into report_data. Pass token_evidence for source traceability.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name": {"type": "string"},
                "drive_folder_url": {"type": "string"},
                "report_data": {"type": "object"},
                "token_evidence": {"type": "object", "description": "Optional dict mapping token names to raw source excerpts for the trace report"},
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
        "name": "get_site_comments",
        "description": "Retrieve Wrike record comments for a site, grouped by suggested report section (q1-q4, appendix, general). Useful for incorporating pre-app meeting notes, vendor updates, and cost overrides.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name_or_id": {"type": "string", "description": "Site name, Wrike record ID, or Wrike permalink URL"},
            },
            "required": ["site_name_or_id"],
        },
    },
    {
        "name": "save_skill_report",
        "description": "Save a skill assessment (E-Occupancy or School Approval) as a standalone Google Doc in the site's M1 subfolder. Pass the FULL result dict from apply_e_occupancy_skill or apply_school_approval_skill as skill_data — the tool formats it into a readable document. Returns doc_url for inclusion in sources.* tokens.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Skill name, e.g. 'E-Occupancy', 'School Approval', 'Capacity Brainlift', or 'RayCon Scenario'"},
                "site_name": {"type": "string", "description": "Site name for the document title"},
                "drive_folder_url": {"type": "string", "description": "Google Drive folder URL for the site"},
                "skill_data": {"type": "object", "description": "Full result dict from the skill tool (pass the entire response)"},
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


async def route_tool_call(tool_name: str, tool_input: dict[str, Any]) -> Any:
    """Route a Claude API tool call to the corresponding Python function."""
    from . import server as srv

    tool_map = {
        "get_site_record": srv.get_site_record,
        "list_drive_documents": srv.list_drive_documents,
        "read_drive_document": srv.read_drive_document,
        "apply_e_occupancy_skill": srv.apply_e_occupancy_skill,
        "apply_school_approval_skill": srv.apply_school_approval_skill,
        "apply_capacity_brainlift_skill": srv.apply_capacity_brainlift_skill,
        "get_cost_estimate": srv.get_cost_estimate,
        "create_dd_report": srv.create_dd_report,
        "check_report_completeness": srv.check_report_completeness,
        "get_site_comments": srv.get_site_comments,
        "save_skill_report": srv.save_skill_report,
        "send_dd_report_email": srv.send_dd_report_email,
    }

    fn = tool_map.get(tool_name)
    if fn is None:
        return {"status": "error", "message": f"Unknown tool: {tool_name}"}

    return await fn(**tool_input)


def route_tool_call_sync(tool_name: str, tool_input: dict[str, Any]) -> Any:
    """Synchronous wrapper for route_tool_call."""
    import asyncio

    return asyncio.run(route_tool_call(tool_name, tool_input))


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
    min_match_score = 20

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


def check_site_readiness_direct(
    gc: GoogleClient,
    drive_folder_url: str,
    match_terms: list[str],
    shared_cache: dict[str, list[dict[str, Any]]],
    *,
    site_title: str | None = None,
    site_address: str | None = None,
) -> dict[str, Any]:
    """Check site document readiness directly without going through MCP."""
    folder_id = extract_folder_id_from_url(drive_folder_url)
    if not folder_id:
        return {
            "sir_found": False, "isp_found": False, "inspection_found": False,
            "report_exists": False,
            "e_occupancy_report_found": False, "school_approval_report_found": False,
            "error": "bad_url",
        }

    # 1. Match docs from pre-fetched shared folder cache (substring + LLM fallback)
    shared_docs = match_site_in_shared_cache(
        match_terms, shared_cache,
        site_title=site_title, site_address=site_address,
    )

    # 2. Recursively list + classify files in the site's own folder (all subfolders)
    all_site_files = [
        {**f, "doc_type": classify_document_type(f.get("name", ""))}
        for f in gc.list_files_recursive(folder_id, max_depth=2)
    ]
    ai_generated_site_files = [
        f for f in all_site_files if f.get("doc_type") in AI_GENERATED_DOC_TYPES
    ]

    # 3. Merge — source docs come only from shared folders; site folder only
    # contributes AI-generated report artifacts.
    files_by_type: dict[str, dict[str, Any] | None] = {
        "sir": shared_docs.get("sir"),
        "isp": shared_docs.get("isp"),
        "building_inspection": shared_docs.get("building_inspection"),
        "dd_report": None,
        "e_occupancy_report": None,
        "school_approval_report": None,
    }
    for f in ai_generated_site_files:
        dt = f.get("doc_type", "unknown")
        if dt in files_by_type and files_by_type[dt] is None:
            files_by_type[dt] = f

    return {
        "sir_found": files_by_type["sir"] is not None,
        "isp_found": files_by_type["isp"] is not None,
        "inspection_found": files_by_type["building_inspection"] is not None,
        "report_exists": files_by_type["dd_report"] is not None,
        "e_occupancy_report_found": files_by_type["e_occupancy_report"] is not None,
        "school_approval_report_found": files_by_type["school_approval_report"] is not None,
        "all_files": ai_generated_site_files,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Claude agentic loop — generates one DD report
# ─────────────────────────────────────────────────────────────────────────────


def run_dd_report_agent(
    site_title: str,
    system_prompt: str,
    model_id: str,
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
        prompt_version=3,
    )
    run_start = time.monotonic()

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": f"Generate a DD Report for: {site_title}"},
    ]

    doc_id: str | None = None
    doc_url: str | None = None
    cached_report_fields: dict[str, Any] = {}
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
            tool_input = dict(tool_use.input)
            if tool_use.name == "create_dd_report":
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

            # Capture doc_id from create_dd_report
            if tool_use.name == "create_dd_report" and isinstance(result, dict):
                doc_data = result.get("document", {})
                if doc_data.get("id"):
                    doc_id = doc_data["id"]
                    doc_url = doc_data.get("url")
                    logger.info("Created DD report: %s", doc_url)
                    trace.doc_id = doc_id
                    trace.tokens_filled = result.get("replacements_applied", 0)
                    trace.tokens_unfilled = result.get("unfilled_template_tokens", 0)
                    # Stash the final, fully-merged report_data for the
                    # dashboard publisher.
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

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": json.dumps(result),
            })

            if doc_id:
                logger.info("Report created during tool batch, skipping remaining tool calls")
                break

        messages.append({"role": "user", "content": tool_results})

        # Stop as soon as we have a report — completeness check happens separately
        if doc_id:
            logger.info("Report created, stopping agent loop after %d iterations", iteration + 1)
            break

    # Finalize trace
    trace.ended_at = datetime.now(UTC).isoformat()
    trace.total_duration_ms = int((time.monotonic() - run_start) * 1000)
    trace.final_status = "success" if doc_id else "no_report"

    if doc_id:
        return {"success": True, "doc_id": doc_id, "doc_url": doc_url, "trace": trace}
    return {"success": False, "error": "Agent completed without creating a report", "trace": trace}


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline result dataclass
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PipelineResult:
    """Structured result from a single-site pipeline run."""

    site_title: str
    status: str  # waiting_on_docs | report_exists | report_created | report_incomplete | generation_failed | error | yielded_to_pipeline
    missing_docs: list[str] = field(default_factory=list)
    doc_id: str | None = None
    doc_url: str | None = None
    unresolved_tokens: list[str] = field(default_factory=list)
    pending_count: int = 0
    error: str | None = None
    trace_url: str | None = None
    trace: ReportTrace | None = None


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
    # Full report_data passed to create_dd_report (flat token dict). Kept on
    # the trace so downstream consumers (dashboard publisher) can re-use it
    # without re-parsing the doc. Not serialized into the trace JSON to keep
    # the provenance file small.
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
    """Merge cached tool report_data_fields into create_dd_report input."""
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

    merged["report_data"] = report_data
    return merged


def _missing_required_docs(readiness: dict[str, Any]) -> list[str]:
    """Return human-readable names for missing required DD docs."""
    missing: list[str] = []
    if not readiness.get("sir_found", False):
        missing.append("SIR")
    if not readiness.get("inspection_found", False):
        missing.append("Building Inspection")
    return missing


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
        problem = (
            event.error
            or str(output.get("error", "")).strip()
            or str(output.get("message", "")).strip()
        )
        has_issue = (
            bool(problem)
            or output.get("status") == "error"
            or output.get("content_length") == 0
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
            "problem": problem or "Document could not be read cleanly",
        })

    return issues


def _notify_source_read_issues(
    webhook_url: str,
    site_title: str,
    trace: ReportTrace | None,
    *,
    drive_folder_url: str = "",
    trace_url: str = "",
) -> None:
    """Alert the team when SIR or Building Inspection reads fail."""
    issues = _extract_source_read_issues(trace)
    if not webhook_url or not issues:
        return

    lines = [
        f"DD Source Review Needed -- {site_title}",
        "Issue reading required source document(s). Please review.",
    ]
    for issue in issues:
        lines.append(f"- {issue['doc_type']}: {issue['file_name']}")
        lines.append(f"  Problem: {issue['problem']}")
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
                "Failed to post source review alert for '%s' to %s: %s",
                site_title,
                url[:60],
                e,
            )


def _resolve_readiness_result(
    site_title: str,
    readiness: dict[str, Any],
) -> PipelineResult | None:
    """Convert readiness payload into an early pipeline result when applicable."""
    readiness_error = _get_payload_error(readiness)
    if readiness_error:
        logger.error("Readiness check failed for '%s': %s", site_title, readiness_error)
        return PipelineResult(site_title=site_title, status="error", error=readiness_error)

    missing_docs = _missing_required_docs(readiness)
    if missing_docs:
        return PipelineResult(
            site_title=site_title,
            status="waiting_on_docs",
            missing_docs=missing_docs,
        )

    if readiness.get("report_exists", False):
        logger.info("'%s' - report already exists, skipping", site_title)
        return PipelineResult(site_title=site_title, status="report_exists")

    return None


def _run_pipeline_agent(
    site_title: str,
    system_prompt: str,
    settings: Settings,
) -> tuple[dict[str, Any] | None, PipelineResult | None]:
    """Run report generation and map failures into a PipelineResult."""
    # Phase B-PR3 cutover: when DD_REPORT_OWNER=pipeline, the
    # alpha-dd-pipeline WU-13 is the sole DD-report producer. Short-circuit
    # before any agent work so reruns and backfills both honor the flag
    # uniformly. We yield a distinct status ("yielded_to_pipeline") so the
    # caller can skip the dashboard publish + email/trace side effects that
    # the Anthropic agent would normally feed. Default "reporter" preserves
    # legacy behavior until soak passes; "pipeline" is the only value that
    # disables the agent run. Mirrors DASHBOARD_PUBLISH_OWNER (Phase A5).
    owner = os.environ.get("DD_REPORT_OWNER", "reporter").strip().lower()
    if owner == "pipeline":
        logger.info(
            "DD_REPORT_OWNER=pipeline; reporter is yielding DD-report "
            "generation to alpha-dd-pipeline WU-13 for %s",
            site_title,
        )
        return None, PipelineResult(
            site_title=site_title,
            status="yielded_to_pipeline",
        )

    logger.info("'%s' - all docs present, generating report...", site_title)
    try:
        agent_result = run_dd_report_agent(
            site_title,
            system_prompt,
            settings.anthropic_report_model,
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


def _save_pipeline_trace(
    gc: GoogleClient,
    drive_folder_url: str,
    site_title: str,
    trace: ReportTrace | None,
) -> str | None:
    """Persist a report trace JSON beside the generated report."""
    if not trace:
        return None

    folder_id = extract_folder_id_from_url(drive_folder_url)
    if not folder_id:
        return None

    trace_date = datetime.now(UTC).strftime("%Y-%m-%d")
    trace_name = f"{site_title} DD Report Trace - {trace_date}.json"
    try:
        trace_json = json.dumps(trace.to_dict(), indent=2)
        trace_file = gc.upload_file_to_folder(
            folder_id=folder_id,
            file_name=trace_name,
            file_bytes=trace_json.encode("utf-8"),
            mime_type="application/json",
        )
        logger.info("Saved report trace: %s", trace_name)
        return trace_file.get("webViewLink")
    except Exception as e:
        logger.warning("Failed to save report trace: %s", e)
        return None


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
    )


def _email_pipeline_report(
    settings: Settings,
    site_title: str,
    doc_url: str,
    p1_email: str | None,
) -> None:
    """Send the completed DD report email when email settings are configured."""
    if not settings.email_sender or not settings.email_app_password:
        return

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
<p>A new Due Diligence report has been generated for <strong>{safe_site_name}</strong>.</p>
{report_link_html}
</body></html>
"""
    try:
        send_email(
            sender=settings.email_sender,
            app_password=settings.email_app_password,
            recipients=recipients,
            subject=f"DD Report Ready - {site_title}",
            html_body=html_body,
            global_cc=settings.global_email_cc,
        )
        logger.info("Email sent for '%s' to %s", site_title, recipients)
    except Exception as e:
        logger.error("Failed to send email for '%s': %s", site_title, e)


# ─────────────────────────────────────────────────────────────────────────────
# Full single-site pipeline
# ─────────────────────────────────────────────────────────────────────────────


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
    # --- Phase 2 DD provenance (Wrike W74 / W81) ---
    # Optional. Callers (scripts/daily_dd_check.py et al.) read these from
    # the Wrike record up front and forward them to the dashboard publish
    # step. None means "don't override the dashboard's stored value".
    school_feasibility: str | None = None,
    timeline_confidence: str | None = None,
) -> PipelineResult:
    """Full single-site pipeline: readiness -> report generation -> completeness -> email.

    Returns a PipelineResult describing what happened.
    """
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
        return PipelineResult(site_title=site_title, status="error", error=str(e))

    readiness_result = _resolve_readiness_result(site_title, readiness)
    if readiness_result is not None:
        return readiness_result

    agent_result, generation_result = _run_pipeline_agent(site_title, system_prompt, settings)
    if generation_result is not None:
        generation_result.trace_url = _save_pipeline_trace(
            gc,
            drive_folder_url,
            site_title,
            generation_result.trace,
        )
        _notify_source_read_issues(
            settings.google_chat_webhook_url,
            site_title,
            generation_result.trace,
            drive_folder_url=drive_folder_url,
            trace_url=generation_result.trace_url or "",
        )
        return generation_result
    assert agent_result is not None

    doc_id = agent_result["doc_id"]
    doc_url = agent_result.get("doc_url", "")
    trace_url = _save_pipeline_trace(
        gc,
        drive_folder_url,
        site_title,
        agent_result.get("trace"),
    )
    _notify_source_read_issues(
        settings.google_chat_webhook_url,
        site_title,
        agent_result.get("trace"),
        drive_folder_url=drive_folder_url,
        trace_url=trace_url or "",
    )

    completeness, completeness_result = _check_generated_report(site_title, doc_id, doc_url)
    if completeness_result is not None:
        completeness_result.trace_url = trace_url
        completeness_result.trace = agent_result.get("trace")
        return completeness_result
    assert completeness is not None

    _email_pipeline_report(settings, site_title, doc_url, p1_email)

    _publish_to_dashboard_best_effort(
        site_title=site_title,
        trace=agent_result.get("trace"),
        drive_folder_url=drive_folder_url,
        dd_report_url=doc_url,
        site_address=site_address,
        p1_name=p1_name,
        school_feasibility=school_feasibility,
        timeline_confidence=timeline_confidence,
    )

    return PipelineResult(
        site_title=site_title,
        status="report_created",
        doc_id=doc_id,
        doc_url=doc_url,
        pending_count=completeness.get("pending_section_count", 0),
        trace_url=trace_url,
        trace=agent_result.get("trace"),
    )


def _publish_to_dashboard_best_effort(
    *,
    site_title: str,
    trace: ReportTrace | None,
    drive_folder_url: str,
    dd_report_url: str,
    site_address: str | None,
    p1_name: str | None = None,
    # Phase 2 DD provenance pass-through. Publisher auto-stamps
    # dd_status="complete" so it's not threaded through here.
    school_feasibility: str | None = None,
    timeline_confidence: str | None = None,
) -> None:
    """Fire-and-forget dashboard publish. Never raises.

    Phase 3 fields (dd_site_score + dd_site_score_band) are derived
    automatically by ``publish_to_dashboard`` from the report's
    ``q2.e_occupancy_score`` token and don't need to be threaded through
    here — the e-occupancy tool emits that token as part of its standard
    output whenever it runs in the pipeline.

    Phase 4 ``dd_risk_flags`` are likewise derived automatically by
    ``publish_to_dashboard`` from the report's flag-like tokens
    (``permit_history.risk_flags``, ``q2.ibc_flags`` /
    ``q2.e_occupancy_ibc_summary``, ``q1.school_approval_*``,
    ``sir.risk_watch``). See ``risk_flags.py`` for the canonical enums
    and severity rules.
    """
    if trace is None or not getattr(trace, "final_report_data", None):
        logger.info(
            "Skipping dashboard publish for %s \u2014 no final_report_data on trace",
            site_title,
        )
        return
    try:
        publish_to_dashboard(
            site_title,
            trace.final_report_data,
            address=site_address,
            drive_folder_url=drive_folder_url,
            dd_report_url=dd_report_url,
            site_owner=p1_name,
            school_feasibility=school_feasibility,
            timeline_confidence=timeline_confidence,
        )
    except Exception as e:
        # publish_to_dashboard already swallows requests errors; this is a
        # belt-and-suspenders guard against anything truly unexpected.
        logger.warning("Dashboard publish raised for %s: %s", site_title, e)


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
        insp = "Building Inspection" not in result.missing_docs
        lines = [
            f"DD Check -- {result.site_title}",
            "Status: WAITING ON DOCUMENTS",
            f"  {'[OK]' if sir else '[  ]'} SIR {'found' if sir else 'not found'}",
            f"  {'[OK]' if insp else '[  ]'} Building Inspection {'found' if insp else 'not found'}",
        ]
        if drive_folder_url:
            lines.append(f"Drive: {drive_folder_url}")
        msg = "\n".join(lines)

    elif result.status == "report_exists":
        msg = f"DD Check -- {result.site_title}\nReport already exists, skipping."

    elif result.status == "report_created":
        msg = (
            f"DD Report CREATED -- {result.site_title}\n"
            f"Report: {result.doc_url or '(no URL)'}"
        )
        if result.trace_url:
            msg += f"\nTrace: {result.trace_url}"
        if result.pending_count:
            msg += f"\nPending fields: {result.pending_count}"

    elif result.status == "report_incomplete":
        count = len(result.unresolved_tokens)
        tokens = ", ".join(result.unresolved_tokens[:10])
        msg = (
            f"DD Report for {result.site_title} has {count} unfilled placeholder(s).\n"
            f"Tokens: {tokens}\n"
            f"Report: {result.doc_url or '(no URL)'}"
        )

    elif result.status == "generation_failed":
        msg = (
            f"DD Report generation FAILED for {result.site_title}\n"
            f"Error: {result.error or 'unknown'}"
        )

    elif result.status == "error":
        msg = (
            f"DD Check ERROR for {result.site_title}\n"
            f"Error: {result.error or 'unknown'}"
        )

    else:
        msg = f"DD Check -- {result.site_title}\nStatus: {result.status}"

    for url in urls:
        try:
            post_google_chat_message(url, msg)
        except Exception as e:
            logger.error("Failed to post Chat message for '%s' to %s: %s", result.site_title, url[:60], e)
