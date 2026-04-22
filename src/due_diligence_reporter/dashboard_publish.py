"""Publish a SiteRecord to the site's Drive folder as JSON.

This is the reporter-side half of the DD dashboard integration.
The aggregator (in alpha-dd-pipeline) reads these per-site JSON files
across the ``All Locations`` Drive tree and produces the dashboard-facing
``sites.json``.

Upsert semantics
----------------

The payload is written as ``{slug}.dashboard.json`` in the site's Drive
folder. If a previous version exists (same exact filename, not trashed),
it is moved to trash before the new file is uploaded. This keeps the
folder clean and guarantees the aggregator sees exactly one authoritative
record per site.

This module is deliberately thin: build the JSON bytes, upsert the file,
return the Drive metadata. Any failures are raised — the caller
(``create_dd_report``) wraps the call in try/except so a publish failure
never blocks the DD report itself.
"""

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
    """Stable filename used by the aggregator to find the payload."""
    return f"{slug}{DASHBOARD_PAYLOAD_SUFFIX}"


def _trash_existing(gc: GoogleClient, folder_id: str, filename: str) -> int:
    """Move any existing file with the exact filename into the trash.

    Returns the count of files trashed. Missing file is not an error.
    Failures are logged and swallowed — the subsequent upload still
    proceeds, which at worst creates a duplicate that the aggregator
    can dedupe by ``modifiedTime``.
    """
    try:
        files = gc.list_files_in_folder(folder_id)
    except Exception as e:
        logger.warning("list_files_in_folder failed during trash-sweep: %s", e)
        return 0

    trashed = 0
    for f in files:
        if f.get("name") == filename:
            file_id = f.get("id")
            if not file_id:
                continue
            try:
                gc.drive_service.files().update(
                    fileId=file_id,
                    body={"trashed": True},
                    supportsAllDrives=True,
                ).execute()
                trashed += 1
                logger.info("Trashed old dashboard payload: %s (id=%s)", filename, file_id)
            except Exception as e:
                logger.warning("Failed to trash old %s (id=%s): %s", filename, file_id, e)
    return trashed


def publish_site_record(
    gc: GoogleClient,
    folder_id: str,
    record: SiteRecord,
) -> dict[str, Any]:
    """Upsert the SiteRecord as JSON into the site's Drive folder.

    Parameters
    ----------
    gc:
        Authenticated GoogleClient.
    folder_id:
        Target Drive folder — the site's root folder.
    record:
        The ``SiteRecord`` to publish. Its ``slug`` drives the filename.

    Returns
    -------
    dict with ``file_id``, ``file_name``, ``web_view_link``,
    ``payload_bytes``, and ``replaced_count`` (how many old versions
    were trashed).
    """
    filename = build_dashboard_filename(record.slug)
    payload = json.dumps(record.to_dict(), indent=2, ensure_ascii=False)
    payload_bytes = payload.encode("utf-8")

    replaced = _trash_existing(gc, folder_id=folder_id, filename=filename)

    uploaded = gc.upload_file_to_folder(
        folder_id=folder_id,
        file_name=filename,
        file_bytes=payload_bytes,
        mime_type=DASHBOARD_MIME_TYPE,
    )

    file_id = uploaded.get("id", "")
    web_view_link = uploaded.get("webViewLink", "")
    logger.info(
        "Published dashboard payload: slug=%s file=%s replaced=%d url=%s",
        record.slug,
        filename,
        replaced,
        web_view_link,
    )

    return {
        "file_id": file_id,
        "file_name": filename,
        "web_view_link": web_view_link,
        "payload_bytes": len(payload_bytes),
        "replaced_count": replaced,
    }
