"""Publish DD report data to the DD Dashboard.

After `create_dd_report` successfully fills out a Google Doc, we also POST the
structured `report_data` to the dashboard's `/api/sites/:slug/publish`
endpoint. The endpoint converts flat pipeline tokens (exec.c_zoning,
exec.fastest_open_capex, sources.sir_link, etc.) into the dashboard's nested
site schema and commits it to `client/public/sites.json`.

Auth: shared bearer secret in the `DASHBOARD_PUBLISH_SECRET` env var.

Silent-fail policy: never raise. The pipeline's primary output is the Google
Doc; dashboard publishing is best-effort. Any failure is logged and
swallowed so a network hiccup doesn't block report delivery.

Environment:
    DASHBOARD_PUBLISH_URL     Base URL of the dashboard (default:
                              https://dd-dashboard-three.vercel.app)
    DASHBOARD_PUBLISH_SECRET  Bearer token (must match Vercel env var)
    DASHBOARD_PUBLISH_ENABLED Set to "0" to disable publishing entirely.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://dd-dashboard-three.vercel.app"
_DEFAULT_TIMEOUT_SEC = 15

# DD turn-time SLA: 14 days from Wrike record creation to report due.
# Drives the default dd_due_date when no explicit value is passed.
_DD_DUE_DATE_OFFSET_DAYS = 14

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def slugify(site_title: str) -> str:
    """Turn 'Palm Beach Gardens' into 'palm-beach-gardens'.

    Mirrors the dashboard's existing slugs — lowercase, hyphen-separated,
    alphanumeric only.
    """
    s = site_title.strip().lower()
    s = s.replace("&", "and")
    s = _SLUG_RE.sub("-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _parse_city_state_zip(address: str | None) -> tuple[str, str]:
    """Best-effort parse of 'Street, City, ST ZIP' → ('City, ST ZIP', 'ST')."""
    if not address:
        return "", ""
    parts = [p.strip() for p in address.split(",")]
    if len(parts) < 2:
        return "", ""
    tail = ", ".join(parts[1:]) if len(parts) > 1 else ""
    state = ""
    # Look for ' XX ' or ' XX,' two-letter USPS state.
    m = re.search(r"\b([A-Z]{2})\b(?:\s+\d{5})?", tail)
    if m:
        state = m.group(1)
    return tail, state


def _derive_dd_dates(
    *,
    explicit_commissioned: str | None,
    explicit_due: str | None,
    today: date | None = None,
) -> tuple[str | None, str | None]:
    """Derive (dd_commissioned_date, dd_due_date) for a publish call.

    Rules (per Greg, 4/25):
      - dd_commissioned_date == the date the DD report is first created.
        On every publish call we send today's date as a candidate. The
        dashboard's sticky-preserve transform locks the first non-empty
        value, so subsequent reruns cannot bump it forward.
      - dd_due_date == commissioned_date + 14 days (DD turn-time SLA).
      - An explicit caller-provided value always wins (back-dated
        manual rerun, custom due date).

    Returns (commissioned, due) as YYYY-MM-DD strings.

    Note on the sticky-preserve guarantee: even though we send today's
    date on every call, the dashboard side only writes it the first
    time. See `transformPipelineToSite` in dd-dashboard's
    api/_lib/transform.ts — dd_commissioned_date / dd_due_date
    fall through the `preserve()` helper.
    """
    today = today or date.today()

    # Caller-provided commissioned date short-circuits the today-default.
    commissioned = (explicit_commissioned or "").strip() or None
    if commissioned is None:
        commissioned = today.isoformat()

    # Caller-provided due date short-circuits the +14d default.
    due = (explicit_due or "").strip() or None
    if due is None:
        try:
            base = date.fromisoformat(commissioned)
            due = (base + timedelta(days=_DD_DUE_DATE_OFFSET_DAYS)).isoformat()
        except ValueError:
            # Malformed commissioned date — don't fabricate a due date.
            due = None

    return commissioned, due


def build_site_meta(
    site_title: str,
    *,
    address: str | None = None,
    school_type: str | None = None,
    drive_folder_url: str | None = None,
    dd_report_url: str | None = None,
    rebl_site_id: str | None = None,
    rebl_url: str | None = None,
    report_date: date | None = None,
    site_owner: str | None = None,
    # --- Phase 1 DD provenance fields (Rhodes data dictionary, 4/24) ---
    # All optional. Pipeline auto-runs leave them blank for now; explicit
    # callers (backfill scripts, future MCP integrations, manual reruns)
    # can populate them. Dashboard-side `transformPipelineToSite` uses a
    # sticky-preserve pattern so blanks here never overwrite a stored
    # value.
    dd_author: str | None = None,
    dd_owner: str | None = None,
    dd_version: str | None = None,
    dd_report_length: int | None = None,
    dd_commissioned_date: str | None = None,  # YYYY-MM-DD
    dd_due_date: str | None = None,  # YYYY-MM-DD
) -> dict[str, Any]:
    """Assemble the `site_meta` payload from pipeline inputs.

    Everything optional falls back to sensible defaults.
    """
    slug = slugify(site_title)
    city_state_zip, state = _parse_city_state_zip(address)
    rd = (report_date or date.today()).isoformat()

    # Dashboard uses "K-8" / "Micro" / "250" / "1000"; pipeline normalizes to
    # "micro"/"250"/"1000". Map to dashboard-facing label.
    type_label_map = {
        "micro": "Microschool (25)",
        "250": "Growth (250)",
        "1000": "Flagship (1000)",
    }
    school_label = type_label_map.get(school_type or "", "K-8")

    payload: dict[str, Any] = {
        "slug": slug,
        "site_name": site_title,
        "marketing_name": f"Alpha School \u2014 {site_title}",
        "address": address or "",
        "city_state_zip": city_state_zip,
        "state": state,
        "school_type": school_label,
        "prepared_by": "DD Pipeline (auto)",
        "site_owner": (site_owner or "").strip(),
        "report_date": rd,
        "published_at": datetime.now(UTC).isoformat(),
        "drive_folder_url": drive_folder_url or "",
        "dd_report_url": dd_report_url or "",
        "rebl": {
            "site_id": rebl_site_id or "",
            "url": rebl_url or "",
        },
    }

    # Only include DD provenance keys when callers actually pass a value.
    # The dashboard's sticky-preserve logic treats omitted keys and blank
    # strings the same way, but omitting them keeps the wire payload tidy
    # and makes the diff in committed sites.json minimal during Phase 1
    # rollout (when most sites won't have these fields yet).
    if dd_author:
        payload["dd_author"] = dd_author.strip()
    if dd_owner:
        payload["dd_owner"] = dd_owner.strip()
    if dd_version:
        payload["dd_version"] = dd_version.strip()
    if isinstance(dd_report_length, int) and dd_report_length >= 0:
        payload["dd_report_length"] = dd_report_length
    if dd_commissioned_date:
        payload["dd_commissioned_date"] = dd_commissioned_date.strip()
    if dd_due_date:
        payload["dd_due_date"] = dd_due_date.strip()

    return payload


def publish_to_dashboard(
    site_title: str,
    report_data: dict[str, Any],
    *,
    address: str | None = None,
    school_type: str | None = None,
    drive_folder_url: str | None = None,
    dd_report_url: str | None = None,
    rebl_site_id: str | None = None,
    rebl_url: str | None = None,
    report_date: date | None = None,
    site_owner: str | None = None,
    # Phase 1 DD provenance pass-through. See build_site_meta() for semantics.
    dd_author: str | None = None,
    dd_owner: str | None = None,
    dd_version: str | None = None,
    dd_report_length: int | None = None,
    dd_commissioned_date: str | None = None,
    dd_due_date: str | None = None,
    base_url: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SEC,
) -> bool:
    """Publish one site's report_data to the dashboard.

    Returns True on HTTP 200, False otherwise (including disabled or
    missing-secret cases).
    """
    if os.environ.get("DASHBOARD_PUBLISH_ENABLED", "1") == "0":
        logger.info("Dashboard publish disabled via env; skipping %s", site_title)
        return False

    secret = os.environ.get("DASHBOARD_PUBLISH_SECRET")
    if not secret:
        logger.warning(
            "DASHBOARD_PUBLISH_SECRET not set; skipping dashboard publish for %s",
            site_title,
        )
        return False

    url_base = (base_url or os.environ.get("DASHBOARD_PUBLISH_URL") or _DEFAULT_BASE_URL).rstrip("/")

    # dd_commissioned_date == the date the DD report is first created.
    # We send today's date on every publish; the dashboard's sticky-
    # preserve transform locks the first non-empty value so reruns
    # cannot bump it forward. dd_due_date == commissioned + 14 days.
    inferred_commissioned, inferred_due = _derive_dd_dates(
        explicit_commissioned=dd_commissioned_date,
        explicit_due=dd_due_date,
    )

    meta = build_site_meta(
        site_title,
        address=address,
        school_type=school_type,
        drive_folder_url=drive_folder_url,
        dd_report_url=dd_report_url,
        rebl_site_id=rebl_site_id or str(report_data.get("meta.rebl_site_id") or ""),
        rebl_url=rebl_url or str(report_data.get("sources.rebl_link") or ""),
        report_date=report_date,
        site_owner=site_owner,
        dd_author=dd_author,
        dd_owner=dd_owner,
        dd_version=dd_version,
        dd_report_length=dd_report_length,
        dd_commissioned_date=inferred_commissioned,
        dd_due_date=inferred_due,
    )
    slug = meta["slug"]

    endpoint = f"{url_base}/api/sites/{slug}/publish"
    payload = {"site_meta": meta, "report_data": report_data}

    try:
        r = requests.post(
            endpoint,
            json=payload,
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
    except requests.RequestException as e:
        logger.warning("Dashboard publish failed for %s (network): %s", site_title, e)
        return False

    if r.status_code == 200:
        try:
            data = r.json()
            logger.info(
                "Dashboard publish OK for %s (%s) → slug=%s",
                site_title,
                data.get("action"),
                slug,
            )
        except ValueError:
            logger.info("Dashboard publish OK for %s → slug=%s", site_title, slug)
        return True

    logger.warning(
        "Dashboard publish HTTP %d for %s: %s",
        r.status_code,
        site_title,
        r.text[:200],
    )
    return False
