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

from typing import Any

from .classifier import classify_document
from .google_client import GoogleClient
from .utils import extract_folder_id_from_url


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
}


def _resolve_m1_folder(
    gc: GoogleClient, drive_folder_url: str
) -> tuple[str | None, str | None]:
    """Return the site's M1 folder ID, creating a plain ``M1`` folder when absent."""
    folder_id = extract_folder_id_from_url(drive_folder_url)
    if not folder_id:
        return None, None
    subfolders = gc.list_subfolders(folder_id)
    for subfolder in subfolders:
        if subfolder.get("name", "").lower().startswith("m1"):
            return subfolder.get("id"), subfolder.get("webViewLink")
    created = gc.create_folder(folder_id, "M1")
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
