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
from datetime import datetime, timezone
from typing import Any

import anthropic

from .config import Settings, get_settings
from .google_client import GoogleClient
from .classifier import classify_document, match_file_to_site_llm
from .server import (
    _build_site_match_terms,
    _classify_document_type,
)
from .utils import (
    extract_folder_id_from_url,
    post_google_chat_message,
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
        "description": "List all files in the site's Google Drive folder, its 01_Due Diligence subfolder, and shared SIR/ISP/Building Inspection folders. Each file includes a doc_type field. Always pass site_name to find shared folder docs.",
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
        "name": "get_cost_estimate",
        "description": "Estimate renovation costs using the Building Optimizer API. Returns report_data_fields with all q3.* template tokens — copy these directly into report_data as flat keys (e.g. report_data['q3.structural_low']). Do NOT nest under q3.cost_estimate_table.",
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
            },
            "required": ["total_building_sf"],
        },
    },
    {
        "name": "create_dd_report",
        "description": "Create a completed DD report Google Doc. The report_data dict must use exact V2 template token keys (e.g. 'exec.c_zoning', 'sources.sir_link'). Copy report_data_fields from skill tools directly into report_data. Pass token_evidence for source traceability.",
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
                "skill_name": {"type": "string", "description": "Skill name: 'E-Occupancy' or 'School Approval'"},
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

    # Pass 1: substring match
    for doc_type, files in shared_cache.items():
        for f in files:
            fname = f.get("name", "").lower()
            if any(needle in fname for needle in needles):
                result[doc_type] = {**f, "doc_type": doc_type}
                break

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
            "report_exists": False, "error": "bad_url",
        }

    # 1. Match docs from pre-fetched shared folder cache (substring + LLM fallback)
    shared_docs = match_site_in_shared_cache(
        match_terms, shared_cache,
        site_title=site_title, site_address=site_address,
    )

    # 2. Recursively list + classify files in the site's own folder (all subfolders)
    all_site_files = [
        {**f, "doc_type": _classify_document_type(f.get("name", ""))}
        for f in gc.list_files_recursive(folder_id, max_depth=2)
    ]

    # 3. Merge — shared folders take priority, site folder fills gaps
    files_by_type: dict[str, dict[str, Any] | None] = {
        "sir": shared_docs.get("sir"),
        "isp": shared_docs.get("isp"),
        "building_inspection": shared_docs.get("building_inspection"),
        "dd_report": None,
    }
    for f in all_site_files:
        dt = f.get("doc_type", "unknown")
        if dt in files_by_type and files_by_type[dt] is None:
            files_by_type[dt] = f

    # 4. LLM classification for unknown site files if docs are still missing
    still_missing = [
        k for k in ("sir", "isp", "building_inspection")
        if files_by_type[k] is None
    ]
    if still_missing:
        unknown_files = [f for f in all_site_files if f.get("doc_type") == "unknown"]
        for f in unknown_files:
            fname = f.get("name", "")
            fid = f.get("id")
            doc_type, conf = classify_document(
                fname, file_id=fid, gc=gc, site_name=site_title,
            )
            if doc_type in still_missing and files_by_type.get(doc_type) is None:
                f["doc_type"] = doc_type
                files_by_type[doc_type] = f
                still_missing.remove(doc_type)
                logger.info(
                    "LLM classified '%s' as %s (conf=%.2f) for '%s'",
                    fname, doc_type, conf, site_title,
                )
            if not still_missing:
                break

    return {
        "sir_found": files_by_type["sir"] is not None,
        "isp_found": files_by_type["isp"] is not None,
        "inspection_found": files_by_type["building_inspection"] is not None,
        "report_exists": files_by_type["dd_report"] is not None,
        "all_files": all_site_files,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Claude agentic loop — generates one DD report
# ─────────────────────────────────────────────────────────────────────────────


def run_dd_report_agent(
    site_title: str,
    system_prompt: str,
) -> dict[str, Any]:
    """Run Claude as a tool-calling agent to generate one DD report.

    Args:
        site_title: Site name to generate the report for.
        system_prompt: Full system prompt text.

    Returns a dict with keys: success, doc_id, doc_url, error.
    """
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not anthropic_api_key:
        return {"success": False, "error": "ANTHROPIC_API_KEY not set"}

    client = anthropic.Anthropic(api_key=anthropic_api_key)

    # Initialize provenance trace
    trace = ReportTrace(
        site_name=site_title,
        started_at=datetime.now(timezone.utc).isoformat(),
        prompt_version=2,
    )
    run_start = time.monotonic()

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": f"Generate a DD Report for: {site_title}"},
    ]

    doc_id: str | None = None
    doc_url: str | None = None
    max_iterations = 40  # Safety limit

    for iteration in range(max_iterations):
        logger.info("Agent iteration %d for site: %s", iteration + 1, site_title)

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=system_prompt,
            tools=TOOL_DEFINITIONS,
            messages=messages,
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
            tool_input = tool_use.input

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
                timestamp=datetime.now(timezone.utc).isoformat(),
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

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": json.dumps(result),
            })

        messages.append({"role": "user", "content": tool_results})

        # Stop as soon as we have a report — completeness check happens separately
        if doc_id:
            logger.info("Report created, stopping agent loop after %d iterations", iteration + 1)
            break

    # Finalize trace
    trace.ended_at = datetime.now(timezone.utc).isoformat()
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
    status: str  # waiting_on_docs | report_exists | report_created | report_incomplete | generation_failed | error
    missing_docs: list[str] = field(default_factory=list)
    doc_id: str | None = None
    doc_url: str | None = None
    unresolved_tokens: list[str] = field(default_factory=list)
    pending_count: int = 0
    error: str | None = None
    trace_url: str | None = None


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
    if "content" in result and isinstance(result["content"], str):
        summary["content_length"] = len(result["content"])
    if "message" in result:
        msg = str(result["message"])
        summary["message"] = msg[:300] if len(msg) > 300 else msg
    if "error" in result:
        summary["error"] = str(result["error"])[:200]
    if "replacements_applied" in result:
        summary["replacements_applied"] = result["replacements_applied"]
    if "unfilled_template_tokens" in result:
        summary["unfilled_template_tokens"] = result["unfilled_template_tokens"]

    return summary


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
) -> PipelineResult:
    """Full single-site pipeline: readiness -> report generation -> completeness -> email.

    Returns a PipelineResult describing what happened.
    """
    import asyncio
    from . import server as srv

    # 1. Check readiness
    try:
        readiness = check_site_readiness_direct(
            gc, drive_folder_url, match_terms, shared_cache,
            site_title=site_title, site_address=site_address,
        )
    except Exception as e:
        logger.error("Failed to check readiness for '%s': %s", site_title, e)
        return PipelineResult(site_title=site_title, status="error", error=str(e))

    sir_found = readiness.get("sir_found", False)
    isp_found = readiness.get("isp_found", False)
    inspection_found = readiness.get("inspection_found", False)
    report_exists = readiness.get("report_exists", False)

    # Case 1: Missing required documents
    if not sir_found or not isp_found or not inspection_found:
        missing = []
        if not sir_found:
            missing.append("SIR")
        if not isp_found:
            missing.append("ISP")
        if not inspection_found:
            missing.append("Building Inspection")
        return PipelineResult(
            site_title=site_title, status="waiting_on_docs", missing_docs=missing,
        )

    # Case 2: Report already exists
    if report_exists:
        logger.info("'%s' — report already exists, skipping", site_title)
        return PipelineResult(site_title=site_title, status="report_exists")

    # Case 3: All docs present, no report yet — generate
    logger.info("'%s' — all docs present, generating report...", site_title)
    agent_result = run_dd_report_agent(site_title, system_prompt)

    if not agent_result.get("success"):
        err = agent_result.get("error", "unknown error")
        logger.error("Report generation failed for '%s': %s", site_title, err)
        return PipelineResult(
            site_title=site_title, status="generation_failed", error=err,
        )

    doc_id = agent_result["doc_id"]
    doc_url = agent_result.get("doc_url", "")

    # 3b. Save provenance trace to Drive
    trace: ReportTrace | None = agent_result.get("trace")
    trace_url: str | None = None
    if trace:
        folder_id = extract_folder_id_from_url(drive_folder_url)
        if folder_id:
            trace_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            trace_name = f"{site_title} DD Report Trace - {trace_date}.json"
            try:
                trace_json = json.dumps(trace.to_dict(), indent=2)
                trace_file = gc.upload_file_to_folder(
                    folder_id=folder_id,
                    file_name=trace_name,
                    file_bytes=trace_json.encode("utf-8"),
                    mime_type="application/json",
                )
                trace_url = trace_file.get("webViewLink")
                logger.info("Saved report trace: %s", trace_name)
            except Exception as e:
                logger.warning("Failed to save report trace: %s", e)

    # 4. Check completeness
    completeness = asyncio.run(srv.check_report_completeness(doc_id))

    if not completeness.get("ready_to_send", False):
        unresolved = completeness.get("unresolved_tokens", [])
        return PipelineResult(
            site_title=site_title,
            status="report_incomplete",
            doc_id=doc_id,
            doc_url=doc_url,
            unresolved_tokens=unresolved,
        )

    # 5. Send email (to configured recipients + P1 Assignee)
    if settings.email_sender and settings.email_app_password:
        base_recipients = [
            r.strip()
            for r in settings.dd_report_email_recipients.split(",")
            if r.strip()
        ] if settings.dd_report_email_recipients else []

        # Add P1 Assignee if available and not already in list
        if p1_email and p1_email.lower() not in {r.lower() for r in base_recipients}:
            base_recipients.append(p1_email)

        recipients = base_recipients
        html_body = f"""
<html><body>
<h2>Due Diligence Report — {site_title}</h2>
<p>A new Due Diligence report has been generated for <strong>{site_title}</strong>.</p>
<p><a href="{doc_url}" style="font-size:16px;font-weight:bold;">View Report in Google Docs</a></p>
</body></html>
"""
        try:
            send_email(
                sender=settings.email_sender,
                app_password=settings.email_app_password,
                recipients=recipients,
                subject=f"DD Report Ready — {site_title}",
                html_body=html_body,
            )
            logger.info("Email sent for '%s' to %s", site_title, recipients)
        except Exception as e:
            logger.error("Failed to send email for '%s': %s", site_title, e)

    return PipelineResult(
        site_title=site_title,
        status="report_created",
        doc_id=doc_id,
        doc_url=doc_url,
        pending_count=completeness.get("pending_section_count", 0),
        trace_url=trace_url,
    )


# ─────────────────────────────────────────────────────────────────────────────
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
        isp = "ISP" not in result.missing_docs
        insp = "Building Inspection" not in result.missing_docs
        lines = [
            f"DD Check -- {result.site_title}",
            "Status: WAITING ON DOCUMENTS",
            f"  {'[OK]' if sir else '[  ]'} SIR {'found' if sir else 'not found'}",
            f"  {'[OK]' if isp else '[  ]'} ISP {'found' if isp else 'not found'}",
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
