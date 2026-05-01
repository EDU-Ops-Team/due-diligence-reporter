#!/usr/bin/env python3
"""raycon_followup.py — pick up RayCon scenario JSON files and publish reports.

When a Block Plan lands in a site's M1 folder, ``inbox_scanner`` pings
RayCon's ``/v1/jobs`` endpoint asynchronously. RayCon then writes a
single ``raycon_scenario.json`` file back into the same M1 folder.

This script runs on a 5-minute cadence (``raycon-followup.yml``) and:

  1. Iterates every active Wrike Site Record.
  2. Looks in each site's M1 folder for ``raycon_scenario.json``.
  3. If the JSON exists and is newer than the corresponding
     ``RayCon Scenario Assessment - <site>`` Google Doc (or that Doc
     doesn't exist yet), publishes a fresh Doc via ``save_skill_report``.
  4. Tracks staleness for alerting: if a Block Plan exists but no
     scenario JSON has been written within ``--alert-after-minutes``
     (default 60), posts a Google Chat alert listing the stuck sites.

The script is idempotent and safe to re-run. Re-runs only re-publish the
RayCon Scenario Doc when the JSON's ``modifiedTime`` is newer than the
Doc's ``modifiedTime``.

Run:
    uv run python scripts/raycon_followup.py             # all active sites
    uv run python scripts/raycon_followup.py --site Keller  # single site
    uv run python scripts/raycon_followup.py --dry-run   # detect only

Env:
    OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET / OAUTH_REFRESH_TOKEN
    WRIKE_ACCESS_TOKEN
    GOOGLE_CHAT_WEBHOOK_URL  (optional; alert sink)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

import requests  # noqa: E402

from due_diligence_reporter.config import get_settings  # noqa: E402
from due_diligence_reporter.google_client import GoogleClient  # noqa: E402
from due_diligence_reporter.m1_lookup import _resolve_m1_folder  # noqa: E402
from due_diligence_reporter.raycon_client import (  # noqa: E402
    RayConSchemaError,
    raycon_scenario_to_report_fields,
    read_raycon_scenario_from_m1,
)
from due_diligence_reporter.server import save_skill_report  # noqa: E402
from due_diligence_reporter.wrike import (  # noqa: E402
    _get_active_status_ids,
    _get_all_site_records,
    build_site_summary,
    filter_active_site_records,
    load_wrike_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("raycon_followup")

PUBLISHED_DOC_PREFIX = "RayCon Scenario Assessment"
BLOCK_PLAN_FILENAME_HINTS = ("block plan", "block_plan", "blockplan")

# Persisted map of {site_name: ISO8601 timestamp of last Chat alert}.
# Prevents the 5-minute cron from spamming ~96 alerts/day for a stuck site.
ALERT_DEDUP_PATH = _project_root / ".raycon_followup_alerts.json"
ALERT_DEDUP_WINDOW = timedelta(hours=24)


# ---------------------------------------------------------------------------


def _load_alert_state(path: Path = ALERT_DEDUP_PATH) -> dict[str, str]:
    """Load the {site_name: last_alert_iso} dedup map. Returns {} on any error."""
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("Failed to read alert dedup state at %s: %s", path, e)
        return {}


def _save_alert_state(state: dict[str, str], path: Path = ALERT_DEDUP_PATH) -> None:
    """Persist the dedup map. Best-effort; logs but does not raise."""
    try:
        path.write_text(json.dumps(state, sort_keys=True, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to write alert dedup state at %s: %s", path, e)


def _filter_dedup_alerts(
    alerts: list[dict[str, Any]],
    state: dict[str, str],
    *,
    now: datetime | None = None,
    window: timedelta = ALERT_DEDUP_WINDOW,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Drop alerts for sites alerted within ``window``. Return (fresh_alerts, updated_state).

    The updated state records ``now`` as the last-alert time for every site we
    are about to notify on, so the next run within the window is suppressed.
    """
    now = now or datetime.now(timezone.utc)
    fresh: list[dict[str, Any]] = []
    new_state = dict(state)
    for row in alerts:
        site = str(row.get("site", "")).strip()
        if not site:
            continue
        last_iso = state.get(site)
        last_dt = _parse_iso(last_iso) if last_iso else None
        if last_dt is not None and (now - last_dt) < window:
            continue
        fresh.append(row)
        new_state[site] = now.isoformat()
    return fresh, new_state


def _site_filter(site_summary: dict[str, Any], needle: str | None) -> bool:
    if not needle:
        return True
    needle_lc = needle.lower()
    title = str(site_summary.get("title", "")).lower()
    address = str(site_summary.get("address", "")).lower()
    return needle_lc in title or needle_lc in address


def _find_block_plan(gc: GoogleClient, m1_folder_id: str) -> dict[str, Any] | None:
    """Return the most recently modified Block Plan PDF in M1, or None."""
    candidate: dict[str, Any] | None = None
    for f in gc.list_files_in_folder(m1_folder_id):
        name = str(f.get("name", "")).lower()
        if not any(hint in name for hint in BLOCK_PLAN_FILENAME_HINTS):
            continue
        if candidate is None or str(f.get("modifiedTime", "")) > str(
            candidate.get("modifiedTime", "")
        ):
            candidate = f
    return candidate


def _find_published_doc(gc: GoogleClient, m1_folder_id: str, site_name: str) -> dict[str, Any] | None:
    """Return the existing RayCon Scenario Doc for a site, or None."""
    target = f"{PUBLISHED_DOC_PREFIX} - {site_name}"
    for f in gc.list_files_in_folder(m1_folder_id):
        if str(f.get("name", "")).strip() == target:
            return f
    return None


def _post_chat(webhook_url: str, text: str) -> None:
    try:
        requests.post(webhook_url, json={"text": text}, timeout=15).raise_for_status()
    except Exception as e:
        logger.warning("Failed to post Google Chat alert: %s", e)


def _process_site(
    gc: GoogleClient,
    site_summary: dict[str, Any],
    *,
    dry_run: bool,
    alert_after: timedelta,
) -> dict[str, Any]:
    """Return a per-site result row for the run summary."""
    site_name = str(site_summary.get("title", "")).strip() or "(unnamed)"
    drive_folder_url = str(site_summary.get("drive_folder_url", "")).strip()
    if not drive_folder_url:
        return {"site": site_name, "skipped": "no drive folder"}

    try:
        m1_folder_id, _ = _resolve_m1_folder(gc, drive_folder_url)
    except Exception as e:
        return {"site": site_name, "error": f"resolve M1 failed: {e}"}
    if not m1_folder_id:
        return {"site": site_name, "skipped": "no M1 folder"}

    block_plan = _find_block_plan(gc, m1_folder_id)
    if block_plan is None:
        return {"site": site_name, "skipped": "no block plan in M1"}

    try:
        scenario = read_raycon_scenario_from_m1(gc, drive_folder_url)
    except RayConSchemaError as e:
        logger.error("[%s] %s", site_name, e)
        return {"site": site_name, "error": f"schema error: {e}"}

    if scenario is None:
        # Stuck or in-flight? Determine by Block Plan age.
        bp_modified = _parse_iso(str(block_plan.get("modifiedTime", "")))
        if bp_modified is not None and (datetime.now(timezone.utc) - bp_modified) > alert_after:
            return {
                "site": site_name,
                "alert": f"no raycon_scenario.json after {alert_after}",
                "block_plan_modified": bp_modified.isoformat(),
            }
        return {"site": site_name, "skipped": "scenario JSON not yet present"}

    # Scenario JSON is here — publish the report Doc if missing or stale.
    published = _find_published_doc(gc, m1_folder_id, site_name)
    json_modified = scenario.get("_drive_modified_time", "")
    doc_modified = (published or {}).get("modifiedTime", "") if published else ""

    if published is not None and str(doc_modified) >= str(json_modified):
        return {"site": site_name, "skipped": "report doc up to date"}

    if dry_run:
        return {
            "site": site_name,
            "would_publish": True,
            "json_modified": json_modified,
            "doc_existed": published is not None,
        }

    payload = {
        **scenario,
        "report_data_fields": raycon_scenario_to_report_fields(scenario),
    }
    result = asyncio.run(
        save_skill_report(
            skill_name="RayCon Scenario",
            site_name=site_name,
            drive_folder_url=drive_folder_url,
            skill_data=payload,
        )
    )
    if result.get("status") != "success":
        return {"site": site_name, "error": str(result.get("message", "publish failed"))}
    return {
        "site": site_name,
        "published": True,
        "doc_url": result.get("doc_url"),
    }


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        # Drive uses RFC 3339 with `Z`; Python <3.11 needs the explicit offset.
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", help="Filter to sites whose title or address contains this substring")
    parser.add_argument("--dry-run", action="store_true", help="Detect only; don't publish docs")
    parser.add_argument(
        "--alert-after-minutes",
        type=int,
        default=60,
        help="Alert when raycon_scenario.json hasn't appeared this long after the Block Plan landed",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )
    config = load_wrike_config()
    records = _get_all_site_records(cfg=config)
    active_status_ids = _get_active_status_ids(access_token=config.access_token)
    active_records = filter_active_site_records(records, active_status_ids)

    summaries = [build_site_summary(r) for r in active_records]
    summaries = [s for s in summaries if _site_filter(s, args.site)]

    alert_after = timedelta(minutes=args.alert_after_minutes)

    results: list[dict[str, Any]] = []
    for site_summary in summaries:
        try:
            row = _process_site(
                gc,
                site_summary,
                dry_run=args.dry_run,
                alert_after=alert_after,
            )
        except Exception as e:
            logger.exception("Unhandled error for site '%s'", site_summary.get("title"))
            row = {"site": site_summary.get("title"), "error": str(e)}
        results.append(row)
        logger.info("%s", json.dumps(row, default=str))

    published = [r for r in results if r.get("published")]
    alerts = [r for r in results if r.get("alert")]
    errors = [r for r in results if r.get("error")]

    if alerts and settings.google_chat_webhook_url:
        dedup_state = _load_alert_state()
        fresh_alerts, new_state = _filter_dedup_alerts(alerts, dedup_state)
        if fresh_alerts:
            lines = ["RayCon scenario follow-up: stuck sites"]
            for row in fresh_alerts:
                lines.append(f"- {row['site']}: {row['alert']}")
            _post_chat(settings.google_chat_webhook_url, "\n".join(lines))
            _save_alert_state(new_state)
        suppressed = len(alerts) - len(fresh_alerts)
        if suppressed:
            logger.info(
                "Suppressed %d stuck-site alert(s) within %s dedup window",
                suppressed,
                ALERT_DEDUP_WINDOW,
            )

    if errors and settings.google_chat_webhook_url:
        lines = ["RayCon scenario follow-up: errors"]
        for row in errors:
            lines.append(f"- {row['site']}: {row['error']}")
        _post_chat(settings.google_chat_webhook_url, "\n".join(lines))

    logger.info(
        "Run complete: published=%d alerts=%d errors=%d total_sites=%d",
        len(published),
        len(alerts),
        len(errors),
        len(results),
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
