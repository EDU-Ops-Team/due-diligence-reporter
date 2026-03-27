"""MCP server for Alpha School Due Diligence Report generation."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

import requests

from dotenv import load_dotenv
from mcp.server import FastMCP

from .classifier import classify_by_keywords, classify_document, match_file_to_site_llm
from .config import get_settings
from .google_client import GoogleClient
from .report_schema import (
    LINK_DISPLAY_LABELS,
    LINK_TOKENS,
    TEMPLATE_TOKENS,
    TOKEN_SOURCES,
    compute_deltas,
    normalize_report_data,
)
from .utils import (
    build_hyperlink_requests,
    build_replace_all_text_requests,
    extract_folder_id_from_url,
    extract_text_from_pdf_bytes,
    find_text_index_in_doc,
    send_email,
)
from .wrike import (
    build_site_summary,
    classify_comment_to_section,
    extract_p1_email_from_record,
    extract_p1_from_record,
    find_site_record,
    get_record_comments,
)

# Load environment variables from the project-root .env if present
load_dotenv()

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
# COST ESTIMATE — Building Optimizer API + per-SF code-required item estimates
# ─────────────────────────────────────────────────────────────────────────────

# Components that map to each DD report cost category (v2 API)
_FINISH_WORK_COMPONENTS = {"floors", "walls", "ceiling"}
_MEP_COMPONENTS = {"hvac", "lighting"}   # electrical (lighting) + mechanical; plumbing → bathrooms
_FFE_COMPONENTS = {"tech", "millwork", "security"}
_BATHROOM_COMPONENTS = {"plumbing", "fixtures"}  # restroom rooms only
_SPRINKLER_COMPONENTS = {"sprinkler"}
_FIRE_ALARM_COMPONENTS = {
    "fireAlarm", "emergencyLighting", "egressHardware",
    "fireCompliance", "fireMonitoring",
}

# Per-SF cost ranges for items NOT available in the Optimizer API
_PER_SF_RANGES: dict[str, tuple[float, float]] = {
    "structural": (8.0, 25.0),    # foundation/structural remediation
    "ada": (2.0, 8.0),            # ADA / accessibility upgrades
}

# Region key aliases — map common city/state names to API region keys
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


def _build_rooms_payload(
    rooms: list[dict[str, Any]],
    finish_level: int,
) -> list[dict[str, Any]]:
    """Build a rooms list for the v2 API with all components set to finish_level.

    Fire-safety components (sprinkler, fireAlarm, emergencyLighting, egressHardware,
    fireCompliance, fireMonitoring) are always included — the API returns $0 at level 0
    and real costs at level >= 1.
    """
    # Fire-safety components present on every v2 room type
    _FIRE_SAFETY = [
        "fireAlarm", "sprinkler", "emergencyLighting",
        "egressHardware", "fireCompliance", "fireMonitoring",
    ]
    component_keys_by_type: dict[str, list[str]] = {
        "learningroom":  ["floors", "walls", "ceiling", "lighting", "hvac", "tech", "millwork", "security"] + _FIRE_SAFETY,
        "hallway":       ["floors", "walls", "ceiling", "lighting", "hvac", "security"] + _FIRE_SAFETY,
        "office":        ["floors", "walls", "ceiling", "lighting", "hvac", "tech", "millwork", "security"] + _FIRE_SAFETY,
        "conferenceroom":["floors", "walls", "ceiling", "lighting", "hvac", "tech", "security"] + _FIRE_SAFETY,
        "breakroom":     ["floors", "walls", "ceiling", "lighting", "hvac", "millwork", "appliances", "security"] + _FIRE_SAFETY,
        "restroom":      ["floors", "walls", "ceiling", "lighting", "plumbing", "fixtures", "security"] + _FIRE_SAFETY,
        "limitlessroom": ["floors", "walls", "ceiling", "lighting", "hvac", "tech", "millwork", "security"] + _FIRE_SAFETY,
        "rocketroom":    ["floors", "walls", "ceiling", "lighting", "hvac", "tech", "millwork", "security"] + _FIRE_SAFETY,
        "multipurpose":  ["floors", "walls", "ceiling", "lighting", "hvac", "security"] + _FIRE_SAFETY,
        "reception":     ["floors", "walls", "ceiling", "lighting", "hvac", "security"] + _FIRE_SAFETY,
        "storage":       ["floors", "walls", "ceiling", "lighting", "hvac", "security"] + _FIRE_SAFETY,
        "lobby":         ["floors", "walls", "ceiling", "lighting", "hvac", "security"] + _FIRE_SAFETY,
        "otherroom":     ["floors", "walls", "ceiling", "lighting", "hvac", "security"] + _FIRE_SAFETY,
        "workshop":      ["floors", "walls", "ceiling", "lighting", "hvac", "tech", "millwork", "security"] + _FIRE_SAFETY,
    }
    default_keys = ["floors", "walls", "ceiling", "lighting"] + _FIRE_SAFETY
    result = []
    for room in rooms:
        room_type = room.get("type", "otherroom")
        keys = component_keys_by_type.get(room_type, default_keys)
        result.append({
            "type": room_type,
            "sqft": room.get("sqft", 400),
            "levels": {k: finish_level for k in keys},
        })
    return result


def _sum_components(api_rooms: list[dict[str, Any]], component_keys: set[str]) -> float:
    """Sum component subtotals across all rooms for the given component keys."""
    total = 0.0
    for room in api_rooms:
        for comp in room.get("components", []):
            if comp.get("key") in component_keys:
                total += comp.get("subtotal", 0.0)
    return total


def _sum_bathroom_components(api_rooms: list[dict[str, Any]]) -> float:
    """Sum plumbing + fixtures only for restroom-type rooms."""
    total = 0.0
    for room in api_rooms:
        if room.get("type") == "restroom":
            for comp in room.get("components", []):
                if comp.get("key") in _BATHROOM_COMPONENTS:
                    total += comp.get("subtotal", 0.0)
    return total


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


def _call_pricing_api(
    api_url: str,
    rooms_payload: list[dict[str, Any]],
    region: str,
) -> dict[str, Any]:
    """POST to the Building Optimizer v2 /v1/estimate endpoint."""
    resp = requests.post(
        f"{api_url}/v1/estimate",
        headers={"Content-Type": "application/json"},
        json={"rooms": rooms_payload, "region": region, "fees": {}},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


def _classify_document_type(filename: str) -> str:
    """Classify a Drive file by its document type based on the filename.

    Thin wrapper around :func:`classifier.classify_by_keywords` for backward
    compatibility.  Returns only the doc_type string (no confidence).
    """
    doc_type, _ = classify_by_keywords(filename)
    return doc_type


def _extract_city_from_address(address: str | None) -> str | None:
    """Extract city from a US-style address like '1234 Main St, Keller, TX 76248'.

    Splits on commas, walks backward skipping state/zip segments, and returns
    the first segment that looks like a city name.  Returns ``None`` if parsing
    fails.
    """
    if not address:
        return None

    parts = [p.strip() for p in address.split(",")]
    if len(parts) < 2:
        return None

    # Matches "TX", "TX 76248", "Texas 78746", "Florida 33431-1234", "Florida"
    _US_STATES = {
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
        "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
        "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
        "maine", "maryland", "massachusetts", "michigan", "minnesota",
        "mississippi", "missouri", "montana", "nebraska", "nevada",
        "new hampshire", "new jersey", "new mexico", "new york",
        "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
        "pennsylvania", "rhode island", "south carolina", "south dakota",
        "tennessee", "texas", "utah", "vermont", "virginia", "washington",
        "west virginia", "wisconsin", "wyoming",
        "district of columbia",
    }
    state_zip_re = re.compile(
        r"^[A-Z]{2}(\s+\d{5}(-\d{4})?)?$"  # TX, TX 76248
        r"|^\w[\w\s]*\s+\d{5}(-\d{4})?$",   # Texas 78746, New York 10001
        re.IGNORECASE,
    )

    for i in range(len(parts) - 1, -1, -1):
        segment = parts[i].strip()
        if not segment:
            continue
        if state_zip_re.match(segment):
            continue
        # Skip full state names without zip (e.g. "Florida", "New York")
        if segment.lower() in _US_STATES:
            continue
        # Skip segments that look like street lines (start with a digit)
        if re.match(r"^\d", segment):
            continue
        return segment

    return None


def _build_site_match_terms(
    site_title: str, address: str | None = None
) -> list[str]:
    """Build match terms from a site title and optional address for filename matching.

    Returns terms ordered most-specific first:
      1. Full site title (backward compat)
      2. City extracted from address
      3. Significant words from title (excluding stop words, short words, small numbers)
    """
    stop_words = {"alpha", "school", "the", "a", "an"}
    terms: list[str] = []
    seen_lower: set[str] = set()

    def _add(term: str) -> None:
        low = term.lower()
        if low not in seen_lower:
            terms.append(term)
            seen_lower.add(low)

    # 1. Full site title
    if site_title.strip():
        _add(site_title.strip())

    # 2. City from address
    city = _extract_city_from_address(address)
    if city:
        _add(city)

    # 3. Significant words from title
    for word in site_title.split():
        w = word.strip()
        low = w.lower()
        if low in stop_words:
            continue
        if len(low) < 3:
            continue
        # Skip pure numbers shorter than 4 digits (avoids matching dates like "25")
        if low.isdigit() and len(low) < 4:
            continue
        _add(w)

    return terms


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

    needles = [t.lower() for t in match_terms if t]

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

            # Pass 1: substring match — collect all matches, prefer PDF over converted Google Doc
            matches = [
                f for f in files
                if any(needle in f.get("name", "").lower() for needle in needles)
            ]
            if matches:
                pdf_matches = [f for f in matches if f.get("mimeType") == PDF_MIME]
                best = pdf_matches[0] if pdf_matches else matches[0]
                result[doc_type] = {**best, "doc_type": doc_type}

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
                    pdf_matched = [f for f in matched_files if f.get("mimeType") == PDF_MIME]
                    best_file = pdf_matched[0] if pdf_matched else matched_files[0]
                    result[doc_type] = {**best_file, "doc_type": doc_type}
                    logger.info(
                        "LLM matched '%s' to site '%s' for %s (conf=%.2f)",
                        best_fn, site_title, doc_type, llm_matches[best_fn],
                    )

    return result


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


@mcp.tool()
async def list_drive_documents(
    drive_folder_url: str, site_name: str = ""
) -> dict[str, Any]:
    """List all files in the site's Google Drive folder (recursive) and shared folders.

    Searches the site folder and all subfolders (up to 2 levels deep), plus the
    shared SIR, ISP, and Building Inspection folders when *site_name* is provided.
    Returns file name, ID, MIME type, modified date, and doc_type classification
    for each file found.  Use the returned file IDs and names with
    read_drive_document to read content.

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

    try:
        gc = _make_google_client()

        # List all files in the site folder recursively (root + all subfolders)
        all_site_files_raw = gc.list_files_recursive(folder_id, max_depth=2)
        site_files = [
            {**f, "doc_type": _classify_document_type(f.get("name", ""))}
            for f in all_site_files_raw
        ]
        logger.info(
            "Found %d files in site folder (recursive, max_depth=2) %s",
            len(site_files), folder_id,
        )

        # Search shared folders if site_name was provided
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
            for doc_type, doc in shared_docs.items():
                if doc is not None:
                    shared_folder_files.append(doc)
            if shared_folder_files:
                logger.info(
                    "Found %d files in shared folders for '%s'",
                    len(shared_folder_files),
                    site_name,
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
                f"Found {len(site_files)} files in site folder (recursive), "
                f"and {len(shared_folder_files)} files in shared folders "
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


@mcp.tool()
async def read_drive_document(file_id: str, file_name: str) -> dict[str, Any]:
    """Read and return the full text content of a Google Drive file.

    Supports:
    - Google Docs: exported as plain text via Drive API
    - PDFs: downloaded and text extracted using pypdf
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

    try:
        gc = _make_google_client()

        # Determine MIME type by fetching file metadata
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

        if mime_type in EXPORTABLE_MIME_TYPES:
            # Google Workspace file — export as plain text
            text_content = gc.export_google_doc_as_text(file_id)

        elif mime_type == PDF_MIME or file_name.lower().endswith(".pdf"):
            # PDF — download bytes then extract text
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

        elif mime_type.startswith("text/") or file_name.lower().endswith(
            (".txt", ".md", ".csv")
        ):
            # Plain text file — download directly
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

        logger.info(
            "read_drive_document: extracted %d characters from %s", len(text_content), file_name
        )

        # Truncate very large documents to avoid exceeding the LLM context window.
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


def _infer_mime_from_name(file_name: str) -> str:
    """Infer MIME type from file name extension."""
    name_lower = file_name.lower()
    if name_lower.endswith(".pdf"):
        return PDF_MIME
    if name_lower.endswith((".doc", ".docx")):
        return "application/msword"
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


@mcp.tool()
async def get_cost_estimate(
    total_building_sf: int,
    region: str = "default",
    rooms: list[dict[str, Any]] | None = None,
    classroom_count: int = 0,
) -> dict[str, Any]:
    """Estimate renovation costs for a school conversion using the Building Optimizer.

    Calls the Building Optimizer pricing API at two finish levels (Refresh = low,
    Alpha = high) to produce low/high cost ranges. Adds per-SF estimates for
    code-required items not in the API (structural, sprinkler, fire alarm, ADA).

    Call this tool in Step 3 after reading source documents (or after apply_e_occupancy_skill
    confirms the site is worth pursuing). Copy all values from report_data_fields into
    report_data before calling create_dd_report.

    Args:
        total_building_sf: Gross building area in square feet (from Wrike or documents).
        region: Location hint for regional cost multiplier. Accepts city or state name
            (e.g., "Austin", "TX", "Florida", "California") or API keys
            ("austin", "miami", "sanfrancisco", "default"). Defaults to "default"
            (national average) when unknown.
        rooms: Optional list of rooms from ISP output. Each item: {"type": str, "sqft": int}.
            Valid types: learningroom, hallway, office, conferenceroom, breakroom, restroom,
            limitlessroom, rocketroom, multipurpose, reception, storage, lobby, otherroom,
            workshop. If omitted, a default school layout is generated from classroom_count
            and total_building_sf.
        classroom_count: Number of classrooms (used only when rooms is not provided).

    Returns:
        Dict with low/high cost ranges for all Q3 cost categories and ready-to-use
        report_data_fields for all q3.* fields.
    """
    logger.info(
        "Tool called: get_cost_estimate — total_sf=%d, region=%s, rooms_provided=%s",
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
    rooms_note = "ISP room list" if rooms else f"auto-generated ({len(room_list)} rooms from {classroom_count or 'inferred'} classrooms)"

    logger.info("Using %s, region=%s, %d rooms", rooms_note, resolved_region, len(room_list))

    try:
        # Call API at level 1 (Refresh) for LOW estimates
        low_payload = _build_rooms_payload(room_list, finish_level=1)
        low_resp = _call_pricing_api(api_url, low_payload, resolved_region)
        low_rooms = low_resp["data"]["rooms"]

        # Call API at level 3 (Alpha) for HIGH estimates
        high_payload = _build_rooms_payload(room_list, finish_level=3)
        high_resp = _call_pricing_api(api_url, high_payload, resolved_region)
        high_rooms = high_resp["data"]["rooms"]

    except requests.HTTPError as e:
        logger.error("Pricing API HTTP error: %s", e)
        return {"status": "error", "error": "Pricing API error", "message": str(e)}
    except Exception as e:
        logger.error("Pricing API call failed: %s", e)
        return {"status": "error", "error": "Pricing API error", "message": str(e)}

    # Map API component sums to DD report cost categories
    finish_low  = _sum_components(low_rooms,  _FINISH_WORK_COMPONENTS)
    finish_high = _sum_components(high_rooms, _FINISH_WORK_COMPONENTS)
    mep_low     = _sum_components(low_rooms,  _MEP_COMPONENTS)
    mep_high    = _sum_components(high_rooms, _MEP_COMPONENTS)
    ffe_low     = _sum_components(low_rooms,  _FFE_COMPONENTS)
    ffe_high    = _sum_components(high_rooms, _FFE_COMPONENTS)
    bath_low    = _sum_bathroom_components(low_rooms)
    bath_high   = _sum_bathroom_components(high_rooms)
    sprinkler_low  = _sum_components(low_rooms,  _SPRINKLER_COMPONENTS)
    sprinkler_high = _sum_components(high_rooms, _SPRINKLER_COMPONENTS)
    fa_low         = _sum_components(low_rooms,  _FIRE_ALARM_COMPONENTS)
    fa_high        = _sum_components(high_rooms, _FIRE_ALARM_COMPONENTS)

    # Per-SF estimates for items still not in the Optimizer API
    sf = total_building_sf
    struct_low,  struct_high = sf * _PER_SF_RANGES["structural"][0], sf * _PER_SF_RANGES["structural"][1]
    ada_low,     ada_high    = sf * _PER_SF_RANGES["ada"][0],        sf * _PER_SF_RANGES["ada"][1]

    # Subtotals (before contingency)
    sub_low  = finish_low  + mep_low  + ffe_low  + bath_low  + struct_low  + sprinkler_low  + fa_low  + ada_low
    sub_high = finish_high + mep_high + ffe_high + bath_high + struct_high + sprinkler_high + fa_high + ada_high

    cont_low  = sub_low  * 0.15
    cont_high = sub_high * 0.20

    total_low  = sub_low  + cont_low
    total_high = sub_high + cont_high

    def fmt(n: float) -> str:
        return f"{round(n):,}"

    report_fields: dict[str, str] = {
        "q3.structural_low":    fmt(struct_low),
        "q3.structural_high":   fmt(struct_high),
        "q3.mep_low":           fmt(mep_low),
        "q3.mep_high":          fmt(mep_high),
        "q3.sprinkler_low":     fmt(sprinkler_low),
        "q3.sprinkler_high":    fmt(sprinkler_high),
        "q3.fire_alarm_low":    fmt(fa_low),
        "q3.fire_alarm_high":   fmt(fa_high),
        "q3.ada_low":           fmt(ada_low),
        "q3.ada_high":          fmt(ada_high),
        "q3.bathrooms_low":     fmt(bath_low),
        "q3.bathrooms_high":    fmt(bath_high),
        "q3.finish_work_low":   fmt(finish_low),
        "q3.finish_work_high":  fmt(finish_high),
        "q3.ffe_low":           fmt(ffe_low),
        "q3.ffe_high":          fmt(ffe_high),
        "q3.contingency_low":   fmt(cont_low),
        "q3.contingency_high":  fmt(cont_high),
        "q3.total_low":         fmt(total_low),
        "q3.total_high":        fmt(total_high),
        "q3.calculated_budget": f"${fmt(total_low)} – ${fmt(total_high)}",
        "q3.budget_formula":    (
            "Building Optimizer v2 (finish, MEP, FF&E, bathrooms, sprinkler, fire alarm) + "
            f"per-SF estimates (structural ${_PER_SF_RANGES['structural'][0]:.0f}–${_PER_SF_RANGES['structural'][1]:.0f}/SF, "
            f"ADA ${_PER_SF_RANGES['ada'][0]:.0f}–${_PER_SF_RANGES['ada'][1]:.0f}/SF) + 15–20% contingency"
        ),
        "q3.budget_status":     "[Review against acquisition budget]",
        "q3.key_cost_risks":    (
            "Structural condition unknown pending field inspection\n"
            "Sprinkler installation required if system not present\n"
            "Fire alarm upgrade required for E-occupancy code compliance\n"
            "ADA scope may increase significantly depending on inspection findings\n"
            "Finish cost range reflects Refresh vs. Alpha standard"
        ),
    }

    return {
        "status": "success",
        "region": resolved_region,
        "total_sf": total_building_sf,
        "rooms_used": rooms_note,
        "room_count": len(room_list),
        "cost_summary": {
            "finish_work":   f"${fmt(finish_low)} – ${fmt(finish_high)}",
            "mep":           f"${fmt(mep_low)} – ${fmt(mep_high)}",
            "ffe":           f"${fmt(ffe_low)} – ${fmt(ffe_high)}",
            "bathrooms":     f"${fmt(bath_low)} – ${fmt(bath_high)}",
            "structural":    f"${fmt(struct_low)} – ${fmt(struct_high)}",
            "sprinkler":     f"${fmt(sprinkler_low)} – ${fmt(sprinkler_high)}",
            "fire_alarm":    f"${fmt(fa_low)} – ${fmt(fa_high)}",
            "ada":           f"${fmt(ada_low)} – ${fmt(ada_high)}",
            "contingency":   f"${fmt(cont_low)} – ${fmt(cont_high)}",
            "grand_total":   f"${fmt(total_low)} – ${fmt(total_high)}",
        },
        "report_data_fields": report_fields,
        "message": (
            f"Cost estimate: ${fmt(total_low)} – ${fmt(total_high)} "
            f"({resolved_region} region, {len(room_list)} rooms, {total_building_sf:,} SF). "
            "IMPORTANT: Copy all report_data_fields directly into report_data as flat "
            "top-level keys (e.g. report_data['q3.structural_low'] = '24,000'). "
            "Do NOT nest them under q3.cost_estimate_table."
        ),
    }


@mcp.tool()
async def create_dd_report(
    site_name: str,
    drive_folder_url: str,
    report_data: dict[str, Any],
    token_evidence: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a completed DD report Google Doc for a site.

    Copies the V2 executive one-pager template to the site's Drive folder,
    names it "[Site Name] DD Report - [MM/DD/YYYY]", then fills all
    {{PLACEHOLDER}} tokens using Google Docs API replaceAllText.

    Args:
        site_name: Site name used for the report document title.
        drive_folder_url: Google Drive folder URL for the site (report is saved here).
        report_data: Nested dict with all report sections and field values.
        token_evidence: Optional dict mapping token names to the raw excerpt from the
            source document that supports the token value. Included in the report trace
            so reviewers can verify each field back to its source.

    Returns:
        Dict with the URL of the newly created DD report Google Doc.
    """
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
            "message": (
                f"Could not extract a Google Drive folder ID from: {drive_folder_url}"
            ),
        }

    settings = get_settings()
    template_id = settings.dd_template_v2_google_doc_id
    if not template_id:
        return {
            "status": "error",
            "error": "Missing configuration",
            "message": (
                "DD_TEMPLATE_V2_GOOGLE_DOC_ID is not configured. "
                "Set this environment variable to the Google Doc template ID."
            ),
        }

    # Build the document name
    today_str = datetime.now().strftime("%m/%d/%Y")
    doc_name = f"{site_name.strip()} DD Report - {today_str}"

    logger.info("Creating DD report: %s", doc_name)

    try:
        gc = _make_google_client()

        # Step 1: Copy the template to the site's Drive folder
        logger.info(
            "Copying template %s to folder %s as '%s'", template_id, folder_id, doc_name
        )
        copied_doc = gc.copy_document(
            template_id=template_id,
            name=doc_name,
            parent_folder_id=folder_id,
        )

        doc_id = copied_doc.get("id")
        doc_url = copied_doc.get("webViewLink")

        if not doc_id or not isinstance(doc_id, str):
            raise RuntimeError("Invalid document ID returned from copy operation")

        logger.info("Copied template to new document: %s (id=%s)", doc_name, doc_id)

        # Step 2: Normalize report_data → template-aligned replacements
        replacements, unmatched, unfilled, token_sources = normalize_report_data(
            report_data, site_name=site_name.strip(), report_date=today_str,
        )
        compute_deltas(replacements)
        # Mark deltas that were computed (not agent-filled)
        for delta_token in ("exec.delta_capacity", "exec.delta_cost", "exec.delta_ready"):
            if delta_token in replacements and token_sources.get(delta_token) == "unfilled":
                token_sources[delta_token] = "computed"

        # Inject the generated doc URL (not in the agent's report_data)
        replacements.setdefault("meta.drive_folder_url", drive_folder_url)

        logger.info(
            "Normalization: %d replacements, %d unmatched keys, %d unfilled tokens",
            len(replacements), len(unmatched), len(unfilled),
        )
        if unmatched:
            logger.warning("Unmatched agent keys (no template token): %s", unmatched)

        # Step 3: Build and apply replaceAllText batch update
        text_replacements = dict(replacements)

        # Step 3a: Swap URL values with display labels for link tokens.
        # Save the original URLs so the hyperlink builder can set link targets.
        link_urls: dict[str, str] = {}
        for token in LINK_TOKENS:
            value = text_replacements.get(token, "")
            if value.startswith("http") and token in LINK_DISPLAY_LABELS:
                link_urls[token] = value
                text_replacements[token] = LINK_DISPLAY_LABELS[token]

        replace_requests = build_replace_all_text_requests(text_replacements)

        if replace_requests:
            gc.batch_update_document(doc_id, replace_requests)
            logger.info(
                "Applied %d text replacements to document %s", len(replace_requests), doc_id
            )
        else:
            logger.warning("No placeholder replacements to apply — report_data may be empty")

        # Step 3b: Hyperlink display labels (convert label text to clickable links)
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

        # Pre-flight: which link tokens did the agent not provide at all?
        missing_from_agent = [t for t in LINK_TOKENS if t not in replacements]
        if missing_from_agent:
            logger.warning(
                "Hyperlinks: agent did not provide values for: %s", missing_from_agent,
            )
        hyperlink_trace["missing_from_agent"] = missing_from_agent

        # Pre-flight: which link tokens have non-URL values?
        non_url_values = {
            k: replacements[k][:120] for k in LINK_TOKENS
            if k in replacements and not replacements[k].startswith("http")
        }
        if non_url_values:
            logger.info("Hyperlinks: link tokens with non-URL values: %s", non_url_values)
        hyperlink_trace["non_url_values"] = non_url_values

        # Pre-flight: did the agent provide URLs under keys NOT in LINK_TOKENS?
        unmapped_agent_urls = [
            k for k, v in replacements.items()
            if k not in LINK_TOKENS and v.startswith("http")
        ]
        if unmapped_agent_urls:
            logger.warning(
                "Hyperlinks: agent provided URLs under keys not in LINK_TOKENS: %s",
                unmapped_agent_urls,
            )
        hyperlink_trace["unmapped_agent_urls"] = unmapped_agent_urls

        # Build and apply hyperlinks using display labels
        try:
            hyperlink_trace["candidates"] = {
                k: {"label": LINK_DISPLAY_LABELS.get(k, v), "url": v[:200]}
                for k, v in link_urls.items()
            }

            if not link_urls:
                logger.info("Hyperlinks: no URL candidates found in link tokens")
            else:
                logger.info(
                    "Hyperlinks: %d URL candidates: %s",
                    len(link_urls), list(link_urls.keys()),
                )
                doc_struct = gc.get_document(doc_id)
                doc_body = doc_struct.get("body", {})
                hl_result = build_hyperlink_requests(
                    doc_body, link_urls, LINK_TOKENS, LINK_DISPLAY_LABELS,
                )
                hyperlink_trace["found_in_doc"] = hl_result.found_tokens
                hyperlink_trace["not_found_in_doc"] = hl_result.not_found_tokens

                if not hl_result.requests:
                    logger.warning(
                        "Hyperlinks: display labels not found in doc body — "
                        "0 of %d candidates matched", len(link_urls),
                    )
                else:
                    gc.batch_update_document(doc_id, hl_result.requests)
                    hyperlink_trace["applied"] = len(hl_result.requests)
                    logger.info(
                        "Applied %d hyperlinks to document %s: %s",
                        hyperlink_trace["applied"], doc_id, hl_result.found_tokens,
                    )
        except Exception as e:
            logger.warning(
                "Hyperlink insertion failed (report still usable): %s", e,
            )
            hyperlink_trace["error"] = str(e)

        logger.info("DD report created successfully: %s", doc_url)

        # Step 4: Upload report trace JSON to the same Drive folder
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

        trace_data = {
            "site_name": site_name.strip(),
            "date": today_str,
            "report_doc_id": doc_id,
            "report_doc_url": doc_url,
            "token_report": token_report,
            "unmatched_keys": unmatched,
            "unfilled_tokens": unfilled,
            "hyperlinks": hyperlink_trace,
        }
        try:
            trace_name = f"{site_name.strip()} Report Trace - {today_str.replace('/', '-')}.json"
            trace_json = json.dumps(trace_data, indent=2)
            trace_file = gc.upload_file_to_folder(
                folder_id=folder_id,
                file_name=trace_name,
                file_bytes=trace_json.encode("utf-8"),
                mime_type="application/json",
            )
            trace_url = trace_file.get("webViewLink", "")
            logger.info("Uploaded report trace: %s", trace_url)

            # Fill {{sources.trace_link}} with display label and hyperlink it
            if trace_url:
                trace_label = LINK_DISPLAY_LABELS.get("sources.trace_link", trace_url)
                gc.batch_update_document(doc_id, build_replace_all_text_requests(
                    {"sources.trace_link": trace_label},
                ))
                doc_struct = gc.get_document(doc_id)
                doc_body = doc_struct.get("body", {})
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
                    logger.info("Linked trace report in doc: %s", trace_label)
        except Exception as e:
            logger.warning("Failed to upload report trace (report still valid): %s", e)

        return {
            "status": "success",
            "document": {
                "id": doc_id,
                "name": doc_name,
                "url": doc_url,
            },
            "replacements_applied": len(replace_requests),
            "unmatched_agent_keys": len(unmatched),
            "unfilled_template_tokens": len(unfilled),
            "hyperlinks_applied": hyperlink_trace["applied"],
            "message": f"DD report created: {doc_url}",
        }

    except Exception as e:
        logger.error("Failed to create DD report: %s", e)
        return {
            "status": "error",
            "error": "Failed to create DD report",
            "message": str(e),
        }


@mcp.tool()
async def check_site_readiness(site_name_or_id: str) -> dict[str, Any]:
    """Check whether a site has all required DD documents and whether a report already exists.

    Looks up the site's Drive folder, lists and classifies all files, then reports
    which key documents (SIR, ISP, building inspection, Phase I ESA) are present and
    whether a DD Report has already been created.

    Args:
        site_name_or_id: Site name, Wrike record ID, or Wrike permalink URL.

    Returns:
        Dict with sir_found, isp_found, inspection_found, report_exists, missing_docs,
        ready_for_report, and a files map keyed by doc_type.
    """
    logger.info("Tool called: check_site_readiness — %s", site_name_or_id)

    if not site_name_or_id or not site_name_or_id.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "site_name_or_id must be a non-empty string",
        }

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

        # 1. Search the three shared folders (SIR/, ISP/, Building Inspection/)
        match_terms = _build_site_match_terms(site_title, address)
        logger.info("Match terms for '%s': %s", site_title, match_terms)
        shared_docs = _find_site_docs_in_shared_folders(
            gc, match_terms,
            site_title=site_title, site_address=address,
        )

        # 2. Recursively list + classify files in the site's own folder (fallback)
        all_site_files = [
            {**f, "doc_type": _classify_document_type(f.get("name", ""))}
            for f in gc.list_files_recursive(folder_id, max_depth=2)
        ]

        # 3. Build files_by_type — shared folders take priority, site folder fills gaps
        files_by_type: dict[str, dict[str, Any] | None] = {
            "sir": shared_docs.get("sir"),
            "isp": shared_docs.get("isp"),
            "building_inspection": shared_docs.get("building_inspection"),
            "phase_i_esa": None,
            "dd_report": None,
        }
        for f in all_site_files:
            dt = f.get("doc_type", "unknown")
            if dt in files_by_type and files_by_type[dt] is None:
                files_by_type[dt] = f
            elif dt in files_by_type and files_by_type[dt] is not None:
                # Prefer PDF over converted Google Doc
                existing_mime = files_by_type[dt].get("mimeType", "")
                new_mime = f.get("mimeType", "")
                if existing_mime != PDF_MIME and new_mime == PDF_MIME:
                    files_by_type[dt] = f

        # 4. LLM classification for unknown site folder files if docs are still missing
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
                        "LLM classified site file '%s' as %s (conf=%.2f) for '%s'",
                        fname, doc_type, conf, site_title,
                    )
                if not still_missing:
                    break

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

        # Resolve P1 Assignee name + email from Wrike contact
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
                f"  SIR: {'found — ' + (files_by_type.get('sir') or {}).get('name', '') if sir_found else 'not found'}",
                f"  ISP: {'found — ' + (files_by_type.get('isp') or {}).get('name', '') if isp_found else 'not found'}",
                f"  Building Inspection: {'found — ' + (files_by_type.get('building_inspection') or {}).get('name', '') if inspection_found else 'not found'}",
                f"  DD Report: {'exists — ' + (files_by_type.get('dd_report') or {}).get('name', '') if report_exists else 'not yet created'}",
                "",
                "Ready for report generation." if ready_for_report else (
                    "Not ready — " + ", ".join(missing_docs) + " missing." if missing_docs else "Report already exists."
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


@mcp.tool()
async def check_report_completeness(doc_id: str) -> dict[str, Any]:
    """Check a generated DD report Google Doc for unresolved placeholders and pending sections.

    Reads the document text, scans for any remaining {{token}} patterns (unfilled
    placeholders — hard block) and [Not found / Pending] gap labels (acceptable sourced gaps).

    Args:
        doc_id: Google Docs file ID of the generated DD report.

    Returns:
        Dict with ready_to_send flag, unresolved_token_count, unresolved_tokens list,
        pending_section_count, pending_sections list, and a human-readable summary.
    """
    logger.info("Tool called: check_report_completeness — doc_id=%s", doc_id)

    if not doc_id or not doc_id.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "doc_id must be a non-empty string",
        }

    try:
        gc = _make_google_client()
        text = gc.export_google_doc_as_text(doc_id)

        # Find unresolved {{token}} patterns — these are hard blocks
        unresolved_tokens = re.findall(r"\{\{([^}]+)\}\}", text)
        unresolved_token_count = len(unresolved_tokens)

        # Find all [Not found — ...] and [Pending...] labels
        pending_labels = re.findall(r"\[Not found\s*—[^]]+\]", text, re.IGNORECASE)
        pending_section_count = len(pending_labels)

        ready_to_send = unresolved_token_count == 0

        if ready_to_send and pending_section_count == 0:
            summary = "Report complete. All fields filled."
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
            "pending_section_count": pending_section_count,
            "pending_sections": pending_labels,
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
    logger.info("Tool called: get_site_comments — %s", site_name_or_id)

    if not site_name_or_id or not site_name_or_id.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "site_name_or_id must be a non-empty string",
        }

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

        # Group by section
        by_section: dict[str, list[dict[str, Any]]] = {}
        for c in comments:
            section = classify_comment_to_section(c["text"])
            by_section.setdefault(section, []).append(c)

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


# ─────────────────────────────────────────────────────────────────────────────
# MatterBot integration
# ─────────────────────────────────────────────────────────────────────────────

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

    This is fire-and-forget — MatterBot processes asynchronously. The images
    will appear in the Drive folder once generation completes (typically 5-15
    minutes depending on room count and tier).

    Args:
        space_sid: Matterport space SID (from the scan URL or Wrike record).
        space_name: Space / site name (used for Drive folder matching).
        tier: Rendering quality tier — "standard" or "premium".
        max_rooms: Maximum rooms to render. 0 = service default (~12).
        room_types: Comma-separated room type filter (e.g., "classroom,commons,gym").
            Empty string = all room types.

    Returns:
        Dict with status and the request URL that was fired.
    """
    logger.info(
        "Tool called: generate_marketing_pack — space_sid=%s, space_name=%s, tier=%s",
        space_sid, space_name, tier,
    )

    if not space_sid or not space_sid.strip():
        return {
            "status": "error",
            "error": "Missing parameter",
            "message": "space_sid must be a non-empty string (Matterport space SID).",
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

    url = f"{MATTERBOT_BASE_URL}/api/batch/generate-marketing-pack/{space_sid.strip()}"
    params: dict[str, str | int] = {"space_name": space_name.strip()}
    if tier != "standard":
        params["tier"] = tier
    if max_rooms > 0:
        params["max_rooms"] = max_rooms
    if room_types.strip():
        params["room_types"] = room_types.strip()

    try:
        resp = requests.get(url, params=params, timeout=MATTERBOT_TIMEOUT_SECONDS)
        resp.raise_for_status()

        logger.info(
            "MatterBot marketing pack triggered: %s (status=%d)",
            url, resp.status_code,
        )

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
        logger.warning("MatterBot request timed out for space %s", space_sid)
        return {
            "status": "error",
            "error": "MatterBot timeout",
            "message": (
                f"MatterBot did not respond within {MATTERBOT_TIMEOUT_SECONDS}s. "
                "The service may be starting up — retry in a minute."
            ),
        }
    except requests.RequestException as e:
        logger.error("MatterBot request failed: %s", e)
        return {
            "status": "error",
            "error": "MatterBot request failed",
            "message": str(e),
        }


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
    document — pass the complete result dict from the skill tool.

    Args:
        skill_name: Skill name (e.g., "E-Occupancy", "School Approval").
        site_name: Site name for the document title.
        drive_folder_url: Google Drive folder URL for the site.
        skill_data: Full result dict from apply_e_occupancy_skill or
            apply_school_approval_skill.

    Returns:
        Dict with status, doc_url, and doc_id.
    """
    logger.info("Tool called: save_skill_report — skill=%s, site=%s", skill_name, site_name)

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

    gc = _make_google_client()
    today_str = datetime.now().strftime("%m/%d/%Y")
    doc_name = f"{skill_name} Assessment - {site_name}"

    # Format the skill data into a readable document
    content = _format_skill_document(skill_name, site_name, today_str, skill_data)

    # Try to find M1 subfolder; fall back to site root
    target_folder_id = folder_id
    try:
        subfolders = gc.list_subfolders(folder_id)
        for sf in subfolders:
            sf_name = sf.get("name", "").lower()
            if sf_name.startswith("m1"):
                target_folder_id = sf["id"]
                logger.info("Found M1 subfolder: %s", sf["name"])
                break
        else:
            logger.warning("M1 subfolder not found for '%s', saving to site root", site_name)
    except Exception as e:
        logger.warning("Failed to list subfolders for '%s': %s — saving to site root", site_name, e)

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
    logger.info("Tool called: send_dd_report_email — site=%s", site_name)

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

    # Build recipient list: configured recipients + additional (e.g., P1 Assignee)
    base_recipients = [
        r.strip() for r in settings.dd_report_email_recipients.split(",") if r.strip()
    ] if settings.dd_report_email_recipients else []

    extra_recipients = [
        r.strip() for r in additional_recipients.split(",") if r.strip()
    ] if additional_recipients else []

    # Deduplicate while preserving order
    seen: set[str] = set()
    recipients: list[str] = []
    for r in base_recipients + extra_recipients:
        r_lower = r.lower()
        if r_lower not in seen:
            seen.add(r_lower)
            recipients.append(r)

    if not recipients:
        return {
            "status": "error",
            "error": "No recipients",
            "message": "No recipients configured and no additional_recipients provided.",
        }

    subject = f"DD Report Ready — {site_name}"
    html_body = f"""
<html><body>
<h2>Due Diligence Report — {site_name}</h2>
<p>A new Due Diligence report has been generated for <strong>{site_name}</strong>.</p>
<p><a href="{report_url}" style="font-size:16px;font-weight:bold;">View Report in Google Docs</a></p>
<h3>Key Findings</h3>
<pre style="background:#f5f5f5;padding:12px;border-radius:4px;">{key_findings}</pre>
<p style="color:#888;font-size:12px;">Generated automatically by the Alpha DD Reporter.</p>
</body></html>
"""

    try:
        send_email(
            sender=settings.email_sender,
            app_password=settings.email_app_password,
            recipients=recipients,
            subject=subject,
            html_body=html_body,
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


def main() -> None:
    """Main entry point for the MCP server."""
    logger.info("Starting Due Diligence Reporter MCP server (stdio transport)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
