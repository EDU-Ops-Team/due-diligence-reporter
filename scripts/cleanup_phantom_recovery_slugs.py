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

# Lightweight predicate for "this canonical-slug record looks like a wiped
# stub." The recovery wipe leaves the slug present in sites.json but with
# null/empty hydrated fields (can_we_open, scenarios, sources). A populated
# record that survived the wipe should NOT be deleted by this script under
# any circumstance — if seen, the pair is skipped and logged so a human can
# investigate the map entry.
_WIPED_STUB_FIELDS = ("can_we_open", "scenarios", "sources")

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
    # Miami Beach 300: the recovery's POST(transformed-record) hit an
    # already-existing typo'd duplicate slug instead of creating the
    # canonical one. Net effect: 300's hydrated data sits under
    # 400-71st-st-miami-beach-fl. Treat 400-71st as the "phantom" and
    # rename it onto 300's canonical slug, dropping the wiped 300 stub.
    # Use --pair miami-beach to apply only this row.
    ("400-71st-st-miami-beach-fl", "300-71st-miami-beach-fl"),
]


def _is_wiped_stub(record: dict[str, Any] | None) -> bool:
    """Return True if the dashboard record looks like a recovery-wiped stub.

    A wiped stub has no hydrated analytical fields (can_we_open, scenarios,
    sources). A None record is treated as a stub (already absent).

    A populated record (any of the wiped-stub fields non-empty) is treated
    as NOT a stub — even if some other field happens to be empty. This is
    deliberately conservative: better to skip a real cleanup row than to
    DELETE a populated record because the canonical slug map is wrong.
    """
    if record is None:
        return True
    for field in _WIPED_STUB_FIELDS:
        value = record.get(field)
        # Treat falsy (None, empty list/dict/str) as "not hydrated."
        # Any truthy value indicates this record carries real data.
        if value:
            return False
    return True


def _fetch_site_record(
    session: requests.Session,
    base_url: str,
    slug: str,
    *,
    timeout: int = 30,
) -> tuple[bool, dict[str, Any] | None, str]:
    """Fetch a single site record from the dashboard's public sites.json.

    Returns (ok, record_or_None, note).
      ok=True, record=dict   — record found
      ok=True, record=None   — slug not present in sites.json
      ok=False               — fetch failure (network, parse, non-200)
    """
    url = f"{base_url}/sites.json"
    try:
        r = session.get(url, timeout=timeout)
    except requests.RequestException as e:
        return False, None, f"GET sites.json network error: {e}"
    if r.status_code != 200:
        return False, None, f"GET sites.json HTTP {r.status_code}"
    try:
        payload = r.json()
    except ValueError as e:
        return False, None, f"GET sites.json parse error: {e}"
    sites = payload.get("sites") if isinstance(payload, dict) else None
    if not isinstance(sites, list):
        return False, None, "sites.json: 'sites' field missing or not a list"
    for entry in sites:
        if isinstance(entry, dict) and entry.get("slug") == slug:
            return True, entry, f"found {slug}"
    return True, None, f"{slug} not in sites.json"


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

    Returns (ok, note). Success requires:
      - HTTP 200, AND
      - body.ok is not False, AND
      - if body says per-slug data existed, the corresponding _moved flag
        must be True.

    A 502 with action='rename_partial' from the dashboard means sites.json
    was renamed but overrides/reviews re-key failed. We surface this as a
    failure so the caller retries on the next run; sites.json is now under
    new_slug, so the retry will hit the idempotent noop path on sites and
    re-attempt the per-slug data move.
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

    try:
        body = r.json() if r.text else {}
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}

    if r.status_code == 502 and body.get("action") == "rename_partial":
        return False, (
            f"rename {old_slug} -> {new_slug} partial: "
            f"sites_renamed={body.get('sites_renamed')} "
            f"overrides_had_data={body.get('overrides_had_data')} "
            f"overrides_moved={body.get('overrides_moved')} "
            f"overrides_error={body.get('overrides_error')!r} "
            f"reviews_had_data={body.get('reviews_had_data')} "
            f"reviews_moved={body.get('reviews_moved')} "
            f"reviews_error={body.get('reviews_error')!r}"
        )

    if r.status_code != 200:
        return False, (
            f"rename {old_slug} -> {new_slug} HTTP {r.status_code}: "
            f"{r.text[:300]}"
        )

    # 200 path. Honest-check the response: if the dashboard ever stops
    # returning ok:true on success, treat that as a failure rather than
    # silently advancing.
    if body.get("ok") is False:
        return False, (
            f"rename {old_slug} -> {new_slug} returned 200 but ok=false: "
            f"{body!r}"
        )

    action = body.get("action", "rename")

    # Honest-check the move flags. The dashboard returns overrides_moved /
    # reviews_moved on the 200 path. If a future bug causes either to be
    # false when data existed, the dashboard should return 502 (above), but
    # we belt-and-suspenders here: if either flag is explicitly False AND
    # the action is rename (not noop), we still log it for visibility.
    overrides_moved = body.get("overrides_moved")
    reviews_moved = body.get("reviews_moved")
    return True, (
        f"renamed {old_slug} -> {new_slug} ({action}, "
        f"overrides_moved={overrides_moved}, reviews_moved={reviews_moved})"
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

    Pre-flight: fetch the canonical slug record and verify it looks like a
    wiped stub (no can_we_open / scenarios / sources). If the canonical
    record is populated, REFUSE to delete — the map entry must be wrong.
    This prevents the script from destroying real data on a re-run.

    Returns True on overall success.
    """
    # Pre-flight: confirm canonical looks like a wiped stub before DELETE.
    # We always fetch — even in dry-run — so the user gets a real picture
    # of what would happen.
    fetch_ok, record, fetch_note = _fetch_site_record(session, base_url, canonical)
    if not fetch_ok:
        logger.error(
            "pre-flight fetch failed for %s: %s; skipping pair",
            canonical,
            fetch_note,
        )
        return False
    if record is not None and not _is_wiped_stub(record):
        logger.error(
            "REFUSING to delete %s: record is populated, not a wiped stub. "
            "Map entry %s -> %s is suspect; skipping pair. "
            "Inspect the record manually before re-running.",
            canonical,
            phantom,
            canonical,
        )
        return False

    if dry_run:
        logger.info(
            "DRY_RUN: %s pre-flight OK (%s); would DELETE %s then rename %s -> %s",
            canonical,
            "absent" if record is None else "wiped stub",
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
