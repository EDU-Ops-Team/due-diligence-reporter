"""Tiered document classification and site-matching for DD pipeline.

Tier 1: Regex keyword matching (free, instant)
Tier 2: LLM filename classification via GPT-4o-mini (cheap, ~200ms)
Tier 3: LLM content classification on first-page PDF text (moderate, ~2s)

All LLM functions degrade gracefully — if OpenAI is unavailable the system
falls back to regex-only behaviour.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from .config import get_settings

logger = logging.getLogger("[classifier]")

# Valid doc types returned by the classifier
DOC_TYPES = frozenset({
    "sir",
    "isp",
    "block_plan",
    "building_inspection",
    "dd_report",
    "matterport",
    "e_occupancy_report",
    "school_approval_report",
    "opening_plan_report",
    "alpha_phasing_plan_report",
    "alpha_capacity_analysis",
    "cost_timeline_estimate",
    "outdoor_play_space_report",
    "traffic_analysis",
    "certificate_of_occupancy",
    "permit_of_record",
    "measured_floor_plan",
    "lidar",
    "capacity_brainlift_report",
    "raycon_scenario_report",
    "raycon_scenario_json",
    "unknown",
})

SOURCE_FOLDER_DOC_TYPES = frozenset({
    "sir",
    "isp",
    "building_inspection",
})

AI_GENERATED_DOC_TYPES = frozenset({
    "dd_report",
    "e_occupancy_report",
    "school_approval_report",
    "opening_plan_report",
    "alpha_phasing_plan_report",
    "alpha_capacity_analysis",
    "cost_timeline_estimate",
    "outdoor_play_space_report",
    "traffic_analysis",
    "capacity_brainlift_report",
    "raycon_scenario_report",
})

SITE_FOLDER_DOC_TYPES = frozenset(
    AI_GENERATED_DOC_TYPES
    | SOURCE_FOLDER_DOC_TYPES
    | {
        "block_plan",
        "certificate_of_occupancy",
        "permit_of_record",
        "measured_floor_plan",
        "floor_plan",
        "lidar",
    }
)

_SITE_SCAN_EXCLUDED_FOLDERS = frozenset({"working"})
_PROVENANCE_CACHE_NAME_RE = re.compile(r"^provenance(?: \(\d+\))?\.json$", re.I)


def is_site_folder_scan_candidate(file_info: dict[str, Any]) -> bool:
    """Return whether a recursive site-folder file should enter source discovery.

    DDR scans the whole site folder tree so it can find operator-filed source
    docs in M1. Generated caches and scratch artifacts should not re-enter that
    source surface: they can trigger unnecessary LLM classification and, worse,
    become stale inputs on the next run.
    """
    name = str(file_info.get("name") or "").strip()
    if not name:
        return False
    if _PROVENANCE_CACHE_NAME_RE.match(name):
        return False

    folder_path = str(file_info.get("folder_path") or "")
    path_segments = [segment.strip().lower() for segment in folder_path.split("/")]
    if any(segment in _SITE_SCAN_EXCLUDED_FOLDERS for segment in path_segments):
        return False

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1 — regex keyword matching
# ─────────────────────────────────────────────────────────────────────────────


def classify_by_keywords(filename: str) -> tuple[str, float]:
    """Classify a document by filename keywords.  Returns (doc_type, confidence)."""
    name = filename.lower()

    if "e-occupancy" in name or "e occupancy" in name:
        return "e_occupancy_report", 0.95
    if re.search(r"school[-_\s]+approval", name):
        return "school_approval_report", 0.95
    if "opening plan" in name:
        return "opening_plan_report", 0.95
    if (
        "alpha phasing plan" in name
        or re.search(r"\bphasing[-_\s]+plan\b", name)
        or ("phase i" in name and "phase ii" in name and "quality" in name)
    ):
        return "alpha_phasing_plan_report", 0.95
    if "outdoor play space" in name or "play_space" in name or "play space report" in name:
        return "outdoor_play_space_report", 0.95
    if "kh traffic" in name or "traffic analysis" in name:
        return "traffic_analysis", 0.95
    if "certificate of occupancy" in name or re.search(r"\bco\b", name):
        return "certificate_of_occupancy", 0.90
    if "permit of record" in name:
        return "permit_of_record", 0.95
    if "measured floor plan" in name or "bim" in name:
        return "measured_floor_plan", 0.95
    if "lidar" in name:
        return "lidar", 0.95
    if "alpha capacity analysis" in name:
        return "alpha_capacity_analysis", 0.95
    if (
        "cost timeline estimate" in name
        or "cost and timeline estimate" in name
        or "cost_timeline_estimate" in name
    ):
        return "cost_timeline_estimate", 0.95
    if "capacity brainlift" in name:
        return "capacity_brainlift_report", 0.95
    # The async hand-off result file. Filename is fixed by the
    # DDR/RayCon contract (raycon_scenario.json) so an exact match wins.
    if name == "raycon_scenario.json":
        return "raycon_scenario_json", 1.0
    if "raycon scenario" in name or "raycon estimate" in name:
        return "raycon_scenario_report", 0.95
    # Block Plan aliases: "block plan", "preliminary floor plan(s)" / "PFP".
    # Vendors and partners use these terms interchangeably for the same
    # artifact, so they all route to the `block_plan` doc_type and land in
    # the site's M1 folder.
    if (
        "block plan" in name
        or "blockplan" in name
        or "block_plan" in name
        or "preliminary floor plan" in name
        or re.search(r"\bpfp\b", name)
        or re.search(r"[-_]pfp(\.[^.]+)?$", name)
    ):
        return "block_plan", 0.95
    if "report trace" in name and name.endswith(".json"):
        return "unknown", 0.0
    if "dd report" in name:
        return "dd_report", 0.95
    if re.search(r"\bisp\b", name) or re.search(r"-isp(\.[^.]+)?$", name):
        return "isp", 0.95
    if re.search(r"\bsir\b", name) or re.search(r"[-_]sir(\.[^.]+)?$", name):
        return "sir", 0.95
    if "inspection" in name:
        return "building_inspection", 0.95
    if "matterport" in name:
        return "matterport", 0.95

    return "unknown", 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2 — LLM filename classification
# ─────────────────────────────────────────────────────────────────────────────

_FILENAME_SYSTEM_PROMPT = """\
You classify documents for an Alpha School due diligence workflow.
Given only a filename (and optionally a site name for context), determine the document type.

Types:
- sir: Site Investigation Report (also called Site Inspection Report)
- isp: Internet Service Provider report / availability report
- block_plan: Block Plan PDF for a specific school site (also called "Preliminary Floor Plan", "Preliminary Floor Plans", or "PFP" — these are all the same artifact)
- building_inspection: Building Inspection Report or property condition report
- dd_report: Due Diligence Report (the final compiled report)
- matterport: Matterport 3D scan or virtual tour
- e_occupancy_report: E-Occupancy Assessment — building conversion scoring for educational use
- school_approval_report: School Approval Assessment — state education registration requirements
- opening_plan_report: Opening Plan generated by the DD workflow
- alpha_phasing_plan_report: Alpha Phasing Plan workbook generated by the DD workflow
- alpha_capacity_analysis: Alpha Capacity Analysis generated by the DD workflow
- outdoor_play_space_report: Outdoor Play Space Report generated by the DD workflow
- traffic_analysis: KH traffic analysis or school operations traffic assessment
- capacity_brainlift_report: Capacity Brainlift assessment generated by the DD workflow
- raycon_scenario_report: RayCon scenario summary generated by the DD workflow
- unknown: Cannot determine from filename

Return ONLY a JSON object:
{"doc_type": "<type>", "confidence": 0.0-1.0, "reasoning": "brief explanation"}
"""


def classify_by_filename_llm(
    filename: str, site_name: str | None = None
) -> tuple[str, float]:
    """Classify a document by sending its filename to GPT-4o-mini."""
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        logger.debug("OPENAI_API_KEY not set — skipping Tier 2 classification")
        return "unknown", 0.0

    try:
        from openai import OpenAI

        settings = get_settings()
        client = OpenAI(api_key=openai_api_key, max_retries=2)

        user_msg = f"Filename: {filename}"
        if site_name:
            user_msg += f"\nSite name: {site_name}"

        response = client.chat.completions.create(
            model=settings.openai_filename_model,
            messages=[
                {"role": "system", "content": _FILENAME_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
        )

        text = response.choices[0].message.content
        if not text:
            return "unknown", 0.0

        result = json.loads(text)
        doc_type = result.get("doc_type", "unknown")
        confidence = float(result.get("confidence", 0.0))

        if doc_type not in DOC_TYPES:
            doc_type = "unknown"

        logger.info(
            "Tier 2 classified '%s' as %s (%.2f): %s",
            filename, doc_type, confidence, result.get("reasoning", ""),
        )
        return doc_type, confidence

    except Exception as e:
        logger.warning("Tier 2 LLM classification failed for '%s': %s", filename, e)
        return "unknown", 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Tier 3 — LLM content classification (first page of PDF)
# ─────────────────────────────────────────────────────────────────────────────

_CONTENT_SYSTEM_PROMPT = """\
You classify documents for an Alpha School due diligence workflow.
You are given the first page text of a PDF and its filename. Determine the document type.

Types:
- sir: Site Investigation Report — covers zoning, permits, AHJ info, building code
- isp: Internet Service Provider report — lists ISPs, speeds, availability
- block_plan: Block Plan PDF describing the school site layout and capacity assumptions (also called "Preliminary Floor Plan", "Preliminary Floor Plans", or "PFP" — these are all the same artifact)
- building_inspection: Building Inspection Report — covers building condition, systems, deficiencies
- dd_report: Due Diligence Report — the final compiled report with executive summary
- matterport: Matterport 3D scan documentation
- e_occupancy_report: E-Occupancy Assessment — building conversion scoring for educational use
- school_approval_report: School Approval Assessment — state education registration requirements
- opening_plan_report: Opening Plan generated by the DD workflow
- alpha_phasing_plan_report: Alpha Phasing Plan workbook generated by the DD workflow
- alpha_capacity_analysis: Alpha Capacity Analysis generated by the DD workflow
- outdoor_play_space_report: Outdoor Play Space Report generated by the DD workflow
- traffic_analysis: KH traffic analysis or school operations traffic assessment
- capacity_brainlift_report: Capacity Brainlift assessment generated by the DD workflow
- raycon_scenario_report: RayCon scenario summary generated by the DD workflow
- unknown: Cannot determine

Return ONLY a JSON object:
{"doc_type": "<type>", "confidence": 0.0-1.0, "reasoning": "brief explanation"}
"""


def classify_by_content_llm(
    first_page_text: str, filename: str
) -> tuple[str, float]:
    """Classify a document by sending its first-page text to GPT-4o-mini."""
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        return "unknown", 0.0

    try:
        from openai import OpenAI

        settings = get_settings()
        client = OpenAI(api_key=openai_api_key, max_retries=2)

        response = client.chat.completions.create(
            model=settings.openai_content_model,
            messages=[
                {"role": "system", "content": _CONTENT_SYSTEM_PROMPT},
                {"role": "user", "content": f"Filename: {filename}\n\nFirst page text:\n{first_page_text[:3000]}"},
            ],
            response_format={"type": "json_object"},
        )

        text = response.choices[0].message.content
        if not text:
            return "unknown", 0.0

        result = json.loads(text)
        doc_type = result.get("doc_type", "unknown")
        confidence = float(result.get("confidence", 0.0))

        if doc_type not in DOC_TYPES:
            doc_type = "unknown"

        logger.info(
            "Tier 3 classified '%s' as %s (%.2f): %s",
            filename, doc_type, confidence, result.get("reasoning", ""),
        )
        return doc_type, confidence

    except Exception as e:
        logger.warning("Tier 3 LLM content classification failed for '%s': %s", filename, e)
        return "unknown", 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator — runs tiers in order
# ─────────────────────────────────────────────────────────────────────────────


def classify_document(
    filename: str,
    *,
    file_id: str | None = None,
    gc: Any | None = None,
    site_name: str | None = None,
) -> tuple[str, float]:
    """Classify a document using the three-tier strategy.

    Tier 1 (regex) → Tier 2 (LLM filename) → Tier 3 (LLM content, PDF only).
    Returns (doc_type, confidence).
    """
    # Tier 1: regex
    doc_type, conf = classify_by_keywords(filename)
    if doc_type != "unknown":
        return doc_type, conf

    # Tier 2: LLM on filename
    doc_type, conf = classify_by_filename_llm(filename, site_name)
    if conf >= 0.7:
        return doc_type, conf

    # Tier 3: LLM on content (PDF only, requires gc + file_id)
    if file_id and gc and filename.lower().endswith(".pdf"):
        try:
            from .utils import extract_text_from_pdf_bytes

            pdf_bytes = gc.download_file_bytes(file_id)
            text = extract_text_from_pdf_bytes(pdf_bytes)
            if text.strip():
                doc_type, conf = classify_by_content_llm(text[:3000], filename)
                if conf >= 0.5:
                    return doc_type, conf
        except Exception as e:
            logger.warning("Tier 3 PDF extraction failed for '%s': %s", filename, e)

    return "unknown", 0.0


# ─────────────────────────────────────────────────────────────────────────────
# LLM site-matching for shared folders
# ─────────────────────────────────────────────────────────────────────────────

_SITE_MATCH_SYSTEM_PROMPT = """\
You match documents to Alpha School site records.
Alpha School sites follow the naming pattern "Alpha {CityName}" (e.g. "Alpha Keller", "Alpha Boca Raton").

Given a site name, address, and a list of filenames in a shared folder, determine which
files (if any) belong to that site. Consider:
- City names, abbreviations, alternate spellings
- Address fragments, zip codes
- Partial site name matches
- The site name might not appear literally in the filename

Return ONLY a JSON object:
{"matches": [{"filename": "...", "confidence": 0.0-1.0}]}

Only include files with confidence >= 0.7. If no files match, return {"matches": []}.
"""


def match_file_to_site_llm(
    filenames: list[str],
    site_title: str,
    site_address: str | None = None,
) -> dict[str, float]:
    """Ask GPT-4o-mini which filenames belong to a given site.

    Returns a dict of {filename: confidence} for matches with confidence >= 0.7.
    """
    if not filenames:
        return {}

    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        return {}

    try:
        from openai import OpenAI

        settings = get_settings()
        client = OpenAI(api_key=openai_api_key, max_retries=2)

        user_msg = f"Site name: {site_title}\n"
        if site_address:
            user_msg += f"Address: {site_address}\n"
        user_msg += "\nFilenames in folder:\n"
        for fn in filenames:
            user_msg += f"- {fn}\n"

        response = client.chat.completions.create(
            model=settings.openai_site_match_model,
            messages=[
                {"role": "system", "content": _SITE_MATCH_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
        )

        text = response.choices[0].message.content
        if not text:
            return {}

        result = json.loads(text)
        matches: dict[str, float] = {}
        for m in result.get("matches", []):
            fn = m.get("filename", "")
            conf = float(m.get("confidence", 0.0))
            if fn and conf >= 0.7:
                matches[fn] = conf

        if matches:
            logger.info(
                "LLM site-match for '%s': %s",
                site_title,
                {k: f"{v:.2f}" for k, v in matches.items()},
            )

        return matches

    except Exception as e:
        logger.warning("LLM site-match failed for '%s': %s", site_title, e)
        return {}


def classify_document_type(filename: str) -> str:
    """Classify a Drive file by its document type based on the filename.

    Thin wrapper around :func:`classify_by_keywords` for callers that only
    need the doc_type string (no confidence score).
    """
    doc_type, _ = classify_by_keywords(filename)
    return doc_type
