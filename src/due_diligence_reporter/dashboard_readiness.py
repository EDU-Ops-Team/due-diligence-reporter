"""Flip Portfolio doc-readiness columns when documents land in Drive.

The DD Dashboard Portfolio table surfaces four "doc readiness" columns:

    cds_sir_status, building_inspection_status, block_plan_status, lidar_status

These are stored as user-style overrides in ``client/public/overrides.json``
on the dashboard repo. They do **not** come from ``sites.json`` and are not
touched by the regular ``/api/sites/:slug/publish`` write path. The dashboard
exposes a dedicated endpoint — ``POST /api/auto-readiness`` — that flips the
status to ``"complete"`` *one-way* (it never overwrites a manual edit).

This module is the reporter-side client for that endpoint. It is called:

  * From ``inbox_scanner`` after a successful upload of an SIR / Building
    Inspection / Block Plan attachment, so the row turns green within
    ~minutes of the doc landing in the shared Drive folder.
  * From ``scripts/backfill_doc_readiness.py`` to bulk-flip everything that
    already exists in Drive when the wiring first ships.

Auth: shared bearer in the ``INBOX_SCANNER_TOKEN`` env var. Must match the
identically-named env var on the dashboard's Vercel project.

Silent-fail policy: like ``dashboard_publisher`` — never raise. The reporter's
primary jobs (uploading docs, generating reports, sending email) must not be
broken by a flaky dashboard write.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://dd-dashboard-three.vercel.app"
_DEFAULT_TIMEOUT_SEC = 15
_PRINCIPAL = "inbox-scanner"

# Map reporter ``doc_type`` strings to the dashboard's readiness fieldPath.
# LiDAR has no reporter-side doc_type today; it's left out on purpose so a
# stray classification can never flip it. When LiDAR ingestion ships, add
# ``"lidar": "lidar_status"`` here.
DOC_TYPE_TO_FIELD: dict[str, str] = {
    "sir": "cds_sir_status",
    "building_inspection": "building_inspection_status",
    "block_plan": "block_plan_status",
}


def _readiness_url(base_url: str | None = None) -> str:
    base = (base_url or os.environ.get("DASHBOARD_PUBLISH_URL") or _DEFAULT_BASE_URL).rstrip("/")
    return f"{base}/api/auto-readiness"


def field_for_doc_type(doc_type: str) -> str | None:
    """Return the readiness fieldPath for ``doc_type`` or None if unmapped."""
    return DOC_TYPE_TO_FIELD.get(doc_type)


def mark_readiness_complete(
    edits: list[dict[str, str]],
    *,
    base_url: str | None = None,
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """POST a batch of readiness flips to the dashboard.

    Each entry in ``edits`` must be ``{"slug": str, "fieldPath": str}``. A
    ``"value": "complete"`` is added automatically — that is the only value
    the endpoint accepts from automation.

    Returns a result dict::

        {"applied": int, "skipped": list, "ok": bool, "reason": str | None}

    Never raises. ``ok=False`` with ``reason`` set when the call could not
    be made or returned a non-2xx.
    """
    if not edits:
        return {"applied": 0, "skipped": [], "ok": True, "reason": None}

    if os.environ.get("DASHBOARD_PUBLISH_ENABLED", "1") == "0":
        logger.info("DASHBOARD_PUBLISH_ENABLED=0 — skipping readiness flips")
        return {"applied": 0, "skipped": [], "ok": True, "reason": "publish disabled"}

    token = os.environ.get("INBOX_SCANNER_TOKEN")
    if not token:
        logger.warning(
            "INBOX_SCANNER_TOKEN not set; skipping %d readiness flip(s)",
            len(edits),
        )
        return {
            "applied": 0,
            "skipped": [],
            "ok": False,
            "reason": "INBOX_SCANNER_TOKEN not configured",
        }

    # Defensive: drop bad entries here so the dashboard's batch isn't poisoned.
    cleaned: list[dict[str, str]] = []
    for e in edits:
        slug = (e.get("slug") or "").strip()
        field = (e.get("fieldPath") or "").strip()
        if not slug or not field:
            continue
        if field not in DOC_TYPE_TO_FIELD.values() and field != "lidar_status":
            # lidar_status is allowed by the endpoint even though we don't
            # auto-flip it from doc_type today; keep the door open.
            logger.warning("Skipping readiness flip with non-allowlisted field %r", field)
            continue
        cleaned.append({"slug": slug, "fieldPath": field, "value": "complete"})

    if not cleaned:
        return {"applied": 0, "skipped": [], "ok": True, "reason": "no valid edits"}

    body = {"editedBy": _PRINCIPAL, "edits": cleaned}
    url = _readiness_url(base_url)

    try:
        resp = requests.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_sec,
        )
    except requests.RequestException as exc:
        logger.warning("Readiness POST to %s failed: %s", url, exc)
        return {"applied": 0, "skipped": [], "ok": False, "reason": str(exc)}

    if resp.status_code >= 300:
        body_text = (resp.text or "")[:200]
        logger.warning(
            "Readiness POST to %s returned HTTP %d: %s",
            url,
            resp.status_code,
            body_text,
        )
        return {
            "applied": 0,
            "skipped": [],
            "ok": False,
            "reason": f"HTTP {resp.status_code}: {body_text}",
        }

    try:
        data = resp.json()
    except ValueError:
        data = {}

    applied = int(data.get("applied", 0)) if isinstance(data, dict) else 0
    skipped = data.get("skipped", []) if isinstance(data, dict) else []
    logger.info(
        "Readiness flips applied=%d skipped=%d (sent=%d)",
        applied,
        len(skipped) if isinstance(skipped, list) else 0,
        len(cleaned),
    )
    return {
        "applied": applied,
        "skipped": skipped if isinstance(skipped, list) else [],
        "ok": True,
        "reason": None,
    }


def edits_from_uploads(uploads: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Translate scanner upload entries into readiness edit dicts.

    Each upload entry coming out of ``inbox_scanner._process_email`` looks
    like::

        {"site_title": "...", "doc_type": "sir", "drive_file_id": "...", ...}

    Only entries with a ``site_title`` and a ``doc_type`` mapped in
    ``DOC_TYPE_TO_FIELD`` produce a flip. Duplicates (same slug+field) are
    deduped. Dry-run uploads are ignored.
    """
    # Local imports to keep this module dependency-light for tests that
    # don't need the publisher / network helpers.
    from .dashboard_publisher import slugify
    from .rebl import canonical_slug_for_address

    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    # Cache Rebl resolutions per (address) so a batch of uploads for the
    # same site (SIR + Building Inspection) doesn't trigger N HTTP calls.
    rebl_slug_by_address: dict[str, str] = {}
    for u in uploads or []:
        if not isinstance(u, dict):
            continue
        if u.get("dry_run"):
            continue
        doc_type = (u.get("doc_type") or "").strip()
        site_title = (u.get("site_title") or "").strip()
        site_address = (u.get("site_address") or "").strip()
        field = DOC_TYPE_TO_FIELD.get(doc_type)
        if not site_title or not field:
            continue
        # Slug precedence mirrors the publisher: Rebl canonical id when
        # available, slugify(title) as fallback. inbox_scanner started
        # plumbing ``site_address`` through the upload payload so this
        # path stays in lock-step with the dashboard.
        rebl_slug = ""
        if site_address:
            if site_address in rebl_slug_by_address:
                rebl_slug = rebl_slug_by_address[site_address]
            else:
                rebl_slug = canonical_slug_for_address(site_address, fallback="")
                rebl_slug_by_address[site_address] = rebl_slug
        slug = (rebl_slug or slugify(site_title)).strip()
        if not slug:
            continue
        key = (slug, field)
        if key in seen:
            continue
        seen.add(key)
        out.append({"slug": slug, "fieldPath": field})
    return out
