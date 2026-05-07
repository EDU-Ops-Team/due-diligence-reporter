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
  4. Safety-net dispatch: if a Block Plan is present in M1 but
     ``raycon_scenario.json`` is missing, calls ``post_raycon_job``
     directly so Block Plans that arrive via any non-email path
     (manual upload, recovery, migration) still trigger RayCon.
     Dispatches are deduped per ``block_plan_file_id`` via
     ``.raycon_dispatch_state.json`` and only re-fire after
     ``--redispatch-after-minutes`` (default 30). RayCon's ``/v1/jobs``
     is itself idempotent on ``block_plan_file_id`` per the
     integration spec, so a re-fire is safe.
  5. Tracks staleness for alerting: if a Block Plan exists but no
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
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

import requests  # noqa: E402

from due_diligence_reporter.classifier import classify_document_type  # noqa: E402
from due_diligence_reporter.config import get_settings  # noqa: E402
from due_diligence_reporter.google_client import GoogleClient  # noqa: E402
from due_diligence_reporter.m1_lookup import _resolve_m1_folder  # noqa: E402
from due_diligence_reporter.raycon_client import (  # noqa: E402
    RayConSchemaError,
    post_raycon_job,
    raycon_payload_failed,
    raycon_scenario_to_report_fields,
    read_raycon_scenario_from_m1,
)
from due_diligence_reporter.report_pipeline import (  # noqa: E402
    list_shared_folders_once,
    process_site_pipeline,
)
from due_diligence_reporter.server import save_skill_report  # noqa: E402
from due_diligence_reporter.utils import (  # noqa: E402
    build_site_match_terms,
    extract_folder_id_from_url,
)
from due_diligence_reporter.wrike import (  # noqa: E402
    _get_active_status_ids,
    _get_all_site_records,
    build_site_summary,
    extract_school_feasibility_from_record,
    extract_timeline_confidence_from_record,
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

# Substrings that unambiguously identify a Block Plan filename. "PFP" and
# "preliminary floor plan(s)" are partner-side aliases for the same artifact
# and must match the Block Plan classifier in `classifier.py`.
BLOCK_PLAN_FILENAME_HINTS = (
    "block plan",
    "block_plan",
    "blockplan",
    "preliminary floor plan",
)
# "pfp" is matched separately with word boundaries so we don't false-positive
# on filenames that merely contain the letters p-f-p (e.g. "epfpro.pdf").
BLOCK_PLAN_PFP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bpfp\b"),
    re.compile(r"[-_]pfp(\.[^.]+)?$"),
)


def _filename_matches_block_plan(name: str) -> bool:
    """Return True if ``name`` (already lowercased) looks like a Block Plan.

    Recognized aliases: "Block Plan", "Preliminary Floor Plan(s)", and
    "PFP". All three refer to the same artifact and must route to the
    same downstream RayCon dispatch path.
    """
    if any(hint in name for hint in BLOCK_PLAN_FILENAME_HINTS):
        return True
    return any(pat.search(name) for pat in BLOCK_PLAN_PFP_PATTERNS)

# Persisted map of {site_name: ISO8601 timestamp of last Chat alert}.
# Prevents the 5-minute cron from spamming ~96 alerts/day for a stuck site.
ALERT_DEDUP_PATH = _project_root / ".raycon_followup_alerts.json"
ALERT_DEDUP_WINDOW = timedelta(hours=24)

# Persisted map of {block_plan_file_id: {"last_dispatch": ISO, "count": int,
# "site": str, "raycon_run_id": str | None}}. Keyed by Drive file ID so
# uploading a fresh Block Plan (which gets a new file ID) always re-fires
# RayCon, while re-walking the same plan within the redispatch window
# does not. RayCon's /v1/jobs is itself idempotent on block_plan_file_id,
# so a duplicate dispatch is at worst a no-op on their side.
DISPATCH_DEDUP_PATH = _project_root / ".raycon_dispatch_state.json"

# Persisted map of {f"{site_id}:{raycon_run_id}": ISO timestamp} so the
# event-driven DD Report republish (Rec. 1) doesn't re-run the full
# pipeline twice for the same RayCon scenario. A scenario that lacks a
# raycon_run_id falls back to a synthetic key derived from the JSON's
# Drive ``modifiedTime`` so we still dedup at run granularity.
DD_REPUBLISH_DEDUP_PATH = _project_root / ".raycon_dd_republish_state.json"

# Force-republish window: regenerate at least once per N hours for the same
# (site, raycon_run_id) pair if conditions still hold. Mirrors the
# ``--redispatch-after-minutes`` shape used by the safety-net dispatcher.
DD_REPUBLISH_FORCE_AFTER = timedelta(hours=12)


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


def _load_dispatch_state(
    path: Path = DISPATCH_DEDUP_PATH,
) -> dict[str, dict[str, Any]]:
    """Load the {block_plan_file_id: {...}} dispatch dedup map.

    Returns ``{}`` on missing file or any read/parse error so a corrupt
    state file never blocks the run.
    """
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        # Defensive: drop any non-dict entries from a malformed file.
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    except Exception as e:
        logger.warning("Failed to read dispatch dedup state at %s: %s", path, e)
        return {}


def _save_dispatch_state(
    state: dict[str, dict[str, Any]], path: Path = DISPATCH_DEDUP_PATH
) -> None:
    """Persist the dispatch dedup map. Best-effort; logs but does not raise."""
    try:
        path.write_text(json.dumps(state, sort_keys=True, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to write dispatch dedup state at %s: %s", path, e)


def _load_republish_state(
    path: Path = DD_REPUBLISH_DEDUP_PATH,
) -> dict[str, str]:
    """Load the {f'{site_id}:{run_id}': last_republish_iso} dedup map."""
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("Failed to read DD republish dedup state at %s: %s", path, e)
        return {}


def _save_republish_state(
    state: dict[str, str], path: Path = DD_REPUBLISH_DEDUP_PATH
) -> None:
    """Persist the DD republish dedup map. Best-effort; logs but does not raise."""
    try:
        path.write_text(json.dumps(state, sort_keys=True, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to write DD republish dedup state at %s: %s", path, e)


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
        if not _filename_matches_block_plan(name):
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


def _dispatch_raycon_job(
    site_summary: dict[str, Any],
    block_plan: dict[str, Any],
    m1_folder_id: str,
    dispatch_state: dict[str, dict[str, Any]],
    *,
    dry_run: bool,
    redispatch_after: timedelta,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fire ``post_raycon_job`` for a site whose Block Plan is present but
    has no ``raycon_scenario.json`` yet, deduped by ``block_plan_file_id``.

    Returns a result dict that is merged into the per-site row by the
    caller. Possible shapes:

      - ``{"dispatched": True, "raycon_run_id": str, "status": str}``
      - ``{"dispatch_skipped": "recently dispatched", "last_dispatch": ISO, "dispatch_count": int}``
      - ``{"dispatch_skipped": "dry_run", ...}``
      - ``{"dispatch_error": str}`` — caller decides whether to alert

    The function mutates ``dispatch_state`` in place on a successful
    dispatch so the caller can persist the updated map at the end of the
    run. On error or skip, ``dispatch_state`` is left untouched.
    """
    now = now or datetime.now(timezone.utc)
    site_name = str(site_summary.get("title", "")).strip() or "(unnamed)"
    block_plan_file_id = str(block_plan.get("id", "")).strip()
    block_plan_url = str(
        block_plan.get("webViewLink")
        or f"https://drive.google.com/file/d/{block_plan_file_id}/view"
    )

    if not block_plan_file_id:
        return {"dispatch_error": "block plan missing Drive file id"}

    # Dedup: skip if we dispatched this same block_plan_file_id within the
    # redispatch window. Different Block Plans (new uploads) get new file
    # IDs and therefore always pass through.
    prior = dispatch_state.get(block_plan_file_id)
    if prior:
        last_iso = str(prior.get("last_dispatch", ""))
        last_dt = _parse_iso(last_iso)
        if last_dt is not None and (now - last_dt) < redispatch_after:
            return {
                "dispatch_skipped": "recently dispatched",
                "last_dispatch": last_iso,
                "dispatch_count": int(prior.get("count", 0)),
            }

    site_id = str(site_summary.get("id", "")).strip()
    site_address = str(site_summary.get("address", "")).strip()
    drive_folder_url = str(site_summary.get("drive_folder_url", "")).strip()

    # Fail-closed on the same required fields post_raycon_job validates,
    # so we surface a clean error row instead of letting ValueError bubble
    # through and abort the whole run.
    missing = []
    for label, val in (
        ("site_id", site_id),
        ("site_address", site_address),
        ("drive_folder_url", drive_folder_url),
        ("m1_folder_id", m1_folder_id),
    ):
        if not val:
            missing.append(label)
    if missing:
        return {
            "dispatch_error": f"missing required field(s) for dispatch: {', '.join(missing)}"
        }

    # total_building_sf is optional on the wire (post_raycon_job sends 0
    # when None); coerce defensively the same way inbox_scanner does.
    raw_sf = site_summary.get("total_building_sf")
    try:
        total_building_sf = int(raw_sf) if raw_sf is not None else None
    except (TypeError, ValueError):
        total_building_sf = None

    if dry_run:
        return {
            "dispatch_skipped": "dry_run",
            "would_dispatch": True,
            "block_plan_file_id": block_plan_file_id,
        }

    try:
        response = post_raycon_job(
            site_id=site_id,
            site_name=site_name,
            address=site_address,
            drive_folder_url=drive_folder_url,
            m1_folder_id=m1_folder_id,
            block_plan_file_id=block_plan_file_id,
            block_plan_url=block_plan_url,
            total_building_sf=total_building_sf,
        )
    except Exception as e:
        logger.warning(
            "RayCon dispatch failed for site=%s block_plan_file_id=%s: %s",
            site_name,
            block_plan_file_id,
            e,
        )
        return {"dispatch_error": f"post_raycon_job failed: {e}"}

    raycon_run_id = str(response.get("raycon_run_id", "")).strip()
    status = str(response.get("status", "accepted"))
    logger.info(
        "RayCon safety-net dispatch for site=%s block_plan_file_id=%s run_id=%s status=%s",
        site_name,
        block_plan_file_id,
        raycon_run_id or "(unknown)",
        status,
    )

    prior_count = int((prior or {}).get("count", 0))
    dispatch_state[block_plan_file_id] = {
        "site": site_name,
        "last_dispatch": now.isoformat(),
        "count": prior_count + 1,
        "raycon_run_id": raycon_run_id or None,
        "status": status,
    }
    return {
        "dispatched": True,
        "raycon_run_id": raycon_run_id,
        "status": status,
        "block_plan_file_id": block_plan_file_id,
    }


def _find_existing_dd_report(
    gc: GoogleClient, site_folder_id: str
) -> dict[str, Any] | None:
    """Return the most recently modified DD Report Doc in the site folder, or None.

    DD Reports live at the site-folder root (not M1) and are named like
    ``<site> DD Report - YYYY-MM-DD``. We classify by filename via the
    shared classifier so we accept any historical naming scheme that
    ``classify_document_type`` flags as ``dd_report``.
    """
    candidate: dict[str, Any] | None = None
    try:
        files = gc.list_files_in_folder(site_folder_id)
    except Exception as e:
        logger.warning(
            "Could not list site folder %s while looking for existing DD Report: %s",
            site_folder_id,
            e,
        )
        return None
    for f in files:
        if classify_document_type(str(f.get("name", ""))) != "dd_report":
            continue
        if candidate is None or str(f.get("modifiedTime", "")) > str(
            candidate.get("modifiedTime", "")
        ):
            candidate = f
    return candidate


def _republish_dd_report_if_present(
    gc: GoogleClient,
    site_summary: dict[str, Any],
    raycon_run_id: str,
    *,
    settings: Any,
    system_prompt: str,
    shared_cache: dict[str, list[dict[str, Any]]],
    republish_state: dict[str, str],
    dry_run: bool,
    now: datetime | None = None,
    force_after: timedelta = DD_REPUBLISH_FORCE_AFTER,
) -> dict[str, Any]:
    """Regenerate the DD Report on top of an existing one when RayCon answers.

    Closes the gap where a partial DD Report (generated before RayCon
    responded) stayed partial forever. Behaviors:

    * No existing DD Report → no-op (the daily/inbox path will create it
      the first time vendor inputs land).
    * Existing DD Report + same ``(site_id, raycon_run_id)`` already
      republished within ``force_after`` → no-op.
    * Existing DD Report + this is a fresh ``(site_id, raycon_run_id)`` →
      call ``process_site_pipeline(force_regenerate=True)``.

    Failures here MUST NOT crash the caller — the RayCon Scenario Doc
    publish has already succeeded by the time we get here, and a
    republish error should not undo that.
    """
    now = now or datetime.now(timezone.utc)
    site_name = str(site_summary.get("title", "")).strip() or "(unnamed)"
    site_id = str(site_summary.get("id", "")).strip()
    drive_folder_url = str(site_summary.get("drive_folder_url", "")).strip()

    if not drive_folder_url:
        return {"dd_report_republish": "skipped_no_drive_folder"}

    site_folder_id = extract_folder_id_from_url(drive_folder_url) or ""
    if not site_folder_id:
        return {"dd_report_republish": "skipped_bad_drive_url"}

    existing = _find_existing_dd_report(gc, site_folder_id)
    if existing is None:
        logger.info(
            "DD Report republish skipped for site=%s — no existing report; "
            "daily/inbox path will create it.",
            site_name,
        )
        return {"dd_report_republish": "skipped_no_existing_report"}

    # Dedup key: synthesize a stable run identifier when RayCon doesn't
    # supply one so we still get per-run dedup rather than per-poll.
    run_key = raycon_run_id.strip() or f"unknown@{now.date().isoformat()}"
    state_key = f"{site_id or site_name}:{run_key}"
    last_iso = republish_state.get(state_key)
    last_dt = _parse_iso(last_iso) if last_iso else None
    if last_dt is not None and (now - last_dt) < force_after:
        logger.info(
            "DD Report republish deduped for site=%s run=%s (last %s ago)",
            site_name,
            run_key,
            now - last_dt,
        )
        return {
            "dd_report_republish": "deduped",
            "raycon_run_id": run_key,
            "last_republish": last_iso,
        }

    if dry_run:
        return {
            "dd_report_republish": "would_republish",
            "raycon_run_id": run_key,
            "existing_dd_report_id": existing.get("id"),
        }

    site_address = str(site_summary.get("address", "")).strip() or None
    match_terms = build_site_match_terms(site_name, site_address)
    # Thread the same Wrike-derived fields the daily/inbox callers pass —
    # otherwise the regenerated DD Report email loses the P1 CC and the
    # dashboard publish loses the W74/W81 ratings + Wrike createdDate.
    record_for_extract = {"customFields": site_summary.get("custom_fields", [])}
    try:
        result = process_site_pipeline(
            gc,
            site_name,
            drive_folder_url,
            match_terms,
            shared_cache,
            system_prompt,
            settings,
            p1_email=site_summary.get("p1_assignee_email"),
            site_address=site_address,
            p1_name=site_summary.get("p1_assignee_name"),
            school_feasibility=extract_school_feasibility_from_record(
                record_for_extract
            ),
            timeline_confidence=extract_timeline_confidence_from_record(
                record_for_extract
            ),
            wrike_created_at=site_summary.get("created_date") or None,
            force_regenerate=True,
        )
    except Exception as e:
        logger.error(
            "DD Report republish failed for site=%s run=%s: %s",
            site_name,
            run_key,
            e,
        )
        return {"dd_report_republish": "failed", "reason": str(e)}

    # Record the attempt regardless of result.status so a permanently
    # waiting_on_docs site doesn't re-enter the pipeline on every cron
    # tick. A future scenario JSON (different run_id) will get a fresh
    # state_key and try again.
    republish_state[state_key] = now.isoformat()
    logger.info(
        "DD Report republish for site=%s run=%s status=%s",
        site_name,
        run_key,
        result.status,
    )
    return {
        "dd_report_republish": "republished",
        "raycon_run_id": run_key,
        "pipeline_status": result.status,
        "doc_url": getattr(result, "doc_url", "") or "",
    }


def _process_site(
    gc: GoogleClient,
    site_summary: dict[str, Any],
    *,
    dry_run: bool,
    alert_after: timedelta,
    dispatch_state: dict[str, dict[str, Any]] | None = None,
    redispatch_after: timedelta = timedelta(minutes=30),
    skip_dd_republish: bool = False,
    dd_republish_callback: Any = None,
) -> dict[str, Any]:
    """Return a per-site result row for the run summary.

    When a Block Plan is present but ``raycon_scenario.json`` has not yet
    appeared, this function will additionally call ``post_raycon_job``
    via :func:`_dispatch_raycon_job` to cover Block Plans that arrived
    via a non-email path (manual upload, recovery). ``dispatch_state``
    is mutated in place on a successful dispatch and the caller is
    expected to persist it at the end of the run.
    """
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
        now = datetime.now(timezone.utc)
        bp_modified = _parse_iso(str(block_plan.get("modifiedTime", "")))
        is_stuck = (
            bp_modified is not None and (now - bp_modified) > alert_after
        )

        # Safety-net dispatch: try to (re-)fire RayCon for this Block Plan
        # before we decide whether to alert. If RayCon is just slow, this
        # is a no-op on their side (idempotent on block_plan_file_id).
        # If the email path never reached RayCon, this is the recovery.
        dispatch_result: dict[str, Any] = {}
        if dispatch_state is not None:
            dispatch_result = _dispatch_raycon_job(
                site_summary,
                block_plan,
                m1_folder_id,
                dispatch_state,
                dry_run=dry_run,
                redispatch_after=redispatch_after,
                now=now,
            )

        # Successful dispatch is the headline outcome for this site.
        if dispatch_result.get("dispatched"):
            row = {
                "site": site_name,
                "dispatched": True,
                "raycon_run_id": dispatch_result.get("raycon_run_id"),
                "status": dispatch_result.get("status"),
                "block_plan_file_id": dispatch_result.get("block_plan_file_id"),
            }
            if bp_modified is not None:
                row["block_plan_modified"] = bp_modified.isoformat()
            return row

        # Dispatch errored — surface as an error row so it lands in the
        # error alert path and we can see it in Chat.
        if dispatch_result.get("dispatch_error"):
            return {
                "site": site_name,
                "error": f"raycon dispatch: {dispatch_result['dispatch_error']}",
            }

        # Dispatch skipped (dedup or dry_run) → fall through to the
        # original stuck-vs-in-flight logic. If the Block Plan has been
        # sitting >alert_after with no scenario, alert.
        if is_stuck:
            return {
                "site": site_name,
                "alert": f"no raycon_scenario.json after {alert_after}",
                "block_plan_modified": bp_modified.isoformat() if bp_modified else None,
                "dispatch_skipped": dispatch_result.get("dispatch_skipped"),
            }
        return {
            "site": site_name,
            "skipped": "scenario JSON not yet present",
            "dispatch_skipped": dispatch_result.get("dispatch_skipped"),
        }

    # Scenario JSON is here — but did the run actually succeed? RayCon
    # writes the same file with ``status: "failed"`` (and ``validation.passed:
    # false``) when it can't compute scenarios. Publishing a Doc in that
    # state would render an empty/zero-dollar scenario the dashboard
    # treats as authoritative. Surface the failure as an alert row instead
    # so EDU Ops sees it in Chat and we don't pollute the site's M1 folder.
    if raycon_payload_failed(scenario):
        report_fields = raycon_scenario_to_report_fields(scenario)
        reason = report_fields.get("exec.raycon_failure_reason", "") or "unspecified"
        return {
            "site": site_name,
            "alert": f"raycon run failed: {reason}",
            "raycon_status": report_fields.get("exec.raycon_status", ""),
            "raycon_run_id": report_fields.get("exec.raycon_run_id", ""),
            "json_modified": scenario.get("_drive_modified_time", ""),
        }

    # Scenario JSON is here and the run succeeded — publish the report
    # Doc if missing or stale.
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

    row: dict[str, Any] = {
        "site": site_name,
        "published": True,
        "doc_url": result.get("doc_url"),
    }

    # Event-driven DD Report republish (Rec. 1). Fires AFTER the RayCon
    # Scenario Doc publish has succeeded, so any failure here cannot
    # undo that. Wrapped in a guard so an exception in the helper never
    # crashes _process_site.
    if not skip_dd_republish and dd_republish_callback is not None:
        try:
            raycon_run_id = str(
                payload.get("report_data_fields", {}).get(
                    "exec.raycon_run_id", ""
                )
                or scenario.get("raycon_run_id", "")
            ).strip()
            republish_result = dd_republish_callback(
                gc=gc,
                site_summary=site_summary,
                raycon_run_id=raycon_run_id,
                dry_run=dry_run,
            )
        except Exception as e:
            logger.error(
                "DD Report republish callback raised for site=%s: %s",
                site_name,
                e,
            )
            republish_result = {"dd_report_republish": "failed", "reason": str(e)}
        if republish_result:
            row.update(republish_result)

    return row


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
    parser.add_argument(
        "--redispatch-after-minutes",
        type=int,
        default=30,
        help=(
            "How long to wait before re-dispatching the same block_plan_file_id "
            "to RayCon's /v1/jobs. Smaller than --alert-after-minutes so we get "
            "at least one re-fire before the stuck-site alert triggers."
        ),
    )
    parser.add_argument(
        "--skip-dd-republish",
        action="store_true",
        help=(
            "Disable the event-driven DD Report republish that fires when a "
            "RayCon Scenario Doc is published. Emergency override; default "
            "behavior is to republish."
        ),
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
    redispatch_after = timedelta(minutes=args.redispatch_after_minutes)

    dispatch_state = _load_dispatch_state()
    republish_state = _load_republish_state()

    # Build the DD Report republish callback once per run so the agent
    # system prompt + shared-folder cache (both expensive to compute) are
    # only paid for when at least one scenario actually publishes.
    _shared_cache: dict[str, list[dict[str, Any]]] | None = None
    _system_prompt: str | None = None

    def _dd_republish_callback(
        *,
        gc: GoogleClient,
        site_summary: dict[str, Any],
        raycon_run_id: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        nonlocal _shared_cache, _system_prompt
        if _system_prompt is None:
            prompt_path = _project_root / "docs" / "prompts" / "prompt_v3.md"
            if not prompt_path.exists():
                logger.error(
                    "DD Report republish: system prompt missing at %s", prompt_path
                )
                return {
                    "dd_report_republish": "failed",
                    "reason": f"system prompt missing at {prompt_path}",
                }
            _system_prompt = prompt_path.read_text(encoding="utf-8")
        if _shared_cache is None:
            _shared_cache = list_shared_folders_once(gc)
        return _republish_dd_report_if_present(
            gc,
            site_summary,
            raycon_run_id,
            settings=settings,
            system_prompt=_system_prompt,
            shared_cache=_shared_cache,
            republish_state=republish_state,
            dry_run=dry_run,
        )

    results: list[dict[str, Any]] = []
    for site_summary in summaries:
        try:
            row = _process_site(
                gc,
                site_summary,
                dry_run=args.dry_run,
                alert_after=alert_after,
                dispatch_state=dispatch_state,
                redispatch_after=redispatch_after,
                skip_dd_republish=args.skip_dd_republish,
                dd_republish_callback=_dd_republish_callback,
            )
        except Exception as e:
            logger.exception("Unhandled error for site '%s'", site_summary.get("title"))
            row = {"site": site_summary.get("title"), "error": str(e)}
        results.append(row)
        logger.info("%s", json.dumps(row, default=str))

    # Persist dispatch dedup state once at end of run (after all sites
    # processed) so partial-run state still gets saved on the next run.
    if not args.dry_run:
        _save_dispatch_state(dispatch_state)
        _save_republish_state(republish_state)

    published = [r for r in results if r.get("published")]
    dispatched = [r for r in results if r.get("dispatched")]
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
        "Run complete: published=%d dispatched=%d alerts=%d errors=%d total_sites=%d",
        len(published),
        len(dispatched),
        len(alerts),
        len(errors),
        len(results),
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
