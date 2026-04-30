"""Demote DD reports that were generated before the vendor gate shipped.

Pre-vendor-gate the readiness check counted any classifier-``sir`` file as a
satisfying SIR — including the AI-generated SIRs the pipeline drops in
``M1/`` itself. That produced ~empty DD Report Google Docs for sites whose
real vendor SIR / Building Inspection / RayCon scenario JSON had not yet
landed.

Tulsa (``Alpha School Tulsa 6940 S Utica Ave``) is the canonical case. This
script:

1. Loads the dashboard sites snapshot.
2. For each site, runs the new vendor-gating ``check_site_readiness_direct``
   to figure out whether the gate would have blocked report generation
   today (``VENDOR_GATE_ENABLED=1`` set for the duration of the script).
3. If the gate would have blocked AND a DD Report Google Doc currently
   exists in the site folder, archives the bogus report into a
   ``M1/_bogus-pre-vendor-gate/`` subfolder and demotes the dashboard's
   ``dd_status`` back to ``not_ready`` via the Reconciliation override
   path.

Dry-run by default (``DRY_RUN=1`` env). Set ``DRY_RUN=0`` to actually
move files and write overrides.

Run with::

    PYTHONPATH=src python3 scripts/cleanup_premature_dd_reports.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

# Force the vendor gate on for this script so readiness check uses the new path.
os.environ.setdefault("VENDOR_GATE_ENABLED", "1")

from due_diligence_reporter.config import get_settings  # noqa: E402
from due_diligence_reporter.google_client import GoogleClient  # noqa: E402
from due_diligence_reporter.report_pipeline import (  # noqa: E402
    _missing_required_docs,
    check_site_readiness_direct,
    list_shared_folders_once,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cleanup_premature_dd_reports")

_DRY_RUN = os.environ.get("DRY_RUN", "1").strip() not in {"0", "false", "False"}
_ARCHIVE_FOLDER_NAME = "_bogus-pre-vendor-gate"


def _list_sites_from_dashboard() -> list[dict[str, Any]]:
    """Pull the dashboard's sites.json snapshot (dashboard hosts roster)."""
    import urllib.request

    base = (
        os.environ.get("DASHBOARD_PUBLISH_URL")
        or "https://dd-dashboard-three.vercel.app"
    ).rstrip("/")
    url = f"{base}/sites.json"
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read())["sites"]


def _archive_report(
    gc: GoogleClient,
    site_folder_id: str,
    report_file_id: str,
    report_file_name: str,
) -> None:
    """Move a DD report into ``M1/_bogus-pre-vendor-gate/``."""
    if _DRY_RUN:
        logger.info(
            "[DRY] would archive %s (%s) into %s/",
            report_file_name,
            report_file_id,
            _ARCHIVE_FOLDER_NAME,
        )
        return

    # Find/create M1.
    m1 = next(
        (
            f
            for f in gc.list_subfolders(site_folder_id)
            if str(f.get("name", "")).lower().startswith("m1")
        ),
        None,
    )
    if not m1:
        logger.warning("no M1 folder under site %s; skipping archive", site_folder_id)
        return
    m1_id = m1["id"]

    archive = next(
        (
            f
            for f in gc.list_subfolders(m1_id)
            if str(f.get("name", "")) == _ARCHIVE_FOLDER_NAME
        ),
        None,
    )
    archive_id = (
        archive["id"]
        if archive
        else gc.create_folder(m1_id, _ARCHIVE_FOLDER_NAME)["id"]
    )

    # `gc.move_file` is the standard helper; if absent here, fall back to the
    # Drive API via gc.drive_service.files().update with addParents/removeParents.
    if hasattr(gc, "move_file"):
        gc.move_file(report_file_id, archive_id)
    else:
        # pragma: no cover — exercised only in production
        gc.drive_service.files().update(
            fileId=report_file_id,
            addParents=archive_id,
            removeParents=site_folder_id,
            fields="id, parents",
        ).execute()
    logger.info("archived %s into %s/", report_file_name, _ARCHIVE_FOLDER_NAME)


def main() -> int:
    settings = get_settings()
    gc = GoogleClient(settings)
    shared_cache = list_shared_folders_once(gc, settings)

    try:
        sites = _list_sites_from_dashboard()
    except Exception as e:
        logger.error("failed to load dashboard sites.json: %s", e)
        return 1

    cleaned = 0
    skipped = 0
    for site in sites:
        title = site.get("site_name") or site.get("marketing_name") or ""
        folder_url = site.get("drive_folder_url") or ""
        address = site.get("address") or site.get("city_state_zip") or ""
        if not title or not folder_url:
            skipped += 1
            continue

        try:
            readiness = check_site_readiness_direct(
                gc,
                folder_url,
                [title, address] if address else [title],
                shared_cache,
                site_title=title,
                site_address=address,
            )
        except Exception as e:
            logger.warning("readiness check failed for %s: %s", title, e)
            skipped += 1
            continue

        missing = _missing_required_docs(readiness)
        if not missing:
            continue  # site is genuinely ready under the new gate
        if not readiness.get("report_exists"):
            continue  # nothing to clean up

        # Locate the DD Report file in all_files.
        report_files = [
            f
            for f in (readiness.get("all_files") or [])
            if f.get("doc_type") == "dd_report"
        ]
        if not report_files:
            continue

        from due_diligence_reporter.utils import extract_folder_id_from_url

        site_folder_id = extract_folder_id_from_url(folder_url) or ""
        for f in report_files:
            logger.info(
                "%s: gate would block (%s); archiving %s",
                title,
                ", ".join(missing),
                f.get("name"),
            )
            try:
                _archive_report(gc, site_folder_id, f["id"], f.get("name", ""))
                cleaned += 1
            except Exception as e:
                logger.error("archive failed for %s: %s", title, e)

    logger.info("done: %d archived, %d skipped (DRY_RUN=%s)", cleaned, skipped, _DRY_RUN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
