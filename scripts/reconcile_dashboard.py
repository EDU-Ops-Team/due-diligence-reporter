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


def _expected_slugs_from_wrike() -> tuple[set[str], dict[str, str], list[tuple[str, bool]]]:
    """Build the set of slugs that *should* exist on the dashboard.

    Returns ``(slug_set, slug_to_status_label, all_titles)``.

    The status label is used purely for log/audit messages on inactive
    records that we are NOT pruning (e.g. they were never published in the
    first place). ``all_titles`` is a list of every Wrike record
    ``(title, is_active)`` tuple, used downstream to suggest near-match
    candidates when an orphan slug doesn't match any Wrike record exactly.
    """
    cfg = load_wrike_config()
    records = _get_all_site_records(cfg=cfg)
    active_ids = _get_active_status_ids(access_token=cfg.access_token)

    expected: set[str] = set()
    inactive_slugs: dict[str, str] = {}
    all_titles: list[tuple[str, bool]] = []
    for rec in records:
        title = (rec.get("title") or "").strip()
        if not title:
            continue
        slug = slugify(title)
        if not slug:
            continue
        active = is_record_active(rec, active_ids)
        all_titles.append((title, active))
        if active:
            expected.add(slug)
        else:
            # Track status id for the audit log, raw value is informative
            # enough even if we don't resolve it to a human name here.
            inactive_slugs[slug] = str(rec.get("customStatusId") or "inactive")
    return expected, inactive_slugs, all_titles


_TOKEN_STOPWORDS = {
    # Brand / role words always present and never disambiguating
    "alpha", "school", "the", "and", "of",
    # Street type abbreviations
    "st", "ave", "rd", "dr", "ln", "blvd", "hwy", "pkwy", "ct",
    # USPS two-letter state codes — dashboard slugs sometimes append the
    # state, Wrike titles usually don't, and these are never the
    # disambiguating piece of an address.
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga",
    "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md",
    "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
    "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
    "dc",
}


def _slug_tokens(slug: str) -> list[str]:
    """Tokenize a slug for fuzzy matching: keep meaningful words/numbers.

    Drops alpha/school/etc. plus single-letter pieces, so tokens like
    'tulsa' / '6940' / 'minneapolis' / '1128' survive for near-match search.
    """
    return [
        t
        for t in slug.split("-")
        if t and len(t) > 1 and t not in _TOKEN_STOPWORDS
    ]


def _find_near_matches(
    slug: str, all_titles: list[tuple[str, bool]], *, limit: int = 3
) -> list[tuple[str, bool]]:
    """Return Wrike ``(title, is_active)`` entries that look like the same site.

    Matching strategy, in order:

    1. **Strict**: every meaningful slug token (after stripping brand words,
       street types, USPS state codes) appears in the candidate title. This
       handles the common case of slug-to-title drift like state suffixes
       (e.g. dashboard slug ``...-tulsa-ok`` vs Wrike title ``... Tulsa ...``).
    2. **Non-numeric fallback**: if no candidate matches strictly, retry
       requiring only the *non-numeric* meaningful tokens to be present. This
       catches the address-number rename case — e.g. dashboard slug
       ``alpha-school-lombard-835`` (the original Wrike title) vs current
       Wrike title ``Alpha School Lombard 995`` (renamed to a new street
       number). Without this fallback, a rename like 835 → 995 looks like a
       genuine orphan and the reconciler would prune real data.

    The fallback only fires when at least one non-numeric token survives, so
    purely numeric slugs (rare) still require the strict path.
    """
    tokens = _slug_tokens(slug)
    if not tokens:
        return []

    matches: list[tuple[str, bool]] = []
    for title, active in all_titles:
        lt = title.lower()
        if all(tok in lt for tok in tokens):
            matches.append((title, active))
            if len(matches) >= limit:
                return matches
    if matches:
        return matches

    non_numeric = [t for t in tokens if not t.isdigit()]
    if not non_numeric or non_numeric == tokens:
        # No fallback available (either all numeric, or fallback equals strict)
        return matches

    for title, active in all_titles:
        lt = title.lower()
        if all(tok in lt for tok in non_numeric):
            matches.append((title, active))
            if len(matches) >= limit:
                break
    return matches


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
        expected, inactive_slugs, all_titles = _expected_slugs_from_wrike()
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

    # Partition orphans into deletable vs rename-suspect.
    #
    # Rename-suspect: the dashboard slug has no exact Wrike match, but a Wrike
    # record that is currently *active* shares the same meaningful tokens. This
    # almost always means the Wrike record was retitled (e.g. 'Alpha School
    # Lombard 835' → 'Alpha School Lombard 995') and the dashboard row is real
    # site data on a stale slug, NOT a cancelled site. Deleting it would
    # clobber real data, so we surface it for human triage and skip.
    deletable: list[tuple[str, str]] = []  # (slug, reason)
    rename_suspects: list[tuple[str, list[tuple[str, bool]]]] = []

    logger.info("Found %d orphan slug(s) on dashboard:", len(orphans))
    for slug in orphans:
        near = _find_near_matches(slug, all_titles)
        active_near = [m for m in near if m[1]]

        if slug in inactive_slugs:
            reason = f"wrike-status:{inactive_slugs[slug]}"
            logger.info("  - %s [DELETABLE] (Wrike status_id=%s)", slug, inactive_slugs[slug])
            deletable.append((slug, reason))
        elif active_near:
            hints = "; ".join(
                f"{title!r} [ACTIVE]" for title, _ in active_near
            )
            logger.info(
                "  - %s [RENAME-SUSPECT] (active near matches: %s)",
                slug,
                hints,
            )
            rename_suspects.append((slug, active_near))
        elif near:
            # All near matches are inactive — still safer to skip; the slug
            # may map to a record that was renamed and then cancelled. Log it.
            hints = "; ".join(
                f"{title!r} [INACTIVE]" for title, _ in near
            )
            logger.info(
                "  - %s [DELETABLE] (no exact match; only inactive near matches: %s)",
                slug,
                hints,
            )
            deletable.append((slug, "wrike-near-match-inactive"))
        else:
            logger.info("  - %s [DELETABLE] (no matching Wrike record)", slug)
            deletable.append((slug, "wrike-record-missing"))

    if rename_suspects:
        logger.info(
            "Skipping %d rename-suspect orphan(s) — these have an active Wrike "
            "record under a different title and are NOT pruned. Migrate the "
            "dashboard data manually if needed.",
            len(rename_suspects),
        )

    if dry_run:
        logger.info(
            "RECONCILE_DRY_RUN=1 — no DELETE calls issued (would delete %d). "
            "Re-run with RECONCILE_DRY_RUN=0 to apply.",
            len(deletable),
        )
        return 0

    if not deletable:
        logger.info("Apply mode: nothing safe to delete. Exiting cleanly.")
        return 0

    deleted, failed = 0, 0
    for slug, reason in deletable:
        if _delete_site(base_url, slug, secret, reason=reason):
            deleted += 1
        else:
            failed += 1

    logger.info(
        "Reconcile complete: %d deleted, %d failed, %d skipped as rename-suspects (of %d orphans)",
        deleted,
        failed,
        len(rename_suspects),
        len(orphans),
    )
    return 0 if failed == 0 else 5


if __name__ == "__main__":
    sys.exit(main())
