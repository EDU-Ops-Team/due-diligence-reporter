"""MCP server for Alpha School Due Diligence Report generation."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import requests
from mcp.server import FastMCP
from tenacity import retry

from .alpha_phasing_plan import (
    AlphaPhasingPlanError,
    alpha_phasing_open_items,
    build_alpha_phasing_report_fields,
    build_alpha_phasing_workbook,
    load_alpha_phasing_skill,
    missing_alpha_phasing_inputs,
)
from .assignment import assign_p1
from .classifier import (
    AI_GENERATED_DOC_TYPES,
    SITE_FOLDER_DOC_TYPES,
    match_file_to_site_llm,
)
from .classifier import (
    classify_document_type as _classify_document_type,
)
from .completeness import (
    REASON_DISPLAY_LABELS,
    compute_completeness_block,
    is_raycon_pending_placeholder,
    project_completeness_from_readiness,
    raycon_token_paths,
)
from .config import get_settings, shovels_status
from .ease_conversion_skill import (
    EaseConversionSkillError,
    load_ease_conversion_skill,
)
from .google_client import GoogleClient
from .google_doc_builder import (
    CITATIONS_BLOCK_KEY,
    SOURCE_QUALITY_NOTES_KEY,
    VERIFICATION_OPEN_ITEMS_KEY,
    build_dd_report_doc,
)
from .m1_lookup import (
    M1_FOLDER_NAME,
    _find_preferred_m1_subfolder,
    _list_m1_documents_by_type,
    _resolve_m1_folder,
)
from .portfolio_automation_gaps import build_portfolio_automation_gap_snapshot
from .raycon_client import RAYCON_BREAKDOWN_ROWS, RAYCON_FAILED_STATUSES
from .rebl import ReblResolution, resolve_address
from .report_schema import (
    ALLOWED_CAN_WE_ANSWERS,
    LINK_DISPLAY_LABELS,
    LINK_TOKENS,
    MISSING_P1_ASSIGNEE_LABEL,
    TEMPLATE_TOKENS,
    normalize_can_we_answer,
    normalize_report_data,
)
from .retry import retry_config
from .rhodes import lookup_rhodes_site_owner as _lookup_rhodes_site_owner
from .rhodes import register_rhodes_document_for_upload
from .school_approval_skill import (
    SchoolApprovalSkillError,
    load_school_approval_skill,
    normalize_school_approval_state,
)
from .utils import (
    build_hyperlink_requests,
    escape_html_text,
    extract_folder_id_from_url,
    extract_text_from_pdf_bytes,
    flatten_report_data_for_replacement,
    is_drive_root_folder_id,
    sanitize_http_url,
    score_site_match_strength,
    send_email,
)
from .utils import build_site_match_terms as _build_site_match_terms

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),  # stderr for MCP protocol compatibility
    ],
)
logger = logging.getLogger(__name__)
logger.info("=" * 80)
logger.info("Due Diligence Reporter MCP server starting")

mcp = FastMCP("dd-reporter")

# MIME type for Google Docs
GOOGLE_DOCS_MIME = "application/vnd.google-apps.document"
# MIME type for Google Sheets
GOOGLE_SHEETS_MIME = "application/vnd.google-apps.spreadsheet"
# MIME type for PDF
PDF_MIME = "application/pdf"
# Google Workspace MIME types that can be exported as plain text
EXPORTABLE_MIME_TYPES: set[str] = {
    GOOGLE_DOCS_MIME,
    "application/vnd.google-apps.presentation",
    GOOGLE_SHEETS_MIME,
}
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOC_MIME = "application/msword"
MIN_SITE_MATCH_SCORE = 40

_READ_CONTEXT_BY_FILE_ID: dict[str, dict[str, str]] = {}
_REBL_RESOLUTION_CACHE: dict[str, ReblResolution] = {}
DDR_AUTOMATION_UPDATED_AT_KEY = "ddrAutomationUpdatedAt"
DDR_AUTOMATION_REVISION_ID_KEY = "ddrAutomationRevisionId"
DDR_AUTOMATION_DOC_ROLE_KEY = "ddrAutomationDocRole"
DDR_AUTOMATION_SOURCE_DOC_ID_KEY = "ddrAutomationSourceDocId"


CAN_WE_SECTION_DELIMITER = "Education Regulatory Approval:"
CURRENT_CAN_WE_HEADING = "Can this school be open in time for the current school year (8/12 or 9/8)?"
LEGACY_CURRENT_CAN_WE_HEADING = "Can this school be open in time for the current school year?"
LEGACY_CAN_WE_HEADING = "Can we do this?"

# ─────────────────────────────────────────────────────────────────────────────
# E-OCCUPANCY SKILL DATA
# ─────────────────────────────────────────────────────────────────────────────

# (base_score, label, keywords) — keyword matching picks the longest keyword hit
_EOCCUPANCY_BUILDING_TYPES: list[tuple[int, str, list[str]]] = [
    (100, "Current K-12 school", ["k-12", "elementary school", "middle school", "high school", "existing school"]),
    (95, "Daycare / childcare", ["daycare", "childcare", "preschool", "pre-k"]),
    (92, "Office 1–3 stories", [
        "1-story office", "2-story office", "3-story office",
        "1 story office", "2 story office", "3 story office",
        "low-rise office", "single story office", "single-story office",
    ]),
    (90, "Gym / fitness center", ["gym", "fitness center", "health club", "yoga studio", "crossfit"]),
    (88, "Flex / light industrial (with HVAC)", [
        "flex space", "light industrial with hvac", "warehouse office",
        "flex industrial", "light industrial (with hvac)",
    ]),
    (85, "Retail strip — individual unit", ["strip mall unit", "retail unit", "individual retail"]),
    (82, "Office — general", ["office building", "professional office", "corporate office", "general office"]),
    (78, "Small / mid-size church", ["small church", "community church", "chapel"]),
    (75, "Medical office / retail strip center", [
        "medical office", "dental office", "dental", "clinic", "urgent care",
        "retail strip center", "strip mall", "shopping center", "strip center",
    ]),
    (58, "Warehouse with HVAC", [
        "warehouse with hvac", "conditioned warehouse",
        "climate controlled warehouse", "heated warehouse",
    ]),
    (55, "Small assembly venue", ["event space", "banquet hall", "small assembly", "small theater", "assembly venue"]),
    (42, "High-rise 4–6 stories (cap)", [
        "4-story", "5-story", "6-story", "4 story", "5 story", "6 story",
    ]),
    (38, "Large church / worship center", [
        "large church", "megachurch", "cathedral", "temple", "mosque",
        "worship center", "church",
    ]),
    (35, "Warehouse without HVAC", ["warehouse", "cold shell", "distribution center"]),
    (32, "Nightclub / large bar", ["nightclub", "large bar", "night club", "lounge"]),
    (30, "Historic / landmark building", ["historic building", "landmark", "national register", "shpo"]),
    (28, "Large assembly / cold storage", [
        "theater", "concert hall", "auditorium", "cinema",
        "cold storage", "freezer storage", "movie theater",
    ]),
    (25, "Data center", ["data center", "server farm"]),
    (22, "Big box retail (100k+ SF)", ["big box", "walmart", "target", "costco", "mall anchor", "anchor store"]),
    (20, "High-rise 7+ stories (cap)", [
        "7-story", "8-story", "9-story", "10-story",
        "7 story", "8 story", "9 story", "10 story",
        "high-rise", "high rise", "skyscraper", "tower",
    ]),
    (18, "Hospital / nursing home", [
        "hospital", "medical center", "surgical center", "nursing home", "assisted living",
    ]),
    (15, "Bank", ["bank branch", "credit union", "bank"]),
    (12, "Restaurant", ["restaurant", "cafe", "diner", "bistro", "grill", "food service"]),
    (0, "Do not pursue", [
        "gas station", "fuel station", "petroleum", "fueling station",
        "dry cleaner", "dry clean", "perchloroethylene",
        "auto body", "collision repair", "body shop", "paint shop",
        "heavy manufacturing", "manufacturing plant", "industrial plant", "fabrication plant",
        "chemical storage", "hazmat storage",
        "mortuary", "funeral home", "crematorium",
        "adult entertainment", "strip club",
        "jail", "prison", "detention center", "correctional",
    ]),
]

_EOCCUPANCY_TENANT_DEDUCTIONS: dict[str, int] = {
    "shared_hvac": -5,
    "shared_egress": -5,
    "building_management_approval_required": -5,
    "no_dedicated_entrance": -5,
    "no_outdoor_space": -5,
    "shared_parking": -3,
    "incompatible_tenants": -5,
}

_EOCCUPANCY_TIMELINES: list[tuple[int, int, str]] = [
    (100, 100, "Ready to proceed"),
    (90, 99, "3–6 months"),
    (70, 89, "6–9 months"),
    (50, 69, "9–12 months"),
    (30, 49, "12–18 months"),
    (15, 29, "18–24+ months"),
    (1, 14, "24+ months"),
    (0, 0, "N/A — do not pursue"),
]

_EOCCUPANCY_TIER_LABELS: dict[int, str] = {
    1: "Tier 1 — Do Not Pursue",
    2: "Tier 2 — Complex",
    3: "Tier 3 — Moderate",
    4: "Tier 4 — Easy-Moderate",
    5: "Tier 5 — Very Easy",
}

# IBC occupancy group → (difficulty label, score cap or None)
# Score cap: None = no override; 0 = do not pursue; 20 = institutional cap
_IBC_GROUP_INFO: dict[str, tuple[str, int | None]] = {
    "E":   ("Educational — already Group E", None),
    "B":   ("Business/Office — Low-Moderate difficulty", None),
    "A-3": ("Assembly: Worship/Recreation — Low-Moderate difficulty", None),
    "A-2": ("Assembly: Food & Drink — Moderate difficulty", None),
    "A-1": ("Assembly: Fixed Seating — Moderate difficulty", None),
    "M":   ("Mercantile — Moderate difficulty", None),
    "F":   ("Factory — Moderate-High difficulty", None),
    "S":   ("Storage — High difficulty", None),
    "R":   ("Residential — High difficulty", None),
    "I":   ("Institutional — Very High difficulty", 20),
    "H":   ("Hazardous — Do Not Pursue", 0),
}

# Required exits by occupant load (IBC Table 1006.2.1)
_IBC_EXIT_REQUIREMENTS: list[tuple[int, int, int]] = [
    (1, 49, 1),
    (50, 500, 2),
    (501, 1000, 3),
    (1001, 99999, 4),
]


def _eval_ibc_gates(
    fire_area_sqft: int,
    has_below_grade_space: bool,
    already_sprinklered: bool,
    max_travel_distance_ft: int,
    existing_exit_count: int,
    projected_occupant_load: int,
    construction_type: str,
) -> tuple[dict[str, str], list[str], int]:
    """Evaluate IBC compliance gates for Group E conversion.

    Returns (gate_statuses dict, flags list, score_adjustment int).
    Score adjustments: sprinkler retrofit needed (−5), travel distance fail (−15),
    exit count fail (−10). All are penalties — negative values.
    """
    gates: dict[str, str] = {}
    flags: list[str] = []
    adjustment = 0

    # Gate 1: Sprinkler requirement (IBC 903.2.3)
    sprinkler_required: bool | None = None
    if fire_area_sqft > 0 or has_below_grade_space:
        if fire_area_sqft > 12000:
            sprinkler_required = True
            gates["sprinkler_required"] = "YES — fire area exceeds 12,000 sq ft (IBC 903.2.3)"
        elif has_below_grade_space:
            sprinkler_required = True
            gates["sprinkler_required"] = "YES — below-grade space present (IBC 903.2.3)"
        elif fire_area_sqft > 0:
            sprinkler_required = False
            gates["sprinkler_required"] = f"NO — fire area {fire_area_sqft:,} sq ft is under 12,000 sq ft threshold"
    else:
        gates["sprinkler_required"] = "UNKNOWN — fire area not provided"

    if sprinkler_required and not already_sprinklered:
        gates["sprinkler_cost"] = "Retrofit required — budget $3–6/sq ft (NFPA 13)"
        flags.append("Sprinkler retrofit required (IBC 903.2.3)")
        adjustment -= 5
    elif already_sprinklered:
        gates["sprinkler_cost"] = "Already sprinklered — no retrofit cost"

    # Gate 2: Travel distance (IBC Table 1017.2)
    if max_travel_distance_ft > 0:
        # Use sprinklered limit if building is or will be sprinklered
        is_sprinklered = already_sprinklered or bool(sprinkler_required)
        limit = 250 if is_sprinklered else 200
        if max_travel_distance_ft > limit:
            gates["travel_distance"] = (
                f"NON-COMPLIANT — {max_travel_distance_ft} ft exceeds {limit} ft limit "
                f"({'sprinklered' if is_sprinklered else 'non-sprinklered'}) (IBC Table 1017.2)"
            )
            flags.append(f"Travel distance non-compliant: {max_travel_distance_ft} ft > {limit} ft limit (IBC 1017.2)")
            adjustment -= 15
        else:
            gates["travel_distance"] = (
                f"COMPLIANT — {max_travel_distance_ft} ft within {limit} ft limit (IBC Table 1017.2)"
            )
    else:
        gates["travel_distance"] = "UNKNOWN — travel distance not provided; verify against 200 ft (non-sprinklered) or 250 ft (sprinklered)"

    # Gate 3: Exit count (IBC Table 1006.2.1)
    if existing_exit_count > 0 and projected_occupant_load > 0:
        required = next(
            req for lo, hi, req in _IBC_EXIT_REQUIREMENTS if lo <= projected_occupant_load <= hi
        )
        if existing_exit_count < required:
            gates["exit_count"] = (
                f"NON-COMPLIANT — {existing_exit_count} exit(s) present, {required} required "
                f"for {projected_occupant_load} occupants (IBC Table 1006.2.1)"
            )
            flags.append(f"Insufficient exits: {existing_exit_count} present, {required} required (IBC 1006.2.1)")
            adjustment -= 10
        else:
            gates["exit_count"] = (
                f"COMPLIANT — {existing_exit_count} exit(s) meets {required}-exit requirement "
                f"for {projected_occupant_load} occupants (IBC Table 1006.2.1)"
            )
    elif projected_occupant_load > 0:
        required = next(
            req for lo, hi, req in _IBC_EXIT_REQUIREMENTS if lo <= projected_occupant_load <= hi
        )
        gates["exit_count"] = f"UNKNOWN — verify {required} exits required for {projected_occupant_load} occupants (IBC Table 1006.2.1)"
    else:
        gates["exit_count"] = "UNKNOWN — occupant load not provided; at 50+ occupants, minimum 2 exits required"

    # Gate 4: Construction type (informational)
    if construction_type:
        gates["construction_type"] = (
            f"Type {construction_type} — verify allowable height/area for Group E "
            "against IBC Tables 504.3, 504.4, 506.2"
        )
    else:
        gates["construction_type"] = "UNKNOWN — verify construction type against IBC Chapter 5"

    # Universal 50-occupant threshold flags (any real school will exceed this)
    load = projected_occupant_load
    if load == 0 or load >= 50:
        flags.append("Fire alarm system required at 50+ occupants (IBC 907.2.3)")
        flags.append("Panic hardware required on all egress doors at 50+ occupants — budget $300–500/door (IBC 1010.2.9)")
        if load == 0:
            flags.append("Storm shelter may be required in 250 mph wind zones at 50+ occupants (IBC 423.5)")
        elif load >= 50:
            flags.append("Storm shelter required in 250 mph wind zones (IBC 423.5)")

    flags.append("CO detectors required in all classrooms with auto-transmission to attended location (IBC 915.2.3)")
    flags.append("Enhanced acoustics required in classrooms ≤ 20,000 cu ft (IBC 1208.2)")

    return gates, flags, adjustment


def _match_building_type(description: str) -> tuple[int, str]:
    """Return (base_score, label) for a free-form building type description.

    Uses longest-keyword-match so more specific terms win over generic ones
    (e.g. "small church" beats "church").
    """
    desc = description.lower()
    best_score: int | None = None
    best_label = ""
    best_len = 0

    for score, label, keywords in _EOCCUPANCY_BUILDING_TYPES:
        for kw in keywords:
            if kw in desc and len(kw) > best_len:
                best_len = len(kw)
                best_score = score
                best_label = label

    if best_score is None:
        return 75, "Office — general (default)"
    return best_score, best_label


def _e_occupancy_timeline(score: int) -> str:
    for low, high, tl in _EOCCUPANCY_TIMELINES:
        if low <= score <= high:
            return tl
    return "Unknown"


def _e_occupancy_tier(score: int) -> int:
    if score == 0:
        return 1
    if score <= 42:
        return 2
    if score <= 69:
        return 3
    if score <= 89:
        return 4
    return 5


# ─────────────────────────────────────────────────────────────────────────────
# SCHOOL APPROVAL SKILL DATA
# ─────────────────────────────────────────────────────────────────────────────

_SCHOOL_APPROVAL_STEPS: dict[str, str] = {
    "NONE": (
        "1. File Articles of Incorporation or LLC formation documents\n"
        "2. Obtain standard local business license\n"
        "3. Notify local fire marshal and schedule occupancy inspection\n"
        "4. Post required notices and begin operations"
    ),
    "REGISTRATION_SIMPLE": (
        "1. Register private school with state Department of Education (online portal)\n"
        "2. Submit curriculum overview and student enrollment plan\n"
        "3. Obtain standard local business license\n"
        "4. Pass health and fire inspections\n"
        "5. Begin operations after registration confirmation"
    ),
    "CERTIFICATE_OR_APPROVAL_REQUIRED": (
        "1. Apply for private school Certificate of Approval from state education department\n"
        "2. Submit detailed curriculum, staff credentials, and facility documentation\n"
        "3. Schedule and pass state facility inspection\n"
        "4. Obtain certificate of approval before opening (gating requirement)\n"
        "5. Maintain annual compliance reporting and renewal"
    ),
    "LICENSE_REQUIRED": (
        "1. Apply for private school license with state education department\n"
        "2. Demonstrate compliance with state educational standards and staffing requirements\n"
        "3. Undergo facility inspection and health review\n"
        "4. Obtain license before opening (gating requirement)\n"
        "5. Maintain license with annual renewal"
    ),
    "LOCAL_APPROVAL_REQUIRED": (
        "1. Submit application to local school committee or board of education\n"
        "2. Present curriculum, educational plan, and facility details at public hearing\n"
        "3. Obtain local board approval (gating requirement)\n"
        "4. Register with state Department of Education after local approval\n"
        "5. Pass health and fire inspections\n"
        "6. Maintain annual reporting requirements"
    ),
    "COMPLEX_OR_OVERSIGHT": (
        "1. Retain legal counsel specializing in state education law\n"
        "2. Submit comprehensive application to state Board of Education\n"
        "3. Undergo curriculum review and staff credential verification\n"
        "4. Complete facility inspections and compliance review\n"
        "5. Attend state board review hearing\n"
        "6. Obtain state approval before opening (gating requirement — 12+ months)\n"
        "7. Maintain ongoing state oversight and annual reporting"
    ),
}

_ALPHA_WORKED_STATE_REFERENCES: dict[str, str] = {
    "TX": "Texas",
    "CA": "California",
    "FL": "Florida",
    "NC": "North Carolina",
    "VA": "Virginia via the DC-MD market (Chantilly, Bethesda)",
    "MD": "Maryland via the Washington-Hagerstown market",
    "NY": "New York",
    "AZ": "Arizona",
    "IL": "Illinois",
    "GA": "Georgia",
    "MA": "Massachusetts",
    "OR": "Oregon",
    "WA": "Washington",
    "TN": "Tennessee",
    "OK": "Oklahoma",
    "RI": "Rhode Island via Providence",
    "CO": "Colorado",
    "MT": "Montana via Bozeman",
    "UT": "Utah via Park City",
    "CT": "Connecticut via Greenwich",
    "PR": "Puerto Rico",
}

_ALPHA_OPERATING_STATES: frozenset[str] = frozenset({
    "TX",
    "CA",
    "FL",
    "VA",
    "AZ",
    "NC",
    "GA",
})


def _school_approval_exec_status(state_upper: str, approval_type: str) -> str:
    """Return the executive-summary education approval status label."""
    if approval_type == "NONE":
        return "Not required"
    if state_upper in _ALPHA_OPERATING_STATES:
        return "Required and have done"
    return "Required have not done"


def _school_approval_alpha_reference(state_upper: str) -> tuple[str, bool, bool]:
    """Return the Alpha state-history note plus worked/operating flags."""
    worked_note = _ALPHA_WORKED_STATE_REFERENCES.get(state_upper)
    is_operating = state_upper in _ALPHA_OPERATING_STATES

    if is_operating and worked_note:
        return (
            f"Alpha currently operates in {state_upper} and has prior execution history in {worked_note}.",
            True,
            True,
        )
    if worked_note:
        return f"Alpha has worked in {worked_note}.", True, False
    return "No Alpha state history reference recorded in this tool.", False, False


def _school_zone(score: int) -> str:
    if score >= 80:
        return "GREEN"
    if score >= 41:
        return "YELLOW"
    return "RED"


def _clear_document_body(
    gc: GoogleClient,
    *,
    doc_id: str,
) -> None:
    """Delete all body content from an existing Google Doc before rebuilding it."""
    doc = gc.docs_service.documents().get(documentId=doc_id).execute()
    body_content = doc.get("body", {}).get("content", [])
    if not body_content:
        return
    end_index = int(body_content[-1].get("endIndex", 1))
    if end_index <= 2:
        return
    gc.docs_service.documents().batchUpdate(
        documentId=doc_id,
        body={
            "requests": [{
                "deleteContentRange": {
                    "range": {
                        "startIndex": 1,
                        "endIndex": end_index - 1,
                    }
                }
            }]
        },
    ).execute()


def _get_dd_report_metadata(
    gc: GoogleClient,
    *,
    doc_id: str,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        metadata = gc.get_file_metadata(
            doc_id,
            fields="id,name,modifiedTime,webViewLink,appProperties",
        )
    except Exception as exc:  # noqa: BLE001 - fail closed in the overwrite guard
        logger.warning("Could not read DD report metadata for %s: %s", doc_id, exc)
        return dict(fallback or {})
    return metadata if isinstance(metadata, dict) else dict(fallback or {})


def _get_dd_report_revision_id(gc: GoogleClient, *, doc_id: str) -> str:
    try:
        document = gc.get_document(doc_id)
    except Exception as exc:  # noqa: BLE001 - fail closed in the overwrite guard
        logger.warning("Could not read DD report revision for %s: %s", doc_id, exc)
        return ""
    return str(document.get("revisionId") or "").strip()


def _dd_report_overwrite_guard(
    gc: GoogleClient,
    *,
    existing_doc: dict[str, Any],
) -> dict[str, Any]:
    """Return whether an existing DD report can be rebuilt in place."""
    doc_id = str(existing_doc.get("id") or "").strip()
    metadata = _get_dd_report_metadata(gc, doc_id=doc_id, fallback=existing_doc)
    app_properties = metadata.get("appProperties")
    if not isinstance(app_properties, dict):
        app_properties = {}

    automation_revision_id = str(
        app_properties.get(DDR_AUTOMATION_REVISION_ID_KEY) or ""
    ).strip()
    modified_raw = str(metadata.get("modifiedTime") or existing_doc.get("modifiedTime") or "").strip()
    active_doc_url = str(metadata.get("webViewLink") or existing_doc.get("webViewLink") or "")
    if not automation_revision_id:
        return {
            "status": "blocked",
            "reason": "missing_automation_revision",
            "message": "Active DDR has no automation-owned revision; candidate update required",
            "active_doc_id": doc_id,
            "active_doc_url": active_doc_url,
            "active_modified_time": modified_raw,
            "automation_revision_id": automation_revision_id,
        }
    current_revision_id = _get_dd_report_revision_id(gc, doc_id=doc_id)
    if not current_revision_id:
        return {
            "status": "blocked",
            "reason": "missing_current_revision",
            "message": "Active DDR current revision could not be verified; candidate update required",
            "active_doc_id": doc_id,
            "active_doc_url": active_doc_url,
            "active_modified_time": modified_raw,
            "automation_revision_id": automation_revision_id,
            "current_revision_id": current_revision_id,
        }
    if current_revision_id != automation_revision_id:
        return {
            "status": "blocked",
            "reason": "content_revision_changed",
            "message": "Active DDR revision changed after the last automation-owned write",
            "active_doc_id": doc_id,
            "active_doc_url": active_doc_url,
            "active_modified_time": modified_raw,
            "automation_revision_id": automation_revision_id,
            "current_revision_id": current_revision_id,
        }
    return {
        "status": "safe",
        "reason": "automation_owned",
        "message": "Active DDR revision matches the last automation-owned write",
        "active_doc_id": doc_id,
        "active_doc_url": active_doc_url,
        "active_modified_time": modified_raw,
        "automation_revision_id": automation_revision_id,
        "current_revision_id": current_revision_id,
    }


def _mark_dd_report_automation_write(
    gc: GoogleClient,
    *,
    doc_id: str,
    role: str,
    source_doc_id: str = "",
) -> dict[str, Any]:
    now = datetime.now(UTC)
    revision_id = _get_dd_report_revision_id(gc, doc_id=doc_id)
    properties = {
        DDR_AUTOMATION_UPDATED_AT_KEY: now.isoformat(),
        DDR_AUTOMATION_DOC_ROLE_KEY: role,
    }
    if revision_id:
        properties[DDR_AUTOMATION_REVISION_ID_KEY] = revision_id
    if source_doc_id:
        properties[DDR_AUTOMATION_SOURCE_DOC_ID_KEY] = source_doc_id
    try:
        return gc.update_file_app_properties(doc_id, properties)
    except Exception as exc:  # noqa: BLE001 - the report is still usable
        logger.warning("Could not mark DD report %s automation metadata: %s", doc_id, exc)
        return {}


def _candidate_dd_report_name(site_name: str, today_str: str) -> str:
    stamp = datetime.now(UTC).strftime("%H%M UTC")
    return f"{site_name.strip()} DD Report Candidate - {today_str} {stamp}"


# _classify_document_type moved to classifier.py (imported above as alias).
# _extract_city_from_address and _build_site_match_terms moved to utils.py.


def _find_site_docs_in_shared_folders(
    gc: GoogleClient,
    match_terms: list[str],
    *,
    site_title: str | None = None,
    site_address: str | None = None,
    drive_folder_url: str | None = None,
) -> dict[str, dict[str, Any] | None]:
    """Look up SIR / ISP / Building Inspection docs for a site.

    When *drive_folder_url* is provided, the per-site ``M1`` subfolder is
    checked first — that's where the live inbox scanner now files net-new
    SIR/BI/ISP uploads. Any doc types still missing fall back to the legacy
    shared Drive folders (SIR / ISP / Building Inspection) using:

    **Pass 1** — substring match on filenames (free, instant).
    **Pass 2** — for any missing doc types, ask GPT-4o-mini to match unmatched
    filenames against the site (handles non-standard naming).

    Returns ``{"sir": file_dict|None, "isp": file_dict|None, "building_inspection": file_dict|None}``.
    """
    settings = get_settings()
    folder_map: dict[str, str] = {
        "sir": settings.sir_folder_id,
        "isp": settings.isp_folder_id,
        "building_inspection": settings.building_inspection_folder_id,
    }

    result: dict[str, dict[str, Any] | None] = {
        "sir": None,
        "isp": None,
        "building_inspection": None,
    }

    # Pass 0 — check the per-site M1 folder first when we know the site folder.
    # Files freshly uploaded by the inbox scanner land here, so M1 wins over
    # the legacy shared folders for the doc types it covers.
    if drive_folder_url:
        try:
            m1_folder_id, _m1_url = _resolve_m1_folder(gc, drive_folder_url)
        except Exception as e:
            logger.warning(
                "Failed to resolve M1 folder for '%s' (%s): %s",
                site_title or "",
                drive_folder_url,
                e,
            )
            m1_folder_id = None
        if m1_folder_id:
            try:
                m1_docs = _list_m1_documents_by_type(gc, m1_folder_id)
            except Exception as e:
                logger.warning(
                    "Failed to list M1 folder %s for '%s': %s",
                    m1_folder_id,
                    site_title or "",
                    e,
                )
                m1_docs = {}
            for doc_type in result:
                m1_file = m1_docs.get(doc_type)
                if m1_file is not None:
                    result[doc_type] = {**m1_file, "doc_type": doc_type}

    # Keep track of all files per folder for the LLM fallback pass
    all_files_by_type: dict[str, list[dict[str, Any]]] = {}

    for doc_type, folder_id in folder_map.items():
        if result.get(doc_type) is not None:
            # Already found in M1; skip the shared-folder scan for this type.
            continue
        if not folder_id:
            continue
        try:
            # Use recursive listing (building inspection has subfolders)
            if doc_type == "building_inspection":
                files = gc.list_files_recursive(folder_id, max_depth=1)
            else:
                files = gc.list_files_in_folder(folder_id)

            all_files_by_type[doc_type] = files

            # Pass 1: substring match — choose the strongest site-specific filename.
            best_match = _pick_best_site_match(
                files,
                match_terms,
                site_title=site_title,
                site_address=site_address,
            )
            if best_match is not None:
                result[doc_type] = {**best_match, "doc_type": doc_type}

        except Exception as e:
            logger.warning(
                "Failed to list shared %s folder (%s): %s", doc_type, folder_id, e
            )

    # Pass 2: LLM site-matching for missing doc types
    if site_title:
        for doc_type in ["sir", "isp", "building_inspection"]:
            if result[doc_type] is not None:
                continue
            files = all_files_by_type.get(doc_type, [])
            if not files:
                continue

            filenames = [f.get("name", "") for f in files if f.get("name")]
            llm_matches = match_file_to_site_llm(filenames, site_title, site_address)

            if llm_matches:
                # Pick highest confidence match, preferring PDF over converted Google Doc
                best_fn = max(llm_matches, key=llm_matches.get)  # type: ignore[arg-type]
                matched_files = [f for f in files if f.get("name") == best_fn]
                if matched_files:
                    best_file = _pick_best_site_match(
                        matched_files,
                        [best_fn],
                        site_title=site_title,
                        site_address=site_address,
                    )
                    if best_file is None:
                        logger.warning(
                            "Ignoring weak LLM site match for '%s': %s",
                            site_title,
                            best_fn,
                        )
                        continue
                    result[doc_type] = {**best_file, "doc_type": doc_type}
                    logger.info(
                        "LLM matched '%s' to site '%s' for %s (conf=%.2f)",
                        best_fn, site_title, doc_type, llm_matches[best_fn],
                    )

    return result


def _pick_best_site_match(
    files: list[dict[str, Any]],
    match_terms: list[str],
    *,
    site_title: str | None = None,
    site_address: str | None = None,
) -> dict[str, Any] | None:
    """Return the strongest filename match for a site from a file list."""
    needles = [t.lower() for t in match_terms if t]
    matches = [
        f for f in files
        if any(needle in f.get("name", "").lower() for needle in needles)
    ]
    if not matches:
        return None

    if not site_title:
        pdf_matches = [f for f in matches if f.get("mimeType") == PDF_MIME]
        return pdf_matches[0] if pdf_matches else matches[0]

    scored: list[tuple[int, int, dict[str, Any]]] = []
    for file_info in matches:
        score = score_site_match_strength(
            file_info.get("name", ""),
            site_title,
            site_address,
        )
        scored.append((score, int(file_info.get("mimeType") == PDF_MIME), file_info))

    best_score, _, best_file = max(scored, key=lambda item: (item[0], item[1]))
    if best_score < MIN_SITE_MATCH_SCORE:
        logger.warning(
            "Rejecting weak shared-folder filename match for '%s': %s (score=%d)",
            site_title,
            best_file.get("name", ""),
            best_score,
        )
        return None
    return {**best_file, "site_match_score": best_score}


def _register_file_read_context(
    files: list[dict[str, Any]],
    *,
    site_title: str,
    site_address: str | None,
) -> None:
    """Store site context so read_drive_document can validate extracted text."""
    if not site_title.strip():
        return
    for file_info in files:
        file_id = str(file_info.get("id", "")).strip()
        if not file_id:
            continue
        _READ_CONTEXT_BY_FILE_ID[file_id] = {
            "site_title": site_title.strip(),
            "site_address": (site_address or "").strip(),
            "doc_type": str(file_info.get("doc_type", "")).strip(),
        }


def _extract_text_from_docx_bytes(docx_bytes: bytes) -> str:
    """Extract plain text from a .docx file without falling back to byte decode."""
    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as archive:
            document_xml = archive.read("word/document.xml")
    except KeyError as exc:
        raise ValueError("DOCX archive missing word/document.xml") from exc

    root = ElementTree.fromstring(document_xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []

    for para in root.findall(".//w:p", namespace):
        parts: list[str] = []
        for node in para.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "t":
                parts.append(node.text or "")
            elif tag == "tab":
                parts.append("\t")
            elif tag in {"br", "cr"}:
                parts.append("\n")
        para_text = "".join(parts).strip()
        if para_text:
            paragraphs.append(para_text)

    return "\n".join(paragraphs)


def _validate_document_site_context(
    file_name: str,
    text_content: str,
    *,
    site_title: str,
    site_address: str | None = None,
    doc_type: str = "",
) -> tuple[bool, list[str], bool]:
    """Validate that document text appears to belong to the requested site."""
    if not site_title.strip():
        return True, [], False

    name_score = score_site_match_strength(file_name, site_title, site_address)
    text_score = score_site_match_strength(text_content[:10_000], site_title, site_address)
    best_score = max(name_score, text_score)
    if best_score >= MIN_SITE_MATCH_SCORE:
        return True, [], True

    doc_label = doc_type.replace("_", " ").title() if doc_type else "Document"
    warning = (
        f"{doc_label} '{file_name}' did not contain expected site identifiers for "
        f"{site_title} and was excluded from this run."
    )
    return False, [warning], False


def _list_ai_generated_site_reports(site_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return report-relevant artifacts from a recursive site-folder listing."""
    reports: list[dict[str, Any]] = []
    for file_info in site_files:
        annotated = {**file_info, "doc_type": _classify_document_type(file_info.get("name", ""))}
        if annotated["doc_type"] == "dd_report":
            continue
        if annotated["doc_type"] not in SITE_FOLDER_DOC_TYPES:
            continue
        annotated["reference_origin"] = (
            "ai_generated" if annotated["doc_type"] in AI_GENERATED_DOC_TYPES else "site_source"
        )
        reports.append(annotated)
    return reports


def _pick_preferred_report(
    existing: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Prefer the newest modified report artifact when duplicates exist."""
    if existing is None:
        return candidate
    existing_time = str(existing.get("modifiedTime", ""))
    candidate_time = str(candidate.get("modifiedTime", ""))
    if candidate_time and candidate_time > existing_time:
        return candidate
    return existing


def _make_google_client() -> GoogleClient:
    """Initialise and return a GoogleClient using settings from config."""
    settings = get_settings()
    return GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )


@mcp.tool()
async def lookup_rhodes_site_owner(
    site_name: str = "",
    site_address: str = "",
    site_id: str = "",
    slug: str = "",
) -> dict[str, Any]:
    """Resolve current site context from Rhodes without mutating Rhodes.

    The tool returns report_data_fields that can be merged into create_dd_report
    so meta.prepared_by comes from Rhodes p1Dri and meta.drive_folder_url comes
    from the linked Rhodes Google Drive folder when one exists.
    """
    logger.info(
        "Tool called: lookup_rhodes_site_owner - site=%s address=%s site_id=%s slug=%s",
        site_name,
        site_address,
        site_id,
        slug,
    )
    return await asyncio.to_thread(
        _lookup_rhodes_site_owner,
        site_name=site_name,
        site_address=site_address,
        site_id=site_id,
        slug=slug,
    )


@mcp.tool()
async def portfolio_automation_gap_snapshot(
    max_sites: int = 100,
    include_clean: bool = True,
) -> dict[str, Any]:
    """Read Rhodes and summarize portfolio-level automation/data-quality gaps.

    The snapshot is read-only and uses Rhodes as the backing record for active
    sites, linked Drive folders, registered documents, site notes, and review
    tasks.
    """
    logger.info(
        "Tool called: portfolio_automation_gap_snapshot - max_sites=%s include_clean=%s",
        max_sites,
        include_clean,
    )
    return await asyncio.to_thread(
        build_portfolio_automation_gap_snapshot,
        max_sites=max_sites,
        include_clean=include_clean,
    )


@mcp.tool()
async def assign_p1_accountable(
    site_name: str,
    school_type: str,
    state: str,
    city: str = "",
) -> dict[str, Any]:
    """Assign a P1 Accountable person to a new site using the three-rule scoring engine.

    Rule 1 (flight scoring, requires SerpAPI key + city):
      Same-day viable (depart ≤ 7am, return ≥ 8pm, ≤ 3hr each way): +50
      Nonstop flight: +30
      Strongly preferred airline available: +15 / not available: -30
      Preferred airline available: +10
      Load penalty per existing assigned site: -5

    Rule 2: Contact in same state → fewest total sites wins.
    Rule 3: Nearest state (Haversine) → fewest sites tiebreaker.

    Growth/Flagship (school_type "250", "1000", "growth", "flagship") auto-assigns
    Thomas Barrow + Israe Zizaoui. JC Fisher is excluded.

    Args:
        site_name: Human-readable site name (e.g., "Alpha Austin").
        school_type: Site school type — "micro", "250", "1000", "growth",
            "flagship", "jc fisher", or "unknown".
        state: Two-letter US state abbreviation (e.g., "TX").
        city: City name — enables Rule 1 flight scoring when provided.

    Returns:
        Dict with status, rule, assignee_name, assignee_email, score, reasoning.
    """
    logger.info(
        "Tool called: assign_p1_accountable — site=%s type=%s state=%s city=%s",
        site_name, school_type, state, city,
    )

    if not school_type or not school_type.strip():
        return {"status": "error", "error": "Missing parameter", "message": "school_type is required"}
    if not state or not state.strip():
        return {"status": "error", "error": "Missing parameter", "message": "state is required"}

    def _work() -> dict[str, Any]:
        settings = get_settings()

        result = assign_p1(
            school_type=school_type,
            city=city,
            state=state,
            settings=settings,
            all_site_records=[],
        )
        result["site_name"] = site_name
        return result

    return await asyncio.to_thread(_work)


@mcp.tool()
async def list_drive_documents(
    drive_folder_url: str, site_name: str = "", site_address: str = ""
) -> dict[str, Any]:
    """List matched shared source reports plus site-folder artifacts.

    Searches the shared SIR, ISP, and Building Inspection folders when *site_name*
    is provided. From the site folder, returns report-relevant artifacts such as
    Block Plans, DD reports, E-Occupancy reports, School Approval reports,
    Capacity Brainlift reports, RayCon Scenario reports, Opening Plans, and
    report traces.

    Args:
        drive_folder_url: Google Drive folder URL.
        site_name: Optional site name used to match docs in shared Drive folders
            (SIR, ISP, Building Inspection).
        site_address: Optional address used to strengthen shared-folder matching.

    Returns:
        Dict with lists of files found in the site folder and shared folders.
    """
    logger.info("Tool called: list_drive_documents")
    logger.info(
        "list_drive_documents params: drive_folder_url=%s, site_name=%s",
        drive_folder_url,
        site_name,
    )

    if not drive_folder_url or not drive_folder_url.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "drive_folder_url must be a non-empty string",
        }

    folder_id = extract_folder_id_from_url(drive_folder_url)
    if not folder_id:
        return {
            "status": "error",
            "error": "Invalid folder URL",
            "message": (
                f"Could not extract a Google Drive folder ID from: {drive_folder_url}. "
                "Expected a URL like https://drive.google.com/drive/folders/FOLDER_ID"
            ),
        }
    if is_drive_root_folder_id(folder_id):
        return {
            "status": "error",
            "error": "Invalid folder URL",
            "message": (
                "drive_folder_url points to Google Drive root. Provide the site Drive "
                "folder URL so source discovery can read the site's M1 folder."
            ),
        }

    def _work() -> dict[str, Any]:
        try:
            shared_folder_files: list[dict[str, Any]] = []
            address = site_address.strip() or None
            search_shared_folders = bool(site_name.strip())
            gc = _make_google_client()

            all_site_files_raw = gc.list_files_recursive(folder_id, max_depth=2)
            site_files = _list_ai_generated_site_reports(all_site_files_raw)
            logger.info(
                "Found %d site-folder report artifacts in folder (recursive, max_depth=2) %s",
                len(site_files), folder_id,
            )

            if site_name.strip() and search_shared_folders:
                match_terms = _build_site_match_terms(site_name.strip(), address)
                shared_docs = _find_site_docs_in_shared_folders(
                    gc, match_terms,
                    site_title=site_name.strip(), site_address=address,
                    drive_folder_url=drive_folder_url,
                )
                for _, doc in shared_docs.items():
                    if doc is not None:
                        shared_folder_files.append({**doc, "reference_origin": "shared_source"})
                if shared_folder_files:
                    logger.info(
                        "Found %d source reports in shared folders for '%s'",
                        len(shared_folder_files),
                        site_name,
                    )
                _register_file_read_context(
                    shared_folder_files + site_files,
                    site_title=site_name.strip(),
                    site_address=address,
                )
            elif site_name.strip():
                _register_file_read_context(
                    site_files,
                    site_title=site_name.strip(),
                    site_address=address,
                )

            total_files = len(site_files) + len(shared_folder_files)

            response: dict[str, Any] = {
                "status": "success",
                "folder_id": folder_id,
                "drive_folder_url": drive_folder_url,
                "site_folder_files": site_files,
                "shared_folder_files": shared_folder_files,
                "total_file_count": total_files,
                "message": (
                    f"Found {len(site_files)} AI-generated reports in the site folder, "
                    f"and {len(shared_folder_files)} source reports in shared folders "
                    f"({total_files} total)"
                ),
            }
            return response

        except Exception as e:
            logger.error("Failed to list Drive documents: %s", e)
            return {
                "status": "error",
                "error": "Google Drive API error",
                "message": str(e),
            }

    return await asyncio.to_thread(_work)


@mcp.tool()
async def read_drive_document(file_id: str, file_name: str) -> dict[str, Any]:
    """Read and return the full text content of a Google Drive file.

    Supports:
    - Google Docs: exported as plain text via Drive API
    - PDFs: downloaded and text extracted using pypdf
    - DOCX files: parsed directly from the WordprocessingML archive
    - Plain text files: downloaded directly

    Args:
        file_id: Google Drive file ID (from list_drive_documents).
        file_name: File name (used to determine how to extract text and for logging).

    Returns:
        Dict with the extracted text content of the document.
    """
    logger.info("Tool called: read_drive_document")
    logger.info(
        "read_drive_document params: file_id=%s, file_name=%s", file_id, file_name
    )

    if not file_id or not file_id.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "file_id must be a non-empty string",
        }

    def _work() -> dict[str, Any]:
        try:
            gc = _make_google_client()

            logger.info("Fetching metadata for file: %s", file_id)
            try:
                file_metadata: dict[str, Any] = (
                    gc.drive_service.files()
                    .get(
                        fileId=file_id,
                        fields="id,name,mimeType,size",
                        supportsAllDrives=True,
                    )
                    .execute()
                )
            except Exception as meta_err:
                logger.warning(
                    "Could not fetch metadata for %s, inferring type from name: %s",
                    file_id,
                    meta_err,
                )
                file_metadata = {"mimeType": _infer_mime_from_name(file_name)}

            mime_type: str = file_metadata.get("mimeType", "")
            logger.info("File %s has MIME type: %s", file_id, mime_type)

            text_content: str = ""
            source_quality_warnings: list[str] = []
            source_usable = True
            unreadable = False
            site_match_verified = False

            if mime_type in EXPORTABLE_MIME_TYPES:
                text_content = gc.export_google_doc_as_text(file_id)

            elif mime_type == PDF_MIME or file_name.lower().endswith(".pdf"):
                pdf_bytes = gc.download_file_bytes(file_id)
                text_content = extract_text_from_pdf_bytes(pdf_bytes)
                if not text_content:
                    logger.warning(
                        "PDF text extraction returned empty for %s — may be image-only", file_id
                    )
                    text_content = (
                        "[PDF text extraction returned no text. "
                        "This may be an image-only PDF that requires OCR.]"
                    )

            elif mime_type == DOCX_MIME or file_name.lower().endswith(".docx"):
                docx_bytes = gc.download_file_bytes(file_id)
                try:
                    text_content = _extract_text_from_docx_bytes(docx_bytes)
                except (ValueError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
                    unreadable = True
                    source_usable = False
                    warning = (
                        f"Document '{file_name}' could not be parsed as a DOCX file "
                        f"and was excluded from this run: {exc}"
                    )
                    source_quality_warnings.append(warning)
                    text_content = f"[Document unreadable -- {warning}]"
                else:
                    if not text_content.strip():
                        unreadable = True
                        source_usable = False
                        warning = (
                            f"Document '{file_name}' contained no readable DOCX text "
                            "and was excluded from this run."
                        )
                        source_quality_warnings.append(warning)
                        text_content = f"[Document unreadable -- {warning}]"

            elif mime_type.startswith("text/") or file_name.lower().endswith(
                (".txt", ".md", ".csv")
            ):
                raw_bytes = gc.download_file_bytes(file_id)
                text_content = raw_bytes.decode("utf-8", errors="replace")

            else:
                logger.warning(
                    "Unsupported MIME type %s for file %s — attempting generic download",
                    mime_type,
                    file_id,
                )
                try:
                    raw_bytes = gc.download_file_bytes(file_id)
                    text_content = raw_bytes.decode("utf-8", errors="replace")
                except Exception as dl_err:
                    logger.error("Could not download file %s: %s", file_id, dl_err)
                    text_content = (
                        f"[Could not extract text from file with MIME type: {mime_type}]"
                    )

            context = _READ_CONTEXT_BY_FILE_ID.get(file_id)
            if context and text_content.strip() and source_usable:
                source_usable, validation_warnings, site_match_verified = (
                    _validate_document_site_context(
                        file_name,
                        text_content,
                        site_title=context.get("site_title", ""),
                        site_address=context.get("site_address") or None,
                        doc_type=context.get("doc_type", ""),
                    )
                )
                if validation_warnings:
                    source_quality_warnings.extend(validation_warnings)
                if not source_usable and validation_warnings:
                    text_content = f"[Source excluded -- {validation_warnings[0]}]"

            logger.info(
                "read_drive_document: extracted %d characters from %s", len(text_content), file_name
            )

            max_chars = 50_000
            truncated = False
            original_length = len(text_content)
            if original_length > max_chars:
                text_content = text_content[:max_chars]
                truncated = True
                logger.warning(
                    "Truncated %s from %d to %d characters", file_name, original_length, max_chars
                )

            return {
                "status": "success",
                "file_id": file_id,
                "file_name": file_name,
                "mime_type": mime_type,
                "character_count": original_length,
                "truncated": truncated,
                "text": text_content,
                "content": text_content,
                "source_usable": source_usable,
                "site_match_verified": site_match_verified,
                "source_quality_warnings": source_quality_warnings,
                "unreadable": unreadable,
                "message": (
                    f"Successfully read {original_length} characters from '{file_name}'"
                    + (f" (truncated to {max_chars} chars)" if truncated else "")
                ),
            }

        except Exception as e:
            logger.error("Failed to read Drive document %s: %s", file_id, e)
            return {
                "status": "error",
                "error": "Failed to read document",
                "file_id": file_id,
                "file_name": file_name,
                "message": str(e),
            }

    return await asyncio.to_thread(_work)


def _infer_mime_from_name(file_name: str) -> str:
    """Infer MIME type from file name extension."""
    name_lower = file_name.lower()
    if name_lower.endswith(".pdf"):
        return PDF_MIME
    if name_lower.endswith(".docx"):
        return DOCX_MIME
    if name_lower.endswith(".doc"):
        return DOC_MIME
    if name_lower.endswith(".txt"):
        return "text/plain"
    return "application/octet-stream"


@mcp.tool()
async def apply_e_occupancy_skill(
    building_type_description: str,
    stories: int,
    floor_level: int = 1,
    shared_hvac: bool = False,
    shared_egress: bool = False,
    building_management_approval_required: bool = False,
    no_dedicated_entrance: bool = False,
    no_outdoor_space: bool = False,
    shared_parking: bool = False,
    incompatible_tenants: bool = False,
    ibc_occupancy_group: str = "",
    fire_area_sqft: int = 0,
    has_below_grade_space: bool = False,
    already_sprinklered: bool = False,
    construction_type: str = "",
    max_travel_distance_ft: int = 0,
    existing_exit_count: int = 0,
    projected_occupant_load: int = 0,
    site_name: str = "",
    drive_folder_url: str = "",
) -> dict[str, Any]:
    """Apply the E-Occupancy Skill to score a building for educational use conversion.

    Evaluates the building's current use against the Alpha School E-Occupancy scoring
    matrix and returns a complete structured assessment including IBC compliance gates.
    Call this tool in Step 4 after identifying the building's current use from source
    documents or supplied site metadata.

    If site_name and drive_folder_url are provided, the full assessment is automatically
    saved as a Google Doc in the site's M1 subfolder and the doc_url is returned.

    Args:
        building_type_description: Free-form description of current building use
            (e.g., "3-story medical office building", "retail strip center", "gym").
        stories: Total number of stories in the building.
        floor_level: Floor level of the tenant space (1 = ground floor). Defaults to 1.
        shared_hvac: True if HVAC is shared with other tenants.
        shared_egress: True if building egress / entrance is shared.
        building_management_approval_required: True if landlord/mgmt approval needed.
        no_dedicated_entrance: True if no dedicated street-level entrance exists.
        no_outdoor_space: True if no access to outdoor space for students.
        shared_parking: True if parking is shared with other tenants.
        incompatible_tenants: True if other tenants are incompatible with school use.
        ibc_occupancy_group: Source building's IBC occupancy group (B, A-1, A-2, A-3,
            M, F, S, R, I, H, E). Overrides score for I (cap 20) and H (score 0).
        fire_area_sqft: Fire area in square feet. Triggers mandatory sprinklers at 12,000 sq ft
            (IBC 903.2.3). Pass 0 if unknown.
        has_below_grade_space: True if any portion is below the level of exit discharge.
            Triggers mandatory sprinklers regardless of size (IBC 903.2.3).
        already_sprinklered: True if building already has a compliant sprinkler system.
        construction_type: IBC construction type (I-A, I-B, II-A, II-B, III-A, III-B,
            IV, V-A, V-B). Informational — flags for cross-reference against IBC Chapter 5.
        max_travel_distance_ft: Maximum travel distance from most remote classroom to
            nearest exit. Compare against 200 ft (non-sprinklered) or 250 ft (sprinklered)
            per IBC Table 1017.2.
        existing_exit_count: Number of compliant exits in the building. Used to verify
            against IBC Table 1006.2.1 requirements based on occupant load.
        projected_occupant_load: Expected total occupant load. Drives exit count check
            and 50-occupant threshold flag (fire alarm, panic hardware, storm shelter).
        site_name: Site name — pass to auto-publish the assessment as a Google Doc.
        drive_folder_url: Site Drive folder URL — pass to auto-publish.

    Returns:
        Dict with score, zone, tier, timeline, confidence, ibc_gates, ibc_flags,
        doc_url (if auto-published), and ready-to-use report_data_fields for q2.e_occupancy_*.
    """
    logger.info(
        "Tool called: apply_e_occupancy_skill — building_type=%s, stories=%d",
        building_type_description,
        stories,
    )

    try:
        hosted_skill = load_ease_conversion_skill()
    except EaseConversionSkillError as exc:
        logger.error("Failed to load Ops-Skills ease-of-conversion skill: %s", exc)
        return {
            "status": "error",
            "error": "Ops-Skills ease-of-conversion unavailable",
            "message": str(exc),
        }

    base_score, matched_type = _match_building_type(building_type_description)

    # IBC group override: H → do not pursue (0), I → cap at 20
    ibc_group_label = ""
    if ibc_occupancy_group:
        group = ibc_occupancy_group.upper().strip()
        group_info = _IBC_GROUP_INFO.get(group)
        if group_info:
            ibc_group_label, cap = group_info
            if cap == 0:
                base_score = 0
            elif cap is not None and base_score > cap:
                base_score = cap

    # Apply height override (absolute ceiling, even on score-0 types this is a no-op)
    if base_score > 0:
        if stories >= 7:
            base_score = min(base_score, 20)
        elif stories >= 4:
            base_score = min(base_score, 42)

    # Apply tenant deductions only for floors 1–3 (floors 4+ already capped by height rules)
    score = base_score
    deductions: list[str] = []

    if base_score > 0 and floor_level <= 3:
        if shared_hvac:
            score += _EOCCUPANCY_TENANT_DEDUCTIONS["shared_hvac"]
            deductions.append("shared HVAC (−5)")
        if shared_egress:
            score += _EOCCUPANCY_TENANT_DEDUCTIONS["shared_egress"]
            deductions.append("shared egress (−5)")
        if building_management_approval_required:
            score += _EOCCUPANCY_TENANT_DEDUCTIONS["building_management_approval_required"]
            deductions.append("building management approval required (−5)")
        if no_dedicated_entrance:
            score += _EOCCUPANCY_TENANT_DEDUCTIONS["no_dedicated_entrance"]
            deductions.append("no dedicated entrance (−5)")
        if no_outdoor_space:
            score += _EOCCUPANCY_TENANT_DEDUCTIONS["no_outdoor_space"]
            deductions.append("no outdoor space (−5)")
        if shared_parking:
            score += _EOCCUPANCY_TENANT_DEDUCTIONS["shared_parking"]
            deductions.append("shared parking (−3)")
        if incompatible_tenants:
            score += _EOCCUPANCY_TENANT_DEDUCTIONS["incompatible_tenants"]
            deductions.append("incompatible tenants (−5)")
        # Never below 1 unless environmental hazard (score 0 from type matching)
        if score < 1:
            score = 1

    # Apply IBC compliance gate checks
    ibc_gates: dict[str, str] = {}
    ibc_flags: list[str] = []
    ibc_adjustment = 0
    if base_score > 0:
        ibc_gates, ibc_flags, ibc_adjustment = _eval_ibc_gates(
            fire_area_sqft=fire_area_sqft,
            has_below_grade_space=has_below_grade_space,
            already_sprinklered=already_sprinklered,
            max_travel_distance_ft=max_travel_distance_ft,
            existing_exit_count=existing_exit_count,
            projected_occupant_load=projected_occupant_load,
            construction_type=construction_type,
        )
        score += ibc_adjustment
        if score < 1:
            score = 1

    score = max(0, min(100, score))

    try:
        zone = hosted_skill.band_for_score(score)
    except EaseConversionSkillError as exc:
        logger.error("Invalid Ops-Skills ease-of-conversion band data: %s", exc)
        return {
            "status": "error",
            "error": "Ops-Skills ease-of-conversion invalid",
            "message": str(exc),
        }
    tier = _e_occupancy_tier(score)
    tier_label = _EOCCUPANCY_TIER_LABELS.get(tier, str(tier))
    timeline = _e_occupancy_timeline(score)

    # Confidence: HIGH if type matched clearly, MEDIUM if deductions/gates applied, LOW if default
    has_ibc_data = any([fire_area_sqft, has_below_grade_space, max_travel_distance_ft, existing_exit_count, projected_occupant_load])
    if matched_type.endswith("(default)"):
        confidence = "LOW"
    elif deductions or (has_ibc_data and ibc_adjustment < 0):
        confidence = "MEDIUM"
    else:
        confidence = "HIGH"

    deduction_note = f"Deductions: {', '.join(deductions)}." if deductions else "No tenant deductions."
    ibc_note = f"IBC gate adjustment: {ibc_adjustment}." if ibc_adjustment else "No IBC gate penalties."

    # Build IBC gate summary for report
    gate_problems = [v for v in ibc_gates.values() if v.startswith("NON-COMPLIANT") or v.startswith("YES —")]
    ibc_summary = "; ".join(gate_problems) if gate_problems else "No critical IBC gate failures identified."

    result: dict[str, Any] = {
        "status": "success",
        "matched_building_type": matched_type,
        "ibc_occupancy_group": ibc_group_label or ibc_occupancy_group or "Not provided",
        "base_score": base_score,
        "deductions_applied": deductions,
        "ibc_adjustment": ibc_adjustment,
        "final_score": score,
        "zone": zone,
        "tier": tier_label,
        "timeline": timeline,
        "confidence": confidence,
        "ease_conversion_skill_version": hosted_skill.version,
        "ease_conversion_skill_source": hosted_skill.source,
        "ease_conversion_reference_source": hosted_skill.reference_source,
        "ease_conversion_scorecard_theme_id": hosted_skill.scorecard_theme_id,
        "ibc_gates": ibc_gates,
        "ibc_flags": ibc_flags,
        "report_data_fields": {
            "q2.e_occupancy_score": str(score),
            "q2.e_occupancy_zone": zone,
            "q2.e_occupancy_tier": tier_label,
            "q2.e_occupancy_timeline": timeline,
            "q2.e_occupancy_confidence": confidence,
            "q2.e_occupancy_ibc_summary": ibc_summary,
            "q2.e_occupancy_skill_version": hosted_skill.version,
            "q2.e_occupancy_skill_source": hosted_skill.source,
            "q2.e_occupancy_reference_source": hosted_skill.reference_source,
            "q2.e_occupancy_scorecard_theme_id": hosted_skill.scorecard_theme_id,
        },
        "message": (
            f"E-Occupancy: {score}/100 — {zone} ({tier_label}, {timeline}). "
            f"Matched building type: {matched_type}. {deduction_note} {ibc_note}"
        ),
    }

    # Auto-publish to Drive if site context provided
    if site_name and drive_folder_url:
        try:
            pub = await save_skill_report(
                skill_name="E-Occupancy",
                site_name=site_name,
                drive_folder_url=drive_folder_url,
                skill_data=result,
            )
            if pub.get("status") == "success":
                result["doc_url"] = pub["doc_url"]
                result["doc_id"] = pub["doc_id"]
                logger.info("Auto-published E-Occupancy assessment: %s", pub["doc_url"])
        except Exception as e:
            logger.warning("Failed to auto-publish E-Occupancy assessment: %s", e)
            result["publish_status"] = "failed"

    return result


@mcp.tool()
async def apply_school_approval_skill(
    state: str = "",
    address: str = "",
    site_name: str = "",
    drive_folder_url: str = "",
) -> dict[str, Any]:
    """Apply the School Approval Skill to determine registration requirements for a state.

    Loads the current school-approval skill from Ops-Skills and applies its
    baseline table for Q1 — State School Registration.

    If site_name and drive_folder_url are provided, the full assessment is automatically
    saved as a Google Doc in the site's M1 subfolder and the doc_url is returned.

    Args:
        state: Two-letter US state abbreviation (e.g., "TX", "CA", "FL").
            Use "DC" for Washington D.C. Optional when address includes a state.
        address: Full site address. Preferred when available because the
            hosted school-approval skill is address-first.
        site_name: Site name — pass to auto-publish the assessment as a Google Doc.
        drive_folder_url: Site Drive folder URL — pass to auto-publish.

    Returns:
        Dict with approval_type, gating, timeline, steps, summary, doc_url (if
        auto-published), and ready-to-use report_data_fields.
    """
    logger.info("Tool called: apply_school_approval_skill — state=%s address=%s", state, address)

    state_upper = normalize_school_approval_state(state=state, address=address)
    if not state_upper:
        return {
            "status": "error",
            "error": "Missing state",
            "message": "Provide a two-letter state or a full site address with a state.",
        }

    try:
        hosted_skill = load_school_approval_skill()
    except SchoolApprovalSkillError as e:
        logger.error("Failed to load Ops-Skills school-approval skill: %s", e)
        return {
            "status": "error",
            "error": "Ops-Skills school-approval unavailable",
            "message": str(e),
        }

    baseline = hosted_skill.baselines.get(state_upper)
    data_quality_flags: list[str] = []
    if baseline is not None:
        score = baseline.score
        archetype = baseline.archetype
        approval_type = baseline.approval_type
        gating = baseline.gating
        timeline_days = baseline.timeline_days
        confidence = "HIGH"
    else:
        archetype = "UNKNOWN"
        score, approval_type, gating, timeline_days = 70, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90
        confidence = "LOW"
        data_quality_flags.append("NO_BASELINE_ROW_IN_OPS_SKILL")
        logger.warning(
            "State '%s' not in Ops-Skills school-approval baseline from %s - using default values",
            state_upper,
            hosted_skill.source,
        )

    zone = _school_zone(score)
    steps = _SCHOOL_APPROVAL_STEPS.get(
        approval_type, _SCHOOL_APPROVAL_STEPS["CERTIFICATE_OR_APPROVAL_REQUIRED"]
    )
    exec_status = _school_approval_exec_status(state_upper, approval_type)
    alpha_reference, has_worked_in_state, is_operating_in_state = _school_approval_alpha_reference(
        state_upper
    )

    if zone == "GREEN":
        summary = (
            f"{state_upper} has minimal private school requirements "
            f"({approval_type.replace('_', ' ').title()}). "
            f"Timeline: {timeline_days} days. "
            f"Executive status: {exec_status}. {alpha_reference}"
        )
    elif zone == "YELLOW":
        gating_note = " This is a gating requirement before opening." if gating else ""
        summary = (
            f"{state_upper} requires {approval_type.replace('_', ' ').title()} "
            f"for private schools. Timeline: {timeline_days} days.{gating_note} "
            f"Executive status: {exec_status}. {alpha_reference}"
        )
    else:
        summary = (
            f"{state_upper} has complex private school oversight requirements. "
            f"Gating approval required; timeline: {timeline_days}+ days. "
            f"Engage legal counsel early. Executive status: {exec_status}. {alpha_reference}"
        )

    result: dict[str, Any] = {
        "status": "success",
        "address": address,
        "state": state_upper,
        "archetype": archetype,
        "score": score,
        "score_0_100": score,
        "zone": zone,
        "approval_type": approval_type,
        "gating": gating,
        "gating_before_open": gating,
        "timeline_days": timeline_days,
        "timeline_days_preopen": {"min": 0, "likely": timeline_days, "max": timeline_days},
        "confidence": confidence,
        "data_quality_flags": data_quality_flags,
        "rules_version": hosted_skill.rules_version,
        "school_approval_skill_version": hosted_skill.version,
        "school_approval_skill_source": hosted_skill.source,
        "exec_c_edreg_status": exec_status,
        "alpha_state_reference": alpha_reference,
        "alpha_has_worked_in_state": has_worked_in_state,
        "alpha_is_operating_in_state": is_operating_in_state,
        "steps_to_allow_operation": steps,
        "state_school_registration_summary": summary,
        "report_data_fields": {
            "q1.state_school_registration": summary,
            "q1.school_approval_type": approval_type,
            "q1.school_approval_gating": str(gating).lower(),
            "q1.school_approval_zone": zone,
            "q1.school_approval_archetype": archetype,
            "q1.school_approval_timeline_days": str(timeline_days),
            "q1.school_approval_exec_status": exec_status,
            "q1.school_approval_alpha_reference": alpha_reference,
            "q1.school_approval_rules_version": hosted_skill.rules_version,
            "q1.school_approval_skill_version": hosted_skill.version,
            "q1.school_approval_skill_source": hosted_skill.source,
            "q1.steps_to_allow_operation": steps,
        },
        "message": (
            f"School approval for {state_upper}: {zone} (score {score}/100), "
            f"{approval_type}, {timeline_days}-day timeline, exec status {exec_status}."
        ),
    }

    if site_name and drive_folder_url:
        try:
            pub = await save_skill_report(
                skill_name="School Approval",
                site_name=site_name,
                drive_folder_url=drive_folder_url,
                skill_data=result,
            )
            if pub.get("status") == "success":
                result["doc_url"] = pub["doc_url"]
                result["doc_id"] = pub["doc_id"]
                logger.info("Auto-published School Approval assessment: %s", pub["doc_url"])
        except Exception as e:
            logger.warning("Failed to auto-publish School Approval assessment: %s", e)
            result["publish_status"] = "failed"

    return result


def _get_or_create_m1_folder(
    gc: GoogleClient,
    folder_id: str,
) -> dict[str, Any]:
    """Return the M1 Acquire Property folder, creating it when absent."""
    subfolders = gc.list_subfolders(folder_id)
    if subfolder := _find_preferred_m1_subfolder(subfolders):
        return subfolder
    return gc.create_folder(folder_id, M1_FOLDER_NAME)


# ---------------------------------------------------------------------------
# Opening Plan v2 skill
# ---------------------------------------------------------------------------

_OPENING_PLAN_SKILL_DIR = Path(__file__).parent.parent.parent / "docs" / "skills" / "opening-plan-v2"


def _resolve_ops_skills_dir() -> Path | None:
    """Return the shared ops-skills directory when configured or discoverable."""
    settings = get_settings()
    configured = settings.ops_skills_repo_path.strip()
    candidates: list[Path] = []
    if configured:
        configured_path = Path(configured)
        candidates.append(
            configured_path if configured_path.name == "skills" else configured_path / "skills"
        )
    sibling_repo = Path(__file__).resolve().parents[3] / "alpha-analysis-downstream-processing" / "skills"
    candidates.append(sibling_repo)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _load_opening_plan_skill_files() -> dict[str, str]:
    """Load the Opening Plan v2 SKILL.md and reference files from docs/skills/."""
    files = {
        "skill": _OPENING_PLAN_SKILL_DIR / "SKILL.md",
        "field_mapping": _OPENING_PLAN_SKILL_DIR / "references" / "field-mapping.md",
        "template_content": _OPENING_PLAN_SKILL_DIR / "references" / "template-content.md",
        "executive_mindset": _OPENING_PLAN_SKILL_DIR / "references" / "executive-mindset.md",
    }
    return {key: path.read_text(encoding="utf-8") for key, path in files.items()}


def _build_opening_plan_prompt(
    skill_files: dict[str, str],
    site_name: str,
    site_address: str,
    sir_content: str,
    school_approval_data: str = "",
    building_inspection_content: str = "",
    target_open_date: str = "",
) -> str:
    """Assemble the Claude prompt for Pass 1 (SIR baseline) of the Opening Plan."""
    sections = [
        "You are executing Pass 1 of the Opening Plan v2 skill (SIR Baseline only).",
        "Do NOT launch research agents or perform web research -- this is the deterministic pass.",
        "Produce a complete Opening Plan document using only the SIR data and reference files below.",
        "",
        "=" * 60,
        "SKILL DEFINITION (Steps 1 and 2 only -- skip Steps 2.9, 3, 3.5, 4):",
        "=" * 60,
        skill_files["skill"],
        "",
        "=" * 60,
        "FIELD MAPPING REFERENCE:",
        "=" * 60,
        skill_files["field_mapping"],
        "",
        "=" * 60,
        "TEMPLATE CONTENT (section-by-section structure to follow):",
        "=" * 60,
        skill_files["template_content"],
        "",
        "=" * 60,
        "EXECUTIVE MINDSET (quality bar to meet):",
        "=" * 60,
        skill_files["executive_mindset"],
        "",
        "=" * 60,
        "SITE DATA:",
        "=" * 60,
        f"Site Name: {site_name}",
        f"Address: {site_address}",
    ]
    if target_open_date:
        sections.append(f"Target Open Date: {target_open_date}")
    sections += [
        "",
        "=" * 60,
        "SIR DOCUMENT (primary data source):",
        "=" * 60,
        sir_content,
    ]
    if school_approval_data:
        sections += [
            "",
            "=" * 60,
            "SCHOOL APPROVAL REPORT (pre-enriches Edu Regulatory section):",
            "=" * 60,
            school_approval_data,
        ]
    if building_inspection_content:
        sections += [
            "",
            "=" * 60,
            "BUILDING INSPECTION REPORT (enriches Construction section):",
            "=" * 60,
            building_inspection_content,
        ]
    sections += [
        "",
        "=" * 60,
        "OUTPUT INSTRUCTIONS:",
        "=" * 60,
        "Produce the complete Opening Plan in markdown following the template structure exactly.",
        "Every section must be populated -- use [PLACEHOLDER -- reason] for items that cannot",
        "be determined from the SIR (refer to the Placeholder Inventory in field-mapping.md).",
        "Do not add any preamble or closing remarks -- output only the plan content itself.",
    ]
    return "\n".join(sections)


@mcp.tool()
async def apply_opening_plan_skill(
    site_name: str,
    site_address: str,
    sir_content: str,
    drive_folder_url: str = "",
    school_approval_data: str = "",
    building_inspection_content: str = "",
    target_open_date: str = "",
) -> dict[str, Any]:
    """Generate an Opening Plan (Permitting Plan) Google Doc for a site using the SIR.

    Runs Pass 1 of the Opening Plan v2 skill: deterministic SIR baseline mapping.
    Produces a complete plan with every section filled -- no web research.
    If drive_folder_url is provided, publishes the plan as a Google Doc in the M1 subfolder.

    Args:
        site_name: Site name (e.g., "Alpha Austin").
        site_address: Full property address.
        sir_content: Full text of the SIR document (read via read_drive_document).
        drive_folder_url: Site's Google Drive folder URL. Triggers auto-publish if set.
        school_approval_data: Optional School Approval report text for edu regulatory section.
        building_inspection_content: Optional Building Inspection text.
        target_open_date: Optional target open date if known from site record.

    Returns:
        Dict with status, plan_content (markdown), and doc_url/doc_id if published.
    """
    logger.info("Tool called: apply_opening_plan_skill - site=%s", site_name)

    if not site_name or not site_address or not sir_content:
        return {
            "status": "error",
            "error": "Missing required parameters",
            "message": "site_name, site_address, and sir_content are all required",
        }

    def _work() -> dict[str, Any]:
        import anthropic

        settings = get_settings()
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return {
                "status": "error",
                "error": "ANTHROPIC_API_KEY not set",
                "message": "Set ANTHROPIC_API_KEY environment variable to use this tool",
            }

        try:
            skill_files = _load_opening_plan_skill_files()
        except FileNotFoundError as e:
            return {
                "status": "error",
                "error": "Skill files not found",
                "message": str(e),
            }

        prompt = _build_opening_plan_prompt(
            skill_files=skill_files,
            site_name=site_name,
            site_address=site_address,
            sir_content=sir_content,
            school_approval_data=school_approval_data,
            building_inspection_content=building_inspection_content,
            target_open_date=target_open_date,
        )

        client = anthropic.Anthropic(api_key=api_key)
        try:
            response = client.messages.create(
                model=settings.anthropic_report_model,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            )
            first_block = response.content[0] if response.content else None
            plan_content = str(getattr(first_block, "text", "") or "")
        except Exception as e:
            logger.error("Claude call failed for opening plan: %s", e)
            return {
                "status": "error",
                "error": "Claude API call failed",
                "message": str(e),
            }

        result: dict[str, Any] = {
            "status": "success",
            "plan_content": plan_content,
            "doc_url": "",
            "doc_id": "",
        }

        if drive_folder_url and plan_content:
            folder_id = extract_folder_id_from_url(drive_folder_url)
            if folder_id:
                try:
                    gc = _make_google_client()
                    doc_name = f"Opening Plan - {site_name}"
                    target_folder = _get_or_create_m1_folder(gc, folder_id)
                    target_folder_id = target_folder["id"]

                    doc = gc.create_document(
                        name=doc_name,
                        folder_id=target_folder_id,
                        text_content=plan_content,
                    )
                    result["doc_url"] = doc.get("webViewLink", "")
                    result["doc_id"] = doc.get("id", "")
                    logger.info("Opening Plan published: %s", result["doc_url"])
                except Exception as e:
                    logger.warning("Failed to publish Opening Plan to Drive: %s", e)
                    result["publish_status"] = "failed"
                    result["publish_error"] = str(e)

        return result

    return await asyncio.to_thread(_work)


# ---------------------------------------------------------------------------
# Shovels.ai permit history helpers — DEPRECATED for DDR.
#
# The Shovels integration runs upstream now (AI SIR / source-evidence
# build), which supplies pre-computed permit history risk flags to DDR
# via the ``permit_history.risk_flags`` token in ``report_data``.
#
# DDR no longer calls Shovels during normal report generation. The
# helpers and ``get_permit_history`` function below stay in the source
# tree for legacy callers and existing unit tests, but the MCP tool is
# only registered when ``DDR_ENABLE_SHOVELS=true`` (default False).
#
# Do not add new callers. To consume permit history evidence in a DD
# report, populate ``report_data["permit_history.risk_flags"]`` from
# the upstream SIR/source-evidence build; ``risk_flags.derive_risk_flags``
# will ingest it into ``dd_risk_flags[]`` automatically.
# ---------------------------------------------------------------------------

# Canonical gap labels for downstream report generation. Shovels failures
# must not crash the DD report run. The current prompt contract tells the agent
# to store these strings in token_evidence["shovels.permit_history"] and
# proceed. Keep these stable so reports that reference them keep parsing.
SHOVELS_GAP_LABEL_NOT_CONFIGURED = (
    "[Not found — Shovels.ai API key not configured; permit history unavailable]"
)
SHOVELS_GAP_LABEL_NOT_FOUND = (
    "[Not found — Shovels.ai did not match this address; permit history unavailable]"
)
SHOVELS_GAP_LABEL_API_ERROR = (
    "[Not found — Shovels.ai API error; permit history unavailable]"
)


def _shovels_error_response(message: str, *, gap_label: str = SHOVELS_GAP_LABEL_API_ERROR) -> dict[str, Any]:
    """Build a non-crashing error response for Shovels failures.

    Always carries:
      * ``status="error"`` so callers can branch.
      * ``gap_label`` — the verbatim string downstream report generation
        should store in ``token_evidence["shovels.permit_history"]``.
      * empty ``risk_flags`` and ``report_data_fields`` so report merging
        code that unconditionally indexes those keys keeps working.
    """
    return {
        "status": "error",
        "error": "Shovels API error",
        "message": message,
        "gap_label": gap_label,
        "risk_flags": [],
        "report_data_fields": {
            "exec.acquisition_conditions": "",
            "exec.tradeoffs_and_deficiencies": "",
        },
    }


@retry(**retry_config())  # type: ignore[untyped-decorator]
def _call_shovels_search(api_key: str, base_url: str, address: str) -> dict[str, Any] | None:
    """Search for an address and return the first result dict, or None if not found."""
    resp = requests.get(
        f"{base_url}/addresses/search",
        params={"q": address, "size": "1"},
        headers={"X-API-Key": api_key},
        timeout=10,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return items[0] if items else None


@retry(**retry_config())  # type: ignore[untyped-decorator]
def _call_shovels_metrics(api_key: str, base_url: str, geo_id: str) -> dict[str, Any]:
    """Get current permit metrics for an address geo_id."""
    resp = requests.get(
        f"{base_url}/addresses/{geo_id}/metrics/current",
        headers={"X-API-Key": api_key},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


@retry(**retry_config())  # type: ignore[untyped-decorator]
def _call_shovels_permits(
    api_key: str, base_url: str, geo_id: str, from_date: str, to_date: str
) -> list[dict[str, Any]]:
    """Get up to 50 permits for an address within the given date range."""
    resp = requests.get(
        f"{base_url}/permits/search",
        params={"geo_id": geo_id, "permit_from": from_date, "permit_to": to_date, "size": "50"},
        headers={"X-API-Key": api_key},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])  # type: ignore[no-any-return]


_SHOVELS_SYSTEM_TAGS: dict[str, set[str]] = {
    "HVAC_PERMIT": {"hvac", "mechanical", "heating", "cooling", "air conditioning"},
    "ROOF_PERMIT": {"roof", "reroof", "roofing"},
    "ELECTRICAL_PERMIT": {"electrical", "electric"},
    "PLUMBING_PERMIT": {"plumbing"},
}


def _analyze_permit_flags(
    metrics: dict[str, Any], permits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Analyze permit metrics and history for DD risk signals. Returns list of flag dicts."""
    flags: list[dict[str, Any]] = []

    # OPEN_PERMIT — unresolved construction work (acquisition condition)
    active = metrics.get("permit_active_count") or 0
    in_review = metrics.get("permit_in_review_count") or 0
    if active > 0 or in_review > 0:
        n = active + in_review
        flags.append({
            "flag_type": "OPEN_PERMIT",
            "severity": "acquisition_condition",
            "description": (
                f"{n} open/active permit(s) — unresolved construction work must be "
                "resolved before lease execution"
            ),
            "evidence": f"Shovels metrics: permit_active_count={active}, permit_in_review_count={in_review}",
        })

    # DEMO_PERMIT — demolition work found in history (acquisition condition)
    for p in permits:
        tags = [t.lower() for t in (p.get("tags") or [])]
        ptype = (p.get("type") or "").lower()
        pdesc = (p.get("description") or "").lower()
        if "demolition" in tags or "demolition" in ptype or "demolition" in pdesc:
            flags.append({
                "flag_type": "DEMO_PERMIT",
                "severity": "acquisition_condition",
                "description": (
                    f"Demolition permit found ({p.get('file_date', 'date unknown')}) — "
                    "verify scope and structural impact before proceeding"
                ),
                "evidence": (
                    f"Permit: type={p.get('type')}, tags={p.get('tags')}, status={p.get('status')}"
                ),
            })
            break  # flag once even if multiple demo permits exist

    # DEFERRED_MAINTENANCE — no permit activity in 10-year window (risk note)
    if (metrics.get("permit_count") or 0) == 0 and not permits:
        flags.append({
            "flag_type": "DEFERRED_MAINTENANCE",
            "severity": "risk_note",
            "description": (
                "No permit activity in the last 10 years — potential deferred maintenance signal; "
                "building systems may not have been serviced or inspected"
            ),
            "evidence": "Shovels metrics: permit_count=0 over 10-year window",
        })

    # LOW_INSPECTION_QUALITY — pass rate below 70% (risk note)
    pass_rate = metrics.get("avg_inspection_pass_rate")
    if pass_rate is not None and pass_rate < 0.70:
        flags.append({
            "flag_type": "LOW_INSPECTION_QUALITY",
            "severity": "risk_note",
            "description": (
                f"Low inspection pass rate ({pass_rate:.0%}) — history of failed inspections; "
                "verify workmanship quality with building inspector"
            ),
            "evidence": f"Shovels metrics: avg_inspection_pass_rate={pass_rate}",
        })

    # Info-level system permits — evidence for cross-referencing with building inspection
    seen_info: set[str] = set()
    for p in permits:
        tag_set = {t.lower() for t in (p.get("tags") or [])}
        pdesc = (p.get("description") or "").lower()
        for flag_type, keywords in _SHOVELS_SYSTEM_TAGS.items():
            if flag_type in seen_info:
                continue
            if tag_set & keywords or any(k in pdesc for k in keywords):
                seen_info.add(flag_type)
                label = flag_type.replace("_PERMIT", "").replace("_", " ").title()
                flags.append({
                    "flag_type": flag_type,
                    "severity": "info",
                    "description": (
                        f"{label} permit on file "
                        f"({p.get('file_date', 'date unknown')}, status: {p.get('status', 'unknown')})"
                    ),
                    "evidence": (
                        f"Permit: type={p.get('type')}, tags={p.get('tags')}, "
                        f"job_value={p.get('job_value')}, status={p.get('status')}"
                    ),
                })

    return flags


def _format_permit_report_fields(risk_flags: list[dict[str, Any]]) -> dict[str, str]:
    """Format acquisition-condition and trade-off flags as bullet text for report fields.

    Info-severity flags are excluded — they are evidence only, not report content.
    """
    conditions: list[str] = []
    risks: list[str] = []
    for flag in risk_flags:
        if flag["severity"] == "acquisition_condition":
            conditions.append(f"- {flag['description']} (Shovels.ai permit data)")
        elif flag["severity"] == "risk_note":
            risks.append(f"- {flag['description']} (Shovels.ai permit data)")
    return {
        "exec.acquisition_conditions": "\n".join(conditions),
        "exec.tradeoffs_and_deficiencies": "\n".join(risks),
    }


async def get_permit_history(
    address: str,
    site_name: str = "",
    drive_folder_url: str = "",
) -> dict[str, Any]:
    """DEPRECATED — legacy Shovels.ai permit history fetch. Not part of DDR scope.

    Permit history evidence is now produced upstream by the AI SIR /
    source-evidence build, which writes ``permit_history.risk_flags``
    into the report_data token bag. DDR ingests that token directly via
    ``risk_flags.derive_risk_flags`` and does not initiate live Shovels
    API calls during report generation.

    This function is retained for legacy callers and is only exposed as
    an MCP tool when ``DDR_ENABLE_SHOVELS=true``. Default is disabled.

    Args:
        address: Full property address.
        site_name: Site name — pass to auto-publish the assessment as a Google Doc.
        drive_folder_url: Site Drive folder URL — pass to auto-publish.

    Returns:
        Dict with status, coverage, metrics, permits, risk_flags,
        report_data_fields, and doc_url (if auto-published).
    """
    logger.info("Tool called: get_permit_history — address=%s", address)

    settings = get_settings()
    status_info = shovels_status(settings)
    base_url = settings.shovels_api_base_url

    if not status_info["configured"]:
        # Preflight failure. Never raise — emit the structured gap label so
        # downstream report generation can store it in token_evidence and
        # proceed. ``reason`` is safe to log (missing / placeholder /
        # whitespace_only); the raw key is never included.
        logger.warning(
            "Shovels.ai is not configured (reason=%s); returning gap label for address=%s",
            status_info["reason"],
            address,
        )
        return {
            "status": "error",
            "error": "Configuration error",
            "message": f"SHOVELS_API_KEY is not configured (reason={status_info['reason']})",
            "gap_label": SHOVELS_GAP_LABEL_NOT_CONFIGURED,
            "risk_flags": [],
            "report_data_fields": {
                "exec.acquisition_conditions": "",
                "exec.tradeoffs_and_deficiencies": "",
            },
            "shovels_status": status_info,
        }

    api_key = settings.shovels_api_key.strip()

    def _work() -> dict[str, Any]:
        # Step A — resolve address to geo_id
        try:
            search_result = _call_shovels_search(api_key, base_url, address)
        except requests.HTTPError as e:
            logger.error("Shovels address search HTTP error: %s", e)
            return _shovels_error_response(str(e))
        except Exception as e:
            logger.error("Shovels address search failed: %s", e)
            return _shovels_error_response(str(e))

        if search_result is None:
            logger.info("Shovels.ai: address not found in coverage — %s", address)
            return {
                "status": "success",
                "coverage": "not_found",
                "address_searched": address,
                "risk_flags": [],
                "report_data_fields": {
                    "exec.acquisition_conditions": "",
                    "exec.tradeoffs_and_deficiencies": "",
                },
                "gap_label": SHOVELS_GAP_LABEL_NOT_FOUND,
                "message": SHOVELS_GAP_LABEL_NOT_FOUND,
            }

        geo_id = search_result.get("geo_id", "")
        normalized_address = search_result.get("name", address)

        # Step B — get current metrics
        try:
            metrics = _call_shovels_metrics(api_key, base_url, geo_id)
        except Exception as e:
            logger.error("Shovels metrics call failed: %s", e)
            return _shovels_error_response(str(e))

        # Defense against malformed upstream responses. The metrics endpoint
        # has historically returned non-dict payloads under partial outages;
        # treat that the same as an API error so callers can rely on the
        # ``metrics.get(...)`` calls below.
        if not isinstance(metrics, dict):
            logger.error("Shovels metrics returned non-dict payload: %r", type(metrics).__name__)
            return _shovels_error_response("malformed metrics response")

        # Step C — get permit history (last 10 years, up to 50 permits)
        today = datetime.now()
        from_date = f"{today.year - 10}-{today.month:02d}-{today.day:02d}"
        to_date = today.strftime("%Y-%m-%d")
        try:
            permits = _call_shovels_permits(api_key, base_url, geo_id, from_date, to_date)
        except Exception as e:
            logger.error("Shovels permits call failed: %s", e)
            return _shovels_error_response(str(e))

        if not isinstance(permits, list):
            logger.error("Shovels permits returned non-list payload: %r", type(permits).__name__)
            return _shovels_error_response("malformed permits response")

        # Extract property attributes from the first permit that carries them
        property_attributes: dict[str, Any] = {}
        for p in permits:
            if p.get("property_year_built") and "year_built" not in property_attributes:
                property_attributes["year_built"] = p["property_year_built"]
            if p.get("property_building_area") and "building_area" not in property_attributes:
                property_attributes["building_area"] = p["property_building_area"]
            if p.get("property_lot_size") and "lot_size" not in property_attributes:
                property_attributes["lot_size"] = p["property_lot_size"]
            if p.get("property_story_count") and "story_count" not in property_attributes:
                property_attributes["story_count"] = p["property_story_count"]
            if len(property_attributes) == 4:
                break

        risk_flags = _analyze_permit_flags(metrics, permits)
        report_data_fields = _format_permit_report_fields(risk_flags)

        condition_count = sum(1 for f in risk_flags if f["severity"] == "acquisition_condition")
        risk_count = sum(1 for f in risk_flags if f["severity"] == "risk_note")

        return {
            "status": "success",
            "coverage": "found",
            "geo_id": geo_id,
            "normalized_address": normalized_address,
            "metrics": {
                "permit_count": metrics.get("permit_count"),
                "permit_active_count": metrics.get("permit_active_count"),
                "permit_in_review_count": metrics.get("permit_in_review_count"),
                "permit_final_count": metrics.get("permit_final_count"),
                "permit_inactive_count": metrics.get("permit_inactive_count"),
                "total_job_value": (metrics.get("total_job_value") or 0) // 100,
                "avg_inspection_pass_rate": metrics.get("avg_inspection_pass_rate"),
            },
            "property_attributes": property_attributes,
            "permits": permits,
            "risk_flags": risk_flags,
            "report_data_fields": report_data_fields,
            "message": (
                f"Shovels.ai: {metrics.get('permit_count', 0)} permit(s) found at {normalized_address}. "
                f"{condition_count} lease condition item(s), {risk_count} trade-off item(s)."
            ),
        }

    try:
        result = await asyncio.to_thread(_work)
    except Exception as e:
        # Final safety net. ``_work`` already catches per-call exceptions and
        # returns structured error dicts, so reaching here implies a logic
        # bug or environment failure (e.g. thread pool exhausted). Still
        # never crash the DD report run.
        logger.exception("Shovels.ai permit history failed unexpectedly: %s", e)
        return _shovels_error_response(str(e))

    # Auto-publish to Drive if address was found and site context provided
    if (
        result.get("status") == "success"
        and result.get("coverage") == "found"
        and site_name
        and drive_folder_url
    ):
        try:
            pub = await save_skill_report(
                skill_name="Permit History",
                site_name=site_name,
                drive_folder_url=drive_folder_url,
                skill_data=result,
            )
            if pub.get("status") == "success":
                result["doc_url"] = pub["doc_url"]
                result["doc_id"] = pub["doc_id"]
                logger.info("Auto-published Permit History assessment: %s", pub["doc_url"])
        except Exception as e:
            logger.warning("Failed to auto-publish Permit History assessment: %s", e)
            result["publish_status"] = "failed"

    return result


# Legacy MCP tool registration — opt-in only.
#
# get_permit_history is NOT advertised as an MCP tool by default. The
# Shovels integration moved upstream to the AI SIR / source-evidence
# build; DDR consumes the resulting ``permit_history.risk_flags`` token
# rather than calling Shovels itself. Setting DDR_ENABLE_SHOVELS=true
# restores the legacy MCP registration for callers that haven't
# migrated yet.
if get_settings().ddr_enable_shovels:
    logger.warning(
        "DDR_ENABLE_SHOVELS=true — registering deprecated get_permit_history "
        "MCP tool. Permit history should come from upstream SIR evidence."
    )
    mcp.tool()(get_permit_history)


def _attach_block_plan_submitted_timestamp(
    *,
    gc: GoogleClient,
    drive_folder_url: str,
    completeness: dict[str, Any],
) -> None:
    """Look up the Block Plan ``modifiedTime`` from the site's M1 folder
    and attach it to ``completeness`` so the report banner can name the
    submission timestamp.

    No-op when ``completeness.stage != "partial"`` (banner won't
    render), when the M1 folder can't be resolved, or when no Block
    Plan has been filed yet. All failures are logged and swallowed —
    the banner falls back to "Block Plan submitted at unknown time"
    rather than blocking the report.
    """
    if completeness.get("stage") != "partial":
        return
    if not drive_folder_url:
        return
    try:
        m1_folder_id, _ = _resolve_m1_folder(gc, drive_folder_url)
    except Exception as e:
        logger.warning("Block Plan timestamp lookup: M1 resolve failed: %s", e)
        return
    if not m1_folder_id:
        return
    try:
        m1_docs = _list_m1_documents_by_type(gc, m1_folder_id)
    except Exception as e:
        logger.warning("Block Plan timestamp lookup: M1 list failed: %s", e)
        return
    block_plan = m1_docs.get("block_plan")
    if not block_plan:
        return
    iso = str(block_plan.get("modifiedTime") or "").strip()
    if not iso:
        return
    completeness["block_plan_submitted_at"] = iso
    completeness["block_plan_submitted_display"] = _format_block_plan_submitted_display(iso)


def _format_block_plan_submitted_display(iso_ts: str) -> str:
    """Format an ISO-8601 ``modifiedTime`` into the banner phrasing
    ``YYYY-MM-DD HH:MM UTC``. Falls back to the raw string when the
    timestamp can't be parsed."""
    from datetime import datetime as _dt

    candidate = iso_ts.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = _dt.fromisoformat(candidate)
    except ValueError:
        return iso_ts
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def _raycon_failure_reason_from_flat(flat_report_data: dict[str, Any]) -> str:
    """Return the RayCon validation failure reason carried in report data."""
    status = str(flat_report_data.get("exec.raycon_status") or "").strip().lower()
    reason = str(
        flat_report_data.get("exec.raycon_failure_reason") or ""
    ).strip()
    if reason:
        return reason
    if status in RAYCON_FAILED_STATUSES:
        return f"RayCon status: {status}"
    return ""


def _fill_raycon_failed_placeholders(replacements: dict[str, str]) -> None:
    """Force RayCon-sourced tokens to an explicit validation-failed label."""
    for token in raycon_token_paths():
        replacements[token] = "[Not found - RayCon validation failed]"


def _normalize_report_replacements(
    report_data: dict[str, Any],
    site_name: str,
    report_date: str,
    drive_folder_url: str,
    site_address: str = "",
) -> tuple[dict[str, str], list[str], list[str], dict[str, str], ReblResolution]:
    """Normalize report data and fill permissive current gap labels."""
    flat_report_data = flatten_report_data_for_replacement(report_data)
    raycon_failure_reason = _raycon_failure_reason_from_flat(flat_report_data)
    report_data, rebl_resolution = _inject_report_defaults(
        report_data,
        site_address=site_address,
    )
    replacements, unmatched, unfilled, token_sources = normalize_report_data(
        report_data,
        site_name=site_name,
        report_date=report_date,
    )
    if "exec.c_answer" in replacements:
        normalized_answer = normalize_can_we_answer(replacements["exec.c_answer"])
        if normalized_answer is not None:
            replacements["exec.c_answer"] = normalized_answer
    if site_name.strip():
        replacements["meta.site_name"] = site_name.strip()
    if raycon_failure_reason:
        _fill_raycon_failed_placeholders(replacements)
    _fill_fastest_open_placeholders(replacements)
    _fill_max_capacity_placeholders(replacements)

    existing_drive_folder_url = str(replacements.get("meta.drive_folder_url") or "").strip()
    existing_drive_folder_id = (
        extract_folder_id_from_url(existing_drive_folder_url)
        if existing_drive_folder_url
        else None
    )
    if (
        drive_folder_url.strip()
        and (not existing_drive_folder_url or is_drive_root_folder_id(existing_drive_folder_id))
    ):
        replacements["meta.drive_folder_url"] = drive_folder_url
    source_quality_notes = (
        flat_report_data.get("source_quality_notes")
        or flat_report_data.get("notes.source_quality")
        or flat_report_data.get(SOURCE_QUALITY_NOTES_KEY)
        or ""
    ).strip()
    if source_quality_notes:
        replacements[SOURCE_QUALITY_NOTES_KEY] = source_quality_notes
    citations_block = (
        flat_report_data.get("exec.citations_block")
        or flat_report_data.get("citations_block")
        or flat_report_data.get(CITATIONS_BLOCK_KEY)
        or ""
    ).strip()
    if citations_block:
        replacements[CITATIONS_BLOCK_KEY] = citations_block
    verification_open_items = (
        flat_report_data.get("verification.open_items")
        or flat_report_data.get("open_items.verification")
        or flat_report_data.get("verification_open_items")
        or flat_report_data.get(VERIFICATION_OPEN_ITEMS_KEY)
        or ""
    ).strip()
    if verification_open_items:
        replacements[VERIFICATION_OPEN_ITEMS_KEY] = verification_open_items
    return replacements, unmatched, unfilled, token_sources, rebl_resolution


def _ensure_report_section(
    report_data: dict[str, Any],
    section: str,
) -> dict[str, Any]:
    block = report_data.get(section)
    if not isinstance(block, dict):
        block = {}
        report_data[section] = block
    return block


def _resolve_rebl_identity(address: str) -> ReblResolution:
    cleaned = address.strip()
    if not cleaned:
        return ReblResolution.missing_address()

    cached = _REBL_RESOLUTION_CACHE.get(cleaned)
    if cached is not None:
        return cached

    settings = get_settings()
    try:
        resolved = resolve_address(cleaned, base_url=settings.rebl_base_url)
    except Exception as e:
        resolved = ReblResolution.error_result(cleaned, str(e))
        logger.warning("REBL resolve failed for '%s': %s", cleaned, e)

    _REBL_RESOLUTION_CACHE[cleaned] = resolved
    return resolved


def _apply_rebl_defaults(
    report_data: dict[str, Any],
    rebl_resolution: ReblResolution,
) -> None:
    if not rebl_resolution.site_id and not rebl_resolution.url:
        return

    meta = _ensure_report_section(report_data, "meta")
    sources = _ensure_report_section(report_data, "sources")
    if rebl_resolution.site_id:
        meta["rebl_site_id"] = rebl_resolution.site_id
    if rebl_resolution.url:
        sources["rebl_link"] = rebl_resolution.url


def _is_missing_prepared_by(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return True
    normalized = value.strip().lower()
    return normalized in {
        "dd report agent",
        "alpha dd reporter",
        "due diligence reporter",
        "system",
    }


def _extract_report_address(flat_report_data: dict[str, Any]) -> str:
    for key in (
        "site.address",
        "site.site_address",
        "property.address",
        "address",
        "site_address",
    ):
        value = flat_report_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _inject_report_defaults(
    report_data: dict[str, Any],
    *,
    site_address: str = "",
) -> tuple[dict[str, Any], ReblResolution]:
    """Inject report defaults that should not depend on agent output."""
    enriched: dict[str, Any] = json.loads(json.dumps(report_data))
    flat_report_data = flatten_report_data_for_replacement(enriched)

    if (
        not flat_report_data.get("p1_assignee_name")
        and not flat_report_data.get("site.p1_assignee_name")
        and _is_missing_prepared_by(flat_report_data.get("meta.prepared_by"))
    ):
        enriched["p1_assignee_name"] = MISSING_P1_ASSIGNEE_LABEL

    rebl_address = _extract_report_address(flat_report_data) or site_address.strip()
    rebl_resolution = _resolve_rebl_identity(rebl_address)
    _apply_rebl_defaults(enriched, rebl_resolution)
    return enriched, rebl_resolution


def _find_existing_report_doc(
    gc: GoogleClient,
    *,
    folder_id: str,
    site_name: str,
) -> dict[str, Any] | None:
    """Return an existing DD report Doc for *site_name* in the folder.

    Matches by prefix ``"{site_name} DD Report - "`` so the cross-day
    ``force_regenerate=True`` path can find yesterday's Doc and rename
    it to today's date in place, rather than creating a duplicate Doc
    per day. Returns the most recently modified match if multiple
    exist (a one-off cleanup we accept for legacy folders).
    """
    try:
        files = gc.list_files_in_folder(folder_id)
    except Exception as e:
        logger.warning("Could not list folder %s while checking for existing report: %s", folder_id, e)
        return None

    prefix = f"{site_name.strip()} DD Report - "
    candidate: dict[str, Any] | None = None
    for file_info in files:
        name = str(file_info.get("name", ""))
        if not name.startswith(prefix):
            continue
        if candidate is None or str(file_info.get("modifiedTime", "")) > str(
            candidate.get("modifiedTime", "")
        ):
            candidate = file_info
    return candidate


def _fill_scenario_placeholders(
    replacements: dict[str, str],
    *,
    scenario: str,
    label: str,
) -> None:
    """Fill missing scenario summary and detailed breakdown fields."""
    for metric in ("capacity", "capex", "open_date"):
        token = f"exec.{scenario}_{metric}"
        if (
            token not in replacements
            or not str(replacements[token]).strip()
            or is_raycon_pending_placeholder(replacements[token])
        ):
            replacements[token] = label
    for row_key, _ in RAYCON_BREAKDOWN_ROWS:
        token = f"exec.cost_{row_key}_{scenario}"
        if (
            token not in replacements
            or not str(replacements[token]).strip()
            or is_raycon_pending_placeholder(replacements[token])
        ):
            replacements[token] = label


def _fill_fastest_open_placeholders(replacements: dict[str, str]) -> None:
    _fill_scenario_placeholders(
        replacements,
        scenario="fastest_open",
        label="[Not found - Fastest Open scenario not extracted]",
    )


def _fill_max_capacity_placeholders(replacements: dict[str, str]) -> None:
    _fill_scenario_placeholders(
        replacements,
        scenario="max_capacity",
        label="[Not found - Max Capacity scenario not extracted]",
    )


def _prepare_report_text_replacements(
    replacements: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Swap link URLs for display labels and keep the original URL targets."""
    text_replacements = dict(replacements)
    link_urls: dict[str, str] = {}
    for token in LINK_TOKENS:
        value = text_replacements.get(token, "")
        if value.startswith("http") and token in LINK_DISPLAY_LABELS:
            link_urls[token] = value
            text_replacements[token] = LINK_DISPLAY_LABELS[token]
    return text_replacements, link_urls


def _apply_report_hyperlinks(
    gc: GoogleClient,
    doc_id: str,
    replacements: dict[str, str],
    link_urls: dict[str, str],
) -> dict[str, Any]:
    """Apply hyperlink styling for URL-backed template tokens."""
    hyperlink_trace: dict[str, Any] = {
        "candidates": {},
        "found_in_doc": [],
        "not_found_in_doc": [],
        "missing_from_agent": [],
        "non_url_values": {},
        "unmapped_agent_urls": [],
        "applied": 0,
        "error": None,
    }
    hyperlink_trace["missing_from_agent"] = [t for t in LINK_TOKENS if t not in replacements]
    hyperlink_trace["non_url_values"] = {
        key: replacements[key][:120]
        for key in LINK_TOKENS
        if key in replacements and not replacements[key].startswith("http")
    }
    hyperlink_trace["unmapped_agent_urls"] = [
        key for key, value in replacements.items()
        if key not in LINK_TOKENS and value.startswith("http")
    ]

    if hyperlink_trace["missing_from_agent"]:
        logger.warning(
            "Hyperlinks: agent did not provide values for: %s",
            hyperlink_trace["missing_from_agent"],
        )
    if hyperlink_trace["non_url_values"]:
        logger.info(
            "Hyperlinks: link tokens with non-URL values: %s",
            hyperlink_trace["non_url_values"],
        )
    if hyperlink_trace["unmapped_agent_urls"]:
        logger.warning(
            "Hyperlinks: agent provided URLs under keys not in LINK_TOKENS: %s",
            hyperlink_trace["unmapped_agent_urls"],
        )

    try:
        hyperlink_trace["candidates"] = {
            key: {"label": LINK_DISPLAY_LABELS.get(key, value), "url": value[:200]}
            for key, value in link_urls.items()
        }
        if not link_urls:
            logger.info("Hyperlinks: no URL candidates found in link tokens")
            return hyperlink_trace

        logger.info("Hyperlinks: %d URL candidates: %s", len(link_urls), list(link_urls.keys()))
        doc_body = gc.get_document(doc_id).get("body", {})
        hl_result = build_hyperlink_requests(doc_body, link_urls, LINK_TOKENS, LINK_DISPLAY_LABELS)
        hyperlink_trace["found_in_doc"] = hl_result.found_tokens
        hyperlink_trace["not_found_in_doc"] = hl_result.not_found_tokens
        if not hl_result.requests:
            logger.warning(
                "Hyperlinks: display labels not found in doc body - 0 of %d candidates matched",
                len(link_urls),
            )
            return hyperlink_trace

        gc.batch_update_document(doc_id, hl_result.requests)
        hyperlink_trace["applied"] = len(hl_result.requests)
        logger.info(
            "Applied %d hyperlinks to document %s: %s",
            hyperlink_trace["applied"],
            doc_id,
            hl_result.found_tokens,
        )
    except Exception as e:
        logger.warning("Hyperlink insertion failed (report still usable): %s", e)
        hyperlink_trace["error"] = str(e)

    return hyperlink_trace


@mcp.tool()
async def create_dd_report(
    site_name: str,
    drive_folder_url: str,
    report_data: dict[str, Any],
    site_address: str = "",
    token_evidence: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a completed DD report Google Doc for a site."""
    logger.info("Tool called: create_dd_report")
    logger.info(
        "create_dd_report params: site_name=%s, drive_folder_url=%s, site_address=%s",
        site_name,
        drive_folder_url,
        site_address,
    )

    if not site_name or not site_name.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "site_name must be a non-empty string",
        }
    if not drive_folder_url or not drive_folder_url.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "drive_folder_url must be a non-empty string",
        }

    folder_id = extract_folder_id_from_url(drive_folder_url)
    if not folder_id:
        return {
            "status": "error",
            "error": "Invalid folder URL",
            "message": f"Could not extract a Google Drive folder ID from: {drive_folder_url}",
        }
    if is_drive_root_folder_id(folder_id):
        return {
            "status": "error",
            "error": "Invalid folder URL",
            "message": (
                "drive_folder_url points to Google Drive root. Provide the site Drive "
                f"folder URL so the DD report can be created in {M1_FOLDER_NAME}."
            ),
        }

    today_str = datetime.now().strftime("%m/%d/%Y")
    doc_name = f"{site_name.strip()} DD Report - {today_str}"
    logger.info("Creating DD report: %s", doc_name)

    def _work() -> dict[str, Any]:
        try:
            gc = _make_google_client()
            target_folder_id, target_folder_url = _resolve_m1_folder(
                gc,
                drive_folder_url,
                allow_legacy_fallback=False,
            )
            if not target_folder_id:
                raise RuntimeError(f"Could not resolve {M1_FOLDER_NAME} folder for DD report")
            logger.info(
                "Using %s folder %s for DD report '%s'",
                M1_FOLDER_NAME,
                target_folder_id,
                doc_name,
            )
            existing_doc = _find_existing_report_doc(
                gc, folder_id=target_folder_id, site_name=site_name
            )
            legacy_source_folder_id: str | None = None
            if existing_doc is None and target_folder_id != folder_id:
                existing_doc = _find_existing_report_doc(
                    gc, folder_id=folder_id, site_name=site_name
                )
                if existing_doc is not None:
                    legacy_source_folder_id = folder_id
            doc_id: str | None = None
            doc_url: str | None = None
            document_name = doc_name
            document_role = "active"
            source_doc_id = ""
            republish_guard: dict[str, Any] = {}
            if existing_doc:
                existing_doc_id = existing_doc.get("id")
                if not isinstance(existing_doc_id, str) or not existing_doc_id:
                    raise RuntimeError(f"Existing DD report is missing a valid document ID: {doc_name}")
                republish_guard = _dd_report_overwrite_guard(gc, existing_doc=existing_doc)
                if republish_guard.get("status") == "blocked":
                    source_doc_id = existing_doc_id
                    document_role = "candidate"
                    document_name = _candidate_dd_report_name(site_name, today_str)
                    logger.info(
                        "Existing DD report is protected (%s); creating candidate %s",
                        republish_guard.get("reason"),
                        document_name,
                    )
                    candidate_doc = gc.create_document(
                        name=document_name,
                        folder_id=target_folder_id,
                        text_content="",
                    )
                    doc_id = candidate_doc.get("id")
                    doc_url = candidate_doc.get("webViewLink")
                    if not doc_id or not isinstance(doc_id, str):
                        raise RuntimeError("Invalid document ID returned from candidate create operation")
                    republish_guard = {
                        **republish_guard,
                        "candidate_created": True,
                        "candidate_doc_id": doc_id,
                        "candidate_doc_url": str(doc_url or ""),
                    }
                else:
                    doc_id = existing_doc_id
                    doc_url = existing_doc.get("webViewLink")
                    if legacy_source_folder_id is not None:
                        logger.info(
                            "Moving existing DD Doc %s from site root %s to %s folder %s",
                            doc_id,
                            legacy_source_folder_id,
                            M1_FOLDER_NAME,
                            target_folder_id,
                        )
                        gc.move_file_to_folder(doc_id, target_folder_id)
                    old_name = str(existing_doc.get("name", "")).strip()
                    if old_name and old_name != doc_name:
                        # Cross-day regenerate: same site, different report-date suffix.
                        # Rename in place so we never accumulate one Doc per day.
                        logger.info(
                            "Renaming existing DD Doc from %s to %s", old_name, doc_name
                        )
                        try:
                            gc.rename_file(doc_id, doc_name)
                        except Exception as rename_err:  # noqa: BLE001
                            logger.warning(
                                "Failed to rename DD Doc %s -> %s; continuing with stale name: %s",
                                old_name,
                                doc_name,
                                rename_err,
                            )
                    logger.info("Existing DD report found, rebuilding in place: %s (id=%s)", doc_name, doc_id)
                    _clear_document_body(gc, doc_id=doc_id)
            else:
                logger.info("Creating blank document in folder %s as '%s'", target_folder_id, doc_name)
                new_doc = gc.create_document(
                    name=doc_name,
                    folder_id=target_folder_id,
                    text_content="",
                )
                doc_id = new_doc.get("id")
                doc_url = new_doc.get("webViewLink")
                if not doc_id or not isinstance(doc_id, str):
                    raise RuntimeError("Invalid document ID returned from create operation")
                logger.info("Created blank document: %s (id=%s)", doc_name, doc_id)
            replacements, unmatched, unfilled, _token_sources, rebl_resolution = _normalize_report_replacements(
                report_data=report_data,
                site_name=site_name.strip(),
                report_date=today_str,
                drive_folder_url=drive_folder_url,
                site_address=site_address,
            )
            logger.info(
                "Normalization: %d replacements, %d unmatched keys, %d unfilled tokens",
                len(replacements), len(unmatched), len(unfilled),
            )
            if unmatched:
                logger.warning("Unmatched agent keys (no template token): %s", unmatched)

            # Compute the partial-on-purpose completeness metadata. Done
            # before the doc builder runs so the renderer can prepend
            # the "PARTIAL REPORT" banner when stage == "partial".
            raycon_failure_reason = _raycon_failure_reason_from_flat(
                flatten_report_data_for_replacement(report_data)
            )
            completeness = compute_completeness_block(
                replacements,
                raycon_failure_reason=raycon_failure_reason,
            )
            _attach_block_plan_submitted_timestamp(
                gc=gc,
                drive_folder_url=drive_folder_url,
                completeness=completeness,
            )
            logger.info(
                "Completeness: stage=%s filled=%d pending=%d auto_republish_on=%s",
                completeness.get("stage"),
                completeness.get("filled_token_count", 0),
                completeness.get("pending_token_count", 0),
                completeness.get("auto_republish_on"),
            )

            # Build the document structure programmatically
            builder_result = build_dd_report_doc(
                docs_service=gc.docs_service,
                drive_service=gc.drive_service,
                doc_id=doc_id,
                replacements=replacements,
                site_title=site_name.strip(),
                completeness=completeness,
            )
            automation_metadata = _mark_dd_report_automation_write(
                gc,
                doc_id=doc_id,
                role=document_role,
                source_doc_id=source_doc_id,
            )

            hyperlink_trace = {
                "candidates": {},
                "found_in_doc": builder_result.get("found_tokens", []),
                "not_found_in_doc": builder_result.get("not_found_tokens", []),
                "missing_from_agent": [t for t in LINK_TOKENS if t not in replacements],
                "non_url_values": {
                    key: replacements[key][:120]
                    for key in LINK_TOKENS
                    if key in replacements and not replacements[key].startswith("http")
                },
                "unmapped_agent_urls": [
                    key for key, value in replacements.items()
                    if key not in LINK_TOKENS and value.startswith("http")
                ],
                "applied": builder_result.get("applied", 0),
                "error": None,
            }

            logger.info("DD report created successfully: %s", doc_url)
            response: dict[str, Any] = {
                "status": "success",
                "document": {
                    "id": doc_id,
                    "name": document_name,
                    "url": doc_url,
                    "folder_id": target_folder_id,
                    "folder_url": target_folder_url
                    or f"https://drive.google.com/drive/folders/{target_folder_id}",
                    "role": document_role,
                    "source_doc_id": source_doc_id,
                },
                "replacements_applied": len(replacements),
                "unmatched_agent_keys": len(unmatched),
                "unfilled_template_tokens": len(unfilled),
                "hyperlinks_applied": hyperlink_trace["applied"],
                "normalized_report_data": replacements,
                "automation_metadata": automation_metadata,
                "report_metadata": {"completeness": completeness},
                "message": f"DD report built: {doc_url}",
            }
            if republish_guard:
                response["republish_guard"] = republish_guard
            return response
        except Exception as e:
            logger.error("Failed to create DD report: %s", e)
            return {
                "status": "error",
                "error": "Failed to create DD report",
                "message": str(e),
            }

    return await asyncio.to_thread(_work)


@mcp.tool()
async def check_site_readiness(
    drive_folder_url: str,
    site_name: str = "",
    site_address: str = "",
) -> dict[str, Any]:
    """Check whether a Drive-backed site folder is ready for DD generation."""
    logger.info("Tool called: check_site_readiness - site=%s", site_name or drive_folder_url)

    if not drive_folder_url or not drive_folder_url.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "drive_folder_url must be a non-empty string",
        }

    def _work() -> dict[str, Any]:
        from .report_pipeline import check_site_readiness_direct, list_shared_folders_once

        try:
            gc = _make_google_client()
            site_title = site_name.strip() or "Provided site"
            address = site_address.strip() or None
            match_terms = _build_site_match_terms(site_title, address)
            shared_cache = list_shared_folders_once(gc)
            readiness = check_site_readiness_direct(
                gc,
                drive_folder_url,
                match_terms,
                shared_cache,
                site_title=site_title,
                site_address=address,
                read_only=True,
            )
            if readiness.get("error") == "bad_url":
                return {
                    "status": "error",
                    "error": "Invalid Drive folder URL",
                    "message": f"Could not parse folder ID from: {drive_folder_url}",
                }

            sir_found = bool(readiness.get("sir_found"))
            report_exists = bool(readiness.get("report_exists"))
            raycon_found = bool(readiness.get("raycon_scenario_found"))
            raycon_usable = bool(readiness.get("raycon_scenario_usable", raycon_found))
            raycon_failed = raycon_found and not raycon_usable
            raycon_failure_reason = str(
                readiness.get("raycon_scenario_failure_reason") or ""
            ).strip()
            missing_docs = [] if sir_found else ["sir"]
            ready_for_report = sir_found and not report_exists
            projected_completeness = project_completeness_from_readiness(
                raycon_scenario_found=raycon_found,
                raycon_scenario_failed=raycon_failed,
                raycon_failure_reason=raycon_failure_reason,
            )
            raycon_status_label = (
                "failed validation"
                if raycon_failed
                else ("found" if raycon_found else "not yet posted")
            )

            return {
                "status": "success",
                "site_title": site_title,
                "sir_found": sir_found,
                "isp_found": bool(readiness.get("isp_found")),
                "inspection_found": bool(readiness.get("inspection_found")),
                "report_exists": report_exists,
                "raycon_scenario_found": raycon_found,
                "raycon_scenario_usable": raycon_usable,
                "raycon_scenario_status": readiness.get("raycon_scenario_status", ""),
                "raycon_scenario_failure_reason": raycon_failure_reason,
                "missing_docs": missing_docs,
                "ready_for_report": ready_for_report,
                "drive_folder_url": drive_folder_url,
                "report_metadata": {"completeness": projected_completeness},
                "message": "\n".join([
                    f"Site '{site_title}' document readiness:",
                    f"  SIR: {'found' if sir_found else 'not found'}",
                    f"  Building Inspection: {'found' if readiness.get('inspection_found') else 'not found'}",
                    f"  DD Report: {'exists' if report_exists else 'not yet created'}",
                    f"  RayCon scenario: {raycon_status_label}",
                    f"  RayCon failure: {raycon_failure_reason}" if raycon_failure_reason else "",
                    "",
                    "Ready for first-round report generation." if ready_for_report else (
                        "Not ready - " + ", ".join(missing_docs) + " missing." if missing_docs else "Report already exists."
                    ),
                    (
                        f"Projected report stage: {projected_completeness['stage']} "
                        f"(pending tokens: {projected_completeness['pending_token_count']}, "
                        f"auto-republish on: {', '.join(projected_completeness['auto_republish_on']) or '-'})."
                    ),
                ]),
            }
        except Exception as e:
            logger.error("check_site_readiness failed: %s", e)
            return {
                "status": "error",
                "error": "check_site_readiness failed",
                "message": str(e),
            }

    return await asyncio.to_thread(_work)

# ---------------------------------------------------------------------------
# diagnose_site_readiness — richer "should I run now or wait?" diagnostic.
# Read-only; never triggers generation, republish, or any side effect.
# ---------------------------------------------------------------------------

# Path to the dispatch dedup map written by ``scripts/raycon_followup.py``.
# Keyed by ``block_plan_file_id`` with values
# ``{"last_dispatch": ISO, "count": int, "site": str, "raycon_run_id": str|None}``.
# We read it best-effort so the diagnose tool never raises if the cron has
# never run yet on this checkout.
_RAYCON_DISPATCH_STATE_PATH = (
    Path(__file__).resolve().parent.parent.parent / ".raycon_dispatch_state.json"
)


def _load_raycon_dispatch_state(
    path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Best-effort read of the RayCon dispatch dedup map.

    Returns ``{}`` on missing file, parse error, or unexpected shape so a
    corrupt state file never breaks the diagnose tool. The default path
    is resolved at call time (not at function-definition time) so tests
    can monkeypatch ``_RAYCON_DISPATCH_STATE_PATH``.
    """
    if path is None:
        path = _RAYCON_DISPATCH_STATE_PATH
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    except Exception as e:
        logger.warning("diagnose_site_readiness: dispatch state read failed (%s): %s", path, e)
        return {}


def _latest_dispatch_for_site(
    state: dict[str, dict[str, Any]],
    site_title: str,
    *,
    block_plan_file_id: str | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(last_dispatch_iso, block_plan_file_id)`` for *site_title*.

    Prefers the entry whose key matches the *block_plan_file_id* we
    actually see in M1 today. Falls back to the most recent entry whose
    ``site`` field matches *site_title* so we still surface a dispatch
    even when the Block Plan was rotated and the prior file ID is now
    stale.
    """
    if block_plan_file_id and block_plan_file_id in state:
        entry = state[block_plan_file_id]
        last = entry.get("last_dispatch")
        if isinstance(last, str) and last:
            return last, block_plan_file_id

    best_iso: str | None = None
    best_file_id: str | None = None
    needle = site_title.strip().lower()
    for file_id, entry in state.items():
        site_val = str(entry.get("site", "")).strip().lower()
        if site_val != needle:
            continue
        last = entry.get("last_dispatch")
        if not isinstance(last, str) or not last:
            continue
        if best_iso is None or last > best_iso:
            best_iso = last
            best_file_id = file_id
    return best_iso, best_file_id


def _minutes_since_iso(iso: str | None, *, now: datetime | None = None) -> int | None:
    """Integer minutes between *iso* and *now* (UTC). ``None`` if unparseable."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None

    current = now or datetime.now(tz=UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = current - dt
    return int(delta.total_seconds() // 60)


def _build_blocking_entries(
    *,
    readiness: dict[str, Any],
    vendor_gate_enabled: bool,
    block_plan_present: bool,
    raycon_last_dispatch: str | None,
    raycon_minutes_since: int | None,
) -> list[dict[str, Any]]:
    """Build the ``blocking`` list in the response.

    Mirrors ``_missing_required_docs`` semantics so the diagnose tool
    reports the same view the cron path uses. The vendor gate state is
    surfaced explicitly so the caller can see what view they're getting.
    """
    if vendor_gate_enabled:
        sir_present = bool(readiness.get("sir_vendor"))
        bi_present = bool(readiness.get("inspection_vendor"))
    else:
        sir_present = bool(readiness.get("sir_found"))
        bi_present = bool(readiness.get("inspection_found"))

    raycon_present = bool(readiness.get("raycon_scenario_found"))
    raycon_usable = bool(readiness.get("raycon_scenario_usable", raycon_present))
    raycon_failed = raycon_present and not raycon_usable

    raycon_entry: dict[str, Any] = {
        "doc": "raycon_scenario",
        "status": (
            str(readiness.get("raycon_scenario_status") or "failed_validation")
            if raycon_failed
            else ("present" if raycon_present else "pending")
        ),
    }
    if raycon_failed:
        raycon_entry["failure_reason"] = str(
            readiness.get("raycon_scenario_failure_reason") or ""
        )
        raycon_entry["raycon_run_id"] = str(
            readiness.get("raycon_scenario_run_id") or ""
        )
    if not raycon_present:
        raycon_entry["block_plan_present"] = block_plan_present
        raycon_entry["last_dispatch"] = raycon_last_dispatch
        raycon_entry["minutes_since"] = raycon_minutes_since

    return [
        {
            "doc": "vendor_sir",
            "status": "present" if sir_present else "missing",
        },
        {
            "doc": "building_inspection",
            "status": "present" if bi_present else "missing",
        },
        raycon_entry,
    ]


@mcp.tool()
async def diagnose_site_readiness(
    site_name: str,
    drive_folder_url: str = "",
    site_address: str = "",
) -> dict[str, Any]:
    """Read-only "should I run now or wait?" diagnostic for a site.

    Surfaces the cron-path readiness view for a site:

    * ``blocking`` — per-doc status (``vendor_sir`` / ``building_inspection``
      / ``raycon_scenario``). For the RayCon entry, when the scenario JSON
      hasn't landed yet, also reports ``block_plan_present``,
      ``last_dispatch`` (most recent ``/v1/jobs`` POST timestamp from the
      dispatch state file written by ``scripts/raycon_followup.py``), and
      ``minutes_since`` (integer minutes since that dispatch).
    * ``ready_for_full_report`` — mirrors ``_missing_required_docs``
      exactly. Under ``VENDOR_GATE_ENABLED=1`` this requires vendor SIR,
      vendor BI, and RayCon scenario to all be present; under
      ``VENDOR_GATE_ENABLED=0`` only SIR + BI presence is required and
      RayCon absence does NOT block readiness (``blocking[]`` still
      reports its actual status, but it's diagnostic-only).
    * ``partial_report_possible`` — true iff the floor for first-round
      publishing is met: any SIR is present, including the AI SIR /
      research output. A partial report is possible even if vendor SIR,
      BI, or RayCon are still pending.
    * ``would_be_filled_now`` / ``would_be_pending`` — token paths the
      report would fill or leave pending if it ran right now.
    * ``vendor_gate_enabled`` — which view (vendor-strict vs. legacy) is
      being reported.
    * ``m1_folder_missing`` — true when the per-site ``M1`` Drive
      subfolder hasn't been created yet (the inbox scanner creates it
      on first upload). When true, ``drive_folder_url`` is ``null`` and
      the vendor / RayCon slots will all surface as missing/pending —
      no folder is created, since this tool is strictly read-only.

    Use this BEFORE ``create_dd_report`` to decide whether to run now
    (accept partial) or wait.

    Differs from ``check_site_readiness``: that tool is the
    pre-generation projection used internally by ``create_dd_report``;
    ``diagnose_site_readiness`` is the user/agent-facing diagnostic that
    composes the cron-path readiness view, RayCon dispatch state, and
    full token projection into one structured payload. Reports the
    cron-path view (vendor gate enforced) by default; never bypass.

    This tool is strictly read-only — it must never trigger generation,
    republish, or any other side effect.
    """
    logger.info("Tool called: diagnose_site_readiness - %s", site_name)

    if not site_name or not site_name.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "site_name must be a non-empty string",
        }
    if not drive_folder_url or not drive_folder_url.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "drive_folder_url must be provided for site readiness diagnostics",
        }

    def _work() -> dict[str, Any]:
        # Local imports to avoid pulling anthropic et al. into the
        # top-level server import path.
        from .report_pipeline import (
            _missing_required_docs,
            _vendor_gate_enabled,
            check_site_readiness_direct,
            list_shared_folders_once,
        )

        try:
            site_title = site_name.strip()
            address = site_address.strip() or None

            gc = _make_google_client()
            match_terms = _build_site_match_terms(site_title, address)
            shared_cache = list_shared_folders_once(gc)

            # ``read_only=True`` propagates two suppressions through the
            # readiness check: it skips the ``M1`` folder creation in
            # ``_resolve_m1_folder`` and skips the provenance cache write
            # in ``classify_provenance``. Both side effects would otherwise
            # break the "strictly read-only" contract.
            readiness = check_site_readiness_direct(
                gc,
                drive_folder_url,
                match_terms,
                shared_cache,
                site_title=site_title,
                site_address=address,
                read_only=True,
            )

            # Resolve M1 (read-only — never create) to find the Block
            # Plan file ID and its modifiedTime. The Block Plan ID is
            # the lookup key into the dispatch state map; its
            # modifiedTime is the proxy for ``last_dispatch`` if the
            # dispatch state file is missing (e.g. the cron has never
            # run on this checkout).
            block_plan_file_id: str | None = None
            block_plan_modified: str | None = None
            try:
                m1_folder_id, _ = _resolve_m1_folder(
                    gc, drive_folder_url, create_if_missing=False
                )
            except Exception as e:
                logger.warning(
                    "diagnose_site_readiness: M1 resolve failed for '%s': %s",
                    site_title,
                    e,
                )
                m1_folder_id = None

            m1_folder_missing = m1_folder_id is None

            if m1_folder_id:
                try:
                    m1_docs = _list_m1_documents_by_type(gc, m1_folder_id)
                    bp = m1_docs.get("block_plan")
                    if bp:
                        block_plan_file_id = str(bp.get("id") or "") or None
                        mt = bp.get("modifiedTime")
                        if isinstance(mt, str):
                            block_plan_modified = mt
                except Exception as e:
                    logger.warning(
                        "diagnose_site_readiness: M1 list failed for '%s': %s",
                        site_title,
                        e,
                    )

            block_plan_present = block_plan_file_id is not None
            raycon_present = bool(readiness.get("raycon_scenario_found"))
            raycon_usable = bool(readiness.get("raycon_scenario_usable", raycon_present))
            raycon_failed = raycon_present and not raycon_usable

            # Resolve last_dispatch:
            #   1. If RayCon scenario is present, the dispatch already
            #      succeeded — surface its modifiedTime as the run
            #      timestamp.
            #   2. Otherwise, look up the dispatch state map for this
            #      site / block_plan_file_id.
            #   3. If neither is available but a Block Plan exists, fall
            #      back to its modifiedTime as a documented proxy.
            last_dispatch: str | None = None
            if raycon_present and m1_folder_id:
                # Re-resolve to pick up modifiedTime on the scenario.
                try:
                    m1_docs = _list_m1_documents_by_type(gc, m1_folder_id)
                    rs = m1_docs.get("raycon_scenario_json")
                    if rs and isinstance(rs.get("modifiedTime"), str):
                        last_dispatch = rs["modifiedTime"]
                except Exception:
                    pass
            if last_dispatch is None:
                state = _load_raycon_dispatch_state()
                last_dispatch, _ = _latest_dispatch_for_site(
                    state,
                    site_title,
                    block_plan_file_id=block_plan_file_id,
                )
            if last_dispatch is None and block_plan_modified:
                # Documented fallback: when the dispatch state file has
                # no entry (e.g. this checkout's cron has never run for
                # this site), use the Block Plan's Drive ``modifiedTime``
                # as a proxy for "RayCon has been notified".
                last_dispatch = block_plan_modified

            minutes_since = _minutes_since_iso(last_dispatch)

            vendor_gate_enabled = _vendor_gate_enabled()
            blocking = _build_blocking_entries(
                readiness=readiness,
                vendor_gate_enabled=vendor_gate_enabled,
                block_plan_present=block_plan_present,
                raycon_last_dispatch=last_dispatch,
                raycon_minutes_since=minutes_since,
            )

            # ``ready_for_full_report`` mirrors ``_missing_required_docs``
            # exactly. Under the legacy gate (VENDOR_GATE_ENABLED=0) this
            # means RayCon and vendor provenance do NOT block readiness —
            # ``blocking[]`` still reports their actual status so the
            # caller sees the full diagnostic view, but they don't pull
            # ``ready_for_full_report`` to False.
            ready_for_full_report = not _missing_required_docs(readiness)

            # First-round floor: any SIR is enough to publish the first
            # DDR slice, including AI-generated research output. Full
            # vendor readiness remains represented by ready_for_full_report.
            partial_report_possible = bool(readiness.get("sir_found"))

            projection = project_completeness_from_readiness(
                raycon_scenario_found=raycon_present,
                raycon_scenario_failed=raycon_failed,
                raycon_failure_reason=str(
                    readiness.get("raycon_scenario_failure_reason") or ""
                ).strip(),
            )
            pending_paths: list[str] = []
            for paths in projection.get("pending_reasons", {}).values():
                pending_paths.extend(paths)
            pending_set = set(pending_paths)
            # ``would_be_filled_now`` is the complement of pending paths
            # within the modeled token space (today: RayCon paths). When
            # more pending axes are added, extend the modeled space here.
            modeled_paths = set(raycon_token_paths())
            would_be_filled_now = sorted(modeled_paths - pending_set)
            would_be_pending = sorted(pending_set)

            return {
                "status": "success",
                "site": site_title,
                "ready_for_full_report": ready_for_full_report,
                "partial_report_possible": partial_report_possible,
                "vendor_gate_enabled": vendor_gate_enabled,
                "m1_folder_missing": m1_folder_missing,
                "blocking": blocking,
                "would_be_filled_now": would_be_filled_now,
                "would_be_pending": would_be_pending,
                "projected_completeness": projection,
                "pending_reason_labels": {
                    reason: REASON_DISPLAY_LABELS.get(reason, reason)
                    for reason in projection.get("pending_reasons", {})
                },
                "drive_folder_url": None if m1_folder_missing else drive_folder_url,
            }
        except Exception as e:
            logger.error("diagnose_site_readiness failed: %s", e)
            return {
                "status": "error",
                "error": "diagnose_site_readiness failed",
                "message": str(e),
                "site": site_name,
            }

    return await asyncio.to_thread(_work)


@mcp.tool()
async def check_report_completeness(doc_id: str) -> dict[str, Any]:
    """Check a generated DD report Google Doc for unresolved placeholders and pending sections.

    Reads the document text, scans for any remaining {{token}} patterns (unfilled
    placeholders - hard block) and [Not found / Pending] gap labels (acceptable sourced gaps).

    Args:
        doc_id: Google Docs file ID of the generated DD report.

    Returns:
        Dict with ready_to_send flag, unresolved_token_count, unresolved_tokens list,
        pending_section_count, pending_sections list, and a human-readable summary.
    """
    logger.info("Tool called: check_report_completeness - doc_id=%s", doc_id)

    if not doc_id or not doc_id.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "doc_id must be a non-empty string",
        }

    def _work() -> dict[str, Any]:
        try:
            gc = _make_google_client()
            text = gc.export_google_doc_as_text(doc_id)

            unresolved_tokens = re.findall(r"\{\{([^}]+)\}\}", text)
            unresolved_token_count = len(unresolved_tokens)
            raw_template_tokens = _extract_raw_template_tokens(text)
            raw_template_token_count = len(raw_template_tokens)
            pending_labels = re.findall(r"\[(?:Not found|Pending)[^\]]+\]", text, re.IGNORECASE)
            pending_section_count = len(pending_labels)
            invalid_can_we_answer, can_we_heading = _extract_invalid_can_we_answer(text)
            ready_to_send = (
                unresolved_token_count == 0
                and raw_template_token_count == 0
                and invalid_can_we_answer is None
            )

            if ready_to_send and pending_section_count == 0:
                summary = "Report complete. All fields filled."
            elif raw_template_token_count:
                summary = (
                    "Report NOT ready to send. "
                    f"{raw_template_token_count} raw template token(s) leaked into the document: "
                    + ", ".join(raw_template_tokens[:10])
                    + (" ..." if raw_template_token_count > 10 else "")
                )
            elif invalid_can_we_answer is not None:
                summary = (
                    "Report NOT ready to send. "
                    f"{can_we_heading} must be one of "
                    f"{', '.join(sorted(ALLOWED_CAN_WE_ANSWERS))}. "
                    f"Found: {invalid_can_we_answer!r}"
                )
            elif ready_to_send:
                summary = (
                    f"Report complete. {pending_section_count} field(s) pending "
                    f"(data not yet available): {'; '.join(pending_labels[:5])}"
                    + (" ..." if len(pending_labels) > 5 else "")
                )
            else:
                summary = (
                    f"Report NOT ready to send. {unresolved_token_count} unfilled placeholder(s): "
                    + ", ".join(f"{{{{{t}}}}}" for t in unresolved_tokens[:10])
                    + (" ..." if len(unresolved_tokens) > 10 else "")
                )

            return {
                "status": "success",
                "doc_id": doc_id,
                "ready_to_send": ready_to_send,
                "unresolved_token_count": unresolved_token_count,
                "unresolved_tokens": unresolved_tokens,
                "raw_template_token_count": raw_template_token_count,
                "raw_template_tokens": raw_template_tokens,
                "pending_section_count": pending_section_count,
                "pending_sections": pending_labels,
                "invalid_can_we_answer": invalid_can_we_answer,
                "summary": summary,
                "message": summary,
            }

        except Exception as e:
            logger.error("check_report_completeness failed: %s", e)
            return {
                "status": "error",
                "error": "check_report_completeness failed",
                "message": str(e),
            }

    return await asyncio.to_thread(_work)


def _extract_invalid_can_we_answer(text: str) -> tuple[str | None, str]:
    """Return non-canonical answer and detected heading label."""
    heading_patterns = (
        CURRENT_CAN_WE_HEADING,
        LEGACY_CURRENT_CAN_WE_HEADING,
        LEGACY_CAN_WE_HEADING,
    )
    for heading in heading_patterns:
        section_match = re.search(re.escape(heading) + r"\s+([^\r\n]+)", text, re.IGNORECASE)
        if not section_match:
            continue
        raw_value = section_match.group(1)
        if CAN_WE_SECTION_DELIMITER in raw_value:
            raw_value = raw_value.split(CAN_WE_SECTION_DELIMITER, 1)[0]
        answer = " ".join(raw_value.replace("*", " ").split()).strip()
        if not answer or answer in ALLOWED_CAN_WE_ANSWERS:
            return None, heading
        if re.match(r"^(?:Yes|No),\s+(?:if|because):?$", answer):
            return None, heading
        return answer, heading

    return None, CURRENT_CAN_WE_HEADING


def _extract_raw_template_tokens(text: str) -> list[str]:
    """Return canonical token names that appear as bare text in the document."""
    found: list[str] = []
    for token in TEMPLATE_TOKENS:
        if f"{{{{{token}}}}}" in text:
            continue
        if token in text:
            found.append(token)
    return found


MATTERBOT_BASE_URL = "https://matterbot-1819903979408.us-central1.run.app"
MATTERBOT_TIMEOUT_SECONDS = 30


@mcp.tool()
async def generate_marketing_pack(
    space_sid: str,
    space_name: str,
    tier: str = "standard",
    max_rooms: int = 0,
    room_types: str = "",
) -> dict[str, Any]:
    """Trigger MatterBot to generate a marketing rendering pack for a site.

    Fires a request to the MatterBot service which produces room-by-room
    marketing images from a Matterport scan. The rendered images are deposited
    into the site's M1 Property Acquired subfolder in Google Drive.

    This is fire-and-forget - MatterBot processes asynchronously. The images
    will appear in the Drive folder once generation completes (typically 5-15
    minutes depending on room count and tier).

    Args:
        space_sid: Matterport space SID from the scan URL.
        space_name: Space / site name (used for Drive folder matching).
        tier: Rendering quality tier - "standard" or "premium".
        max_rooms: Maximum rooms to render. 0 = service default (~12).
        room_types: Comma-separated room type filter (e.g., "classroom,commons,gym").
            Empty string = all room types.

    Returns:
        Dict with status and the request URL that was fired.
    """
    logger.info(
        "Tool called: generate_marketing_pack - space_sid=%s, space_name=%s, tier=%s",
        space_sid, space_name, tier,
    )

    if not space_sid or not space_sid.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "space_sid must be a non-empty string (Matterport space SID).",
        }
    normalized_space_sid = space_sid.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized_space_sid):
        return {
            "status": "error",
            "error": "Invalid parameter",
            "message": "space_sid may contain only letters, numbers, underscores, and hyphens.",
        }
    if not space_name or not space_name.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "space_name must be a non-empty string.",
        }
    if tier not in ("standard", "premium"):
        return {
            "status": "error",
            "error": "Invalid tier",
            "message": f"tier must be 'standard' or 'premium', got '{tier}'.",
        }

    url = f"{MATTERBOT_BASE_URL}/api/batch/generate-marketing-pack/{normalized_space_sid}"
    params: dict[str, str | int] = {"space_name": space_name.strip()}
    if tier != "standard":
        params["tier"] = tier
    if max_rooms > 0:
        params["max_rooms"] = max_rooms
    if room_types.strip():
        params["room_types"] = room_types.strip()

    def _work() -> dict[str, Any]:
        try:
            resp = requests.get(url, params=params, timeout=MATTERBOT_TIMEOUT_SECONDS)
            resp.raise_for_status()
            logger.info("MatterBot marketing pack triggered: %s (status=%d)", url, resp.status_code)
            return {
                "status": "success",
                "message": (
                    f"Marketing pack generation triggered for '{space_name.strip()}' "
                    f"(tier={tier}). Images will appear in the site's M1 folder "
                    "once MatterBot finishes processing (typically 5-15 minutes)."
                ),
                "request_url": resp.url,
                "http_status": resp.status_code,
            }
        except requests.Timeout:
            logger.warning("MatterBot request timed out for space %s", normalized_space_sid)
            return {
                "status": "error",
                "error": "MatterBot timeout",
                "message": (
                    f"MatterBot did not respond within {MATTERBOT_TIMEOUT_SECONDS}s. "
                    "The service may be starting up - retry in a minute."
                ),
            }
        except requests.RequestException as e:
            logger.error("MatterBot request failed: %s", e)
            return {
                "status": "error",
                "error": "MatterBot request failed",
                "message": str(e),
            }

    return await asyncio.to_thread(_work)


@mcp.tool()
async def apply_alpha_phasing_plan_skill(
    site_name: str,
    drive_folder_url: str,
    site_address: str = "",
    site_id: str = "",
    source_of_truth: str = "",
    quality_bar_target: str = "",
    opening_target_date: str = "",
    must_complete_before_opening: str = "",
    deferred_scopes: list[Any] | str | None = None,
    phase_i_scope_summary: str = "",
    phase_i_budget_items: list[dict[str, Any]] | list[str] | None = None,
    phase_ii_budget_items: list[dict[str, Any]] | list[str] | None = None,
    phase_ii_total_allowance: str = "",
    recommended_timing: str = "",
    render_deck_inputs: list[dict[str, Any]] | list[str] | None = None,
    source_notes: list[str] | str | None = None,
    budget_tracker_url: str = "",
) -> dict[str, Any]:
    """Publish an Alpha Phasing Plan workbook and return DDR-ready fields.

    The tool is deliberately strict about minimum inputs: it does not invent
    Phase II line items. When phasing inputs are incomplete, it returns concrete
    verification open items for the DDR instead of publishing a placeholder
    workbook.
    """

    logger.info("Tool called: apply_alpha_phasing_plan_skill - site=%s", site_name)

    missing = missing_alpha_phasing_inputs(
        site_name=site_name,
        site_address=site_address,
        source_of_truth=source_of_truth,
        quality_bar_target=quality_bar_target,
        opening_target_date=opening_target_date,
        must_complete_before_opening=must_complete_before_opening,
        deferred_scopes=deferred_scopes,
    )
    if not drive_folder_url.strip():
        missing.append("site Drive folder URL")
    if missing:
        open_items = alpha_phasing_open_items(missing)
        return {
            "status": "blocked",
            "source_usable": False,
            "missing_inputs": missing,
            "report_data_fields": {
                "verification.open_items": open_items,
            },
            "message": (
                "Alpha Phasing Plan not published because minimum phasing "
                "inputs are incomplete."
            ),
        }

    folder_id = extract_folder_id_from_url(drive_folder_url)
    if not folder_id:
        return {
            "status": "error",
            "error": "Invalid Drive folder URL",
            "message": f"Could not extract folder ID from: {drive_folder_url}",
        }

    def _work() -> dict[str, Any]:
        try:
            skill = load_alpha_phasing_skill()
        except AlphaPhasingPlanError as e:
            logger.error("Failed to load alpha-phasing-plan skill: %s", e)
            return {
                "status": "error",
                "error": "Failed to load alpha-phasing-plan skill",
                "message": str(e),
            }

        gc = _make_google_client()
        try:
            target_folder = _get_or_create_m1_folder(gc, folder_id)
            target_folder_id = target_folder["id"]
        except Exception as e:
            logger.error("Failed to resolve M1 subfolder for '%s': %s", site_name, e)
            return {
                "status": "error",
                "error": "Failed to resolve M1 folder",
                "message": str(e),
            }

        workbook_bytes = build_alpha_phasing_workbook(
            site_name=site_name,
            site_address=site_address,
            source_of_truth=source_of_truth,
            quality_bar_target=quality_bar_target,
            opening_target_date=opening_target_date,
            must_complete_before_opening=must_complete_before_opening,
            deferred_scopes=deferred_scopes,
            phase_i_scope_summary=phase_i_scope_summary,
            phase_i_budget_items=phase_i_budget_items,
            phase_ii_budget_items=phase_ii_budget_items,
            phase_ii_total_allowance=phase_ii_total_allowance,
            recommended_timing=recommended_timing,
            render_deck_inputs=render_deck_inputs,
            source_notes=source_notes,
            budget_tracker_url=budget_tracker_url,
            skill=skill,
        )
        today_str = datetime.now().strftime("%Y-%m-%d")
        safe_site = re.sub(r"[^A-Za-z0-9._ -]+", "", site_name).strip() or "Site"
        workbook_name = f"Alpha Phasing Plan - {safe_site} - {today_str}.xlsx"

        try:
            workbook = gc.upload_file_to_folder(
                target_folder_id,
                workbook_name,
                workbook_bytes,
                mime_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
            )
        except Exception as e:
            logger.error("Alpha Phasing workbook upload failed: %s", e)
            return {
                "status": "error",
                "error": "Failed to upload Alpha Phasing Plan workbook",
                "message": str(e),
            }

        workbook_url = str(workbook.get("webViewLink") or "").strip()
        workbook_id = str(workbook.get("id") or "").strip()
        rhodes_registration = register_rhodes_document_for_upload(
            site_id=site_id,
            ddr_doc_type="alpha_phasing_plan_report",
            title=workbook_name,
            drive_file_id=workbook_id,
            drive_url=workbook_url,
            mime_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            original_filename=workbook_name,
            source="apply_alpha_phasing_plan_skill",
        )
        report_fields = build_alpha_phasing_report_fields(
            workbook_url=workbook_url,
            phase_i_scope_summary=phase_i_scope_summary,
            must_complete_before_opening=must_complete_before_opening,
            deferred_scopes=deferred_scopes,
            phase_ii_budget_items=phase_ii_budget_items,
            phase_ii_total_allowance=phase_ii_total_allowance,
            recommended_timing=recommended_timing,
            quality_bar_target=quality_bar_target,
        )
        return {
            "status": "success",
            "source_usable": True,
            "doc_type": "alpha_phasing_plan_report",
            "source_type": "alpha_phasing_plan_report",
            "workbook_id": workbook_id,
            "workbook_url": workbook_url,
            "workbook_name": workbook_name,
            "rhodes_registration": rhodes_registration,
            "skill_version": skill.version,
            "skill_source": skill.source,
            "scorecard_theme_id": skill.scorecard_theme_id,
            "report_data_fields": report_fields,
            "message": f"Created '{workbook_name}' in Drive",
        }

    return await asyncio.to_thread(_work)


@mcp.tool()
async def save_skill_report(
    skill_name: str,
    site_name: str,
    drive_folder_url: str,
    skill_data: dict[str, Any],
) -> dict[str, Any]:
    """Save a skill assessment as a standalone Google Doc in the site's M1 subfolder.

    Creates a document named "{skill_name} Assessment - {site_name}" containing
    the full structured skill output. The tool formats the data into a readable
    document - pass the complete result dict from the skill tool.

    Args:
        skill_name: Skill name (e.g., "E-Occupancy", "School Approval").
        site_name: Site name for the document title.
        drive_folder_url: Google Drive folder URL for the site.
        skill_data: Full result dict from apply_e_occupancy_skill or
            apply_school_approval_skill.

    Returns:
        Dict with status, doc_url, and doc_id.
    """
    logger.info("Tool called: save_skill_report - skill=%s, site=%s", skill_name, site_name)

    if not skill_name or not site_name or not drive_folder_url or not skill_data:
        return {
            "status": "error",
            "error": "Missing parameters",
            "message": "skill_name, site_name, drive_folder_url, and skill_data are required",
        }

    folder_id = extract_folder_id_from_url(drive_folder_url)
    if not folder_id:
        return {
            "status": "error",
            "error": "Invalid Drive folder URL",
            "message": f"Could not extract folder ID from: {drive_folder_url}",
        }

    def _work() -> dict[str, Any]:
        gc = _make_google_client()
        today_str = datetime.now().strftime("%m/%d/%Y")
        doc_name = f"{skill_name} Assessment - {site_name}"
        content = _format_skill_document(skill_name, site_name, today_str, skill_data)

        try:
            target_folder = _get_or_create_m1_folder(gc, folder_id)
            target_folder_id = target_folder["id"]
        except Exception as e:
            logger.error("Failed to resolve M1 subfolder for '%s': %s", site_name, e)
            return {
                "status": "error",
                "error": "Failed to resolve M1 folder",
                "message": str(e),
            }

        try:
            doc = gc.create_document(
                name=doc_name,
                folder_id=target_folder_id,
                text_content=content,
            )
            return {
                "status": "success",
                "doc_id": doc.get("id", ""),
                "doc_url": doc.get("webViewLink", ""),
                "doc_name": doc_name,
                "message": f"Created '{doc_name}' in Drive",
            }
        except Exception as e:
            logger.error("save_skill_report failed: %s", e)
            return {
                "status": "error",
                "error": "Failed to create document",
                "message": str(e),
            }

    return await asyncio.to_thread(_work)


def _format_skill_document(
    skill_name: str,
    site_name: str,
    date: str,
    data: dict[str, Any],
) -> str:
    """Format a skill result dict into readable document text."""
    lines: list[str] = [
        f"{skill_name} Assessment",
        f"Site: {site_name}",
        f"Date: {date}",
        "",
    ]

    if skill_name == "E-Occupancy":
        lines.extend([
            "Scoring",
            f"  Final Score: {data.get('final_score', 'N/A')}/100",
            f"  Zone: {data.get('zone', 'N/A')}",
            f"  Conversion Tier: {data.get('tier', 'N/A')}",
            f"  Estimated Timeline: {data.get('timeline', 'N/A')}",
            f"  Confidence Level: {data.get('confidence', 'N/A')}",
            f"  Skill Version: {data.get('ease_conversion_skill_version', 'N/A')}",
            f"  Skill Source: {data.get('ease_conversion_skill_source', 'N/A')}",
            f"  Reference Source: {data.get('ease_conversion_reference_source', 'N/A')}",
            f"  Scorecard Theme: {data.get('ease_conversion_scorecard_theme_id', 'N/A')}",
            "",
            "Building Type Analysis",
            f"  Matched Building Type: {data.get('matched_building_type', 'N/A')}",
            f"  Base Score (before deductions): {data.get('base_score', 'N/A')}",
            "",
        ])
        deductions = data.get("deductions_applied", [])
        if deductions:
            lines.append("Tenant Deductions Applied")
            for d in deductions:
                lines.append(f"  - {d}")
            lines.append("")
        else:
            lines.extend(["Tenant Deductions Applied", "  None", ""])

        rdf = data.get("report_data_fields", {})
        if rdf:
            lines.append("Report Fields")
            e_occ_labels = {
                "q2.e_occupancy_score": "E-Occupancy Score",
                "q2.e_occupancy_zone": "E-Occupancy Zone",
                "q2.e_occupancy_tier": "E-Occupancy Tier",
                "q2.e_occupancy_timeline": "E-Occupancy Timeline",
                "q2.e_occupancy_confidence": "E-Occupancy Confidence",
                "q2.e_occupancy_skill_version": "Skill Version",
                "q2.e_occupancy_skill_source": "Skill Source",
                "q2.e_occupancy_reference_source": "Reference Source",
                "q2.e_occupancy_scorecard_theme_id": "Scorecard Theme",
            }
            for k, v in sorted(rdf.items()):
                label = e_occ_labels.get(k, k)
                lines.append(f"  {label}: {v}")
            lines.append("")

    elif skill_name == "RayCon Scenario":
        # The skill_data is the full parsed raycon_scenario.json plus a
        # flattened ``report_data_fields`` map. We surface envelope status
        # first (so a failed run is unmistakable), then scenarios when
        # available, then provenance.
        rdf = data.get("report_data_fields", {}) or {}
        status = rdf.get("exec.raycon_status", "") or "completed"
        run_id = rdf.get("exec.raycon_run_id", "")
        bp_used = rdf.get("exec.raycon_block_plan_used", "")
        summary = rdf.get("exec.raycon_summary", "")
        failure_reason = rdf.get("exec.raycon_failure_reason", "")

        lines.extend([
            "RayCon Run",
            f"  Status: {status}",
            f"  Run ID: {run_id or 'N/A'}",
            f"  Block Plan File ID: {bp_used or 'N/A'}",
        ])
        if summary:
            lines.append(f"  Summary: {summary}")
        lines.append("")

        if failure_reason:
            lines.extend([
                "Validation Errors",
                f"  {failure_reason}",
                "",
                "No scenario pricing was produced. The Block Plan, SIR, or other",
                "inputs need to be corrected and re-uploaded for RayCon to retry.",
                "",
            ])
        else:
            # Scenario summary table — capex + open date for both buckets.
            lines.extend([
                "Scenario Summary",
                f"  Fastest Open  CapEx: {rdf.get('exec.fastest_open_capex', 'N/A')}",
                f"  Fastest Open  Open Date: {rdf.get('exec.fastest_open_open_date', 'N/A')}",
                f"  Max Capacity  CapEx: {rdf.get('exec.max_capacity_capex', 'N/A')}",
                f"  Max Capacity  Open Date: {rdf.get('exec.max_capacity_open_date', 'N/A')}",
                "",
            ])
            # Detailed cost breakdown table — row keys come from
            # RAYCON_BREAKDOWN_ROWS so the column count matches the current
            # template exactly.
            lines.append("Detailed Cost Breakdown")
            for row_key, row_label in RAYCON_BREAKDOWN_ROWS:
                fo = rdf.get(f"exec.cost_{row_key}_fastest_open", "")
                mc = rdf.get(f"exec.cost_{row_key}_max_capacity", "")
                lines.append(f"  {row_label}: Fastest Open {fo} | Max Capacity {mc}")
            lines.append("")

        # Always surface RayCon's room schedule when present — useful even
        # on failed runs, because the rooms tell us what RayCon read out
        # of the Block Plan before it got stuck on validation.
        analysis = data.get("analysis")
        rooms = []
        if isinstance(analysis, dict) and isinstance(analysis.get("rooms"), list):
            rooms = analysis["rooms"]
        elif isinstance(data.get("rooms"), list):  # legacy flat shape
            rooms = data["rooms"]
        if rooms:
            lines.append("Room Schedule")
            for room in rooms:
                if not isinstance(room, dict):
                    continue
                name = room.get("name", "")
                rtype = room.get("type", "")
                sqft = room.get("sqft", "")
                lines.append(f"  {name} ({rtype}): {sqft} SF")
            lines.append("")

    elif skill_name == "School Approval":
        lines.extend([
            "State Requirements",
            f"  State: {data.get('state', 'N/A')}",
            f"  Archetype: {data.get('archetype', 'N/A')}",
            f"  Score: {data.get('score', 'N/A')}/100",
            f"  Zone: {data.get('zone', 'N/A')}",
            f"  Approval Type: {_humanize_approval_type(data.get('approval_type', 'N/A'))}",
            f"  Gating Requirement: {'Yes' if data.get('gating') else 'No'}",
            f"  Timeline: {data.get('timeline_days', 'N/A')} days",
            f"  Confidence Level: {data.get('confidence', 'N/A')}",
            f"  Rules Version: {data.get('rules_version', 'N/A')}",
            f"  Skill Version: {data.get('school_approval_skill_version', 'N/A')}",
            f"  Skill Source: {data.get('school_approval_skill_source', 'N/A')}",
            f"  Executive Status: {data.get('exec_c_edreg_status', 'N/A')}",
            f"  Alpha State Reference: {data.get('alpha_state_reference', 'N/A')}",
            "",
            "Steps to Allow Operation",
            f"  {data.get('steps_to_allow_operation', 'N/A')}",
            "",
            "Summary",
            f"  {data.get('state_school_registration_summary', 'N/A')}",
            "",
        ])

        rdf = data.get("report_data_fields", {})
        if rdf:
            lines.append("Report Fields")
            school_labels = {
                "q1.state_school_registration": "State School Registration",
                "q1.school_approval_type": "Approval Type",
                "q1.school_approval_gating": "Gating Requirement",
                "q1.school_approval_zone": "School Approval Zone",
                "q1.school_approval_archetype": "State Archetype",
                "q1.school_approval_timeline_days": "Approval Timeline (days)",
                "q1.school_approval_exec_status": "Executive Status",
                "q1.school_approval_alpha_reference": "Alpha State Reference",
                "q1.school_approval_rules_version": "Rules Version",
                "q1.school_approval_skill_version": "Skill Version",
                "q1.school_approval_skill_source": "Skill Source",
                "q1.steps_to_allow_operation": "Steps to Allow Operation",
            }
            for k, v in sorted(rdf.items()):
                label = school_labels.get(k, k)
                lines.append(f"  {label}: {v}")
            lines.append("")

    else:
        # Generic fallback — humanize keys
        lines.append("Data")
        for k, v in sorted(data.items()):
            label = k.replace("_", " ").replace(".", " — ").title()
            if isinstance(v, dict):
                lines.append(f"  {label}:")
                for rk, rv in sorted(v.items()):
                    sub_label = rk.replace("_", " ").replace(".", " — ").title()
                    lines.append(f"    {sub_label}: {rv}")
            elif isinstance(v, list):
                lines.append(f"  {label}:")
                for item in v:
                    lines.append(f"    - {item}")
            else:
                lines.append(f"  {label}: {v}")
        lines.append("")

    return "\n".join(lines)


def _humanize_approval_type(approval_type: str) -> str:
    """Convert UPPER_SNAKE_CASE approval type to readable text."""
    return approval_type.replace("_", " ").title()


@mcp.tool()
async def send_dd_report_email(
    site_name: str,
    report_url: str,
    key_findings: str,
    additional_recipients: str = "",
) -> dict[str, Any]:
    """Send the completed DD report by email.

    Sends to the configured DD_REPORT_EMAIL_RECIPIENTS plus any additional
    recipients. Duplicates are removed.

    Args:
        site_name: Site name for the email subject line.
        report_url: URL of the generated DD report Google Doc.
        key_findings: Short summary of key findings to include in the email body.
        additional_recipients: Comma-separated email addresses to add (e.g., P1 Assignee).

    Returns:
        Dict indicating success or error with recipient details.
    """
    logger.info("Tool called: send_dd_report_email - site=%s", site_name)

    if not site_name or not report_url:
        return {
            "status": "error",
            "error": "Missing parameters",
            "message": "site_name and report_url are required",
        }

    settings = get_settings()
    if not settings.email_sender or not settings.email_app_password:
        return {
            "status": "error",
            "error": "Email not configured",
            "message": "EMAIL_SENDER and EMAIL_APP_PASSWORD must be set.",
        }

    base_recipients = [
        recipient.strip()
        for recipient in settings.dd_report_email_recipients.split(",")
        if recipient.strip()
    ] if settings.dd_report_email_recipients else []
    extra_recipients = [
        recipient.strip()
        for recipient in additional_recipients.split(",")
        if recipient.strip()
    ] if additional_recipients else []

    seen: set[str] = set()
    recipients: list[str] = []
    for recipient in base_recipients + extra_recipients:
        recipient_lower = recipient.lower()
        if recipient_lower not in seen:
            seen.add(recipient_lower)
            recipients.append(recipient)

    if not recipients:
        return {
            "status": "error",
            "error": "No recipients",
            "message": "No recipients configured and no additional_recipients provided.",
        }

    subject = f"DD Report Ready - {site_name}"
    safe_site_name = escape_html_text(site_name)
    safe_key_findings = escape_html_text(key_findings)
    safe_report_url = sanitize_http_url(report_url)
    if not safe_report_url:
        return {
            "status": "error",
            "error": "Invalid report_url",
            "message": "report_url must be a valid http or https URL.",
        }

    html_body = f"""
<html><body>
<h2>Due Diligence Report - {safe_site_name}</h2>
<p>A new Due Diligence report has been generated for <strong>{safe_site_name}</strong>.</p>
<p><a href="{safe_report_url}" style="font-size:16px;font-weight:bold;">View Report in Google Docs</a></p>
<h3>Key Findings</h3>
<pre style="background:#f5f5f5;padding:12px;border-radius:4px;">{safe_key_findings}</pre>
<p style="color:#888;font-size:12px;">Generated automatically by the Alpha DD Reporter.</p>
</body></html>
"""

    def _work() -> dict[str, Any]:
        try:
            send_email(
                sender=settings.email_sender,
                app_password=settings.email_app_password,
                recipients=recipients,
                subject=subject,
                html_body=html_body,
                global_cc=settings.global_email_cc,
            )
            return {
                "status": "success",
                "recipients": recipients,
                "subject": subject,
                "message": f"Email sent to {len(recipients)} recipient(s): {', '.join(recipients)}",
            }
        except Exception as e:
            logger.error("send_dd_report_email failed: %s", e)
            return {
                "status": "error",
                "error": "Email send failed",
                "message": str(e),
            }

    return await asyncio.to_thread(_work)


def main() -> None:
    """Main entry point for the MCP server."""
    logger.info("Starting Due Diligence Reporter MCP server (stdio transport)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
