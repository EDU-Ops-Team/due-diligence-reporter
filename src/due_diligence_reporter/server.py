"""MCP server for Alpha School Due Diligence Report generation."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import requests
from mcp.server import FastMCP
from tenacity import retry

from .assignment import assign_p1
from .classifier import (
    AI_GENERATED_DOC_TYPES,
    match_file_to_site_llm,
)
from .classifier import (
    classify_document_type as _classify_document_type,
)
from .config import get_settings
from .dashboard_publish import publish_site_record
from .google_client import GoogleClient
from .google_doc_builder import SOURCE_QUALITY_NOTES_KEY, build_dd_report_doc
from .report_schema import (
    ALLOWED_CAN_WE_ANSWERS,
    LINK_DISPLAY_LABELS,
    LINK_TOKENS,
    MISSING_P1_ASSIGNEE_LABEL,
    TEMPLATE_TOKENS,
    TOKEN_SOURCES,
    normalize_can_we_answer,
    normalize_report_data,
)
from .retry import retry_config
from .site_record import SiteRecord
from .utils import (
    build_hyperlink_requests,
    escape_html_text,
    extract_folder_id_from_url,
    extract_text_from_pdf_bytes,
    find_text_index_in_doc,
    flatten_report_data_for_replacement,
    sanitize_http_url,
    score_site_match_strength,
    send_email,
)
from .utils import build_site_match_terms as _build_site_match_terms
from .wrike import (
    build_site_summary,
    classify_comment_to_section,
    extract_p1_from_record,
    find_site_record,
    get_record_comments,
)

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
MIN_SITE_MATCH_SCORE = 20

_READ_CONTEXT_BY_FILE_ID: dict[str, dict[str, str]] = {}

CAN_WE_SECTION_DELIMITER = "Education Regulatory Approval:"
V3_CAN_WE_HEADING = "Can this school be open in time for the current school year (8/12 or 9/8)?"
LEGACY_V3_CAN_WE_HEADING = "Can this school be open in time for the current school year?"
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

# state -> (score, approval_type, gating, timeline_days)
_STATE_APPROVAL_TABLE: dict[str, tuple[int, str, bool, int]] = {
    "TX": (95, "NONE", False, 7),
    "ID": (92, "NONE", False, 7),
    "AK": (90, "NONE", False, 7),
    "OK": (90, "REGISTRATION_SIMPLE", False, 30),
    "WY": (90, "NONE", False, 7),
    "MT": (88, "NONE", False, 7),
    "MO": (88, "NONE", False, 7),
    "IN": (87, "NONE", False, 7),
    "IL": (86, "NONE", False, 7),
    "KS": (86, "NONE", False, 7),
    "NE": (86, "NONE", False, 7),
    "AL": (85, "NONE", False, 7),
    "AZ": (82, "REGISTRATION_SIMPLE", False, 30),
    "CO": (80, "REGISTRATION_SIMPLE", False, 30),
    "FL": (78, "REGISTRATION_SIMPLE", False, 30),
    "GA": (78, "REGISTRATION_SIMPLE", False, 30),
    "NC": (78, "REGISTRATION_SIMPLE", False, 30),
    "TN": (78, "REGISTRATION_SIMPLE", False, 30),
    "UT": (78, "REGISTRATION_SIMPLE", False, 30),
    "AR": (76, "REGISTRATION_SIMPLE", False, 30),
    "LA": (76, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "SC": (76, "REGISTRATION_SIMPLE", False, 30),
    "VA": (75, "REGISTRATION_SIMPLE", False, 30),
    "WI": (75, "REGISTRATION_SIMPLE", False, 30),
    "MI": (74, "REGISTRATION_SIMPLE", False, 30),
    "MN": (74, "REGISTRATION_SIMPLE", False, 30),
    "OH": (74, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "NM": (72, "REGISTRATION_SIMPLE", False, 30),
    "NV": (72, "LICENSE_REQUIRED", True, 150),
    "WA": (72, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "OR": (70, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "DE": (68, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "KY": (68, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "WV": (68, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "HI": (65, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "IA": (65, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "NH": (65, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "CT": (62, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "ME": (62, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "VT": (62, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "CA": (60, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "NJ": (60, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "PA": (60, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "MA": (58, "LOCAL_APPROVAL_REQUIRED", True, 120),
    "MD": (55, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "RI": (55, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90),
    "NY": (45, "COMPLEX_OR_OVERSIGHT", True, 365),
    "ND": (42, "COMPLEX_OR_OVERSIGHT", True, 365),
    "DC": (40, "COMPLEX_OR_OVERSIGHT", True, 365),
}

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


def _school_zone(score: int) -> str:
    if score >= 80:
        return "GREEN"
    if score >= 41:
        return "YELLOW"
    return "RED"


# ─────────────────────────────────────────────────────────────────────────────
# COST ESTIMATE - RayCon API
# ─────────────────────────────────────────────────────────────────────────────

# Region key aliases - map common city/state names to API region keys
_REGION_ALIASES: dict[str, str] = {
    "austin": "austin",
    "texas": "austin",
    "tx": "austin",
    "miami": "miami",
    "florida": "miami",
    "fl": "miami",
    "georgia": "miami",
    "ga": "miami",
    "san francisco": "sanfrancisco",
    "california": "sanfrancisco",
    "ca": "sanfrancisco",
    "bay area": "sanfrancisco",
    "los angeles": "sanfrancisco",
    "la": "sanfrancisco",
}


def _resolve_region(region_hint: str) -> str:
    """Map a city/state name to an API region key."""
    return _REGION_ALIASES.get(region_hint.lower().strip(), "default")


_RAYCON_BREAKDOWN_ROWS: tuple[tuple[str, str], ...] = (
    ("demolition", "Demolition"),
    ("framing_doors", "Framing / Doors"),
    ("mep_fire_life_safety", "MEP / Fire / Life Safety"),
    ("plumbing_bathrooms", "Plumbing / Bathrooms"),
    ("finish_work", "Finish Work"),
    ("furniture", "Furniture"),
    ("tech_security_signage", "Tech / Security / Signage"),
    ("other_hard_costs", "Other Hard Costs"),
    ("soft_costs", "Soft Costs"),
    ("gc_fee", "GC Fee"),
    ("contingency", "Contingency"),
    ("grand_total", "Grand Total"),
)


def _build_raycon_rooms_payload(rooms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a RayCon-compatible rooms payload from ISP room data."""
    payload: list[dict[str, Any]] = []
    for index, room in enumerate(rooms, start=1):
        payload.append({
            "name": str(room.get("name") or f"Room {index}"),
            "type": str(room.get("type") or "otherroom"),
            "sqft": int(room.get("sqft", 400)),
            "perimeterLinearFt": int(room.get("perimeterLinearFt", 0)),
            "wallHeightFt": int(room.get("wallHeightFt", 10)),
            "floor": str(room.get("floor") or room.get("floorLabel") or "1st Floor"),
        })
    return payload


def _auto_generate_rooms(total_sf: int, classroom_count: int) -> list[dict[str, Any]]:
    """Generate a default room mix when no ISP room list is available."""
    classrooms = max(1, classroom_count) if classroom_count > 0 else max(1, total_sf // 900)
    restroom_count = max(2, classrooms // 5)
    hallway_sf = max(200, int(total_sf * 0.15))
    multipurpose_sf = max(400, int(total_sf * 0.05))
    rooms: list[dict[str, Any]] = []
    for _ in range(classrooms):
        rooms.append({"type": "learningroom", "sqft": 450})
    for _ in range(restroom_count):
        rooms.append({"type": "restroom", "sqft": 150})
    rooms.append({"type": "hallway", "sqft": hallway_sf})
    rooms.append({"type": "lobby", "sqft": 400})
    rooms.append({"type": "multipurpose", "sqft": multipurpose_sf})
    return rooms


def _build_raycon_request_payload(
    *,
    site_name: str,
    total_building_sf: int,
    region: str,
    room_list: list[dict[str, Any]],
    address: str | None = None,
    inspection_summary: str | None = None,
    sir_summary: str | None = None,
) -> dict[str, Any]:
    """Build the POST body for RayCon chat."""
    payload: dict[str, Any] = {
        "messages": [
            {
                "role": "user",
                "content": (
                    "Calculate two deterministic school conversion scenarios for this space: "
                    "Fastest Open and Max Capacity scenarios. "
                    "Return structured estimate cards and cost breakdowns for both."
                ),
            }
        ],
        "space": {
            "name": site_name or "School Conversion",
            "rooms": _build_raycon_rooms_payload(room_list),
            "spaceDetails": {
                "address": address or "",
                "totalArea": total_building_sf,
                "grossFloorArea": total_building_sf,
            },
            "metadata": {
                "roomCount": len(room_list),
                "totalSqft": total_building_sf,
            },
            "source": "isp",
        },
        "region": region,
        "temperature": 0,
        "max_tool_rounds": 8,
    }
    drive_doc_summaries: dict[str, str] = {}
    if inspection_summary:
        drive_doc_summaries["inspection"] = inspection_summary
    if sir_summary:
        drive_doc_summaries["sir"] = sir_summary
    if drive_doc_summaries:
        payload["drive_doc_summaries"] = drive_doc_summaries
    return payload


def _read_raycon_done_event(response: requests.Response) -> dict[str, Any]:
    """Read an SSE stream and return the JSON payload from the final done event."""
    current_event = ""
    data_lines: list[str] = []
    error_message = ""
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.strip()
        if not line:
            if current_event == "done" and data_lines:
                return json.loads("\n".join(data_lines))  # type: ignore[no-any-return]
            if current_event == "error" and data_lines:
                error_payload = json.loads("\n".join(data_lines))
                error_message = error_payload.get("message", "Unknown RayCon error")
            current_event = ""
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            current_event = line.partition(":")[2].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.partition(":")[2].lstrip())
    if error_message:
        raise ValueError(error_message)
    raise ValueError("RayCon stream ended before a done event was received")


@retry(**retry_config())  # type: ignore[untyped-decorator]
def _call_raycon_api(
    api_url: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST to RayCon /v1/chat and return the final structured response."""
    response = requests.post(
        f"{api_url}/v1/chat",
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        json=payload,
        timeout=60,
        stream=True,
    )
    response.raise_for_status()
    return _read_raycon_done_event(response)


def _extract_raycon_scenario(
    response_data: dict[str, Any],
    scenario_key: str,
    card_label_hint: str,
) -> dict[str, Any]:
    """Extract one scenario from RayCon structured output with card fallback."""
    structured = response_data.get("structured", {})
    scenario = structured.get(scenario_key)
    if isinstance(scenario, dict):
        return scenario
    cards = response_data.get("estimate_cards", {}).get("cards", [])
    for card in cards:
        label = str(card.get("label", "")).lower()
        if card_label_hint in label:
            return {
                "categories": card.get("costBreakdown", []),
                "grandTotal": card.get("totals", {}).get("grandTotal", 0),
                "softCosts": card.get("totals", {}).get("softCosts", 0),
                "gcFee": card.get("totals", {}).get("gcFee", 0),
                "contingency": card.get("totals", {}).get("contingency", 0),
                "furniture": card.get("totals", {}).get("furniture", 0),
            }
    return {}


def _match_breakdown_bucket(category_name: str) -> str:
    """Normalize a RayCon category label into a fixed report row key."""
    label = category_name.lower()
    if "demo" in label:
        return "demolition"
    if any(term in label for term in ("framing", "drywall", "partition", "door")):
        return "framing_doors"
    if any(term in label for term in ("mep", "hvac", "electrical", "sprinkler", "fire", "alarm", "lighting")):
        return "mep_fire_life_safety"
    if any(term in label for term in ("plumbing", "restroom", "bathroom", "fixture")):
        return "plumbing_bathrooms"
    if "finish" in label:
        return "finish_work"
    if "furniture" in label:
        return "furniture"
    if any(term in label for term in ("internet", "low voltage", "security", "signage", "wayfinding", "tech")):
        return "tech_security_signage"
    return "other_hard_costs"


def _format_currency(value: float | int | None) -> str:
    """Format a numeric amount as a whole-dollar string."""
    amount = float(value or 0)
    return f"${round(amount):,}"


def _build_breakdown_fields(
    scenario_suffix: str,
    scenario_data: dict[str, Any],
) -> dict[str, str]:
    """Build fixed-row report fields from a RayCon scenario."""
    bucket_totals = {row_key: 0.0 for row_key, _ in _RAYCON_BREAKDOWN_ROWS}
    for category in scenario_data.get("categories", []):
        row_key = _match_breakdown_bucket(str(category.get("category", "")))
        bucket_totals[row_key] += float(category.get("subtotal", 0) or 0)
    bucket_totals["soft_costs"] = float(scenario_data.get("softCosts", 0) or 0)
    bucket_totals["gc_fee"] = float(scenario_data.get("gcFee", 0) or 0)
    bucket_totals["contingency"] = float(scenario_data.get("contingency", 0) or 0)
    bucket_totals["grand_total"] = float(scenario_data.get("grandTotal", 0) or 0)
    if not bucket_totals["furniture"]:
        bucket_totals["furniture"] = float(scenario_data.get("furniture", 0) or 0)
    return {
        f"exec.cost_{row_key}_{scenario_suffix}": _format_currency(bucket_totals[row_key])
        for row_key, _ in _RAYCON_BREAKDOWN_ROWS
    }


def _blank_breakdown_fields(scenario_suffix: str) -> dict[str, str]:
    """Return blank placeholders for a scenario that is not modeled yet."""
    return {
        f"exec.cost_{row_key}_{scenario_suffix}": ""
        for row_key, _ in _RAYCON_BREAKDOWN_ROWS
    }


# _classify_document_type moved to classifier.py (imported above as alias).
# _extract_city_from_address and _build_site_match_terms moved to utils.py.


def _find_site_docs_in_shared_folders(
    gc: GoogleClient,
    match_terms: list[str],
    *,
    site_title: str | None = None,
    site_address: str | None = None,
) -> dict[str, dict[str, Any] | None]:
    """Search the three shared Drive folders (SIR, ISP, Building Inspection) for docs matching a site.

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

    # Keep track of all files per folder for the LLM fallback pass
    all_files_by_type: dict[str, list[dict[str, Any]]] = {}

    for doc_type, folder_id in folder_map.items():
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
    if not site_title.strip() or doc_type == "report_trace":
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
    """Return only AI-generated report artifacts from a recursive site-folder listing."""
    reports: list[dict[str, Any]] = []
    for file_info in site_files:
        annotated = {**file_info, "doc_type": _classify_document_type(file_info.get("name", ""))}
        if annotated["doc_type"] not in AI_GENERATED_DOC_TYPES:
            continue
        annotated["reference_origin"] = "ai_generated"
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
        from .wrike import _get_all_site_records, load_wrike_config

        settings = get_settings()
        cfg = load_wrike_config()

        try:
            all_records = _get_all_site_records(cfg=cfg)
        except Exception as e:
            logger.warning("Could not fetch Wrike records for load counts: %s", e)
            all_records = []

        result = assign_p1(
            school_type=school_type,
            city=city,
            state=state,
            settings=settings,
            all_site_records=all_records,
            wrike_cfg=cfg,
        )
        result["site_name"] = site_name
        return result

    return await asyncio.to_thread(_work)


@mcp.tool()
async def get_site_record(site_name_or_id: str) -> dict[str, Any]:
    """Fetch a Wrike Site Record by name or ID.

    Searches for the site record matching the given name or Wrike ID. Returns
    address, school type, current stage, Drive folder URL, and all DD-relevant
    custom field metadata stored in Wrike.

    Args:
        site_name_or_id: Site name (e.g., "Alpha Austin Demo"), Wrike record ID,
            or Wrike permalink URL.

    Returns:
        Dict with site metadata, or error dict if not found.
    """
    logger.info("Tool called: get_site_record")
    logger.info("get_site_record params: site_name_or_id=%s", site_name_or_id)

    if not site_name_or_id or not site_name_or_id.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "site_name_or_id must be a non-empty string",
        }

    def _work() -> dict[str, Any]:
        try:
            record = find_site_record(site_name_or_id=site_name_or_id)

            if not record:
                logger.warning("No Site Record found for: %s", site_name_or_id)
                return {
                    "status": "error",
                    "error": "Site record not found",
                    "message": (
                        f"Could not find a Wrike Site Record matching '{site_name_or_id}'. "
                        "Try using the exact site name, a Wrike ID, or a Wrike permalink."
                    ),
                }

            summary = build_site_summary(record)
            logger.info(
                "Found Site Record: %s (id=%s, stage=%s)",
                summary.get("title"),
                summary.get("id"),
                summary.get("stage"),
            )

            return {
                "status": "success",
                "site": summary,
                "message": f"Found Site Record: {summary.get('title')}",
            }

        except Exception as e:
            logger.error("Failed to fetch Site Record: %s", e)
            return {
                "status": "error",
                "error": "Wrike API error",
                "message": str(e),
            }

    return await asyncio.to_thread(_work)


@mcp.tool()
async def list_drive_documents(
    drive_folder_url: str, site_name: str = ""
) -> dict[str, Any]:
    """List matched shared source reports plus AI-generated site reports.

    Searches the shared SIR, ISP, and Building Inspection folders when *site_name*
    is provided. From the site folder, returns only AI-generated report artifacts
    such as DD reports, E-Occupancy reports, School Approval reports, Opening Plans,
    and report traces.

    Args:
        drive_folder_url: Google Drive folder URL (from the site's Wrike record).
        site_name: Optional site name used to match docs in shared Drive folders
            (SIR, ISP, Building Inspection).  Pass the Wrike site title for best
            results.

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

    def _work() -> dict[str, Any]:
        try:
            gc = _make_google_client()

            all_site_files_raw = gc.list_files_recursive(folder_id, max_depth=2)
            site_files = _list_ai_generated_site_reports(all_site_files_raw)
            logger.info(
                "Found %d AI-generated reports in site folder (recursive, max_depth=2) %s",
                len(site_files), folder_id,
            )

            shared_folder_files: list[dict[str, Any]] = []
            address: str | None = None
            if site_name.strip():
                record = find_site_record(site_name_or_id=site_name)
                if record:
                    summary = build_site_summary(record)
                    address = summary.get("address")
                match_terms = _build_site_match_terms(site_name.strip(), address)
                shared_docs = _find_site_docs_in_shared_folders(
                    gc, match_terms,
                    site_title=site_name.strip(), site_address=address,
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

            total_files = len(site_files) + len(shared_folder_files)

            return {
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
    documents or the Wrike record.

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

    zone = "GREEN" if score == 100 else ("RED" if score == 0 else "YELLOW")
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
        "ibc_gates": ibc_gates,
        "ibc_flags": ibc_flags,
        "report_data_fields": {
            "q2.e_occupancy_score": str(score),
            "q2.e_occupancy_zone": zone,
            "q2.e_occupancy_tier": tier_label,
            "q2.e_occupancy_timeline": timeline,
            "q2.e_occupancy_confidence": confidence,
            "q2.e_occupancy_ibc_summary": ibc_summary,
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
    state: str,
    site_name: str = "",
    drive_folder_url: str = "",
) -> dict[str, Any]:
    """Apply the School Approval Skill to determine registration requirements for a state.

    Looks up the state in the Alpha School private school approval difficulty table and
    returns all registration requirements needed for Q1 — State School Registration.
    Call this tool in Step 5 using the state extracted from the site address.

    If site_name and drive_folder_url are provided, the full assessment is automatically
    saved as a Google Doc in the site's M1 subfolder and the doc_url is returned.

    Args:
        state: Two-letter US state abbreviation (e.g., "TX", "CA", "FL").
            Use "DC" for Washington D.C.
        site_name: Site name — pass to auto-publish the assessment as a Google Doc.
        drive_folder_url: Site Drive folder URL — pass to auto-publish.

    Returns:
        Dict with approval_type, gating, timeline, steps, summary, doc_url (if
        auto-published), and ready-to-use report_data_fields.
    """
    logger.info("Tool called: apply_school_approval_skill — state=%s", state)

    state_upper = state.strip().upper()

    if state_upper in _STATE_APPROVAL_TABLE:
        score, approval_type, gating, timeline_days = _STATE_APPROVAL_TABLE[state_upper]
        confidence = "HIGH"
    else:
        score, approval_type, gating, timeline_days = 70, "CERTIFICATE_OR_APPROVAL_REQUIRED", True, 90
        confidence = "LOW"
        logger.warning("State '%s' not in approval table — using default values", state_upper)

    zone = _school_zone(score)
    steps = _SCHOOL_APPROVAL_STEPS.get(
        approval_type, _SCHOOL_APPROVAL_STEPS["CERTIFICATE_OR_APPROVAL_REQUIRED"]
    )

    if zone == "GREEN":
        summary = (
            f"{state_upper} has minimal private school requirements "
            f"({approval_type.replace('_', ' ').title()}). "
            f"Timeline: {timeline_days} days."
        )
    elif zone == "YELLOW":
        gating_note = " This is a gating requirement before opening." if gating else ""
        summary = (
            f"{state_upper} requires {approval_type.replace('_', ' ').title()} "
            f"for private schools. Timeline: {timeline_days} days.{gating_note}"
        )
    else:
        summary = (
            f"{state_upper} has complex private school oversight requirements. "
            f"Gating approval required; timeline: {timeline_days}+ days. "
            "Engage legal counsel early."
        )

    result: dict[str, Any] = {
        "status": "success",
        "state": state_upper,
        "score": score,
        "zone": zone,
        "approval_type": approval_type,
        "gating": gating,
        "timeline_days": timeline_days,
        "confidence": confidence,
        "steps_to_allow_operation": steps,
        "state_school_registration_summary": summary,
        "report_data_fields": {
            "q1.state_school_registration": summary,
            "q1.school_approval_type": approval_type,
            "q1.school_approval_gating": str(gating).lower(),
            "q1.school_approval_timeline_days": str(timeline_days),
            "q1.steps_to_allow_operation": steps,
        },
        "message": (
            f"School approval for {state_upper}: {zone} (score {score}/100), "
            f"{approval_type}, {timeline_days}-day timeline."
        ),
    }

    # Auto-publish to Drive if site context provided
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


# ---------------------------------------------------------------------------
# Opening Plan v2 skill
# ---------------------------------------------------------------------------

_OPENING_PLAN_SKILL_DIR = Path(__file__).parent.parent.parent / "docs" / "skills" / "opening-plan-v2"


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
            plan_content = response.content[0].text if response.content else ""
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
                    target_folder_id = folder_id
                    try:
                        subfolders = gc.list_subfolders(folder_id)
                        for subfolder in subfolders:
                            if subfolder.get("name", "").lower().startswith("m1"):
                                target_folder_id = subfolder["id"]
                                logger.info("Found M1 subfolder for opening plan: %s", subfolder["name"])
                                break
                    except Exception as e:
                        logger.warning("Failed to list subfolders: %s -- saving to site root", e)

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
# Shovels.ai permit history helpers
# ---------------------------------------------------------------------------

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
    """Format acquisition_condition and risk_note flags as bullet text for report fields.

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
        "exec.risk_notes": "\n".join(risks),
    }


@mcp.tool()
async def get_permit_history(
    address: str,
    site_name: str = "",
    drive_folder_url: str = "",
) -> dict[str, Any]:
    """Fetch permit history for a property from Shovels.ai and identify DD risk flags.

    Calls the Shovels.ai API to retrieve permit counts, inspection quality metrics,
    and full permit history for a property address. Analyzes the history for
    acquisition condition triggers and risk signals.

    If site_name and drive_folder_url are provided, the full assessment is
    automatically saved as a Google Doc in the site's Drive folder.

    Args:
        address: Full property address (e.g., "345 Peachtree St NE, Atlanta, GA 30308").
        site_name: Site name — pass to auto-publish the assessment as a Google Doc.
        drive_folder_url: Site Drive folder URL — pass to auto-publish.

    Returns:
        Dict with status, coverage, metrics, permits, risk_flags,
        report_data_fields, and doc_url (if auto-published).
    """
    logger.info("Tool called: get_permit_history — address=%s", address)

    settings = get_settings()
    api_key = settings.shovels_api_key
    base_url = settings.shovels_api_base_url

    if not api_key:
        return {
            "status": "error",
            "error": "Configuration error",
            "message": "SHOVELS_API_KEY is not configured",
        }

    def _work() -> dict[str, Any]:
        # Step A — resolve address to geo_id
        try:
            search_result = _call_shovels_search(api_key, base_url, address)
        except requests.HTTPError as e:
            logger.error("Shovels address search HTTP error: %s", e)
            return {"status": "error", "error": "Shovels API error", "message": str(e)}
        except Exception as e:
            logger.error("Shovels address search failed: %s", e)
            return {"status": "error", "error": "Shovels API error", "message": str(e)}

        if search_result is None:
            logger.info("Shovels.ai: address not found in coverage — %s", address)
            return {
                "status": "success",
                "coverage": "not_found",
                "address_searched": address,
                "risk_flags": [],
                "report_data_fields": {
                    "exec.acquisition_conditions": "",
                    "exec.risk_notes": "",
                },
                "message": (
                    "[Not found — Shovels.ai did not match this address; permit history unavailable]"
                ),
            }

        geo_id = search_result.get("geo_id", "")
        normalized_address = search_result.get("name", address)

        # Step B — get current metrics
        try:
            metrics = _call_shovels_metrics(api_key, base_url, geo_id)
        except Exception as e:
            logger.error("Shovels metrics call failed: %s", e)
            return {"status": "error", "error": "Shovels API error", "message": str(e)}

        # Step C — get permit history (last 10 years, up to 50 permits)
        today = datetime.now()
        from_date = f"{today.year - 10}-{today.month:02d}-{today.day:02d}"
        to_date = today.strftime("%Y-%m-%d")
        try:
            permits = _call_shovels_permits(api_key, base_url, geo_id, from_date, to_date)
        except Exception as e:
            logger.error("Shovels permits call failed: %s", e)
            return {"status": "error", "error": "Shovels API error", "message": str(e)}

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
                f"{condition_count} acquisition condition(s), {risk_count} risk note(s)."
            ),
        }

    result = await asyncio.to_thread(_work)

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


@mcp.tool()
async def get_cost_estimate(
    total_building_sf: int,
    region: str = "default",
    rooms: list[dict[str, Any]] | None = None,
    classroom_count: int = 0,
    site_name: str = "",
    address: str = "",
    inspection_summary: str = "",
    sir_summary: str = "",
) -> dict[str, Any]:
    """Estimate Fastest Open and Max Capacity costs for a school conversion using RayCon."""
    logger.info(
        "Tool called: get_cost_estimate - total_sf=%d, region=%s, rooms_provided=%s",
        total_building_sf,
        region,
        rooms is not None,
    )

    if total_building_sf <= 0:
        return {
            "status": "error",
            "error": "Invalid parameter",
            "message": "total_building_sf must be a positive integer",
        }

    settings = get_settings()
    api_url = settings.pricing_api_url
    resolved_region = _resolve_region(region)
    room_list = rooms if rooms else _auto_generate_rooms(total_building_sf, classroom_count)
    rooms_note = (
        "ISP room list"
        if rooms
        else f"auto-generated ({len(room_list)} rooms from {classroom_count or 'inferred'} classrooms)"
    )
    logger.info("Using %s, region=%s, %d rooms", rooms_note, resolved_region, len(room_list))

    def _work() -> dict[str, Any]:
        try:
            payload = _build_raycon_request_payload(
                site_name=site_name,
                total_building_sf=total_building_sf,
                region=resolved_region,
                room_list=room_list,
                address=address or None,
                inspection_summary=inspection_summary or None,
                sir_summary=sir_summary or None,
            )
            raycon_data = _call_raycon_api(api_url, payload)
        except requests.HTTPError as e:
            logger.error("RayCon API HTTP error: %s", e)
            return {"status": "error", "error": "RayCon API error", "message": str(e)}
        except Exception as e:
            logger.error("RayCon API call failed: %s", e)
            return {"status": "error", "error": "RayCon API error", "message": str(e)}

        mvp_data = _extract_raycon_scenario(raycon_data, "costs_mvp", "mvp")
        max_capacity_data = _extract_raycon_scenario(raycon_data, "costs_ideal", "ideal")
        if not mvp_data and not max_capacity_data:
            return {
                "status": "error",
                "error": "RayCon API error",
                "message": "RayCon response did not include any cost scenarios",
            }

        report_fields: dict[str, str] = {}
        if mvp_data:
            report_fields["exec.fastest_open_capex"] = _format_currency(mvp_data.get("grandTotal"))
            report_fields.update(_build_breakdown_fields("fastest_open", mvp_data))
        else:
            logger.warning("RayCon did not return Fastest Open scenario; blanking Fastest Open cost fields")
            report_fields["exec.fastest_open_capex"] = ""
            report_fields.update(_blank_breakdown_fields("fastest_open"))
        if max_capacity_data:
            report_fields["exec.max_capacity_capex"] = _format_currency(max_capacity_data.get("grandTotal"))
            report_fields.update(_build_breakdown_fields("max_capacity", max_capacity_data))
        else:
            logger.warning("RayCon did not return Max Capacity scenario; blanking Max Capacity cost fields")
            report_fields["exec.max_capacity_capex"] = ""
            report_fields.update(_blank_breakdown_fields("max_capacity"))
        report_fields.update(_blank_breakdown_fields("recommended_path"))
        report_fields.update(_blank_breakdown_fields("max_value"))

        scenario_parts = []
        if mvp_data:
            scenario_parts.append(f"Fastest Open at {_format_currency(mvp_data.get('grandTotal'))}")
        if max_capacity_data:
            scenario_parts.append(f"Max Capacity at {_format_currency(max_capacity_data.get('grandTotal'))}")

        return {
            "status": "success",
            "region": resolved_region,
            "total_sf": total_building_sf,
            "rooms_used": rooms_note,
            "room_count": len(room_list),
            "cost_summary": {
                "fastest_open": _format_currency(mvp_data.get("grandTotal")) if mvp_data else None,
                "max_capacity": _format_currency(max_capacity_data.get("grandTotal")) if max_capacity_data else None,
            },
            "raycon_estimate_cards": raycon_data.get("estimate_cards", {}),
            "raycon_structured": raycon_data.get("structured", {}),
            "report_data_fields": report_fields,
            "message": (
                f"RayCon estimated {' and '.join(scenario_parts)} "
                f"({resolved_region} region, {len(room_list)} rooms, {total_building_sf:,} SF). "
                "Copy report_data_fields into report_data as flat top-level keys."
            ),
        }

    return await asyncio.to_thread(_work)


def _normalize_report_replacements(
    report_data: dict[str, Any],
    site_name: str,
    report_date: str,
    drive_folder_url: str,
) -> tuple[dict[str, str], list[str], list[str], dict[str, str]]:
    """Normalize report data and fill permissive V3 gap labels."""
    flat_report_data = flatten_report_data_for_replacement(report_data)
    report_data = _inject_wrike_report_defaults(report_data, site_name)
    replacements, unmatched, unfilled, token_sources = normalize_report_data(
        report_data,
        site_name=site_name,
        report_date=report_date,
    )
    if "exec.c_answer" in replacements:
        normalized_answer = normalize_can_we_answer(replacements["exec.c_answer"])
        if normalized_answer is not None:
            replacements["exec.c_answer"] = normalized_answer
    _fill_recommended_path_placeholders(replacements)
    _fill_fastest_open_placeholders(replacements)
    _fill_max_capacity_placeholders(replacements)
    _fill_max_value_placeholders(replacements)

    replacements.setdefault("meta.drive_folder_url", drive_folder_url)
    source_quality_notes = (
        flat_report_data.get("source_quality_notes")
        or flat_report_data.get("notes.source_quality")
        or flat_report_data.get(SOURCE_QUALITY_NOTES_KEY)
        or ""
    ).strip()
    if source_quality_notes:
        replacements[SOURCE_QUALITY_NOTES_KEY] = source_quality_notes
    return replacements, unmatched, unfilled, token_sources


def _inject_wrike_report_defaults(
    report_data: dict[str, Any],
    site_name: str,
) -> dict[str, Any]:
    """Inject authoritative Wrike defaults that should not depend on agent output."""
    enriched: dict[str, Any] = json.loads(json.dumps(report_data))
    try:
        record = find_site_record(site_name_or_id=site_name)
    except Exception as e:
        logger.warning("Could not fetch Wrike defaults for '%s': %s", site_name, e)
        record = None

    if not record:
        enriched["p1_assignee_name"] = MISSING_P1_ASSIGNEE_LABEL
        return enriched

    summary = build_site_summary(record)
    p1_name = summary.get("p1_assignee_name")
    p1_email = summary.get("p1_assignee_email")

    if isinstance(p1_name, str) and p1_name.strip():
        enriched["p1_assignee_name"] = p1_name.strip()
        site_block = enriched.get("site")
        if not isinstance(site_block, dict):
            site_block = {}
            enriched["site"] = site_block
        site_block["p1_assignee_name"] = p1_name.strip()
    # When Wrike P1 is absent, leave p1_assignee_name unset so _resolve_prepared_by
    # falls through to agent-provided meta.prepared_by (e.g. from LocationOS getSite)

    if isinstance(p1_email, str) and p1_email.strip():
        enriched["p1_assignee_email"] = p1_email.strip()

    return enriched


def _find_existing_report_doc(
    gc: GoogleClient,
    *,
    folder_id: str,
    doc_name: str,
) -> dict[str, Any] | None:
    """Return an existing same-name DD report in the site folder, if present."""
    try:
        files = gc.list_files_in_folder(folder_id)
    except Exception as e:
        logger.warning("Could not list folder %s while checking for existing report: %s", folder_id, e)
        return None

    for file_info in files:
        if file_info.get("name") == doc_name:
            return file_info
    return None


def _fill_scenario_placeholders(
    replacements: dict[str, str],
    *,
    scenario: str,
    label: str,
) -> None:
    """Fill missing V3 scenario summary and detailed breakdown fields."""
    for metric in ("capacity", "capex", "open_date"):
        token = f"exec.{scenario}_{metric}"
        if token not in replacements or not str(replacements[token]).strip():
            replacements[token] = label
    for row_key, _ in _RAYCON_BREAKDOWN_ROWS:
        token = f"exec.cost_{row_key}_{scenario}"
        if token not in replacements or not str(replacements[token]).strip():
            replacements[token] = label


def _fill_recommended_path_placeholders(replacements: dict[str, str]) -> None:
    _fill_scenario_placeholders(
        replacements,
        scenario="recommended_path",
        label="[Not found - Recommended Path scenario not provided]",
    )


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


def _fill_max_value_placeholders(replacements: dict[str, str]) -> None:
    _fill_scenario_placeholders(
        replacements,
        scenario="max_value",
        label="[Not found - Max Value scenario not yet defined]",
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


def _build_report_trace_data(
    site_name: str,
    report_date: str,
    doc_id: str,
    doc_url: str | None,
    replacements: dict[str, str],
    unfilled: list[str],
    unmatched: list[str],
    hyperlink_trace: dict[str, Any],
    token_evidence: dict[str, str] | None,
) -> dict[str, Any]:
    """Build the report trace payload persisted beside the DD report."""
    evidence = token_evidence or {}
    token_report = {
        token: {
            "value": replacements.get(token, "")[:200],
            "source": TOKEN_SOURCES.get(token, "Unknown"),
            "filled": token not in unfilled,
            **({"evidence": evidence[token][:500]} if token in evidence else {}),
        }
        for token in TEMPLATE_TOKENS
        if token not in LINK_TOKENS
    }
    # Collect any non-template keys from token_evidence (e.g. full API payloads)
    # into a dedicated supplemental_evidence section so they are visible in the trace.
    template_key_set = set(TEMPLATE_TOKENS)
    supplemental_evidence = {
        k: v for k, v in evidence.items() if k not in template_key_set
    }
    return {
        "site_name": site_name,
        "date": report_date,
        "report_doc_id": doc_id,
        "report_doc_url": doc_url,
        "token_report": token_report,
        "unmatched_keys": unmatched,
        "unfilled_tokens": unfilled,
        "hyperlinks": hyperlink_trace,
        **({"supplemental_evidence": supplemental_evidence} if supplemental_evidence else {}),
    }


def _upload_report_trace(
    gc: GoogleClient,
    folder_id: str,
    site_name: str,
    report_date: str,
    doc_id: str,
    trace_data: dict[str, Any],
) -> None:
    """Upload the report trace JSON and link it from the generated report.

    Finds the "Report Trace" row in the source documents table and
    inserts the trace label as a hyperlink in the link cell.
    """
    trace_name = f"{site_name} Report Trace - {report_date.replace('/', '-')}.json"
    trace_json = json.dumps(trace_data, indent=2)
    trace_file = gc.upload_file_to_folder(
        folder_id=folder_id,
        file_name=trace_name,
        file_bytes=trace_json.encode("utf-8"),
        mime_type="application/json",
    )
    trace_url = trace_file.get("webViewLink", "")
    logger.info("Uploaded report trace: %s", trace_url)
    if not trace_url:
        return

    trace_label = LINK_DISPLAY_LABELS.get("sources.trace_link", trace_url)

    # Find the Report Trace row in the source documents table.
    # The builder inserts the trace link cell as empty; we look for
    # the "Report Trace" label in column 0 and insert into column 1.
    doc = gc.get_document(doc_id)
    doc_body = doc.get("body", {})
    body_content = doc_body.get("content", [])

    trace_cell_idx = _find_trace_link_cell(body_content)
    if trace_cell_idx is not None:
        # Insert trace label text and apply hyperlink styling
        gc.batch_update_document(doc_id, [
            {
                "insertText": {
                    "location": {"index": trace_cell_idx},
                    "text": trace_label,
                }
            },
            {
                "updateTextStyle": {
                    "range": {
                        "startIndex": trace_cell_idx,
                        "endIndex": trace_cell_idx + len(trace_label),
                    },
                    "textStyle": {
                        "link": {"url": trace_url},
                        "foregroundColor": {
                            "color": {
                                "rgbColor": {"red": 0.067, "green": 0.333, "blue": 0.800},
                            },
                        },
                    },
                    "fields": "link,foregroundColor",
                }
            },
        ])
        logger.info("Linked trace report in doc: %s", trace_label)
    else:
        # Fallback: search for any existing trace label text
        start_idx = find_text_index_in_doc(doc_body, trace_label)
        if start_idx is not None:
            gc.batch_update_document(doc_id, [{
                "updateTextStyle": {
                    "range": {
                        "startIndex": start_idx,
                        "endIndex": start_idx + len(trace_label),
                    },
                    "textStyle": {"link": {"url": trace_url}},
                    "fields": "link",
                }
            }])
            logger.info("Linked trace report in doc (fallback): %s", trace_label)
        else:
            logger.warning("Could not find trace link cell in document")


def _find_trace_link_cell(body_content: list[dict[str, Any]]) -> int | None:
    """Find the insertion index for the trace link cell in the source docs table.

    Scans tables for a row whose first cell contains "Report Trace" and
    returns the start index of the second cell's first paragraph.
    """
    for element in body_content:
        if "table" not in element:
            continue
        for row in element["table"].get("tableRows", []):
            cells = row.get("tableCells", [])
            if len(cells) < 2:
                continue
            # Check if first cell contains "Report Trace"
            cell0_text = _extract_cell_text(cells[0])
            if "Report Trace" in cell0_text:
                # Return the start index of the second cell's content
                cell1_content = cells[1].get("content", [])
                if cell1_content:
                    first_para = cell1_content[0]
                    if "paragraph" in first_para:
                        elements = first_para["paragraph"].get("elements", [])
                        if elements:
                            return int(elements[0].get("startIndex", 0))
                        return int(first_para.get("startIndex", 0))
    return None


def _extract_cell_text(cell: dict[str, Any]) -> str:
    """Extract all text from a table cell."""
    texts: list[str] = []
    for content_el in cell.get("content", []):
        if "paragraph" in content_el:
            for pe in content_el["paragraph"].get("elements", []):
                text_run = pe.get("textRun")
                if text_run:
                    texts.append(text_run.get("content", ""))
    return "".join(texts)


@mcp.tool()
async def create_dd_report(
    site_name: str,
    drive_folder_url: str,
    report_data: dict[str, Any],
    token_evidence: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a completed DD report Google Doc for a site."""
    logger.info("Tool called: create_dd_report")
    logger.info(
        "create_dd_report params: site_name=%s, drive_folder_url=%s",
        site_name,
        drive_folder_url,
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

    today_str = datetime.now().strftime("%m/%d/%Y")
    doc_name = f"{site_name.strip()} DD Report - {today_str}"
    logger.info("Creating DD report: %s", doc_name)

    def _work() -> dict[str, Any]:
        try:
            gc = _make_google_client()
            existing_doc = _find_existing_report_doc(gc, folder_id=folder_id, doc_name=doc_name)
            if existing_doc:
                existing_doc_id = existing_doc.get("id")
                existing_doc_url = existing_doc.get("webViewLink")
                logger.info("Existing DD report found, reusing: %s (id=%s)", doc_name, existing_doc_id)
                return {
                    "status": "success",
                    "document": {
                        "id": existing_doc_id,
                        "name": doc_name,
                        "url": existing_doc_url,
                    },
                    "replacements_applied": 0,
                    "unmatched_agent_keys": 0,
                    "unfilled_template_tokens": 0,
                    "hyperlinks_applied": 0,
                    "message": f"DD report already exists: {existing_doc_url}",
                }

            logger.info("Creating blank document in folder %s as '%s'", folder_id, doc_name)
            new_doc = gc.create_document(
                name=doc_name,
                folder_id=folder_id,
                text_content="",
            )
            doc_id = new_doc.get("id")
            doc_url = new_doc.get("webViewLink")
            if not doc_id or not isinstance(doc_id, str):
                raise RuntimeError("Invalid document ID returned from create operation")

            logger.info("Created blank document: %s (id=%s)", doc_name, doc_id)
            replacements, unmatched, unfilled, _token_sources = _normalize_report_replacements(
                report_data=report_data,
                site_name=site_name.strip(),
                report_date=today_str,
                drive_folder_url=drive_folder_url,
            )
            logger.info(
                "Normalization: %d replacements, %d unmatched keys, %d unfilled tokens",
                len(replacements), len(unmatched), len(unfilled),
            )
            if unmatched:
                logger.warning("Unmatched agent keys (no template token): %s", unmatched)

            # Build the document structure programmatically
            builder_result = build_dd_report_doc(
                docs_service=gc.docs_service,
                drive_service=gc.drive_service,
                doc_id=doc_id,
                replacements=replacements,
                site_title=site_name.strip(),
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
            trace_data = _build_report_trace_data(
                site_name=site_name.strip(),
                report_date=today_str,
                doc_id=doc_id,
                doc_url=doc_url,
                replacements=replacements,
                unfilled=unfilled,
                unmatched=unmatched,
                hyperlink_trace=hyperlink_trace,
                token_evidence=token_evidence,
            )
            try:
                _upload_report_trace(
                    gc=gc,
                    folder_id=folder_id,
                    site_name=site_name.strip(),
                    report_date=today_str,
                    doc_id=doc_id,
                    trace_data=trace_data,
                )
            except Exception as e:
                logger.warning("Failed to upload report trace (report still valid): %s", e)

            # ── Publish to DD Dashboard ──────────────────────────────
            # Build a SiteRecord from the V3 replacements we already
            # have in memory and upsert it as JSON into the site
            # folder. The pipeline aggregator picks these up and
            # rebuilds the dashboard's sites.json. A publish failure
            # must never block the DD report itself, so this is
            # wrapped and logged.
            dashboard_payload: dict[str, Any] | None = None
            try:
                site_record = SiteRecord.from_replacements(
                    replacements,
                    site_name=site_name.strip(),
                    report_date=today_str,
                    drive_folder_url=drive_folder_url,
                    dd_report_url=doc_url or "",
                )
                dashboard_payload = publish_site_record(
                    gc=gc,
                    folder_id=folder_id,
                    record=site_record,
                )
                logger.info(
                    "Dashboard publish: slug=%s classification=%s confidence=%.2f",
                    site_record.slug,
                    site_record.classification.label,
                    site_record.classification.confidence,
                )
            except Exception as e:
                logger.warning(
                    "Failed to publish dashboard payload (report still valid): %s", e,
                )

            response: dict[str, Any] = {
                "status": "success",
                "document": {"id": doc_id, "name": doc_name, "url": doc_url},
                "replacements_applied": len(replacements),
                "unmatched_agent_keys": len(unmatched),
                "unfilled_template_tokens": len(unfilled),
                "hyperlinks_applied": hyperlink_trace["applied"],
                "message": f"DD report created: {doc_url}",
            }
            if dashboard_payload is not None:
                response["dashboard_payload"] = {
                    "file_id": dashboard_payload.get("file_id", ""),
                    "file_name": dashboard_payload.get("file_name", ""),
                    "web_view_link": dashboard_payload.get("web_view_link", ""),
                    "replaced_count": dashboard_payload.get("replaced_count", 0),
                }
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
async def check_site_readiness(site_name_or_id: str) -> dict[str, Any]:
    """Check whether a site has all required DD documents and whether a report already exists."""
    logger.info("Tool called: check_site_readiness - %s", site_name_or_id)

    if not site_name_or_id or not site_name_or_id.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "site_name_or_id must be a non-empty string",
        }

    def _work() -> dict[str, Any]:
        try:
            record = find_site_record(site_name_or_id=site_name_or_id)
            if not record:
                return {
                    "status": "error",
                    "error": "Site record not found",
                    "message": f"Could not find a Wrike Site Record matching '{site_name_or_id}'.",
                }

            summary = build_site_summary(record)
            site_title = summary.get("title", site_name_or_id)
            address = summary.get("address")
            drive_folder_url = summary.get("drive_folder_url")
            if not drive_folder_url:
                return {
                    "status": "error",
                    "error": "No Drive folder",
                    "message": f"Site record '{site_title}' has no Google Drive folder URL in Wrike.",
                }

            folder_id = extract_folder_id_from_url(drive_folder_url)
            if not folder_id:
                return {
                    "status": "error",
                    "error": "Invalid Drive folder URL",
                    "message": f"Could not parse folder ID from: {drive_folder_url}",
                }

            gc = _make_google_client()
            match_terms = _build_site_match_terms(site_title, address)
            logger.info("Match terms for '%s': %s", site_title, match_terms)
            shared_docs = _find_site_docs_in_shared_folders(
                gc, match_terms, site_title=site_title, site_address=address,
            )
            site_reports = _list_ai_generated_site_reports(
                gc.list_files_recursive(folder_id, max_depth=2)
            )

            files_by_type: dict[str, dict[str, Any] | None] = {
                "sir": shared_docs.get("sir"),
                "isp": shared_docs.get("isp"),
                "building_inspection": shared_docs.get("building_inspection"),
                "dd_report": None,
            }
            for file_info in site_reports:
                if file_info.get("doc_type") != "dd_report":
                    continue
                files_by_type["dd_report"] = _pick_preferred_report(
                    files_by_type["dd_report"],
                    file_info,
                )

            sir_found = files_by_type["sir"] is not None
            isp_found = files_by_type["isp"] is not None
            inspection_found = files_by_type["building_inspection"] is not None
            report_exists = files_by_type["dd_report"] is not None

            missing_docs: list[str] = []
            if not sir_found:
                missing_docs.append("sir")
            if not isp_found:
                missing_docs.append("isp")
            if not inspection_found:
                missing_docs.append("building_inspection")

            ready_for_report = sir_found and isp_found and inspection_found and not report_exists
            p1_profile = extract_p1_from_record(record)
            p1_email = p1_profile.get("email") if p1_profile else None
            p1_name = p1_profile.get("name") if p1_profile else None

            return {
                "status": "success",
                "site_title": site_title,
                "p1_assignee_name": p1_name,
                "p1_assignee_email": p1_email,
                "sir_found": sir_found,
                "isp_found": isp_found,
                "inspection_found": inspection_found,
                "report_exists": report_exists,
                "missing_docs": missing_docs,
                "ready_for_report": ready_for_report,
                "files": files_by_type,
                "drive_folder_url": drive_folder_url,
                "message": "\n".join([
                    f"Site '{site_title}' document readiness:",
                    f"  SIR: {'found - ' + (files_by_type.get('sir') or {}).get('name', '') if sir_found else 'not found'}",
                    f"  ISP: {'found - ' + (files_by_type.get('isp') or {}).get('name', '') if isp_found else 'not found'}",
                    f"  Building Inspection: {'found - ' + (files_by_type.get('building_inspection') or {}).get('name', '') if inspection_found else 'not found'}",
                    f"  DD Report: {'exists - ' + (files_by_type.get('dd_report') or {}).get('name', '') if report_exists else 'not yet created'}",
                    "",
                    "Ready for report generation." if ready_for_report else (
                        "Not ready - " + ", ".join(missing_docs) + " missing." if missing_docs else "Report already exists."
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
        V3_CAN_WE_HEADING,
        LEGACY_V3_CAN_WE_HEADING,
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
        if normalize_can_we_answer(answer) in ALLOWED_CAN_WE_ANSWERS:
            return answer, heading
        return answer, heading

    return None, V3_CAN_WE_HEADING


def _extract_raw_template_tokens(text: str) -> list[str]:
    """Return canonical token names that appear as bare text in the document."""
    found: list[str] = []
    for token in TEMPLATE_TOKENS:
        if f"{{{{{token}}}}}" in text:
            continue
        if token in text:
            found.append(token)
    return found


@mcp.tool()
async def get_site_comments(site_name_or_id: str) -> dict[str, Any]:
    """Retrieve Wrike record comments for a site, grouped by suggested report section.

    Comments are classified into report sections (q1, q2, q3, q4, appendix, general)
    using keyword matching. Useful for incorporating pre-app meeting notes, vendor
    updates, and other contextual information into the DD report.

    Args:
        site_name_or_id: Site name, Wrike record ID, or Wrike permalink URL.

    Returns:
        Dict with comments grouped by section, plus a flat list of all comments.
    """
    logger.info("Tool called: get_site_comments - %s", site_name_or_id)

    if not site_name_or_id or not site_name_or_id.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "site_name_or_id must be a non-empty string",
        }

    def _work() -> dict[str, Any]:
        try:
            record = find_site_record(site_name_or_id=site_name_or_id)
            if not record:
                return {
                    "status": "error",
                    "error": "Site record not found",
                    "message": f"Could not find a Wrike Site Record matching '{site_name_or_id}'.",
                }

            record_id = record.get("id")
            if not record_id:
                return {"status": "error", "error": "No record ID", "message": "Record has no ID."}

            comments = get_record_comments(record_id=record_id)
            if not comments:
                return {
                    "status": "success",
                    "site_title": record.get("title", site_name_or_id),
                    "comment_count": 0,
                    "by_section": {},
                    "all_comments": [],
                    "message": f"No comments found on Wrike record for '{record.get('title', site_name_or_id)}'.",
                }

            by_section: dict[str, list[dict[str, Any]]] = {}
            for comment in comments:
                section = classify_comment_to_section(comment["text"])
                by_section.setdefault(section, []).append(comment)

            return {
                "status": "success",
                "site_title": record.get("title", site_name_or_id),
                "comment_count": len(comments),
                "by_section": by_section,
                "all_comments": comments,
                "message": (
                    f"Found {len(comments)} comment(s) on '{record.get('title', site_name_or_id)}'. "
                    f"Sections: {', '.join(sorted(by_section.keys()))}."
                ),
            }

        except Exception as e:
            logger.error("get_site_comments failed: %s", e)
            return {
                "status": "error",
                "error": "get_site_comments failed",
                "message": str(e),
            }

    return await asyncio.to_thread(_work)


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
        space_sid: Matterport space SID (from the scan URL or Wrike record).
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

        target_folder_id = folder_id
        try:
            subfolders = gc.list_subfolders(folder_id)
            for subfolder in subfolders:
                if subfolder.get("name", "").lower().startswith("m1"):
                    target_folder_id = subfolder["id"]
                    logger.info("Found M1 subfolder: %s", subfolder["name"])
                    break
            else:
                logger.warning("M1 subfolder not found for '%s', saving to site root", site_name)
        except Exception as e:
            logger.warning("Failed to list subfolders for '%s': %s - saving to site root", site_name, e)

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
            }
            for k, v in sorted(rdf.items()):
                label = e_occ_labels.get(k, k)
                lines.append(f"  {label}: {v}")
            lines.append("")

    elif skill_name == "School Approval":
        lines.extend([
            "State Requirements",
            f"  State: {data.get('state', 'N/A')}",
            f"  Score: {data.get('score', 'N/A')}/100",
            f"  Zone: {data.get('zone', 'N/A')}",
            f"  Approval Type: {_humanize_approval_type(data.get('approval_type', 'N/A'))}",
            f"  Gating Requirement: {'Yes' if data.get('gating') else 'No'}",
            f"  Timeline: {data.get('timeline_days', 'N/A')} days",
            f"  Confidence Level: {data.get('confidence', 'N/A')}",
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
                "q1.school_approval_timeline_days": "Approval Timeline (days)",
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
    recipients (e.g., the P1 Assignee from Wrike). Duplicates are removed.

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
