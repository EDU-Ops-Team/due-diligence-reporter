#!/usr/bin/env python3
"""
backfill_dashboard.py — One-time backfill of the DD Dashboard from
existing pipeline trace JSONs.

For each active Wrike Site Record that has a DD report + trace file in its
Drive folder:

  1. Find the most recent "... DD Report Trace - YYYY-MM-DD.json" file.
  2. Download and parse it.
  3. Reconstruct a flat `report_data` dict from `token_report`.
  4. POST to /api/sites/:slug/publish on the dashboard.

The pipeline's "live" publish hook already fires on every new report going
forward. This script exists to catch up the 12 sites that existed before
the hook was added.

Run:
    uv run python scripts/backfill_dashboard.py           # all sites
    uv run python scripts/backfill_dashboard.py austin    # single site

Env (from .env):
    DASHBOARD_PUBLISH_URL, DASHBOARD_PUBLISH_SECRET
    plus the usual pipeline env (Wrike, Google)
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

from due_diligence_reporter.config import get_settings  # noqa: E402
from due_diligence_reporter.dashboard_publisher import publish_to_dashboard  # noqa: E402
from due_diligence_reporter.google_client import GoogleClient  # noqa: E402
from due_diligence_reporter.utils import extract_folder_id_from_url  # noqa: E402
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("backfill_dashboard")

# Matches both filename conventions used by the pipeline:
#   - "<Site> Report Trace - YYYY-MM-DD.json"    (older server.py path)
#   - "<Site> DD Report Trace - YYYY-MM-DD.json" (newer report_pipeline.py path)
_TRACE_NAME_RE = re.compile(r"Report Trace.*\.json$", re.IGNORECASE)
_TRACE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _latest_trace_file(
    gc: GoogleClient, folder_id: str
) -> dict[str, Any] | None:
    """Return the most recently modified DD Report Trace JSON in a folder.

    Kept for backwards compatibility; prefer ``_candidate_trace_files``.
    """
    candidates = _candidate_trace_files(gc, folder_id)
    return candidates[0] if candidates else None


def _candidate_trace_files(
    gc: GoogleClient, folder_id: str
) -> list[dict[str, Any]]:
    """Return all Report Trace JSONs in a folder, newest first.

    The pipeline can write two trace files in the same day (older
    ``... Report Trace - YYYY-MM-DD.json`` from the legacy server.py path and
    newer ``... DD Report Trace - YYYY-MM-DD.json`` from report_pipeline.py).
    The newer one is sometimes written before the token_report is populated,
    leaving the trace empty. Callers should iterate this list and fall back to
    the next candidate when the chosen trace has no usable token_report.
    """
    files = gc.list_files_in_folder(folder_id)
    traces = [f for f in files if _TRACE_NAME_RE.search(f.get("name", ""))]
    traces.sort(key=lambda f: f.get("modifiedTime", ""), reverse=True)
    return traces


def _report_date_from_trace(trace_data: dict[str, Any], fallback: str) -> date:
    """Pull the report date out of the trace JSON, falling back to filename."""
    raw = trace_data.get("date") or ""
    # Pipeline stores as "MM/DD/YYYY"
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", raw)
    if m:
        mm, dd, yyyy = m.groups()
        try:
            return date(int(yyyy), int(mm), int(dd))
        except ValueError:
            pass
    m = _TRACE_DATE_RE.search(fallback)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.today()


def _reconstruct_report_data(trace_data: dict[str, Any]) -> dict[str, Any]:
    """Flatten token_report back into the flat {token: value} dict."""
    token_report = trace_data.get("token_report") or {}
    report_data: dict[str, Any] = {}
    for token, entry in token_report.items():
        if not isinstance(entry, dict):
            continue
        val = entry.get("value")
        if val is None or val == "":
            continue
        report_data[token] = val

    # The trace also captures link tokens separately in `hyperlinks` or as
    # entries on token_report itself. Pull any source.* link-style tokens that
    # look like URLs out of token_report.
    for token, entry in token_report.items():
        if not isinstance(entry, dict):
            continue
        val = entry.get("value", "")
        if token.startswith("sources.") and isinstance(val, str) and val.startswith("http"):
            report_data[token] = val

    return report_data


def _report_doc_url_from_trace(trace_data: dict[str, Any]) -> str:
    return str(trace_data.get("report_doc_url") or "")


def backfill_one(
    gc: GoogleClient,
    site_title: str,
    drive_folder_url: str,
    address: str | None,
    school_type: str | None,
    site_owner: str | None = None,
    force_slug: str | None = None,
) -> bool:
    """Backfill one site from its Drive trace.

    Args:
        force_slug: Optional canonical dashboard slug to publish under. When
            provided, suppresses the `publish_to_dashboard` slug-derivation
            chain (rebl_site_id token -> slugify(title)) that has historically
            minted phantom legacy-slug records when callers already knew the
            correct slug. Used by the migration recovery flow.
    """
    folder_id = extract_folder_id_from_url(drive_folder_url)
    if not folder_id:
        logger.warning("%s: could not extract folder id from %s", site_title, drive_folder_url)
        return False

    candidates = _candidate_trace_files(gc, folder_id)
    if not candidates:
        logger.info("%s: no Report Trace file found, skipping", site_title)
        return False

    trace_file: dict[str, Any] | None = None
    trace_data: dict[str, Any] = {}
    report_data: dict[str, Any] = {}
    for candidate in candidates:
        try:
            data = gc.download_file_bytes(candidate["id"])
            candidate_data = json.loads(data.decode("utf-8"))
        except Exception as e:
            logger.warning(
                "%s: could not load trace '%s': %s",
                site_title,
                candidate.get("name"),
                e,
            )
            continue

        candidate_report = _reconstruct_report_data(candidate_data)
        if candidate_report:
            trace_file = candidate
            trace_data = candidate_data
            report_data = candidate_report
            break
        logger.info(
            "%s: trace '%s' has no token_report values, trying next candidate",
            site_title,
            candidate.get("name"),
        )

    if not trace_file or not report_data:
        logger.info(
            "%s: no trace with usable token_report found across %d candidate(s), skipping",
            site_title,
            len(candidates),
        )
        return False

    rd = _report_date_from_trace(trace_data, trace_file.get("name", ""))
    doc_url = _report_doc_url_from_trace(trace_data)

    logger.info(
        "%s: publishing %d tokens from trace '%s' (report_date=%s)",
        site_title,
        len(report_data),
        trace_file.get("name"),
        rd.isoformat(),
    )

    return publish_to_dashboard(
        site_title,
        report_data,
        address=address,
        school_type=school_type,
        drive_folder_url=drive_folder_url,
        dd_report_url=doc_url,
        report_date=rd,
        site_owner=site_owner,
        force_slug=force_slug,
    )


def main(single_site_filter: str | None = None) -> int:
    settings = get_settings()
    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )
    wrike_cfg = load_wrike_config()
    records = _get_all_site_records(cfg=wrike_cfg)
    active_status_ids = _get_active_status_ids(access_token=wrike_cfg.access_token)
    active = filter_active_site_records(records, active_status_ids)

    total, published, skipped = 0, 0, 0
    for rec in active:
        title = rec.get("title", "").strip()
        if not title:
            continue
        if single_site_filter and single_site_filter.lower() not in title.lower():
            continue

        drive_url = extract_google_folder_from_record(rec)
        if not drive_url:
            continue
        total += 1
        address = extract_address_from_record(rec)
        school_type = extract_school_type_from_record(rec)
        p1_profile = extract_p1_from_record(rec) or {}
        site_owner = p1_profile.get("name")

        try:
            ok = backfill_one(gc, title, drive_url, address, school_type, site_owner=site_owner)
        except Exception as e:
            logger.exception("%s: unexpected error during backfill: %s", title, e)
            ok = False

        if ok:
            published += 1
        else:
            skipped += 1

    logger.info(
        "Backfill complete: %d total, %d published, %d skipped", total, published, skipped
    )
    return 0 if published > 0 else 1


if __name__ == "__main__":
    filter_arg = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(main(filter_arg))
