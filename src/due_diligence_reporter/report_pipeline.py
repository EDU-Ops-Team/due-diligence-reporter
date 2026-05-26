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
from typing import Any, cast

import anthropic

from .classifier import (
    AI_GENERATED_DOC_TYPES,
    SOURCE_FOLDER_DOC_TYPES,
    classify_document_type,
    match_file_to_site_llm,
)
from .config import Settings, get_settings
from .google_client import GoogleClient
from .m1_lookup import _list_m1_documents_by_type, _resolve_m1_folder
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
from .pipeline_manifest import manifest_has_secret_like_value, persist_run_manifest
from .pipeline_quality import evaluate_run_quality
from .provenance import classify_provenance
from .rhodes import lookup_rhodes_site_owner
from .sir_learning import build_sir_learning_review
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
        "name": "list_drive_documents",
        "description": "List matched shared DD source reports plus site-folder artifacts found in the site folder or its M1 subfolder. Results may include Block Plan PDFs and derived reports such as Capacity Brainlift, RayCon Scenario, Opening Plan, and DD reports. Each file includes a doc_type field. Pass the full request site_name and site_address so shared-folder matching cannot use city-only evidence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drive_folder_url": {"type": "string", "description": "Google Drive folder URL"},
                "site_name": {"type": "string", "description": "Site name used to match docs in shared folders"},
                "site_address": {"type": "string", "description": "Optional full property address used to strengthen shared-folder matching"},
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
        "name": "lookup_rhodes_site_owner",
        "description": (
            "Read the Rhodes/LocationOS site record for the supplied site and return "
            "the current P1 DRI / site owner. Call this before create_dd_report. "
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
        "name": "create_dd_report",
        "description": "Create a completed DD report Google Doc. The report_data dict must use exact current template token keys (e.g. 'exec.c_zoning', 'exec.fastest_open_capex', 'sources.sir_link'). Copy report_data_fields from skill tools directly into report_data. Pass token_evidence for source traceability.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_name": {"type": "string"},
                "drive_folder_url": {"type": "string"},
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
        "list_drive_documents": srv.list_drive_documents,
        "read_drive_document": srv.read_drive_document,
        "lookup_rhodes_site_owner": srv.lookup_rhodes_site_owner,
        "apply_e_occupancy_skill": srv.apply_e_occupancy_skill,
        "apply_school_approval_skill": srv.apply_school_approval_skill,
        "create_dd_report": srv.create_dd_report,
        "check_report_completeness": srv.check_report_completeness,
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


def _canonicalize_site_tool_input(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    site_title: str,
    drive_folder_url: str | None,
    site_address: str | None,
) -> dict[str, Any]:
    """Keep agent tool calls anchored to the pipeline's canonical site context."""
    canonical = dict(tool_input)
    if tool_name in {
        "list_drive_documents",
        "apply_e_occupancy_skill",
        "apply_school_approval_skill",
        "create_dd_report",
        "save_skill_report",
        "send_dd_report_email",
    }:
        canonical["site_name"] = site_title

    if drive_folder_url and tool_name in {
        "list_drive_documents",
        "apply_e_occupancy_skill",
        "apply_school_approval_skill",
        "create_dd_report",
        "save_skill_report",
    }:
        canonical["drive_folder_url"] = drive_folder_url

    if site_address and tool_name == "list_drive_documents":
        canonical["site_address"] = site_address

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
    ]
    keep_doc_types = AI_GENERATED_DOC_TYPES | SOURCE_FOLDER_DOC_TYPES
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

    # ── RayCon scenario JSON ────────────────────────────────────────────────
    # The third gating input. Lives only in M1 (written by RayCon's async
    # /v1/jobs hand-off) and is keyed by the dedicated ``raycon_scenario_json``
    # doc_type so an AI raycon_scenario_report can never satisfy this slot.
    raycon_scenario_file: dict[str, Any] | None = None
    if m1_folder_id:
        try:
            m1_files_by_type = _list_m1_documents_by_type(gc, m1_folder_id)
            raycon_scenario_file = m1_files_by_type.get("raycon_scenario_json")
        except Exception as e:  # pragma: no cover
            logger.warning(
                "M1 lookup failed for raycon_scenario_json in %s: %s", site_title, e
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
        "raycon_scenario_found": raycon_scenario_file is not None,
        "report_exists": files_by_type["dd_report"] is not None,
        "e_occupancy_report_found": files_by_type["e_occupancy_report"] is not None,
        "school_approval_report_found": files_by_type["school_approval_report"] is not None,
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
    if site_address:
        request_lines.append(f"Site address: {site_address}")
    if drive_folder_url:
        request_lines.append(f"Drive folder URL: {drive_folder_url}")
        request_lines.append("Use the provided Drive folder directly.")
    if rhodes_owner_context:
        owner_name = str(rhodes_owner_context.get("p1_assignee_name") or "").strip()
        owner_email = str(rhodes_owner_context.get("p1_assignee_email") or "").strip()
        owner_status = str(rhodes_owner_context.get("status") or "").strip()
        if owner_name or owner_email:
            owner_email_suffix = f" <{owner_email}>" if owner_email else ""
            request_lines.append(
                f"Rhodes P1 DRI / site owner: {owner_name or '[name missing]'}"
                f"{owner_email_suffix}"
            )
        elif owner_status:
            request_lines.append(f"Rhodes P1 DRI / site owner lookup status: {owner_status}")

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "\n".join(request_lines)},
    ]

    doc_id: str | None = None
    doc_url: str | None = None
    cached_report_fields: dict[str, Any] = dict(initial_report_fields or {})
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
                drive_folder_url=drive_folder_url,
                site_address=site_address,
            )
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
    run_id: str | None = None
    failed_step: str | None = None
    quality_score: int | None = None
    quality_band: str | None = None
    manifest_path: str | None = None
    sir_review_status: str | None = None
    sir_learning_review: dict[str, Any] | None = None
    steps: list[StepResult] = field(default_factory=list)


class _RunRecorder:
    """Collect step results for one pipeline run."""

    def __init__(self, site_title: str, site_id: str | None = None) -> None:
        self.run_id = make_run_id(site_title)
        self.site_title = site_title
        self.site_id = site_id
        self.started_at = utc_now_iso()
        self.steps: list[StepResult] = []
        self.sir_learning_review: dict[str, Any] | None = None

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
            sir_learning_review=self.sir_learning_review,
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
    # the in-memory trace for validation and local run diagnostics.
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
      * RayCon scenario JSON (always vendor by definition — RayCon writes it)

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
            missing.append(
                "Vendor Building Inspection"
                if readiness.get("inspection_found")
                else "Building Inspection"
            )
        if not readiness.get("raycon_scenario_found", False):
            missing.append("RayCon Scenario JSON")
        return missing

    if not readiness.get("sir_found", False):
        missing.append("SIR")
    if not readiness.get("inspection_found", False):
        missing.append("Building Inspection")
    return missing


def _missing_first_round_docs(readiness: dict[str, Any]) -> list[str]:
    """Return blocking inputs for first-round DDR publishing.

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
    RayCon scenario JSON were all on hand when generation was attempted. If
    the agent still couldn't produce a complete report, the inputs almost
    certainly need a human to disambiguate — OCR failure, malformed RayCon
    payload, conflicting permit narratives, etc. We escalate to Google Chat
    rather than silently leaving the row in ``report_incomplete``.

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
        "Inspection, RayCon Scenario JSON) but the report could not be "
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
    initial_report_fields: dict[str, Any] | None = None,
    rhodes_owner_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, PipelineResult | None]:
    """Run report generation and map failures into a PipelineResult."""
    # Phase B-PR3 cutover: when DD_REPORT_OWNER=pipeline, the
    # alpha-dd-pipeline WU-13 is the sole DD-report producer. Short-circuit
    # before any agent work so reruns and backfills both honor the flag
    # uniformly. We yield a distinct status ("yielded_to_pipeline") so the
    # caller can skip the email and validation side effects that the Anthropic
    # agent would normally feed. Default "reporter" preserves legacy behavior
    # until soak passes; "pipeline" is the only value that disables the agent run.
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
            drive_folder_url=drive_folder_url,
            site_address=site_address,
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
        return None
    except Exception as e:
        logger.error("Failed to send email for '%s': %s", site_title, e)
        return str(e)


def _resolve_rhodes_owner_for_pipeline(
    site_title: str,
    site_address: str | None,
) -> dict[str, Any]:
    """Best-effort Rhodes owner lookup for pipeline runs."""
    try:
        result = lookup_rhodes_site_owner(
            site_name=site_title,
            site_address=site_address or "",
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


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


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
) -> None:
    started_at, started_monotonic = recorder.start()
    issues = _extract_source_read_issues(trace)
    _notify_source_read_issues(
        settings.google_chat_webhook_url,
        site_title,
        trace,
        drive_folder_url=drive_folder_url,
        trace_url=trace_url,
    )
    if issues:
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
) -> None:
    started_at, started_monotonic = recorder.start()
    error = _email_pipeline_report(settings, site_title, doc_url, p1_email)
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
    # When True, bypass the ``report_exists`` short-circuit so a fresh
    # report is generated on top of an existing DD Report Doc. Used by the
    # event-driven republish path (raycon_followup) when authoritative
    # inputs (e.g. raycon_scenario.json) have just landed. All other gates
    # — vendor gate, missing required docs — still apply.
    force_regenerate: bool = False,
) -> PipelineResult:
    """Full single-site pipeline: readiness -> report generation -> completeness -> email.

    Returns a PipelineResult describing what happened.
    """
    recorder = _RunRecorder(site_title)
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

    readiness_result = _resolve_readiness_result(
        site_title, readiness, force_regenerate=force_regenerate
    )
    sir_learning_review = readiness.get("sir_learning_review")
    if readiness_result is not None:
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
    full_report_inputs_present = not _missing_required_docs(readiness)
    recorder.record("readiness.check", started_at, started_monotonic, "succeeded")
    _record_sir_learning_review_step(recorder, sir_learning_review)

    rhodes_owner_context: dict[str, Any] | None = None
    initial_report_fields: dict[str, Any] = {}
    if not (p1_name and p1_email):
        started_at, started_monotonic = recorder.start()
        rhodes_owner_context = _resolve_rhodes_owner_for_pipeline(site_title, site_address)
        rhodes_status = str(rhodes_owner_context.get("status") or "")
        if rhodes_status == "not_configured":
            recorder.record(
                "rhodes.owner_lookup",
                started_at,
                started_monotonic,
                "skipped",
                skipped_reason="RHODES_API_KEY not configured",
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
        initial_report_fields=initial_report_fields,
        rhodes_owner_context=rhodes_owner_context,
    )
    if generation_result is not None:
        gen_status = "skipped" if generation_result.status == "yielded_to_pipeline" else "failed"
        gen_error = None
        if gen_status == "failed":
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
            skipped_reason=generation_result.status if gen_status == "skipped" else None,
        )
        generation_result.trace_url = None
        _record_source_alert_step(
            recorder,
            settings,
            site_title,
            generation_result.trace,
            drive_folder_url=drive_folder_url,
            trace_url=generation_result.trace_url or "",
        )
        # First-round publishing can proceed before every full-report
        # vendor/RayCon input is present. Escalate only when the full input
        # set was actually present and generation still failed.
        if (
            _vendor_gate_enabled()
            and full_report_inputs_present
            and generation_result.status == "generation_failed"
        ):
            _notify_vendor_gate_extraction_failure(
                settings.google_chat_webhook_url,
                site_title,
                drive_folder_url=drive_folder_url,
                failure_reason=generation_result.error or "",
                trace_url=generation_result.trace_url or "",
            )
        return _finalize_pipeline_result(
            generation_result,
            recorder,
            gc=gc,
            drive_folder_url=drive_folder_url,
        )
    assert agent_result is not None
    recorder.record("report.generate", started_at, started_monotonic, "succeeded")

    doc_id = agent_result["doc_id"]
    doc_url = agent_result.get("doc_url", "")
    final_report_data = getattr(agent_result.get("trace"), "final_report_data", None)
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
        agent_result.get("trace"),
        drive_folder_url=drive_folder_url,
        trace_url=trace_url or "",
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
        # vendor/RayCon input set was present but the resulting report is
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
            _notify_vendor_gate_extraction_failure(
                settings.google_chat_webhook_url,
                site_title,
                drive_folder_url=drive_folder_url,
                failure_reason=failure_reason,
                trace_url=trace_url or "",
            )
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

    _record_email_step(recorder, settings, site_title, doc_url, p1_email)

    return _finalize_pipeline_result(
        PipelineResult(
            site_title=site_title,
            status="report_created",
            doc_id=doc_id,
            doc_url=doc_url,
            pending_count=completeness.get("pending_section_count", 0),
            trace_url=trace_url,
            trace=agent_result.get("trace"),
        ),
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
        insp = "Building Inspection" not in result.missing_docs
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
        msg = (
            f"DD Report CREATED -- {result.site_title}\n"
            f"Report: {result.doc_url or '(no URL)'}"
        )
        if result.trace_url:
            msg += f"\nTrace: {result.trace_url}"
        if result.pending_count:
            msg += f"\nPending fields: {result.pending_count}"
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
    action = next_operator_action(result.steps)
    if action:
        lines.append(f"Next action: {action}")
    return lines
