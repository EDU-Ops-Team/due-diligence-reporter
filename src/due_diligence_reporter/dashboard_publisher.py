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
from datetime import UTC, date, datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://dd-dashboard-three.vercel.app"
_DEFAULT_TIMEOUT_SEC = 15

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

    return {
        "slug": slug,
        "site_name": site_title,
        "marketing_name": f"Alpha School \u2014 {site_title}",
        "address": address or "",
        "city_state_zip": city_state_zip,
        "state": state,
        "school_type": school_label,
        "prepared_by": "DD Pipeline (auto)",
        "report_date": rd,
        "published_at": datetime.now(UTC).isoformat(),
        "drive_folder_url": drive_folder_url or "",
        "dd_report_url": dd_report_url or "",
        "rebl": {
            "site_id": rebl_site_id or "",
            "url": rebl_url or "",
        },
    }


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

    meta = build_site_meta(
        site_title,
        address=address,
        school_type=school_type,
        drive_folder_url=drive_folder_url,
        dd_report_url=dd_report_url,
        rebl_site_id=rebl_site_id or str(report_data.get("meta.rebl_site_id") or ""),
        rebl_url=rebl_url or str(report_data.get("sources.rebl_link") or ""),
        report_date=report_date,
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
