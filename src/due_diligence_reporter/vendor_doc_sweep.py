"""Active Rhodes-backed sweep for DDR source document arrivals."""

from __future__ import annotations

import logging
from collections.abc import Callable
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
    "raycon_scenario_json",
    "e_occupancy_report",
    "school_approval_report",
}

RepublishCallback = Callable[..., Any]


def collect_core_source_events(
    gc: GoogleClient,
    site_record: dict[str, Any],
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
    # five core v1 source types; the helper will still no-op when no DDR exists.
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
            events = collect_core_source_events(gc, site_summary)
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
                    "reason": "no_core_sources_found",
                }
            )
            continue

        for event in events:
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
            outcome = republish_callback(
                gc,
                **kwargs,
            )
            row = outcome.as_dict() if hasattr(outcome, "as_dict") else dict(outcome)
            row["site_title"] = site_summary.get("title", "")
            rows.append(row)

    return {
        "sites_scanned": len(site_records),
        "source_events": sum(1 for row in rows if row.get("content_fingerprint")),
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


def _is_vendor_source(
    gc: GoogleClient,
    file_info: dict[str, Any],
    *,
    doc_type: str,
    m1_folder_id: str | None,
) -> bool:
    return is_vendor_sourced(
        file_info,
        gc=gc,
        m1_folder_id=m1_folder_id,
        doc_type=doc_type,
    )


def _folder_id_from_url(url: str) -> str:
    from .utils import extract_folder_id_from_url

    return extract_folder_id_from_url(url) or ""
