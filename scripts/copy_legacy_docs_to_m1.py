#!/usr/bin/env python3
"""copy_legacy_docs_to_m1.py — One-shot migration to copy SIR / Building
Inspection / ISP files from the legacy shared Drive folders into each site's
own M1 subfolder.

Going forward, ``inbox_scanner`` writes new uploads straight into the per-site
M1 folder. This script catches up the historical state by COPYING the matching
files from the three shared folders (SIR / ISP / Building Inspection) into
each active Wrike site's M1. Originals are intentionally left in place as a
backup.

For each active Wrike Site Record we:

  1. Ensure the site has a Drive folder URL (else skip with a warning).
  2. Resolve / create the per-site M1 subfolder.
  3. Run the same ``_find_site_docs_in_shared_folders`` matcher the scanner
     and report pipeline use, so we never invent a match the live system
     wouldn't make.
  4. For each (sir / building_inspection / isp) hit, list M1 contents through
     ``_list_m1_documents_by_type``. If the doc_type is already present in M1
     we skip — the scanner has already deposited a copy.
  5. Otherwise call ``gc.copy_document`` to put the file in M1, preserving
     the original filename.

The script is idempotent: re-runs only copy files that aren't already in M1.

Run:
    uv run python scripts/copy_legacy_docs_to_m1.py            # all sites
    uv run python scripts/copy_legacy_docs_to_m1.py austin     # single site
    uv run python scripts/copy_legacy_docs_to_m1.py --dry-run  # log only

Env (from .env or the workflow secrets):
    OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET / OAUTH_REFRESH_TOKEN  Google OAuth
    WRIKE_ACCESS_TOKEN      to enumerate active sites
    SIR_FOLDER_ID, ISP_FOLDER_ID, BUILDING_INSPECTION_FOLDER_ID  shared folders
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

from due_diligence_reporter.config import get_settings  # noqa: E402
from due_diligence_reporter.google_client import GoogleClient  # noqa: E402
from due_diligence_reporter.inbox_scanner import (  # noqa: E402
    _list_m1_documents_by_type,
    _resolve_m1_folder,
)
from due_diligence_reporter.server import (  # noqa: E402
    _find_site_docs_in_shared_folders,
)
from due_diligence_reporter.utils import build_site_match_terms  # noqa: E402
from due_diligence_reporter.wrike import (  # noqa: E402
    _get_active_status_ids,
    _get_all_site_records,
    extract_address_from_record,
    extract_google_folder_from_record,
    filter_active_site_records,
    load_wrike_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("copy_legacy_docs_to_m1")

# Doc types we mirror into M1. Block Plan never lived in a shared folder, so
# it's not part of this migration.
MIGRATED_DOC_TYPES: tuple[str, ...] = ("sir", "building_inspection", "isp")


def _copy_one_site(
    gc: GoogleClient,
    *,
    site_title: str,
    site_address: str,
    drive_folder_url: str,
    dry_run: bool,
) -> tuple[int, int, int]:
    """Return ``(copied, skipped_already_present, skipped_not_found)`` for one site."""

    m1_folder_id, _ = _resolve_m1_folder(gc, drive_folder_url)
    if not m1_folder_id:
        logger.warning("%s: could not resolve M1 folder, skipping", site_title)
        return 0, 0, 0

    # Existing M1 contents — keyed by doc_type.
    try:
        existing = _list_m1_documents_by_type(gc, m1_folder_id)
    except Exception as exc:
        logger.warning("%s: M1 listing failed (%s); proceeding without dedup", site_title, exc)
        existing = {}

    # Same matcher the scanner & reporter use.
    match_terms = build_site_match_terms(site_title, site_address)
    try:
        shared = _find_site_docs_in_shared_folders(
            gc,
            match_terms,
            site_title=site_title,
            site_address=site_address,
        )
    except Exception as exc:
        logger.warning("%s: shared-folder scan failed: %s", site_title, exc)
        return 0, 0, 0

    copied = 0
    skipped_present = 0
    skipped_missing = 0

    for doc_type in MIGRATED_DOC_TYPES:
        match = shared.get(doc_type)
        if not match:
            skipped_missing += 1
            continue
        if doc_type in existing:
            logger.info(
                "%s: %s already in M1 (%s) - skip",
                site_title,
                doc_type,
                existing[doc_type].get("name"),
            )
            skipped_present += 1
            continue

        src_id = match.get("id")
        src_name = match.get("name") or f"{site_title} {doc_type}"
        if not src_id:
            logger.warning("%s: %s match missing file id, skipping", site_title, doc_type)
            continue

        if dry_run:
            logger.info(
                "%s: would copy %s '%s' (%s) -> M1 (%s)",
                site_title,
                doc_type,
                src_name,
                src_id,
                m1_folder_id,
            )
            copied += 1
            continue

        try:
            new_doc = gc.copy_document(
                template_id=src_id,
                name=src_name,
                parent_folder_id=m1_folder_id,
            )
            logger.info(
                "%s: copied %s '%s' -> M1 (new id %s)",
                site_title,
                doc_type,
                src_name,
                new_doc.get("id"),
            )
            copied += 1
        except Exception as exc:
            logger.error(
                "%s: failed to copy %s '%s' (%s): %s",
                site_title,
                doc_type,
                src_name,
                src_id,
                exc,
            )

    return copied, skipped_present, skipped_missing


def main(*, site_filter: str | None, dry_run: bool) -> int:
    settings = get_settings()
    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )
    wrike_cfg = load_wrike_config()
    records = _get_all_site_records(cfg=wrike_cfg)
    active_status_ids = _get_active_status_ids(access_token=wrike_cfg.access_token)
    active = filter_active_site_records(records, active_status_ids)

    sites_processed = 0
    sites_with_no_drive = 0
    total_copied = 0
    total_skipped_present = 0
    total_skipped_missing = 0

    for rec in active:
        title = (rec.get("title") or "").strip()
        if not title:
            continue
        if site_filter and site_filter.lower() not in title.lower():
            continue

        drive_url = extract_google_folder_from_record(rec) or ""
        address = extract_address_from_record(rec) or ""

        if not drive_url:
            logger.info("%s: no Drive folder URL, skipping", title)
            sites_with_no_drive += 1
            continue

        copied, skipped_present, skipped_missing = _copy_one_site(
            gc,
            site_title=title,
            site_address=address,
            drive_folder_url=drive_url,
            dry_run=dry_run,
        )
        sites_processed += 1
        total_copied += copied
        total_skipped_present += skipped_present
        total_skipped_missing += skipped_missing

    verb = "would copy" if dry_run else "copied"
    logger.info(
        "Migration complete: %d sites processed, %d sites without Drive folder. "
        "%s %d files; skipped %d already-in-M1, %d not-found-in-shared.",
        sites_processed,
        sites_with_no_drive,
        verb,
        total_copied,
        total_skipped_present,
        total_skipped_missing,
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "site_filter",
        nargs="?",
        default=None,
        help="Optional substring filter on Wrike site title.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect matches and log proposed copies, but do not call Drive API.",
    )
    args = parser.parse_args()
    sys.exit(main(site_filter=args.site_filter, dry_run=args.dry_run))
