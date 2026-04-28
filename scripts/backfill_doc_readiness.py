#!/usr/bin/env python3
"""backfill_doc_readiness.py — One-shot Pending -> Complete flips for the
DD Dashboard's Portfolio doc-readiness columns.

The dashboard exposes four "doc readiness" columns on the Portfolio page:

    cds_sir_status, building_inspection_status, block_plan_status, lidar_status

Going forward, ``inbox_scanner`` flips them as new attachments land. This
script catches up every site that had docs in Drive *before* the wiring
shipped. It does NOT touch ``lidar_status`` — the reporter has no LiDAR
ingestion today, so any LiDAR rows must be set by hand.

For each active Wrike Site Record we:

  1. Search the shared SIR / Building Inspection folders for a file whose
     filename matches the site (mirrors ``inbox_scanner``'s match logic).
  2. List the site's M1 folder for an existing Block Plan PDF.
  3. Collect every doc that is present into a single batch and POST it to
     ``/api/auto-readiness``.

The endpoint is one-way and never overwrites manual edits, so re-running
the script is harmless.

Run:
    uv run python scripts/backfill_doc_readiness.py            # all sites
    uv run python scripts/backfill_doc_readiness.py austin     # single site
    uv run python scripts/backfill_doc_readiness.py --dry-run  # don't POST

Env (from .env or the workflow secrets):
    INBOX_SCANNER_TOKEN     bearer for /api/auto-readiness
    DASHBOARD_PUBLISH_URL   defaults to https://dd-dashboard-three.vercel.app
    OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET / OAUTH_REFRESH_TOKEN  Google OAuth
    WRIKE_ACCESS_TOKEN      to enumerate active sites
    SIR_FOLDER_ID, BUILDING_INSPECTION_FOLDER_ID  shared Drive folders
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

from due_diligence_reporter.config import get_settings  # noqa: E402
from due_diligence_reporter.dashboard_publisher import slugify  # noqa: E402
from due_diligence_reporter.dashboard_readiness import (  # noqa: E402
    DOC_TYPE_TO_FIELD,
    mark_readiness_complete,
)
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
logger = logging.getLogger("backfill_doc_readiness")


def _detect_doc_types_for_site(
    gc: GoogleClient,
    *,
    site_title: str,
    site_address: str,
    drive_folder_url: str,
) -> list[str]:
    """Return the list of reporter doc_types present in Drive for this site.

    Uses the same matchers as the live scanner so behaviour is consistent.
    Detected types are a subset of ``DOC_TYPE_TO_FIELD`` keys.
    """
    found: list[str] = []

    # SIR + Building Inspection live in shared Drive folders. We reuse the
    # scanner's matcher for filename-then-LLM matching so we don't drift.
    try:
        match_terms = build_site_match_terms(site_title, site_address)
        shared = _find_site_docs_in_shared_folders(
            gc,
            match_terms,
            site_title=site_title,
            site_address=site_address,
        )
    except Exception as exc:  # network/oauth hiccup — log, keep going
        logger.warning("%s: shared-folder scan failed: %s", site_title, exc)
        shared = {}

    if shared.get("sir"):
        found.append("sir")
    if shared.get("building_inspection"):
        found.append("building_inspection")

    # Block Plan lives in the site's own M1 subfolder.
    try:
        if drive_folder_url:
            m1_folder_id, _url = _resolve_m1_folder(gc, drive_folder_url)
            if m1_folder_id:
                m1_docs = _list_m1_documents_by_type(gc, m1_folder_id)
                if m1_docs.get("block_plan"):
                    found.append("block_plan")
    except Exception as exc:
        logger.warning("%s: M1 scan failed: %s", site_title, exc)

    return found


def _build_edits_for_site(
    *, site_title: str, doc_types: list[str]
) -> list[dict[str, str]]:
    slug = slugify(site_title)
    if not slug:
        return []
    edits: list[dict[str, str]] = []
    for dt in doc_types:
        field = DOC_TYPE_TO_FIELD.get(dt)
        if not field:
            continue
        edits.append({"slug": slug, "fieldPath": field})
    return edits


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

    all_edits: list[dict[str, str]] = []
    sites_with_any_doc = 0
    sites_skipped = 0
    breakdown: dict[str, int] = {dt: 0 for dt in DOC_TYPE_TO_FIELD}

    for rec in active:
        title = (rec.get("title") or "").strip()
        if not title:
            continue
        if site_filter and site_filter.lower() not in title.lower():
            continue

        drive_url = extract_google_folder_from_record(rec) or ""
        address = extract_address_from_record(rec) or ""

        if not drive_url:
            logger.info("%s: no Drive folder, skipping", title)
            sites_skipped += 1
            continue

        doc_types = _detect_doc_types_for_site(
            gc,
            site_title=title,
            site_address=address,
            drive_folder_url=drive_url,
        )
        if not doc_types:
            logger.info("%s: no readiness docs detected", title)
            sites_skipped += 1
            continue

        for dt in doc_types:
            breakdown[dt] = breakdown.get(dt, 0) + 1

        site_edits = _build_edits_for_site(site_title=title, doc_types=doc_types)
        all_edits.extend(site_edits)
        sites_with_any_doc += 1

        logger.info(
            "%s -> %s",
            title,
            ", ".join(f"{dt}:{DOC_TYPE_TO_FIELD[dt]}" for dt in doc_types),
        )

    logger.info(
        "Detection complete: %d sites with at least one doc, %d sites skipped, %d total flips",
        sites_with_any_doc,
        sites_skipped,
        len(all_edits),
    )
    for dt, n in breakdown.items():
        logger.info("  %s: %d site(s)", dt, n)

    if not all_edits:
        logger.info("Nothing to flip.")
        return 0

    if dry_run:
        logger.info("--dry-run: not posting to /api/auto-readiness")
        for edit in all_edits[:50]:
            logger.info("  would flip: %s.%s", edit["slug"], edit["fieldPath"])
        if len(all_edits) > 50:
            logger.info("  ... and %d more", len(all_edits) - 50)
        return 0

    # The endpoint caps at 500 edits per call; chunk just in case the
    # active site count grows past that in the future.
    CHUNK = 400
    total_applied = 0
    total_skipped: list[Any] = []
    for i in range(0, len(all_edits), CHUNK):
        batch = all_edits[i : i + CHUNK]
        result = mark_readiness_complete(batch)
        if not result.get("ok"):
            logger.error(
                "Batch %d-%d failed: %s",
                i,
                i + len(batch),
                result.get("reason"),
            )
            return 1
        total_applied += int(result.get("applied", 0))
        skipped = result.get("skipped", []) or []
        if isinstance(skipped, list):
            total_skipped.extend(skipped)

    logger.info(
        "Auto-readiness backfill complete: %d applied, %d skipped (already-set or manual)",
        total_applied,
        len(total_skipped),
    )
    if total_skipped:
        # Log up to a handful for visibility.
        for s in total_skipped[:10]:
            logger.info("  skipped: %s", s)
        if len(total_skipped) > 10:
            logger.info("  ... and %d more", len(total_skipped) - 10)

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
        help="Detect docs and log proposed flips, but do not POST.",
    )
    args = parser.parse_args()
    sys.exit(main(site_filter=args.site_filter, dry_run=args.dry_run))
