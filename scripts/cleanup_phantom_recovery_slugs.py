"""cleanup_phantom_recovery_slugs.py — one-shot cleanup for the 2026-05-01
recover_migration_wiped_sites.py run.

Why
---
The first apply=true run of recover_migration_wiped_sites.py (workflow
run 25226179557) called ``backfill_one()`` for each of the 26 wiped
sites. ``backfill_one`` re-derives the dashboard slug from the trace's
``meta.rebl_site_id`` token (or ``slugify(title)`` if absent), but the
recovery script never threaded the canonical slug through. Result on
the dashboard:

    14 sites: hydrated under their canonical slug (Update commits) ✓
     1 site: hydrated under the wrong existing slug (Miami Beach 300
             trace landed on 400-71st-st-miami-beach-fl) ✗
    11 sites: published as new records under reporter-generated
             legacy slugs (Add alpha-school-* commits). The wiped
             canonical-slug stubs were left untouched. ✗

This script fixes the 11-phantom case in two steps per pair:
  1. DELETE the wiped canonical-slug stub (it's empty).
  2. Rename the phantom legacy slug onto the canonical slug.

Outcome: 11 canonical slugs become populated with the hydrated data
the phantoms hold, 11 phantom legacy-slug records disappear.

Miami Beach 300 is handled separately (single re-run of the patched
recovery script). This cleanup does NOT touch 400-71st-st-miami-beach-fl.

Auth
----
``DASHBOARD_PUBLISH_SECRET`` env var (same bearer as /publish + /rename).

Usage
-----
    python -m scripts.cleanup_phantom_recovery_slugs --dry-run
    python -m scripts.cleanup_phantom_recovery_slugs --apply

Idempotent: re-running after partial success skips already-completed
pairs (rename returns 200 noop if the canonical slug is the only one
present; if neither exists, logs WARNING and skips).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

import requests

logger = logging.getLogger(__name__)

DASHBOARD_BASE_URL = "https://dd-dashboard-three.vercel.app"

# Phantom legacy-slug -> canonical Rebl slug. Source: 2026-05-01 recovery
# workflow run 25226179557 vs PR #60's expected-canonical-slugs list.
PHANTOM_TO_CANONICAL: list[tuple[str, str]] = [
    (
        "alpha-school-los-angeles-1726-whitley-ave",
        "1726-whitley-ave-los-angeles-ca",
    ),
    ("alpha-early-center-new-york-156", "156-william-st-new-york-ny"),
    ("alpha-school-bethesda-7514", "7514-wisconsin-ave-bethesda-md"),
    ("alpha-school-burlingame-205", "205-park-rd-burlingame-ca"),
    ("alpha-school-chicago-350-gems", "350-e-s-water-st-chicago-il"),
    ("alpha-school-park-city-3770", "3770-ut-224-park-city-ut"),
    (
        "alpha-school-sunny-isles-beach-17070-collins-ave",
        "17070-collins-ave-sunny-isles-beach-fl",
    ),
    ("alpha-early-center-new-york-775", "775-columbus-ave-new-york-ny"),
    ("alpha-school-lombard-995", "995-oak-creek-dr-lombard-il"),
    ("alpha-school-portland-838", "838-sw-1st-ave-portland-or"),
    ("alpha-school-winter-park-460", "460-e-new-england-ave-winter-park-fl"),
]


def _delete_stub(
    session: requests.Session,
    base_url: str,
    secret: str,
    slug: str,
    *,
    timeout: int = 30,
) -> tuple[bool, str]:
    """DELETE the wiped canonical-slug stub.

    Returns (ok, note). 200 = removed; 404 = already gone (idempotent).
    """
    url = f"{base_url}/api/sites/{slug}/publish"
    try:
        r = session.delete(
            url,
            headers={
                "Authorization": f"Bearer {secret}",
                "X-Reconcile-Reason": "cleanup-phantom-recovery-slugs (drop wiped stub)",
            },
            timeout=timeout,
        )
    except requests.RequestException as e:
        return False, f"DELETE {slug} network error: {e}"
    if r.status_code == 200:
        return True, f"deleted stub {slug}"
    if r.status_code == 404:
        return True, f"stub {slug} already absent"
    return False, f"DELETE {slug} HTTP {r.status_code}: {r.text[:200]}"


def _rename(
    session: requests.Session,
    base_url: str,
    secret: str,
    *,
    old_slug: str,
    new_slug: str,
    timeout: int = 30,
) -> tuple[bool, str]:
    """POST /api/sites/{old}/rename {new_slug}.

    Returns (ok, note). Treats 200 (rename or noop) as success.
    """
    url = f"{base_url}/api/sites/{old_slug}/rename"
    try:
        r = session.post(
            url,
            json={"new_slug": new_slug},
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
                "X-Reconcile-Reason": "cleanup-phantom-recovery-slugs",
            },
            timeout=timeout,
        )
    except requests.RequestException as e:
        return False, f"rename {old_slug} -> {new_slug} network error: {e}"
    if r.status_code == 200:
        try:
            body = r.json()
        except ValueError:
            body = {}
        action = body.get("action", "rename") if isinstance(body, dict) else "rename"
        return True, f"renamed {old_slug} -> {new_slug} ({action})"
    return False, (
        f"rename {old_slug} -> {new_slug} HTTP {r.status_code}: {r.text[:300]}"
    )


def _process_pair(
    session: requests.Session,
    base_url: str,
    secret: str,
    *,
    phantom: str,
    canonical: str,
    dry_run: bool,
) -> bool:
    """Drop canonical stub then rename phantom onto canonical.

    Returns True on overall success.
    """
    if dry_run:
        logger.info(
            "DRY_RUN: would DELETE %s (wiped stub) then rename %s -> %s",
            canonical,
            phantom,
            canonical,
        )
        return True

    ok, note = _delete_stub(session, base_url, secret, canonical)
    if ok:
        logger.info(note)
    else:
        logger.error(note)
        return False

    ok, note = _rename(
        session,
        base_url,
        secret,
        old_slug=phantom,
        new_slug=canonical,
    )
    if ok:
        logger.info(note)
        return True
    logger.error(note)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument(
        "--apply",
        action="store_true",
        help="Actually issue DELETE + rename calls.",
    )
    grp.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen, don't write.",
    )
    parser.add_argument(
        "--base-url",
        default=DASHBOARD_BASE_URL,
        help=f"Dashboard base URL (default: {DASHBOARD_BASE_URL})",
    )
    parser.add_argument(
        "--pair",
        action="append",
        default=None,
        help=(
            "Filter to a single phantom slug (substring match). "
            "May be repeated. Default: process all 11 pairs."
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    secret = os.environ.get("DASHBOARD_PUBLISH_SECRET", "")
    if not args.dry_run and not secret:
        logger.error("DASHBOARD_PUBLISH_SECRET not set; cannot --apply")
        return 2

    pairs = list(PHANTOM_TO_CANONICAL)
    if args.pair:
        needles = [n.lower() for n in args.pair]
        pairs = [
            (p, c) for (p, c) in pairs if any(n in p.lower() for n in needles)
        ]
        if not pairs:
            logger.error(
                "--pair %r matched none of the %d known phantoms",
                args.pair,
                len(PHANTOM_TO_CANONICAL),
            )
            return 2

    logger.info(
        "%s: %d phantom -> canonical pair(s) at %s",
        "APPLY" if args.apply else "DRY_RUN",
        len(pairs),
        args.base_url,
    )

    session = requests.Session()
    succeeded = 0
    failed: list[tuple[str, str]] = []
    for phantom, canonical in pairs:
        ok = _process_pair(
            session,
            args.base_url,
            secret,
            phantom=phantom,
            canonical=canonical,
            dry_run=args.dry_run,
        )
        if ok:
            succeeded += 1
        else:
            failed.append((phantom, canonical))

    logger.info(
        "Cleanup %s: %d/%d succeeded; %d failed.",
        "DRY_RUN" if args.dry_run else "APPLY",
        succeeded,
        len(pairs),
        len(failed),
    )
    if failed:
        for phantom, canonical in failed:
            logger.warning("  FAILED: %s -> %s", phantom, canonical)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
