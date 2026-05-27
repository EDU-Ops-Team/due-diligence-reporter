"""Reconcile existing M1 Drive files into Rhodes document records."""

from __future__ import annotations

import logging
from typing import Any

from .classifier import classify_document
from .google_client import GoogleClient
from .m1_lookup import M1_RECOGNIZED_DOC_TYPES, _resolve_m1_folder
from .rhodes import (
    RhodesClient,
    RhodesError,
    map_ddr_doc_type_to_rhodes,
    register_rhodes_document_for_upload,
)

logger = logging.getLogger("drive_rhodes_reconciliation")

RECONCILIATION_SOURCE = "drive_rhodes_reconciliation"


def run_drive_rhodes_reconciliation(
    gc: GoogleClient,
    *,
    site_records: list[dict[str, Any]],
    dry_run: bool = False,
    rhodes_client: RhodesClient | None = None,
) -> dict[str, Any]:
    """Register existing recognized M1 Drive files on their Rhodes site records.

    This sweep is intentionally idempotent. Rhodes document registration is
    checked by Drive file ID before any write, so repeated runs become no-ops
    once each file has a Rhodes document record.
    """
    rows: list[dict[str, Any]] = []
    for site_record in site_records:
        site_summary = _site_summary(site_record)
        rows.extend(
            _reconcile_site(
                gc,
                site_summary=site_summary,
                dry_run=dry_run,
                rhodes_client=rhodes_client,
            )
        )

    return {
        "sites_scanned": len(site_records),
        "recognized_files": sum(1 for row in rows if row.get("drive_file_id")),
        "registered": sum(1 for row in rows if row.get("status") == "registered"),
        "already_registered": sum(
            1 for row in rows if row.get("status") == "already_registered"
        ),
        "would_register": sum(1 for row in rows if row.get("status") == "would_register"),
        "skipped": sum(1 for row in rows if row.get("status") == "skipped"),
        "errors": sum(1 for row in rows if row.get("status") in {"error", "failed"}),
        "rows": rows,
    }


def _reconcile_site(
    gc: GoogleClient,
    *,
    site_summary: dict[str, str],
    dry_run: bool,
    rhodes_client: RhodesClient | None,
) -> list[dict[str, Any]]:
    site_id = site_summary["id"]
    site_title = site_summary["title"]
    drive_folder_url = site_summary["drive_folder_url"]
    base = {
        "site_id": site_id,
        "site_title": site_title,
    }
    if not site_id:
        return [{**base, "status": "skipped", "reason": "missing_site_id"}]
    if not drive_folder_url:
        return [{**base, "status": "skipped", "reason": "missing_drive_folder_url"}]

    try:
        m1_folder_id, _m1_folder_url = _resolve_m1_folder(
            gc,
            drive_folder_url,
            create_if_missing=False,
            allow_legacy_fallback=False,
        )
    except Exception as exc:  # noqa: BLE001 - one site should not stop the sweep
        logger.warning("Failed to resolve M1 for %s: %s", site_title or site_id, exc)
        return [
            {
                **base,
                "status": "error",
                "reason": "m1_resolution_failed",
                "error": str(exc),
            }
        ]
    if not m1_folder_id:
        return [{**base, "status": "skipped", "reason": "m1_folder_missing"}]

    try:
        files = gc.list_files_in_folder(m1_folder_id)
    except Exception as exc:  # noqa: BLE001 - one site should not stop the sweep
        logger.warning("Failed to list M1 files for %s: %s", site_title or site_id, exc)
        return [
            {
                **base,
                "status": "error",
                "reason": "m1_file_listing_failed",
                "error": str(exc),
            }
        ]

    rows: list[dict[str, Any]] = []
    for file_info in files:
        row = _reconcile_file(
            site_summary=site_summary,
            file_info=file_info,
            dry_run=dry_run,
            rhodes_client=rhodes_client,
        )
        if row is not None:
            rows.append(row)
    if not rows:
        rows.append({**base, "status": "skipped", "reason": "no_recognized_m1_files"})
    return rows


def _reconcile_file(
    *,
    site_summary: dict[str, str],
    file_info: dict[str, Any],
    dry_run: bool,
    rhodes_client: RhodesClient | None,
) -> dict[str, Any] | None:
    file_name = str(file_info.get("name") or "").strip()
    if not file_name:
        return None
    ddr_doc_type, confidence = classify_document(file_name)
    if ddr_doc_type not in M1_RECOGNIZED_DOC_TYPES:
        return None

    site_id = site_summary["id"]
    drive_file_id = str(file_info.get("id") or "").strip()
    drive_url = str(file_info.get("webViewLink") or "").strip()
    mime_type = str(file_info.get("mimeType") or "application/pdf").strip()
    mapping = map_ddr_doc_type_to_rhodes(ddr_doc_type)
    base: dict[str, Any] = {
        "site_id": site_id,
        "site_title": site_summary["title"],
        "drive_file_id": drive_file_id,
        "drive_file_name": file_name,
        "drive_link": drive_url,
        "ddr_doc_type": ddr_doc_type,
        "classification_confidence": confidence,
        "rhodes_doc_type": mapping.doc_type if mapping else "",
        "rhodes_milestone": mapping.milestone if mapping else "",
    }
    if not drive_file_id:
        return {**base, "status": "error", "reason": "missing_drive_file_id"}
    if mapping is None:
        return {**base, "status": "skipped", "reason": "unmapped_doc_type"}

    if dry_run:
        existing = _find_existing_document(
            rhodes_client,
            site_id=site_id,
            drive_file_id=drive_file_id,
            doc_type=mapping.doc_type,
            milestone=mapping.milestone,
        )
        if existing is not None:
            return {
                **base,
                "status": "already_registered",
                "reason": "already_linked",
                "rhodes_document_id": _document_id(existing),
            }
        return {**base, "status": "would_register", "reason": "dry_run"}

    registration = register_rhodes_document_for_upload(
        site_id=site_id,
        ddr_doc_type=ddr_doc_type,
        title=file_name,
        drive_file_id=drive_file_id,
        drive_url=drive_url,
        mime_type=mime_type,
        original_filename=file_name,
        source=RECONCILIATION_SOURCE,
        client=rhodes_client,
    )
    return {**base, **registration}


def _find_existing_document(
    rhodes_client: RhodesClient | None,
    *,
    site_id: str,
    drive_file_id: str,
    doc_type: str,
    milestone: str | None,
) -> dict[str, Any] | None:
    if rhodes_client is None:
        return None
    try:
        return rhodes_client.find_document_by_drive_file_id(
            site_id=site_id,
            drive_file_id=drive_file_id,
            doc_type=doc_type,
            milestone=milestone,
        )
    except RhodesError as exc:
        logger.warning(
            "Dry-run Rhodes document lookup failed for site=%s file=%s: %s",
            site_id,
            drive_file_id,
            exc,
        )
        return None


def _site_summary(site_record: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(site_record.get("id") or site_record.get("site_id") or "").strip(),
        "title": str(site_record.get("title") or site_record.get("name") or "").strip(),
        "drive_folder_url": str(site_record.get("drive_folder_url") or "").strip(),
    }


def _document_id(document: dict[str, Any]) -> str:
    for key in ("documentId", "_id", "id"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
