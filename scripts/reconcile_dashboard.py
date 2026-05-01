#!/usr/bin/env python3
"""
reconcile_dashboard.py — Prune the DD Dashboard to match Wrike's Active set.

Workflow flow (live publish path) only ever ADDS or UPDATES sites in the
dashboard's sites.json. When Wrike moves a site out of the Active status
group (cancelled / on-hold / deferred), the row stays on the dashboard
forever. This script reconciles by:

  1. Fetching the live sites.json from the dashboard.
  2. Listing every Wrike Site Record and computing the set of *expected* slugs
     (active records only, slug derived via dashboard_publisher.slugify).
  3. Diffing: any slug present on the dashboard but NOT in the active set is
     an orphan candidate.
  4. Default RECONCILE_DRY_RUN=1: log the orphan list and exit. No changes.
  5. Set RECONCILE_DRY_RUN=0 to actually issue
     DELETE /api/sites/<slug>/publish for each orphan, with a reason header
     so the GitHub commit message captures why it was removed.

Env (from .env):
    DASHBOARD_PUBLISH_URL, DASHBOARD_PUBLISH_SECRET
    plus the usual pipeline env (Wrike)
    RECONCILE_DRY_RUN  (default "1"; set "0" to actually delete)

Run:
    uv run python scripts/reconcile_dashboard.py            # dry-run, all
    RECONCILE_DRY_RUN=0 uv run python scripts/reconcile_dashboard.py  # apply
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import requests

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

from due_diligence_reporter.dashboard_publisher import slugify  # noqa: E402
from due_diligence_reporter.wrike import (  # noqa: E402
    _get_active_status_ids,
    _get_all_site_records,
    is_record_active,
    load_wrike_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("reconcile_dashboard")

_DEFAULT_BASE_URL = "https://dd-dashboard-three.vercel.app"


def _is_dry_run() -> bool:
    raw = os.environ.get("RECONCILE_DRY_RUN", "1").strip().lower()
    # Default-on: anything other than an explicit "0"/"false"/"no" stays dry.
    return raw not in {"0", "false", "no"}


def _dashboard_base_url() -> str:
    return (os.environ.get("DASHBOARD_PUBLISH_URL") or _DEFAULT_BASE_URL).rstrip("/")


def _fetch_dashboard_slugs(base_url: str, *, timeout: int = 20) -> list[dict[str, Any]]:
    """Return the sites array from the live sites.json (slug + minimal meta)."""
    url = f"{base_url}/sites.json"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    payload = r.json()
    sites = payload.get("sites") or []
    if not isinstance(sites, list):
        return []
    return [s for s in sites if isinstance(s, dict) and s.get("slug")]


def _expected_slugs_from_wrike() -> tuple[set[str], dict[str, str]]:
    """Build the set of slugs that *should* exist on the dashboard.

    Returns (slug_set, slug_to_status_label) — the status label is used purely
    for log/audit messages on inactive records that we are NOT pruning (e.g.
    they were never published in the first place).
    """
    cfg = load_wrike_config()
    records = _get_all_site_records(cfg=cfg)
    active_ids = _get_active_status_ids(access_token=cfg.access_token)

    expected: set[str] = set()
    inactive_slugs: dict[str, str] = {}
    for rec in records:
        title = (rec.get("title") or "").strip()
        if not title:
            continue
        slug = slugify(title)
        if not slug:
            continue
        if is_record_active(rec, active_ids):
            expected.add(slug)
        else:
            # Track status id for the audit log, raw value is informative
            # enough even if we don't resolve it to a human name here.
            inactive_slugs[slug] = str(rec.get("customStatusId") or "inactive")
    return expected, inactive_slugs


def _delete_site(
    base_url: str,
    slug: str,
    secret: str,
    *,
    reason: str,
    timeout: int = 20,
) -> bool:
    endpoint = f"{base_url}/api/sites/{slug}/publish"
    try:
        r = requests.delete(
            endpoint,
            headers={
                "Authorization": f"Bearer {secret}",
                "X-Reconcile-Reason": reason,
            },
            timeout=timeout,
        )
    except requests.RequestException as e:
        logger.warning("DELETE %s failed (network): %s", slug, e)
        return False

    if r.status_code == 200:
        logger.info("Removed %s from dashboard (%s)", slug, reason)
        return True
    if r.status_code == 404:
        logger.info("%s already absent on dashboard (404), treating as success", slug)
        return True
    logger.warning(
        "DELETE %s returned HTTP %d: %s", slug, r.status_code, r.text[:200]
    )
    return False


def main() -> int:
    dry_run = _is_dry_run()
    base_url = _dashboard_base_url()
    secret = os.environ.get("DASHBOARD_PUBLISH_SECRET", "")

    if not dry_run and not secret:
        logger.error(
            "RECONCILE_DRY_RUN=0 but DASHBOARD_PUBLISH_SECRET is unset; refusing to delete"
        )
        return 2

    logger.info(
        "Reconcile mode=%s base_url=%s",
        "DRY_RUN" if dry_run else "APPLY",
        base_url,
    )

    try:
        dashboard_sites = _fetch_dashboard_slugs(base_url)
    except requests.RequestException as e:
        logger.error("Failed to fetch %s/sites.json: %s", base_url, e)
        return 3

    dashboard_slugs = {str(s["slug"]) for s in dashboard_sites}
    logger.info("Dashboard currently has %d sites", len(dashboard_slugs))

    try:
        expected, inactive_slugs = _expected_slugs_from_wrike()
    except Exception as e:
        logger.error("Failed to load Wrike active set: %s", e)
        return 4

    logger.info(
        "Wrike Active set: %d slugs (plus %d known inactive)",
        len(expected),
        len(inactive_slugs),
    )

    orphans = sorted(dashboard_slugs - expected)
    if not orphans:
        logger.info("No orphan slugs on dashboard — nothing to reconcile")
        return 0

    logger.info("Found %d orphan slug(s) on dashboard:", len(orphans))
    for slug in orphans:
        if slug in inactive_slugs:
            logger.info("  - %s (Wrike status_id=%s)", slug, inactive_slugs[slug])
        else:
            logger.info("  - %s (no matching Wrike record)", slug)

    if dry_run:
        logger.info(
            "RECONCILE_DRY_RUN=1 — no DELETE calls issued. "
            "Re-run with RECONCILE_DRY_RUN=0 to apply."
        )
        return 0

    deleted, failed = 0, 0
    for slug in orphans:
        reason = (
            f"wrike-status:{inactive_slugs[slug]}"
            if slug in inactive_slugs
            else "wrike-record-missing"
        )
        if _delete_site(base_url, slug, secret, reason=reason):
            deleted += 1
        else:
            failed += 1

    logger.info(
        "Reconcile complete: %d deleted, %d failed (of %d orphans)",
        deleted,
        failed,
        len(orphans),
    )
    return 0 if failed == 0 else 5


if __name__ == "__main__":
    sys.exit(main())
