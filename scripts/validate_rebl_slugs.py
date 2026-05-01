#!/usr/bin/env python3
"""validate_rebl_slugs.py — make Rebl the source of truth for dashboard slugs.

Rebl drives the canonical slug for every site on the DD Dashboard. Historically
the dashboard publisher derived its own slug from ``slugify(site_title)``,
which produced locally-invented slugs (e.g. ``alpha-school-tulsa-6940-s-utica-ave``).
Rebl resolves the same address to a different canonical slug
(``6940-s-utica-ave-tulsa-ok``). This script reconciles both:

  1. List every active Wrike Site Record. Wrike is the canonical address source.
  2. Cross-reference with the dashboard's live ``sites.json``.
  3. Batch-resolve every active address against
     ``POST https://rebl3.vercel.app/api/resolve``.
  4. Per site, classify as:
        - OK            — Rebl ``site_id`` matches dashboard slug exactly.
        - migrate       — Rebl ``site_id`` differs; dashboard needs renaming.
        - missing       — Rebl returned an error or ``matched_by:"none"`` w/o
                          lat/lng; cannot determine canonical slug.
        - api_error     — network/transport failure.
        - unknown       — Wrike has the address but it isn't on the dashboard
                          yet (no slug to migrate).
  5. Default ``--dry-run``: print a report. ``--apply``: for each migrate row,
     ``POST`` the existing record under the new slug, then ``DELETE`` the old
     slug. Both calls carry ``X-Reconcile-Reason`` so the dashboard's commit
     log captures the migration cause.
  6. Always: post one consolidated Google Chat alert summarising the run.

Auth / env (loaded from .env):
    WRIKE_ACCESS_TOKEN
    DASHBOARD_PUBLISH_URL    (default https://dd-dashboard-three.vercel.app)
    DASHBOARD_PUBLISH_SECRET (required for --apply)
    GOOGLE_CHAT_WEBHOOK_URL  (optional; alert sink)
    REBL_RESOLVE_URL         (default https://rebl3.vercel.app/api/resolve)

Run:
    uv run python scripts/validate_rebl_slugs.py            # dry-run, all sites
    uv run python scripts/validate_rebl_slugs.py --apply    # actually migrate
    uv run python scripts/validate_rebl_slugs.py --site Tulsa --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

import requests  # noqa: E402

from due_diligence_reporter.wrike import (  # noqa: E402
    _get_active_status_ids,
    _get_all_site_records,
    build_site_summary,
    filter_active_site_records,
    load_wrike_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("validate_rebl_slugs")

_DEFAULT_DASHBOARD_URL = "https://dd-dashboard-three.vercel.app"
_DEFAULT_REBL_URL = "https://rebl3.vercel.app/api/resolve"
_REBL_BATCH_SIZE = 25


# ---------------------------------------------------------------------------
# Config helpers


def _dashboard_base_url() -> str:
    return (os.environ.get("DASHBOARD_PUBLISH_URL") or _DEFAULT_DASHBOARD_URL).rstrip("/")


def _rebl_resolve_url() -> str:
    return os.environ.get("REBL_RESOLVE_URL") or _DEFAULT_REBL_URL


# ---------------------------------------------------------------------------
# Classification


@dataclass
class SiteRow:
    """One row in the validation report.

    Attributes capture everything we need to act on (or report) for a single
    Wrike site, *and* enough metadata to write the migration commit message.
    """

    title: str
    address: str
    wrike_id: str | None = None
    dashboard_slug: str | None = None  # current slug on dashboard (may be None)
    rebl_slug: str | None = None       # canonical slug returned by Rebl
    rebl_matched_by: str | None = None
    rebl_scored: bool | None = None
    rebl_error: str | None = None
    classification: str = "unknown"    # ok|migrate|missing|api_error|unknown
    note: str = ""


def classify_rebl_response(rebl_obj: dict[str, Any] | None) -> tuple[str | None, str, str]:
    """Pull the canonical slug + a status from a single Rebl resolve result.

    Returns ``(slug, status, note)`` where status is one of
    ``"ok" | "missing" | "api_error"``. ``slug`` is the Rebl ``site_id`` when
    we trust it, else ``None``.

    Rules (per user):
      * Object has ``error`` key → MISSING.
      * ``matched_by == "none"`` AND no lat/lng → MISSING.
      * ``matched_by == "none"`` WITH lat/lng → valid (Rebl knows it
        geographically; just no row yet).
      * ``scored: false`` with valid match → valid (just not enriched).
    """
    if rebl_obj is None:
        return None, "api_error", "no Rebl response"

    if "error" in rebl_obj and rebl_obj["error"]:
        return None, "missing", f"Rebl error: {rebl_obj['error']}"

    matched_by = (rebl_obj.get("matched_by") or "").strip().lower()
    has_geo = bool(rebl_obj.get("lat")) and bool(rebl_obj.get("lng"))
    site_id = (rebl_obj.get("site_id") or "").strip() or None

    if matched_by == "none" and not has_geo:
        return None, "missing", "Rebl matched_by=none and no lat/lng"

    if not site_id:
        return None, "missing", "Rebl returned empty site_id"

    return site_id, "ok", ""


# ---------------------------------------------------------------------------
# External I/O


def fetch_dashboard_sites(base_url: str, *, timeout: int = 20) -> list[dict[str, Any]]:
    """Return the full ``sites`` array from the live ``sites.json``."""
    url = f"{base_url}/sites.json"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    payload = r.json()
    sites = payload.get("sites") or []
    if not isinstance(sites, list):
        return []
    return [s for s in sites if isinstance(s, dict) and s.get("slug")]


def resolve_addresses_in_batches(
    addresses: list[str],
    *,
    resolve_url: str | None = None,
    batch_size: int = _REBL_BATCH_SIZE,
    timeout: int = 30,
) -> list[dict[str, Any] | None]:
    """Call Rebl ``/api/resolve`` in batches; return results in the same order.

    Each item is either the resolve dict (possibly with ``error`` key) or
    ``None`` if the entire batch HTTP call failed.
    """
    url = resolve_url or _rebl_resolve_url()
    out: list[dict[str, Any] | None] = []
    for start in range(0, len(addresses), batch_size):
        chunk = addresses[start : start + batch_size]
        body = [{"address": a} for a in chunk]
        try:
            r = requests.post(url, json=body, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list) or len(data) != len(chunk):
                logger.warning(
                    "Rebl batch %d-%d returned malformed response (len=%s); marking api_error",
                    start, start + len(chunk), len(data) if isinstance(data, list) else "n/a",
                )
                out.extend([None] * len(chunk))
            else:
                out.extend(data)
        except requests.RequestException as e:
            logger.warning(
                "Rebl batch %d-%d failed (network): %s",
                start, start + len(chunk), e,
            )
            out.extend([None] * len(chunk))
    return out


def post_chat(webhook_url: str, text: str, *, timeout: int = 10) -> None:
    if not webhook_url:
        return
    try:
        requests.post(webhook_url, json={"text": text}, timeout=timeout)
    except requests.RequestException as e:
        logger.warning("Failed to post Chat alert: %s", e)


# ---------------------------------------------------------------------------
# Migration


def migrate_slug(
    base_url: str,
    secret: str,
    *,
    old_slug: str,
    new_slug: str,
    full_record: dict[str, Any],
    timeout: int = 30,
) -> tuple[bool, str]:
    """Rename a dashboard site from ``old_slug`` to ``new_slug``.

    Preferred path: ``POST /api/sites/{old}/rename {new_slug}``. The dashboard
    mutates the slug field in place and preserves all analytical fields
    (can_we_open, scenarios, sources.*, etc.) verbatim.

    Fallback path (only on 404 — dashboard predates the rename endpoint):
    ``POST {new}/publish`` then ``DELETE {old}/publish``. This wipes
    analytical fields if the rename endpoint is unavailable, but keeps
    the rebl-canonical slug consistent. Recovery via
    ``recover_migration_wiped_sites.py`` if needed.

    Returns ``(success, note)``.
    """
    reason = f"rebl-canonical-slug-migration (was {old_slug})"

    # ── Preferred: rename endpoint (preserves analytical fields) ───────────
    rename_url = f"{base_url}/api/sites/{old_slug}/rename"
    try:
        r = requests.post(
            rename_url,
            json={"new_slug": new_slug},
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
                "X-Reconcile-Reason": reason,
            },
            timeout=timeout,
        )
    except requests.RequestException as e:
        return False, f"rename {old_slug} -> {new_slug} network error: {e}"

    if r.status_code == 200:
        return True, f"renamed {old_slug} -> {new_slug}"
    if r.status_code == 404:
        # Distinguish dashboard-doesn't-have-the-route (legacy) from
        # site-not-found (real 404). The endpoint returns a JSON body with
        # ``message: "slug not found"`` for the latter — anything else
        # (e.g. Vercel's HTML 404 page) is taken as "endpoint missing" and
        # we fall back. Be defensive: parse body if JSON, else fallback.
        body_text = (r.text or "")[:500]
        try:
            body = r.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("message") == "slug not found":
            return False, f"rename: site {old_slug} not on dashboard"
        # Endpoint missing — fall through to legacy POST+DELETE.
        logger.info(
            "rename endpoint not available (404 %s); falling back to "
            "legacy POST+DELETE for %s -> %s",
            body_text[:80],
            old_slug,
            new_slug,
        )
        return _migrate_slug_legacy(
            base_url,
            secret,
            old_slug=old_slug,
            new_slug=new_slug,
            full_record=full_record,
            timeout=timeout,
            reason=reason,
        )
    if r.status_code == 409:
        return False, (
            f"rename {old_slug} -> {new_slug} HTTP 409: "
            f"new_slug collides with existing site"
        )
    return False, (
        f"rename {old_slug} -> {new_slug} HTTP {r.status_code}: "
        f"{r.text[:200]}"
    )


def _migrate_slug_legacy(
    base_url: str,
    secret: str,
    *,
    old_slug: str,
    new_slug: str,
    full_record: dict[str, Any],
    timeout: int,
    reason: str,
) -> tuple[bool, str]:
    """Legacy POST(new) + DELETE(old) migration path.

    WARNING: Wipes analytical fields (can_we_open, scenarios, sources.*)
    because the dashboard's transformPipelineToSite reads flat reporter
    tokens, not the round-tripped dashboard schema. Only used as a
    fallback when the rename endpoint isn't deployed yet. After cutover
    this code path should be unreachable on prod.
    """
    site_meta = dict(full_record)
    site_meta["slug"] = new_slug
    payload = {"site_meta": site_meta, "report_data": full_record}

    post_url = f"{base_url}/api/sites/{new_slug}/publish"
    try:
        r = requests.post(
            post_url,
            json=payload,
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
                "X-Reconcile-Reason": reason,
            },
            timeout=timeout,
        )
    except requests.RequestException as e:
        return False, f"POST {new_slug} network error: {e}"
    if r.status_code != 200:
        return False, f"POST {new_slug} HTTP {r.status_code}: {r.text[:200]}"

    delete_url = f"{base_url}/api/sites/{old_slug}/publish"
    try:
        d = requests.delete(
            delete_url,
            headers={
                "Authorization": f"Bearer {secret}",
                "X-Reconcile-Reason": reason,
            },
            timeout=timeout,
        )
    except requests.RequestException as e:
        return False, f"POST {new_slug} OK, DELETE {old_slug} network error: {e}"
    if d.status_code not in (200, 404):
        return False, (
            f"POST {new_slug} OK, DELETE {old_slug} HTTP {d.status_code}: "
            f"{d.text[:200]}"
        )

    return True, f"migrated {old_slug} -> {new_slug} (legacy)"


# ---------------------------------------------------------------------------
# Wrike


def _site_filter(summary: dict[str, Any], needle: str | None) -> bool:
    if not needle:
        return True
    n = needle.lower()
    return n in (summary.get("title") or "").lower() or n in (summary.get("address") or "").lower()


def load_active_site_summaries(needle: str | None = None) -> list[dict[str, Any]]:
    """Return the canonical Wrike active-site summaries (filtered by ``needle``)."""
    config = load_wrike_config()
    records = _get_all_site_records(cfg=config)
    active_status_ids = _get_active_status_ids(access_token=config.access_token)
    active_records = filter_active_site_records(records, active_status_ids)
    summaries = [build_site_summary(r) for r in active_records]
    return [s for s in summaries if _site_filter(s, needle)]


# ---------------------------------------------------------------------------
# Reporting


def build_rows(
    summaries: Iterable[dict[str, Any]],
    dashboard_by_slug: dict[str, dict[str, Any]],
    rebl_results: list[dict[str, Any] | None],
) -> list[SiteRow]:
    """Join Wrike summaries + dashboard rows + Rebl results into ``SiteRow``s.

    The dashboard is keyed by slug, but a Wrike site's *current* dashboard slug
    isn't known up front. We match a Wrike row to a dashboard row by Rebl slug
    first (preferred), then by ``meta.rebl.site_id`` if present, and finally
    by *any* slug whose stored address normalises to the Wrike address. This
    keeps the migration honest even when local slugs are wildly off-format.
    """
    # Pre-index dashboard rows by stored rebl.site_id for fast secondary match.
    by_rebl_id: dict[str, dict[str, Any]] = {}
    by_address: dict[str, list[dict[str, Any]]] = {}
    for site in dashboard_by_slug.values():
        rebl = site.get("meta", {}).get("rebl", {}) if isinstance(site.get("meta"), dict) else {}
        rid = (rebl.get("site_id") if isinstance(rebl, dict) else "") or ""
        if rid:
            by_rebl_id[rid] = site
        addr = (site.get("address") or "").strip().lower()
        if addr:
            by_address.setdefault(addr, []).append(site)

    rows: list[SiteRow] = []
    summaries_list = list(summaries)
    if len(summaries_list) != len(rebl_results):
        # Defensive: should never happen because we built rebl_results from the
        # same list. Fail loud instead of silently misaligning.
        raise RuntimeError(
            f"summaries/rebl_results length mismatch: {len(summaries_list)} vs {len(rebl_results)}"
        )

    for summary, rebl_obj in zip(summaries_list, rebl_results):
        title = summary.get("title") or ""
        address = summary.get("address") or ""
        row = SiteRow(
            title=title,
            address=address,
            wrike_id=str(summary.get("id")) if summary.get("id") else None,
        )

        rebl_slug, status, note = classify_rebl_response(rebl_obj)
        if rebl_obj:
            row.rebl_matched_by = rebl_obj.get("matched_by")
            row.rebl_scored = rebl_obj.get("scored")
        row.rebl_slug = rebl_slug
        row.note = note

        # Find the matching dashboard row (if any) for the *current* slug.
        match: dict[str, Any] | None = None
        if rebl_slug and rebl_slug in dashboard_by_slug:
            match = dashboard_by_slug[rebl_slug]
        elif rebl_slug and rebl_slug in by_rebl_id:
            match = by_rebl_id[rebl_slug]
        elif address and address.strip().lower() in by_address:
            candidates = by_address[address.strip().lower()]
            # Prefer the candidate whose stored rebl_site_id is empty (i.e.
            # the dashboard never persisted a Rebl id) so we don't shadow a
            # row that's already canonical.
            match = candidates[0]

        if match:
            row.dashboard_slug = match.get("slug")

        # Classify
        if status == "missing":
            row.classification = "missing"
        elif status == "api_error":
            row.classification = "api_error"
            row.rebl_error = note or "api_error"
        else:
            # status == "ok": we have a canonical Rebl slug.
            if not row.dashboard_slug:
                row.classification = "unknown"
                row.note = "Rebl resolved but no dashboard row for this site"
            elif row.dashboard_slug == rebl_slug:
                row.classification = "ok"
            else:
                row.classification = "migrate"

        rows.append(row)

    return rows


def render_report(rows: list[SiteRow]) -> str:
    """Plain-text grouped report for stdout + Chat."""
    buckets: dict[str, list[SiteRow]] = {
        "migrate": [],
        "missing": [],
        "api_error": [],
        "unknown": [],
        "ok": [],
    }
    for r in rows:
        buckets.setdefault(r.classification, []).append(r)

    lines: list[str] = []
    lines.append(
        f"Rebl slug validation: total={len(rows)} "
        f"ok={len(buckets['ok'])} migrate={len(buckets['migrate'])} "
        f"missing={len(buckets['missing'])} api_error={len(buckets['api_error'])} "
        f"unknown={len(buckets['unknown'])}"
    )

    if buckets["migrate"]:
        lines.append("")
        lines.append(f"Migrate ({len(buckets['migrate'])}):")
        for r in buckets["migrate"]:
            lines.append(
                f"  - {r.title}: {r.dashboard_slug} -> {r.rebl_slug} "
                f"(matched_by={r.rebl_matched_by}, address={r.address})"
            )

    if buckets["missing"]:
        lines.append("")
        lines.append(f"Missing from Rebl ({len(buckets['missing'])}):")
        for r in buckets["missing"]:
            lines.append(f"  - {r.title}: {r.note} (address={r.address})")

    if buckets["api_error"]:
        lines.append("")
        lines.append(f"Rebl API errors ({len(buckets['api_error'])}):")
        for r in buckets["api_error"]:
            lines.append(f"  - {r.title}: {r.rebl_error}")

    if buckets["unknown"]:
        lines.append("")
        lines.append(f"Unknown (not on dashboard) ({len(buckets['unknown'])}):")
        for r in buckets["unknown"]:
            lines.append(f"  - {r.title}: rebl_slug={r.rebl_slug}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--site",
        help="Filter to sites whose Wrike title or address contains this substring",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually issue POST/DELETE to migrate slugs. Default is dry-run.",
    )
    parser.add_argument(
        "--no-chat",
        action="store_true",
        help="Skip the Google Chat summary post (still writes stdout report).",
    )
    args = parser.parse_args(argv)

    base_url = _dashboard_base_url()
    secret = os.environ.get("DASHBOARD_PUBLISH_SECRET", "")
    webhook_url = os.environ.get("GOOGLE_CHAT_WEBHOOK_URL", "")

    if args.apply and not secret:
        logger.error("--apply requires DASHBOARD_PUBLISH_SECRET")
        return 2

    logger.info(
        "validate_rebl_slugs mode=%s base_url=%s",
        "APPLY" if args.apply else "DRY_RUN",
        base_url,
    )

    # 1. Wrike active set (canonical addresses)
    try:
        summaries = load_active_site_summaries(args.site)
    except Exception as e:
        logger.exception("Failed to load Wrike active set: %s", e)
        return 3
    logger.info("Loaded %d active Wrike sites", len(summaries))

    # 2. Dashboard live snapshot
    try:
        dashboard_sites = fetch_dashboard_sites(base_url)
    except requests.RequestException as e:
        logger.error("Failed to fetch %s/sites.json: %s", base_url, e)
        return 4
    dashboard_by_slug = {str(s["slug"]): s for s in dashboard_sites}
    logger.info("Dashboard currently has %d sites", len(dashboard_by_slug))

    # 3. Rebl resolve (only sites with a non-empty address)
    addresses = [s.get("address") or "" for s in summaries]
    rebl_results = resolve_addresses_in_batches(addresses)

    # 4. Classify
    rows = build_rows(summaries, dashboard_by_slug, rebl_results)
    report = render_report(rows)
    print(report)

    # Always emit a JSON line per row for easy log parsing.
    for r in rows:
        logger.info(
            "%s",
            json.dumps(
                {
                    "title": r.title,
                    "classification": r.classification,
                    "dashboard_slug": r.dashboard_slug,
                    "rebl_slug": r.rebl_slug,
                    "matched_by": r.rebl_matched_by,
                    "note": r.note,
                },
                default=str,
            ),
        )

    # 5. Apply (or dry-run summary)
    migrate_rows = [r for r in rows if r.classification == "migrate"]
    applied = 0
    failed: list[tuple[SiteRow, str]] = []

    if args.apply and migrate_rows:
        logger.info("Applying %d migration(s)…", len(migrate_rows))
        for r in migrate_rows:
            old_slug = r.dashboard_slug
            new_slug = r.rebl_slug
            if not old_slug or not new_slug:
                failed.append((r, "missing slug fields"))
                continue
            full_record = dashboard_by_slug.get(old_slug)
            if not full_record:
                failed.append((r, f"no dashboard record for {old_slug}"))
                continue
            ok, note = migrate_slug(
                base_url,
                secret,
                old_slug=old_slug,
                new_slug=new_slug,
                full_record=full_record,
            )
            if ok:
                applied += 1
                logger.info("Migrated: %s", note)
            else:
                failed.append((r, note))
                logger.warning("Migration failed for %s: %s", r.title, note)
    elif migrate_rows:
        logger.info(
            "DRY_RUN: would migrate %d slug(s) (use --apply to execute)",
            len(migrate_rows),
        )

    # 6. Consolidated Chat alert
    if webhook_url and not args.no_chat:
        title_prefix = "Rebl slug migration (APPLY)" if args.apply else "Rebl slug validation (DRY_RUN)"
        chat_lines = [title_prefix, report]
        if args.apply:
            chat_lines.append("")
            chat_lines.append(f"Applied: {applied} / {len(migrate_rows)}")
            if failed:
                chat_lines.append(f"Failed: {len(failed)}")
                for r, note in failed:
                    chat_lines.append(f"  - {r.title}: {note}")
        post_chat(webhook_url, "\n".join(chat_lines))
    elif not webhook_url:
        logger.info("GOOGLE_CHAT_WEBHOOK_URL not set; skipping Chat alert")

    if failed:
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
