#!/usr/bin/env python3
"""
backfill_wrike_created_at.py — One-shot backfill of `wrike_created_at`
onto every row in the DD Dashboard's sites.json.

For each entry in dd-dashboard/client/public/sites.json:
  1. Look up the Wrike folder by site_name.
  2. Pull its `createdDate` (ISO 8601).
  3. Write it back onto the row as `wrike_created_at`.

Run:
    uv run python scripts/backfill_wrike_created_at.py           # all sites
    uv run python scripts/backfill_wrike_created_at.py --dry-run # no writes
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

from due_diligence_reporter.wrike import (  # noqa: E402
    build_site_summary,
    find_site_record,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("backfill_wrike_created_at")

# Path into the dashboard repo. Relative to workspace root since both live
# as siblings under /home/user/workspace.
SITES_JSON = (
    _project_root.parent / "dd-dashboard" / "client" / "public" / "sites.json"
)


def backfill(*, dry_run: bool) -> int:
    if not SITES_JSON.exists():
        logger.error("sites.json not found at %s", SITES_JSON)
        return 2

    payload = json.loads(SITES_JSON.read_text())
    sites = payload.get("sites", [])
    logger.info("Loaded %d sites from %s", len(sites), SITES_JSON)

    updated = 0
    skipped = 0
    failed = 0

    for row in sites:
        slug = row.get("slug", "?")
        name = row.get("site_name") or row.get("marketing_name") or ""
        if row.get("wrike_created_at"):
            logger.info("[%s] already has wrike_created_at — skip", slug)
            skipped += 1
            continue
        if not name:
            logger.warning("[%s] no site_name — skip", slug)
            failed += 1
            continue
        try:
            record = find_site_record(site_name_or_id=name)
        except Exception as e:
            logger.warning("[%s] Wrike lookup failed for '%s': %s", slug, name, e)
            failed += 1
            continue
        if not record:
            logger.warning("[%s] no Wrike record for '%s'", slug, name)
            failed += 1
            continue
        summary = build_site_summary(record)
        created = summary.get("created_date") or ""
        if not created:
            logger.warning("[%s] Wrike record has no createdDate", slug)
            failed += 1
            continue
        row["wrike_created_at"] = created
        logger.info("[%s] %s  →  %s", slug, name[:50], created)
        updated += 1

    logger.info(
        "Summary: updated=%d  skipped=%d  failed=%d  total=%d",
        updated,
        skipped,
        failed,
        len(sites),
    )

    if dry_run:
        logger.info("Dry-run: not writing sites.json")
        return 0

    if updated > 0:
        SITES_JSON.write_text(json.dumps(payload, indent=2) + "\n")
        logger.info("Wrote updated sites.json")

    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return backfill(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
