"""Publish dashboard payloads into a site's Drive folder."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from .site_record import SiteRecord

if TYPE_CHECKING:
    from .google_client import GoogleClient

logger = logging.getLogger(__name__)

DASHBOARD_PAYLOAD_SUFFIX = ".dashboard.json"
DASHBOARD_MIME_TYPE = "application/json"


def build_dashboard_filename(slug: str) -> str:
    """Return the stable dashboard payload filename for a site slug."""
    return f"{slug}{DASHBOARD_PAYLOAD_SUFFIX}"


def _list_existing_payload_ids(
    gc: GoogleClient,
    folder_id: str,
    filename: str,
) -> list[str]:
    """Return IDs of pre-existing payload files with the same name."""
    try:
        files = gc.list_files_in_folder(folder_id)
    except Exception as exc:
        logger.warning("list_files_in_folder failed during dashboard upsert: %s", exc)
        return []
    return [
        str(file_info.get("id"))
        for file_info in files
        if file_info.get("name") == filename and file_info.get("id")
    ]


def _trash_file_ids(gc: GoogleClient, file_ids: list[str]) -> int:
    """Trash older payload file IDs after a successful replacement upload."""
    trashed = 0
    for file_id in file_ids:
        try:
            gc.drive_service.files().update(
                fileId=file_id,
                body={"trashed": True},
                supportsAllDrives=True,
            ).execute()
            trashed += 1
        except Exception as exc:
            logger.warning("Failed to trash dashboard payload id=%s: %s", file_id, exc)
    return trashed


def publish_site_record(
    gc: GoogleClient,
    folder_id: str,
    record: SiteRecord,
) -> dict[str, Any]:
    """Upload the dashboard payload without deleting the last good copy first."""
    filename = build_dashboard_filename(record.slug)
    payload_bytes = json.dumps(
        record.to_dict(),
        indent=2,
        ensure_ascii=False,
    ).encode("utf-8")
    existing_ids = _list_existing_payload_ids(gc, folder_id, filename)

    uploaded = gc.upload_file_to_folder(
        folder_id=folder_id,
        file_name=filename,
        file_bytes=payload_bytes,
        mime_type=DASHBOARD_MIME_TYPE,
    )

    uploaded_id = str(uploaded.get("id", ""))
    stale_ids = [file_id for file_id in existing_ids if file_id and file_id != uploaded_id]
    replaced = _trash_file_ids(gc, stale_ids)
    web_view_link = str(uploaded.get("webViewLink", ""))

    logger.info(
        "Published dashboard payload: slug=%s file=%s replaced=%d url=%s",
        record.slug,
        filename,
        replaced,
        web_view_link,
    )
    return {
        "file_id": uploaded_id,
        "file_name": filename,
        "web_view_link": web_view_link,
        "payload_bytes": len(payload_bytes),
        "replaced_count": replaced,
    }
