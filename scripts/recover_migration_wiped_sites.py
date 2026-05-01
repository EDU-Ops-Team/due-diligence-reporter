#!/usr/bin/env python3
"""recover_migration_wiped_sites.py — rehydrate the 26 sites the rebl-canonical
slug migration self-wiped on 2026-05-01.

Background
----------
``scripts/validate_rebl_slugs.py --apply`` (PR #57) renamed dashboard slugs
to Rebl-canonical form. Its ``migrate_slug`` helper POSTed each site's
*transformed* dashboard record back to ``/api/sites/<new-slug>/publish``
under both ``site_meta`` and ``report_data``. The dashboard's transform
(see ``dd-dashboard/api/_lib/transform.ts``) reads only flat reporter
tokens (``exec.c_answer``, ``exec.fastest_open_capacity``,
``sources.sir_link``, …) — it has no fallback to nested dashboard fields.
Result: every analytical field on the 26 migrated sites was wiped.

Recovery
--------
Re-publish each affected site from its Drive trace JSON, exactly the same
way ``backfill_dashboard.py`` does. Targets only the broken set (live
sites where ``dd_status=="complete"`` and ``can_we_open`` is blank). Skips
the 4 stuck-empty stubs and any healthy site.

Run
---
    uv run python scripts/recover_migration_wiped_sites.py --dry-run
    uv run python scripts/recover_migration_wiped_sites.py --apply
    uv run python scripts/recover_migration_wiped_sites.py --apply --site austin

Env (from .env):
    DASHBOARD_PUBLISH_URL, DASHBOARD_PUBLISH_SECRET
    plus the usual pipeline env (Wrike, Google)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

import requests

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))
sys.path.insert(0, str(_project_root / "scripts"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

# Reuse backfill_dashboard's trace-discovery + reconstruction so we stay in
# lockstep with the proven publish path. Importing as a script-relative
# module keeps the recovery logic surgical (one helper) rather than
# duplicating the trace-walking machinery.
from backfill_dashboard import backfill_one  # noqa: E402
from due_diligence_reporter.config import get_settings  # noqa: E402
from due_diligence_reporter.google_client import GoogleClient  # noqa: E402
from due_diligence_reporter.wrike import (  # noqa: E402
    _get_active_status_ids,
    _get_all_site_records,
    extract_address_from_record,
    extract_google_folder_from_record,
    extract_p1_from_record,
    extract_school_type_from_record,
    filter_active_site_records,
    load_wrike_config,
)
from due_diligence_reporter.rebl import canonical_slugs_for_addresses  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("recover_migration_wiped_sites")

_DEFAULT_DASHBOARD_URL = "https://dd-dashboard-three.vercel.app"


def _dashboard_base_url() -> str:
    return (
        os.environ.get("DASHBOARD_PUBLISH_URL") or _DEFAULT_DASHBOARD_URL
    ).rstrip("/")


def _fetch_live_sites() -> list[dict[str, Any]]:
    """Pull the live ``sites.json`` snapshot from the dashboard."""
    url = f"{_dashboard_base_url()}/sites.json"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    payload = r.json()
    sites = payload.get("sites") or []
    if not isinstance(sites, list):
        return []
    return [s for s in sites if isinstance(s, dict) and s.get("slug")]


def _is_migration_wiped(site: dict[str, Any]) -> bool:
    """Migration-wiped signature: dd_status=complete + can_we_open blank.

    The rebl-slug migration's ``migrate_slug`` round-tripped a transformed
    record through the dashboard's flat-token transform, blanking every
    analytical field. ``dd_status`` and ``dd_recommendation`` survived
    because they live on ``site_meta``. ``can_we_open`` is the cleanest
    fingerprint of the wipe — it comes from ``report_data["exec.c_answer"]``
    and is non-empty on every healthy DD report.
    """
    if (site.get("dd_status") or "").strip().lower() != "complete":
        return False
    return not (site.get("can_we_open") or "").strip()


def _match_wrike_to_broken_sites(
    active_records: list[dict[str, Any]],
    broken_by_slug: dict[str, dict[str, Any]],
) -> list[tuple[dict[str, Any], str]]:
    """Pair every Wrike active record with its broken dashboard slug, if any.

    Match strategy:
      1. Batch-resolve every Wrike record's address through Rebl in a single
         POST. The dashboard slug after migration *is* the Rebl canonical id,
         so equal Rebl slugs are a hard match.
      2. Skip records whose address is empty or whose Rebl slug isn't in the
         broken set.

    Returns a list of (wrike_record, dashboard_slug) pairs to recover.

    Why batch: the prior serial implementation issued one POST per Wrike
    record. With ~80 active sites that's 80 sequential network calls, plus
    80 chances to trip Rebl's rate limiter. ``canonical_slugs_for_addresses``
    folds the entire list into one POST.
    """
    addr_by_rec: list[tuple[dict[str, Any], str]] = []
    for rec in active_records:
        addr = (extract_address_from_record(rec) or "").strip()
        if addr:
            addr_by_rec.append((rec, addr))

    slug_map = canonical_slugs_for_addresses([addr for _, addr in addr_by_rec])

    pairs: list[tuple[dict[str, Any], str]] = []
    for rec, addr in addr_by_rec:
        rebl_slug = slug_map.get(addr) or ""
        if not rebl_slug:
            continue
        if rebl_slug in broken_by_slug:
            pairs.append((rec, rebl_slug))
    return pairs


def _recover_one(
    gc: GoogleClient,
    rec: dict[str, Any],
    dashboard_slug: str,
    *,
    dry_run: bool,
) -> bool:
    """Recover a single site by republishing from its Drive trace.

    Returns True on success (or on a clean dry-run preview).
    """
    title = (rec.get("title") or "").strip()
    drive_url = extract_google_folder_from_record(rec) or ""
    address = extract_address_from_record(rec) or ""
    school_type = extract_school_type_from_record(rec)
    p1 = extract_p1_from_record(rec) or {}
    site_owner = p1.get("name")

    if not drive_url:
        logger.warning(
            "%s [%s]: no Drive folder on Wrike record; cannot recover",
            title,
            dashboard_slug,
        )
        return False

    if dry_run:
        # Mask PII (street address + site_owner name) from the dry-run log line.
        # GHA workflow logs are visible to every collaborator on the repo, and
        # the recover workflow runs across active sites whose owners may not
        # have consented to having their name and street address surfaced in
        # CI logs. The slug, school_type, and Drive URL are sufficient for an
        # operator to identify a row at a glance; if the operator needs the
        # full record, the Wrike record id is one click away.
        logger.info(
            "DRY_RUN: would recover %s [slug=%s | drive=%s | type=%s]",
            title,
            dashboard_slug,
            drive_url,
            school_type,
        )
        return True

    logger.info("Recovering %s [slug=%s] …", title, dashboard_slug)
    try:
        # Pass dashboard_slug as force_slug so the publisher does NOT re-derive
        # a fresh slug from the trace's rebl_site_id token (empty on legacy
        # traces) or slugify(title). Without this, recovery has historically
        # minted phantom legacy-slug records on the dashboard.
        return backfill_one(
            gc,
            title,
            drive_url,
            address,
            school_type,
            site_owner=site_owner,
            force_slug=dashboard_slug,
        )
    except Exception:
        # logger.exception attaches the traceback automatically via exc_info=True.
        # We intentionally don't %s-format the exception message here: backfill_one
        # error strings have historically embedded the raw address (e.g. "failed to
        # fetch trace for 123 Main St, Anytown XX"). Keeping the args slug-only and
        # letting the traceback carry the detail keeps the single-line summary
        # readable in GHA logs without re-leaking PII at the top level.
        logger.exception(
            "backfill_one raised for %s [slug=%s]",
            title,
            dashboard_slug,
        )
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Require an explicit choice to match cleanup_phantom_recovery_slugs.py's
    # behavior: a missing flag now exits 2 rather than silently dry-running.
    # In CI the workflow always passes one of --apply or --dry-run; locally,
    # this prevents a careless `python recover_migration_wiped_sites.py` from
    # being interpreted as "dry-run, but for real on the next paste."
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Actually issue POSTs to recover sites.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview recovery actions without issuing POSTs.",
    )
    parser.add_argument(
        "--site",
        help=(
            "Filter to dashboard slugs containing this substring "
            "(case-insensitive). Useful for one-off retries."
        ),
    )
    args = parser.parse_args(argv)

    apply = bool(args.apply)
    if not apply:
        logger.info("DRY_RUN mode. Pass --apply to actually issue POSTs.")

    if apply and not os.environ.get("DASHBOARD_PUBLISH_SECRET"):
        logger.error("--apply requires DASHBOARD_PUBLISH_SECRET in env")
        return 2

    # 1. Pull live sites + identify the migration-wiped set.
    try:
        sites = _fetch_live_sites()
    except requests.RequestException as e:
        logger.error("Failed to fetch live sites.json: %s", e)
        return 3
    broken = {s["slug"]: s for s in sites if _is_migration_wiped(s)}
    logger.info(
        "Live dashboard has %d sites; %d match the migration-wiped signature.",
        len(sites),
        len(broken),
    )
    if args.site:
        needle = args.site.lower()
        broken = {k: v for k, v in broken.items() if needle in k.lower()}
        logger.info("Filtered to %d slug(s) matching --site %r", len(broken), args.site)
    if not broken:
        logger.info("Nothing to recover.")
        return 0

    # 2. Match each broken slug to its Wrike record (by Rebl-resolved address).
    wrike_cfg = load_wrike_config()
    records = _get_all_site_records(cfg=wrike_cfg)
    active_status_ids = _get_active_status_ids(access_token=wrike_cfg.access_token)
    active = filter_active_site_records(records, active_status_ids)
    pairs = _match_wrike_to_broken_sites(active, broken)
    matched_slugs = {slug for _, slug in pairs}
    unmatched = sorted(set(broken.keys()) - matched_slugs)
    logger.info(
        "Matched %d/%d broken slug(s) to Wrike records via Rebl resolve.",
        len(pairs),
        len(broken),
    )
    if unmatched:
        logger.warning(
            "Could not match %d broken slug(s) to a Wrike record: %s",
            len(unmatched),
            ", ".join(unmatched),
        )

    # 3. Recover each matched pair.
    gc = None
    if apply:
        settings = get_settings()
        gc = GoogleClient.from_oauth_config(
            client_config_path=str(settings.get_client_config_path()),
            token_file_path=str(settings.get_token_file_path()),
            oauth_port=settings.oauth_port,
            scopes=settings.google_scopes,
        )

    succeeded = 0
    failed: list[tuple[str, str]] = []
    for rec, dashboard_slug in pairs:
        ok = _recover_one(gc, rec, dashboard_slug, dry_run=not apply)
        if ok:
            succeeded += 1
        else:
            failed.append((rec.get("title", "?"), dashboard_slug))

    logger.info(
        "Recovery %s: %d/%d succeeded; %d failed; %d unmatched.",
        "APPLY" if apply else "DRY_RUN",
        succeeded,
        len(pairs),
        len(failed),
        len(unmatched),
    )
    if failed:
        for title, slug in failed:
            logger.warning("  FAILED: %s [%s]", title, slug)
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
