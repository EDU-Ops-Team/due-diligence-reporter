#!/usr/bin/env python3
"""sync_site_roster.py - Seed dashboard rows for active Wrike sites that
don't have a DD report yet.

The Portfolio table is built from ``client/public/sites.json`` on the
dashboard repo. ``sites.json`` is populated by the per-report publish path,
which means a brand-new Wrike site (e.g. Sausalito) doesn't appear on the
dashboard at all until its first DD report runs. Operators want a single
view of every active site so doc readiness can be tracked from day one.

This script closes the gap. It:

  1. Fetches the live ``sites.json`` from the dashboard.
  2. Walks every active Wrike Site Record.
  3. For each Wrike record whose slug is NOT in ``sites.json``, calls
     ``publish_to_dashboard`` with ``report_data={}`` and ``dd_status =
     "not_ready"``. The publisher emits a full SiteRecord shape with
     identity fields populated (site_name, address, state, school_type,
     site_owner, drive_folder_url, wrike_created_at) and analytical
     fields blank.
  4. Skips slugs that already exist - the dashboard's sticky-preserve
     transform would treat blanks as "no change", but we'd still bump
     ``published_at`` and trigger a Vercel rebuild for nothing. Better
     to no-op cleanly.

Idempotent. Safe to run on any cadence. The dashboard's
``/api/sites/:slug/publish`` endpoint never overwrites manual overrides
or doc-readiness flips, so re-publishing a stub for an existing slug
would be harmless if we did it; we just don't, to keep diffs tiny.

Run:
    uv run python scripts/sync_site_roster.py            # all missing sites
    uv run python scripts/sync_site_roster.py austin     # single site filter
    uv run python scripts/sync_site_roster.py --dry-run  # don't POST

Env (from .env or workflow secrets):
    DASHBOARD_PUBLISH_SECRET   bearer for /api/sites/:slug/publish
    DASHBOARD_PUBLISH_URL      defaults to https://dd-dashboard-three.vercel.app
    WRIKE_ACCESS_TOKEN         Wrike API
    OAUTH_*                    Google OAuth (not strictly required here, but
                               GoogleClient bootstrap touches them)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import requests

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

from due_diligence_reporter.dashboard_publisher import (  # noqa: E402
    publish_to_dashboard,
    slugify,
)
from due_diligence_reporter.wrike import (  # noqa: E402
    _get_active_status_ids,
    _get_all_site_records,
    extract_address_from_record,
    extract_created_date_from_record,
    extract_google_folder_from_record,
    extract_p1_from_record,
    extract_school_type_from_record,
    filter_active_site_records,
    load_wrike_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("sync_site_roster")

_DEFAULT_BASE_URL = "https://dd-dashboard-three.vercel.app"


def _existing_slugs() -> set[str]:
    """Pull the live sites.json and return the set of slugs already on it."""
    base = (
        os.environ.get("DASHBOARD_PUBLISH_URL") or _DEFAULT_BASE_URL
    ).rstrip("/")
    url = f"{base}/sites.json"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Could not fetch %s: %s", url, exc)
        # Fail loud rather than seed everything (which would re-publish 27
        # rows and clobber published_at on every existing site).
        raise SystemExit(1)
    data = resp.json()
    slugs = {
        s.get("slug")
        for s in data.get("sites", [])
        if isinstance(s, dict) and s.get("slug")
    }
    logger.info("Live sites.json carries %d slugs", len(slugs))
    return slugs


def _publish_stub(
    *,
    site_title: str,
    address: str,
    school_type: str | None,
    drive_folder_url: str,
    site_owner: str,
    wrike_created_at: str | None,
) -> bool:
    """Publish an empty-report stub for one site. Returns True on success."""
    return publish_to_dashboard(
        site_title,
        report_data={},
        address=address or None,
        school_type=school_type,
        drive_folder_url=drive_folder_url or None,
        site_owner=site_owner or None,
        wrike_created_at=wrike_created_at,
        # Force the not_ready label - without this the publisher auto-stamps
        # "complete" since it normally only runs after a finished report.
        dd_status="not_ready",
    )


def main(*, site_filter: str | None, dry_run: bool) -> int:
    if not os.environ.get("DASHBOARD_PUBLISH_SECRET"):
        logger.error(
            "DASHBOARD_PUBLISH_SECRET not set - cannot publish to the dashboard"
        )
        return 1

    existing = _existing_slugs()

    wrike_cfg = load_wrike_config()
    records = _get_all_site_records(cfg=wrike_cfg)
    active_status_ids = _get_active_status_ids(access_token=wrike_cfg.access_token)
    active = filter_active_site_records(records, active_status_ids)
    logger.info("Wrike returned %d active site record(s)", len(active))

    missing: list[dict] = []
    for rec in active:
        title = (rec.get("title") or "").strip()
        if not title:
            continue
        if site_filter and site_filter.lower() not in title.lower():
            continue
        slug = slugify(title)
        if not slug:
            continue
        if slug in existing:
            continue
        missing.append(rec)

    logger.info("%d Wrike site(s) missing from the dashboard", len(missing))
    if not missing:
        return 0

    failures = 0
    succeeded = 0

    for rec in missing:
        title = (rec.get("title") or "").strip()
        address = extract_address_from_record(rec) or ""
        school_type = extract_school_type_from_record(rec)
        drive = extract_google_folder_from_record(rec) or ""
        p1 = extract_p1_from_record(rec) or {}
        owner = p1.get("name") or ""
        created = extract_created_date_from_record(rec)

        logger.info(
            "Stub publish: %s | addr=%r | type=%r | owner=%r | created=%s",
            title,
            address,
            school_type,
            owner,
            created,
        )

        if dry_run:
            continue

        try:
            ok = _publish_stub(
                site_title=title,
                address=address,
                school_type=school_type,
                drive_folder_url=drive,
                site_owner=owner,
                wrike_created_at=created,
            )
        except Exception as exc:  # publish_to_dashboard already swallows;
            # belt-and-suspenders.
            logger.warning("Publish raised for %s: %s", title, exc)
            ok = False

        if ok:
            succeeded += 1
        else:
            failures += 1

    logger.info(
        "Roster sync complete: %d stub(s) published, %d failed", succeeded, failures
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "site_filter",
        nargs="?",
        default=None,
        help="Substring filter on Wrike site title.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect missing sites but do not POST stub publishes.",
    )
    args = parser.parse_args()
    sys.exit(main(site_filter=args.site_filter, dry_run=args.dry_run))
