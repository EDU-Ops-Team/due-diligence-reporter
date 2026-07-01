"""Helpers for resolving and reading per-site M1 Drive folders.

These were originally defined inside :mod:`inbox_scanner`. They live here
so :mod:`server` and :mod:`report_pipeline` can use them without creating
an import cycle (inbox_scanner already imports from server inside its
function bodies).

``inbox_scanner`` re-exports the symbols below for backward compatibility
with scripts and tests that patch
``due_diligence_reporter.inbox_scanner._resolve_m1_folder`` /
``_list_m1_documents_by_type`` / ``M1_RECOGNIZED_DOC_TYPES``.
"""

from __future__ import annotations

import re
from typing import Any

from .classifier import classify_document, is_site_folder_scan_candidate
from .google_client import GoogleClient
from .utils import extract_folder_id_from_url

M1_FOLDER_NAME = "M1 - Acquire Property"

# Doc types the scanner now persists into per-site M1 folders, plus the
# downstream reports the Block Plan pipeline derives. ``_list_m1_documents_by_type``
# returns any of these keyed by doc_type so dedup, readiness backfill, and
# the Block Plan rerun guard can all introspect a single source of truth.
M1_RECOGNIZED_DOC_TYPES = {
    "sir",
    "building_inspection",
    "isp",
    "block_plan",
    "capacity_brainlift_report",
    "raycon_scenario_report",
    # The async-handoff result file RayCon writes when its job finishes.
    "raycon_scenario_json",
    "e_occupancy_report",
    "school_approval_report",
    "opening_plan_report",
    "alpha_capacity_analysis",
    "cost_timeline_estimate",
    "outdoor_play_space_report",
    "security_due_diligence_report",
    "alpha_phasing_plan_report",
    "traffic_analysis",
    "certificate_of_occupancy",
    "permit_of_record",
    "measured_floor_plan",
    "floor_plan",
    "lidar",
}


def _normalize_folder_name(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _is_m1_folder_name(name: str) -> bool:
    return re.match(r"^m1(?:$|[^a-z0-9])", name.strip().lower()) is not None


def _is_acquire_property_m1_folder(name: str) -> bool:
    normalized = _normalize_folder_name(name)
    return (
        _is_m1_folder_name(name)
        and "propert" in normalized
        and ("acquir" in normalized or "aquir" in normalized)
    )


def _find_preferred_m1_subfolder(
    subfolders: list[dict[str, Any]],
    *,
    allow_legacy_fallback: bool = True,
) -> dict[str, Any] | None:
    """Return the canonical M1 milestone folder before legacy generic M1 folders."""
    fallback: dict[str, Any] | None = None
    for subfolder in subfolders:
        name = str(subfolder.get("name") or "").strip()
        if not name:
            continue
        if _is_acquire_property_m1_folder(name):
            return subfolder
        if fallback is None and _is_m1_folder_name(name):
            fallback = subfolder
    return fallback if allow_legacy_fallback else None


def _resolve_m1_folder(
    gc: GoogleClient,
    drive_folder_url: str,
    *,
    create_if_missing: bool = True,
    allow_legacy_fallback: bool = True,
) -> tuple[str | None, str | None]:
    """Return the site's M1 Acquire Property folder ID, creating it when absent.

    Pass ``create_if_missing=False`` from read-only callers (e.g. the
    diagnose tool) to skip the ``gc.create_folder`` side effect; this
    returns ``(None, None)`` instead when M1 doesn't yet exist.

    Pass ``allow_legacy_fallback=False`` from upload callers that must land in
    the Acquire Property milestone folder instead of a legacy plain ``M1``.
    """
    folder_id = extract_folder_id_from_url(drive_folder_url)
    if not folder_id:
        return None, None
    subfolders = gc.list_subfolders(folder_id)
    if subfolder := _find_preferred_m1_subfolder(
        subfolders,
        allow_legacy_fallback=allow_legacy_fallback,
    ):
        return subfolder.get("id"), subfolder.get("webViewLink")
    if not create_if_missing:
        return None, None
    created = gc.create_folder(folder_id, M1_FOLDER_NAME)
    return created.get("id"), created.get("webViewLink")


def _list_m1_documents_by_type(
    gc: GoogleClient,
    folder_id: str,
) -> dict[str, dict[str, Any]]:
    """Return recognized report files in an M1 folder keyed by doc_type.

    When multiple files of the same doc_type exist (e.g. an older copy and a
    newly uploaded one), the most recently modified one wins so callers see
    the freshest version.
    """
    files_by_type: dict[str, dict[str, Any]] = {}
    for file_info in gc.list_files_in_folder(folder_id):
        if not is_site_folder_scan_candidate(file_info):
            continue
        name = str(file_info.get("name", "")).strip()
        if not name:
            continue
        doc_type, _confidence = classify_document(name)
        if doc_type not in M1_RECOGNIZED_DOC_TYPES:
            continue
        existing = files_by_type.get(doc_type)
        if existing is None:
            files_by_type[doc_type] = file_info
            continue
        # Prefer the most recently modified file when duplicates exist.
        prev_mtime = str(existing.get("modifiedTime") or "")
        new_mtime = str(file_info.get("modifiedTime") or "")
        if new_mtime > prev_mtime:
            files_by_type[doc_type] = file_info
    return files_by_type
