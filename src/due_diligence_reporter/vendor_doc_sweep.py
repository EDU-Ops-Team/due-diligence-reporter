"""Active Rhodes-backed sweep for DDR source document arrivals."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from .classifier import classify_document
from .config import Settings
from .dd_republish import maybe_republish_dd_report, record_dd_republish_failure_event
from .google_client import GoogleClient
from .m1_lookup import _list_m1_documents_by_type, _resolve_m1_folder
from .open_questions import source_event_from_drive_file, source_type_for_doc_type
from .provenance import is_vendor_sourced
from .report_pipeline import PipelineResult

logger = logging.getLogger("vendor_doc_sweep")

CORE_SWEEP_DOC_TYPES = {
    "sir",
    "building_inspection",
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
    "block_plan",
    "measured_floor_plan",
    "floor_plan",
    "lidar",
}

RepublishCallback = Callable[..., Any]
SourceEventEmitter = Callable[[dict[str, Any], dict[str, Any]], Any]
SWEEP_CURSOR_STATE_KEY = "__vendor_doc_republish_sweep_cursor__"


def select_sweep_site_records(
    site_records: Sequence[dict[str, Any]],
    republish_state: dict[str, str],
    *,
    max_sites: int = 0,
) -> list[dict[str, Any]]:
    """Return the next deterministic site slice for a bounded sweep."""
    records = _ordered_site_records(site_records)
    limit = _positive_limit(max_sites)
    if limit <= 0 or len(records) <= limit:
        return records

    cursor = str(republish_state.get(SWEEP_CURSOR_STATE_KEY) or "").strip()
    start_index = _site_index(records, cursor)
    if start_index < 0:
        start_index = 0
    return [records[(start_index + offset) % len(records)] for offset in range(limit)]


def advance_sweep_cursor(
    republish_state: dict[str, str],
    site_records: Sequence[dict[str, Any]],
    scanned_records: Sequence[dict[str, Any]],
    *,
    max_sites: int = 0,
) -> str:
    """Persist the cursor for the next bounded sweep and return it."""
    records = _ordered_site_records(site_records)
    scanned = list(scanned_records)
    limit = _positive_limit(max_sites)
    if limit <= 0 or not records or not scanned or len(records) <= limit:
        republish_state.pop(SWEEP_CURSOR_STATE_KEY, None)
        return ""

    last_key = _site_cursor_key(scanned[-1])
    last_index = _site_index(records, last_key)
    if last_index < 0:
        return str(republish_state.get(SWEEP_CURSOR_STATE_KEY) or "").strip()

    next_key = _site_cursor_key(records[(last_index + 1) % len(records)])
    if next_key:
        republish_state[SWEEP_CURSOR_STATE_KEY] = next_key
    return next_key


def collect_core_source_events(
    gc: GoogleClient,
    site_record: dict[str, Any],
    *,
    read_only: bool = False,
) -> list[dict[str, Any]]:
    """Return material DDR source events for one Rhodes site."""
    drive_folder_url = str(site_record.get("drive_folder_url") or "").strip()
    if not drive_folder_url:
        return []

    m1_folder_id, _m1_url = _resolve_m1_folder(
        gc,
        drive_folder_url,
        create_if_missing=False,
        allow_legacy_fallback=False,
    )
    candidates: dict[str, dict[str, Any]] = {}
    if m1_folder_id:
        candidates.update(_list_m1_documents_by_type(gc, m1_folder_id))

    # Some legacy/manual uploads can land at the site root. Include only the
    # core source types; the helper will still no-op when no DDR exists.
    root_folder_id = _folder_id_from_url(drive_folder_url)
    if root_folder_id:
        for file_info in gc.list_files_in_folder(root_folder_id):
            name = str(file_info.get("name") or "").strip()
            if not name:
                continue
            doc_type, _confidence = classify_document(name)
            if doc_type not in CORE_SWEEP_DOC_TYPES:
                continue
            existing = candidates.get(doc_type)
            if existing is None or str(file_info.get("modifiedTime") or "") > str(
                existing.get("modifiedTime") or ""
            ):
                candidates[doc_type] = file_info

    events: list[dict[str, Any]] = []
    for doc_type in sorted(CORE_SWEEP_DOC_TYPES):
        candidate = candidates.get(doc_type)
        if not candidate:
            continue
        source_type = source_type_for_doc_type(doc_type)
        if source_type is None:
            continue
        if doc_type in {"sir", "building_inspection"} and not _is_vendor_source(
            gc,
            candidate,
            doc_type=doc_type,
            m1_folder_id=m1_folder_id,
            read_only=read_only,
        ):
            continue
        event = source_event_from_drive_file(source_type, candidate, doc_type=doc_type)
        if not event.fingerprint:
            continue
        events.append(event.to_dict())
    return events


def run_vendor_doc_republish_sweep(
    gc: GoogleClient,
    *,
    settings: Settings,
    system_prompt: str,
    shared_cache: dict[str, list[dict[str, Any]]],
    republish_state: dict[str, str],
    site_records: list[dict[str, Any]],
    dry_run: bool = False,
    republish_callback: RepublishCallback = maybe_republish_dd_report,
    pipeline_runner: Callable[..., PipelineResult] | None = None,
    source_event_emitter: SourceEventEmitter | None = None,
    run_without_existing_report: bool = False,
) -> dict[str, Any]:
    """Scan active Rhodes sites for core source changes and republish in place."""
    rows: list[dict[str, Any]] = []
    for site_record in site_records:
        site_summary = _site_summary(site_record)
        if not site_summary.get("drive_folder_url"):
            rows.append(
                {
                    "site_id": site_summary.get("id", ""),
                    "site_title": site_summary.get("title", ""),
                    "status": "skipped",
                    "reason": "missing_drive_folder_url",
                }
            )
            continue
        try:
            events = collect_core_source_events(gc, site_summary, read_only=dry_run)
        except Exception as exc:  # noqa: BLE001 - one site should not stop the sweep
            logger.warning(
                "Core source sweep failed for %s: %s",
                site_summary.get("title") or site_summary.get("id"),
                exc,
            )
            rows.append(
                {
                    "site_id": site_summary.get("id", ""),
                    "site_title": site_summary.get("title", ""),
                    "status": "error",
                    "reason": str(exc),
                }
            )
            continue

        if not events:
            rows.append(
                {
                    "site_id": site_summary.get("id", ""),
                    "site_title": site_summary.get("title", ""),
                    "status": "skipped",
                    "reason": "no_runnable_skill_inputs",
                    "message": (
                        "No core source documents were found in the linked Drive "
                        "folder/M1 folder, so no DDR source skills have their "
                        "required inputs."
                    ),
                }
            )
            continue

        for event in events:
            source_event_status = "not_configured"
            source_event_error = ""
            if source_event_emitter is not None:
                try:
                    source_event_emitter(site_summary, event)
                    source_event_status = "emitted" if not dry_run else "dry_run"
                except Exception as exc:  # noqa: BLE001 - republish remains the compatibility path
                    source_event_status = "failed"
                    source_event_error = str(exc)
            kwargs = {
                "site_summary": site_summary,
                "reason": event["source_type"],
                "content_fingerprint": event["fingerprint"],
                "settings": settings,
                "system_prompt": system_prompt,
                "shared_cache": shared_cache,
                "republish_state": republish_state,
                "dry_run": dry_run,
                "source_event": event,
                "pipeline_runner": pipeline_runner,
            }
            if republish_callback is maybe_republish_dd_report:
                kwargs["failure_event_recorder"] = record_dd_republish_failure_event
                kwargs["run_without_existing_report"] = run_without_existing_report
            outcome = republish_callback(
                gc,
                **kwargs,
            )
            row = outcome.as_dict() if hasattr(outcome, "as_dict") else dict(outcome)
            row["site_title"] = site_summary.get("title", "")
            row["source_event_status"] = source_event_status
            if source_event_error:
                row["source_event_error"] = source_event_error
            rows.append(row)

    return {
        "sites_scanned": len(site_records),
        "source_events": sum(1 for row in rows if row.get("content_fingerprint")),
        "canonical_source_events": sum(1 for row in rows if row.get("source_event_status") == "emitted"),
        "source_event_errors": sum(1 for row in rows if row.get("source_event_status") == "failed"),
        "republished": sum(1 for row in rows if row.get("dd_report_republish") == "republish"),
        "skipped": sum(
            1
            for row in rows
            if str(row.get("dd_report_republish") or row.get("status") or "").startswith("skip")
            or row.get("status") == "skipped"
        ),
        "errors": sum(1 for row in rows if row.get("status") == "error" or row.get("error")),
        "rows": rows,
    }


def _site_summary(site_record: dict[str, Any]) -> dict[str, Any]:
    p1_dri = site_record.get("p1_dri") or site_record.get("p1Dri")
    p1_user_id = ""
    if isinstance(p1_dri, dict):
        p1_user_id = str(
            p1_dri.get("userId")
            or p1_dri.get("user_id")
            or p1_dri.get("_id")
            or p1_dri.get("id")
            or ""
        ).strip()
    return {
        "id": str(site_record.get("id") or site_record.get("site_id") or "").strip(),
        "slug": str(site_record.get("slug") or site_record.get("site_slug") or "").strip(),
        "title": str(site_record.get("title") or site_record.get("name") or "").strip(),
        "address": str(site_record.get("address") or site_record.get("site_address") or "").strip(),
        "drive_folder_url": str(site_record.get("drive_folder_url") or "").strip(),
        "p1_assignee_name": str(site_record.get("p1_assignee_name") or "").strip(),
        "p1_assignee_email": str(site_record.get("p1_assignee_email") or "").strip(),
        "p1_assignee_user_id": str(site_record.get("p1_assignee_user_id") or p1_user_id).strip(),
        "created_date": str(site_record.get("created_date") or "").strip(),
    }


def _ordered_site_records(site_records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(site_records, key=lambda record: (_site_cursor_key(record), str(record)))


def _site_cursor_key(site_record: dict[str, Any]) -> str:
    for key in ("id", "site_id", "slug", "site_slug", "title", "name", "address"):
        value = str(site_record.get(key) or "").strip()
        if value:
            return value
    return ""


def _site_index(site_records: Sequence[dict[str, Any]], cursor: str) -> int:
    if not cursor:
        return -1
    for index, site_record in enumerate(site_records):
        if _site_cursor_key(site_record) == cursor:
            return index
    return -1


def _positive_limit(value: int) -> int:
    return value if value > 0 else 0


def _is_vendor_source(
    gc: GoogleClient,
    file_info: dict[str, Any],
    *,
    doc_type: str,
    m1_folder_id: str | None,
    read_only: bool = False,
) -> bool:
    return is_vendor_sourced(
        file_info,
        gc=gc,
        m1_folder_id=m1_folder_id,
        doc_type=doc_type,
        read_only=read_only,
    )


def _folder_id_from_url(url: str) -> str:
    from .utils import extract_folder_id_from_url

    return extract_folder_id_from_url(url) or ""
