#!/usr/bin/env python3
"""raycon_followup.py — pick up RayCon scenario JSON files and publish reports.

When a Block Plan lands in a site's M1 folder, ``inbox_scanner`` pings
RayCon's ``/v1/jobs`` endpoint asynchronously. RayCon then writes a
single ``raycon_scenario.json`` file back into the same M1 folder.

This script runs on a 5-minute cadence (``raycon-followup.yml``) and:

  1. Iterates every active Rhodes site with a linked Drive folder.
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
     (default 60), records a Rhodes AutomationEvent for the site owner
     and falls back to Google Chat when no owner can be notified.

The script is idempotent and safe to re-run. Re-runs only re-publish the
RayCon Scenario Doc when the JSON's ``modifiedTime`` is newer than the
Doc's ``modifiedTime``.

Run:
    uv run python scripts/raycon_followup.py             # all active sites
    uv run python scripts/raycon_followup.py --site Keller  # single site
    uv run python scripts/raycon_followup.py --dry-run   # detect only

Env:
    OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET / OAUTH_REFRESH_TOKEN
    GOOGLE_DRIVE_ROOT_FOLDER_ID
    GOOGLE_CHAT_WEBHOOK_URL  (optional; fallback alert sink)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

import requests  # noqa: E402

from due_diligence_reporter.automation_event import (  # noqa: E402
    build_raycon_followup_alert_event,
)
from due_diligence_reporter.config import get_settings  # noqa: E402
from due_diligence_reporter.dd_republish import (  # noqa: E402
    DD_REPUBLISH_STATE_PATH,
    REASON_RAYCON,
    RepublishOutcome,
    find_existing_dd_report,
    maybe_republish_dd_report,
    record_dd_republish_failure_event,
)
from due_diligence_reporter.dd_republish_state_store import (  # noqa: E402
    build_dd_republish_state_store,
)
from due_diligence_reporter.google_client import GoogleClient  # noqa: E402
from due_diligence_reporter.m1_lookup import _resolve_m1_folder  # noqa: E402
from due_diligence_reporter.raycon_client import (  # noqa: E402
    RAYCON_IN_PROGRESS_STATUSES,
    RAYCON_TERMINAL_STATUSES,
    RayConSchemaError,
    get_raycon_job_status,
    post_raycon_job,
    raycon_payload_failed,
    raycon_scenario_to_report_fields,
    read_raycon_scenario_from_m1,
)
from due_diligence_reporter.raycon_runtime_state_store import (  # noqa: E402
    build_raycon_alert_state_store,
    build_raycon_dispatch_state_store,
)
from due_diligence_reporter.report_pipeline import (  # noqa: E402
    list_shared_folders_once,
    process_site_pipeline,
)
from due_diligence_reporter.rhodes import (  # noqa: E402
    RhodesError,
    add_rhodes_site_note,
    list_rhodes_site_records,
)
from due_diligence_reporter.rhodes_events import (  # noqa: E402
    record_rhodes_automation_event,
    should_alert_google_chat,
)
from due_diligence_reporter.server import save_skill_report  # noqa: E402
from due_diligence_reporter.utils import (  # noqa: E402
    extract_folder_id_from_url,
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


_CALLBACK_INPUT_RE = re.compile(r"^[A-Za-z0-9_:\-]+$")


def _validate_callback_input(
    name: str,
    value: str | None,
    *,
    max_len: int = 64,
    pattern: re.Pattern[str] = _CALLBACK_INPUT_RE,
) -> str | None:
    """Reject malformed RayCon callback inputs (workflow_dispatch surface).

    Returns the value unchanged when valid, ``None`` when malformed (or
    when the input was already None/empty). Drops malformed values
    silently — the cron safety net always re-runs, so a rejected
    callback degrades to "next scheduled run picks it up" rather than
    a script-killing crash.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > max_len or not pattern.fullmatch(value):
        logger.warning(
            "Rejecting malformed %s callback input (len=%d)", name, len(value)
        )
        return None
    return value


def _filename_matches_block_plan(name: str) -> bool:
    """Return True if ``name`` (already lowercased) looks like a Block Plan.

    Recognized aliases: "Block Plan", "Preliminary Floor Plan(s)", and
    "PFP". All three refer to the same artifact and must route to the
    same downstream RayCon dispatch path.
    """
    if any(hint in name for hint in BLOCK_PLAN_FILENAME_HINTS):
        return True
    return any(pat.search(name) for pat in BLOCK_PLAN_PFP_PATTERNS)

# Persisted map of {dedupe_key: ISO8601 timestamp of last owner/Chat alert}.
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
# pipeline twice for the same RayCon scenario.
#
# Pre-Rec.3, this file lived at ``.raycon_dd_republish_state.json`` and
# was keyed only on ``site_id:run_id``. Post-Rec.3 the canonical state
# file is the shared ``.dd_republish_state.json`` (see ``dd_republish``)
# keyed on ``site_id:reason:fingerprint`` so vendor SIR + Building
# Inspection arrivals dedup against the same store. The legacy path is
# read once at startup for migration and never written again.
LEGACY_DD_REPUBLISH_DEDUP_PATH = _project_root / ".raycon_dd_republish_state.json"
DD_REPUBLISH_DEDUP_PATH = DD_REPUBLISH_STATE_PATH

# Force-republish window: regenerate at least once per N hours for the same
# (site, raycon_run_id) pair if conditions still hold. Mirrors the
# ``--redispatch-after-minutes`` shape used by the safety-net dispatcher.
DD_REPUBLISH_FORCE_AFTER = timedelta(hours=12)


# ---------------------------------------------------------------------------


def _load_alert_state(path: Path = ALERT_DEDUP_PATH) -> dict[str, str]:
    """Load the {site_name: last_alert_iso} dedup map. Returns {} on any error."""
    return build_raycon_alert_state_store(path).load()


def _save_alert_state(state: dict[str, str], path: Path = ALERT_DEDUP_PATH) -> None:
    """Persist the dedup map. Best-effort; logs but does not raise."""
    build_raycon_alert_state_store(path).save(state)


def _load_dispatch_state(
    path: Path = DISPATCH_DEDUP_PATH,
) -> dict[str, dict[str, Any]]:
    """Load the {block_plan_file_id: {...}} dispatch dedup map.

    Returns ``{}`` on missing file or any read/parse error so a corrupt
    state file never blocks the run.
    """
    return build_raycon_dispatch_state_store(path).load()


def _save_dispatch_state(
    state: dict[str, dict[str, Any]], path: Path = DISPATCH_DEDUP_PATH
) -> None:
    """Persist the dispatch dedup map. Best-effort; logs but does not raise."""
    build_raycon_dispatch_state_store(path).save(state)


def _load_republish_state(
    path: Path = DD_REPUBLISH_DEDUP_PATH,
    *,
    legacy_path: Path = LEGACY_DD_REPUBLISH_DEDUP_PATH,
) -> dict[str, str]:
    """Load the shared DD republish dedup map.

    Delegates to ``dd_republish.load_state``, which now owns the
    one-shot legacy migration so both raycon_followup and scan_inbox
    pick it up. Kept as a thin wrapper so existing tests that patch
    ``scripts.raycon_followup._load_republish_state`` still observe
    the call.
    """
    return build_dd_republish_state_store(path, legacy_path=legacy_path).load()


def _save_republish_state(
    state: dict[str, str], path: Path = DD_REPUBLISH_DEDUP_PATH
) -> None:
    """Persist the DD republish dedup map. Best-effort; logs but does not raise."""
    build_dd_republish_state_store(path).save(state)


def _filter_dedup_alerts(
    alerts: list[dict[str, Any]],
    state: dict[str, str],
    *,
    now: datetime | None = None,
    window: timedelta = ALERT_DEDUP_WINDOW,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Drop alerts recently notified within ``window``.

    Return ``(fresh_alerts, updated_state)``. Rows may provide
    ``alert_dedup_key``; otherwise the site name remains the key for backward
    compatibility with the existing stuck-site suppression file.
    """
    now = now or datetime.now(UTC)
    fresh: list[dict[str, Any]] = []
    new_state = dict(state)
    for row in alerts:
        site = str(row.get("site", "")).strip()
        dedupe_key = _alert_dedup_key(row)
        if not site or not dedupe_key:
            continue
        last_iso = state.get(dedupe_key)
        last_dt = _parse_iso(last_iso) if last_iso else None
        if last_dt is not None and (now - last_dt) < window:
            continue
        fresh.append(row)
        new_state[dedupe_key] = now.isoformat()
    return fresh, new_state


def _fresh_dedup_alerts(
    alerts: list[dict[str, Any]],
    state: dict[str, str],
    *,
    now: datetime | None = None,
    window: timedelta = ALERT_DEDUP_WINDOW,
) -> list[dict[str, Any]]:
    """Return alerts that are outside the dedup window without mutating state."""
    now = now or datetime.now(UTC)
    fresh: list[dict[str, Any]] = []
    for row in alerts:
        site = str(row.get("site", "")).strip()
        dedupe_key = _alert_dedup_key(row)
        if not site or not dedupe_key:
            continue
        last_iso = state.get(dedupe_key)
        last_dt = _parse_iso(last_iso) if last_iso else None
        if last_dt is not None and (now - last_dt) < window:
            continue
        fresh.append(row)
    return fresh


def _raycon_followup_notification_succeeded(row: dict[str, Any]) -> bool:
    event_status = row.get("raycon_followup_event")
    if not isinstance(event_status, dict):
        return False
    if (
        event_status.get("status") == "created"
        and event_status.get("owner_notification") == "mentioned"
        and str(event_status.get("rhodes_note_id") or "").strip()
    ):
        return True
    google_chat = event_status.get("google_chat")
    return isinstance(google_chat, dict) and google_chat.get("status") == "posted"


def _row_has_site_owner(row: dict[str, Any]) -> bool:
    return bool(
        str(row.get("p1_assignee_user_id") or "").strip()
        or str(row.get("p1_assignee_email") or "").strip()
    )


def _mark_notified_alerts(
    alerts: list[dict[str, Any]],
    state: dict[str, str],
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    """Advance dedup state only for rows that notified an owner or Chat."""
    now = now or datetime.now(UTC)
    new_state = dict(state)
    for row in alerts:
        dedupe_key = _alert_dedup_key(row)
        if not dedupe_key or not _raycon_followup_notification_succeeded(row):
            continue
        new_state[dedupe_key] = now.isoformat()
    return new_state


def _alert_dedup_key(row: dict[str, Any]) -> str:
    explicit_key = str(row.get("alert_dedup_key") or "").strip()
    if explicit_key:
        return explicit_key
    return str(row.get("site") or "").strip()


def _error_alert_dedup_key(row: dict[str, Any]) -> str:
    site = str(row.get("site") or "").strip()
    message = str(row.get("error") or "").strip()
    if not site or not message:
        return ""
    return f"{site}:error:{message[:250]}"


def _is_failed_scenario_alert(row: dict[str, Any]) -> bool:
    status = str(row.get("raycon_status") or "").strip()
    message = str(row.get("alert") or "").strip()
    return bool(status) or message.startswith("raycon run failed:")


def _site_filter(site_summary: dict[str, Any], needle: str | None) -> bool:
    if not needle:
        return True
    needle_lc = needle.lower()
    title = str(site_summary.get("title", "")).lower()
    address = str(site_summary.get("address", "")).lower()
    return needle_lc in title or needle_lc in address


def _folder_url(folder: dict[str, Any]) -> str:
    link = str(folder.get("webViewLink") or "").strip()
    if link:
        return link
    folder_id = str(folder.get("id") or "").strip()
    return f"https://drive.google.com/drive/folders/{folder_id}" if folder_id else ""


def _site_summary_from_folder(folder: dict[str, Any]) -> dict[str, Any]:
    folder_id = str(folder.get("id") or "").strip()
    title = str(folder.get("name") or "").strip()
    return {
        "id": folder_id,
        "title": title,
        "address": "",
        "drive_folder_id": folder_id,
        "drive_folder_url": _folder_url(folder),
        "site_metadata_source": "drive_folder",
    }


def _site_summary_from_rhodes_record(record: dict[str, Any]) -> dict[str, Any]:
    drive_folder_url = str(record.get("drive_folder_url") or "").strip()
    drive_folder_id = str(record.get("drive_folder_id") or "").strip()
    if not drive_folder_id:
        drive_folder_id = extract_folder_id_from_url(drive_folder_url) or ""
    site_id = str(record.get("site_id") or record.get("id") or "").strip()
    title = str(record.get("title") or record.get("name") or "").strip()
    slug = str(record.get("slug") or "").strip()
    return {
        "id": site_id,
        "site_id": site_id,
        "title": title,
        "name": title,
        "slug": slug,
        "site_slug": slug,
        "address": str(record.get("address") or record.get("site_address") or "").strip(),
        "drive_folder_id": drive_folder_id,
        "drive_folder_url": drive_folder_url,
        "p1_assignee_name": str(record.get("p1_assignee_name") or "").strip(),
        "p1_assignee_email": str(record.get("p1_assignee_email") or "").strip(),
        "p1_assignee_user_id": str(record.get("p1_assignee_user_id") or "").strip(),
        "created_date": str(record.get("created_date") or "").strip(),
        "site_metadata_source": "rhodes",
    }


def _site_identity_values(site_summary: dict[str, Any]) -> set[str]:
    values = {
        str(site_summary.get("id") or "").strip(),
        str(site_summary.get("site_id") or "").strip(),
        str(site_summary.get("drive_folder_id") or "").strip(),
    }
    drive_folder_id = extract_folder_id_from_url(
        str(site_summary.get("drive_folder_url") or "").strip()
    )
    if drive_folder_id:
        values.add(drive_folder_id)
    return {value for value in values if value}


def _site_id_matches(site_summary: dict[str, Any], target_id: str) -> bool:
    return target_id.strip() in _site_identity_values(site_summary)


def _load_site_summaries(
    gc: GoogleClient,
    google_drive_root_folder_id: str,
    *,
    target_site_id: str | None = None,
) -> list[dict[str, Any]]:
    if target_site_id:
        try:
            rhodes_records = list_rhodes_site_records(site_ids=[target_site_id])
        except RhodesError as exc:
            logger.warning(
                "Direct Rhodes lookup failed for RayCon callback site_id=%s; "
                "falling back to full inventory: %s",
                target_site_id,
                exc,
            )
        else:
            summaries = [
                _site_summary_from_rhodes_record(record)
                for record in rhodes_records
                if str(record.get("drive_folder_url") or "").strip()
            ]
            if summaries:
                logger.info(
                    "Loaded Rhodes site record for RayCon callback site_id=%s",
                    target_site_id,
                )
                return summaries
            logger.warning(
                "Direct Rhodes lookup for RayCon callback site_id=%s returned "
                "no Drive-linked site; falling back to full inventory",
                target_site_id,
            )

    try:
        rhodes_records = list_rhodes_site_records()
    except RhodesError as exc:
        logger.warning(
            "Rhodes site inventory unavailable for RayCon follow-up; "
            "falling back to Drive folder scan: %s",
            exc,
        )
    else:
        summaries = [
            _site_summary_from_rhodes_record(record)
            for record in rhodes_records
            if str(record.get("drive_folder_url") or "").strip()
        ]
        if summaries:
            logger.info(
                "Loaded %d Rhodes site record(s) for RayCon follow-up",
                len(summaries),
            )
            return summaries
        logger.warning(
            "Rhodes site inventory returned no Drive-linked sites; "
            "falling back to Drive folder scan"
        )

    site_folders = gc.list_subfolders(google_drive_root_folder_id)
    return [_site_summary_from_folder(folder) for folder in site_folders]


def _find_block_plan(
    gc: GoogleClient,
    m1_folder_id: str,
    *,
    m1_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return the most recently modified Block Plan PDF in M1, or None."""
    candidate: dict[str, Any] | None = None
    files = m1_files if m1_files is not None else gc.list_files_in_folder(m1_folder_id)
    for f in files:
        name = str(f.get("name", "")).lower()
        if not _filename_matches_block_plan(name):
            continue
        if candidate is None or str(f.get("modifiedTime", "")) > str(
            candidate.get("modifiedTime", "")
        ):
            candidate = f
    return candidate


def _find_published_doc(
    gc: GoogleClient,
    m1_folder_id: str,
    site_name: str,
    *,
    m1_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return the existing RayCon Scenario Doc for a site, or None."""
    target = f"{PUBLISHED_DOC_PREFIX} - {site_name}"
    files = m1_files if m1_files is not None else gc.list_files_in_folder(m1_folder_id)
    for f in files:
        if str(f.get("name", "")).strip() == target:
            return f
    return None


def _post_chat(webhook_url: str, text: str) -> None:
    try:
        requests.post(webhook_url, json={"text": text}, timeout=15).raise_for_status()
    except Exception as e:
        logger.warning("Failed to post Google Chat alert: %s", e)


def _with_site_context(
    row: dict[str, Any],
    site_summary: dict[str, Any],
) -> dict[str, Any]:
    enriched = dict(row)
    site_name = str(site_summary.get("title") or site_summary.get("name") or "").strip()
    if site_name:
        enriched.setdefault("site", site_name)

    site_id = str(site_summary.get("site_id") or "").strip()
    if not site_id and site_summary.get("site_metadata_source") == "rhodes":
        site_id = str(site_summary.get("id") or "").strip()
    if site_id:
        enriched.setdefault("site_id", site_id)

    for key in (
        "drive_folder_url",
        "drive_folder_id",
        "site_slug",
        "p1_assignee_user_id",
        "p1_assignee_email",
        "p1_assignee_name",
    ):
        value = str(site_summary.get(key) or "").strip()
        if value:
            enriched.setdefault(key, value)
    return enriched


def _raycon_followup_run_id(
    callback_run_id: str | None,
    *,
    now: datetime | None = None,
) -> str:
    clean_run_id = str(callback_run_id or "").strip()
    if clean_run_id:
        return clean_run_id
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d%H%M%S")
    return f"raycon-followup-{stamp}"


def _record_raycon_followup_event(
    row: dict[str, Any],
    *,
    run_id: str,
    alert_type: str,
    message: str,
    extra_mention_user_ids: list[str] | None = None,
) -> tuple[dict[str, Any], str]:
    event = build_raycon_followup_alert_event(
        site_id=str(row.get("site_id") or "").strip(),
        site_name=str(row.get("site") or "").strip(),
        run_id=run_id,
        alert_type=alert_type,
        message=message,
        drive_folder_url=str(row.get("drive_folder_url") or "").strip(),
        block_plan_file_id=str(row.get("block_plan_file_id") or "").strip(),
        raycon_run_id=str(row.get("raycon_run_id") or "").strip(),
    )
    return record_rhodes_automation_event(
        event,
        owner_user_id=str(row.get("p1_assignee_user_id") or "").strip(),
        owner_email=str(row.get("p1_assignee_email") or "").strip(),
        site_slug=str(row.get("site_slug") or "").strip(),
        extra_mention_user_ids=extra_mention_user_ids,
        add_note=add_rhodes_site_note,
    )


def _notify_raycon_followup_rows(
    rows: list[dict[str, Any]],
    settings: Any,
    *,
    run_id: str,
    alert_type: str,
    message_field: str,
    heading: str,
) -> list[dict[str, Any]]:
    chat_bodies: list[str] = []
    chat_rows: list[dict[str, Any]] = []
    extra_mention_user_ids = _csv_values(
        getattr(settings, "raycon_followup_extra_mention_user_ids", "")
    )
    for row in rows:
        message = str(row.get(message_field) or "").strip()
        if not message:
            continue
        event_status, body = _record_raycon_followup_event(
            row,
            run_id=run_id,
            alert_type=alert_type,
            message=message,
            extra_mention_user_ids=extra_mention_user_ids,
        )
        row["raycon_followup_event"] = event_status
        logger.info(
            "RayCon follow-up notification status: %s",
            json.dumps(_notification_status_summary(row, event_status), default=str),
        )
        if should_alert_google_chat(event_status):
            chat_bodies.append(body)
            chat_rows.append(row)

    if not chat_bodies:
        return [
            row
            for row in rows
            if row.get(message_field) and not _raycon_followup_notification_succeeded(row)
        ]

    webhook_url = str(getattr(settings, "google_chat_webhook_url", "") or "").strip()
    chat_result: dict[str, str]
    if webhook_url:
        _post_chat(webhook_url, "\n\n".join([heading, *chat_bodies]))
        chat_result = {"status": "posted"}
    else:
        chat_result = {"status": "skipped", "reason": "missing_webhook"}

    for row in chat_rows:
        stored_event_status = row.get("raycon_followup_event")
        if isinstance(stored_event_status, dict):
            stored_event_status["google_chat"] = chat_result
            logger.info(
                "RayCon follow-up Chat fallback status: %s",
                json.dumps(
                    _notification_status_summary(row, stored_event_status),
                    default=str,
                ),
            )

    return [
        row
        for row in rows
        if row.get(message_field) and not _raycon_followup_notification_succeeded(row)
    ]


def _notification_status_summary(
    row: dict[str, Any],
    event_status: dict[str, Any],
) -> dict[str, Any]:
    google_chat = event_status.get("google_chat")
    return {
        "site": row.get("site"),
        "site_id": row.get("site_id"),
        "owner_assigned": _row_has_site_owner(row),
        "status": event_status.get("status"),
        "reason": event_status.get("reason"),
        "owner_notification": event_status.get("owner_notification"),
        "rhodes_note_id": event_status.get("rhodes_note_id"),
        "mentioned_user_ids": event_status.get("mentioned_user_ids"),
        "google_chat": google_chat if isinstance(google_chat, dict) else None,
    }


def _csv_values(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


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
    now = now or datetime.now(UTC)
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

    raycon_run_id = str(response.get("raycon_run_id", "") or "").strip()
    job_id = str(response.get("job_id", "")).strip()
    status_url = str(response.get("status_url", "")).strip()
    status = str(response.get("status", "accepted"))
    logger.info(
        "RayCon safety-net dispatch for site=%s block_plan_file_id=%s "
        "job_id=%s run_id=%s status=%s",
        site_name,
        block_plan_file_id,
        job_id or "(unknown)",
        raycon_run_id or "(unknown)",
        status,
    )

    prior_count = int((prior or {}).get("count", 0))
    dispatch_state[block_plan_file_id] = {
        "site": site_name,
        "last_dispatch": now.isoformat(),
        "count": prior_count + 1,
        "job_id": job_id or None,
        "raycon_run_id": raycon_run_id or None,
        "status_url": status_url or None,
        "status": status,
        "raycon_job": response,
    }
    return {
        "dispatched": True,
        "job_id": job_id,
        "raycon_run_id": raycon_run_id,
        "status_url_present": bool(status_url),
        "status": status,
        "block_plan_file_id": block_plan_file_id,
    }


def _poll_dispatch_state_status(
    block_plan_file_id: str,
    dispatch_state: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Poll a prior RayCon job status URL and merge non-sensitive metadata."""
    prior = dispatch_state.get(block_plan_file_id)
    if not prior:
        return {}
    status_url = str(prior.get("status_url", "") or "").strip()
    if not status_url:
        return {}
    try:
        status_response = get_raycon_job_status(status_url)
    except Exception as e:
        prior["status_poll_error"] = str(e)
        return {"status_poll_error": str(e)}

    status = str(status_response.get("status", "") or "").strip().lower()
    if status:
        prior["status"] = status
    for key in (
        "job_id",
        "raycon_run_id",
        "idempotency_key",
        "retry_after_seconds",
        "result_filename",
        "drive_action",
    ):
        if key in status_response:
            prior[key] = status_response.get(key)
    drive_file = status_response.get("drive_file")
    if isinstance(drive_file, dict):
        prior["drive_file_id"] = drive_file.get("id")
    return {"status": status, "status_response": status_response}


def _failed_scenario_base_row(
    site_name: str,
    scenario: dict[str, Any],
    report_fields: dict[str, str],
) -> dict[str, Any]:
    reason = report_fields.get("exec.raycon_failure_reason", "") or "unspecified"
    run_id = report_fields.get("exec.raycon_run_id", "")
    modified = str(scenario.get("_drive_modified_time", "") or "")
    return {
        "site": site_name,
        "alert": f"raycon run failed: {reason}",
        "alert_dedup_key": (
            f"{site_name}:failed_scenario:{run_id or reason[:120]}:{modified}:"
            "owner_note_v2"
        ),
        "raycon_status": report_fields.get("exec.raycon_status", ""),
        "raycon_run_id": run_id,
        "json_modified": modified,
    }


def _failed_scenario_dispatch_row(
    site_name: str,
    reason: str,
    dispatch_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "site": site_name,
        "dispatched": True,
        "dispatch_reason": "failed_scenario_retry",
        "previous_failure": reason,
        "job_id": dispatch_result.get("job_id"),
        "retry_raycon_run_id": dispatch_result.get("raycon_run_id"),
        "status": dispatch_result.get("status"),
        "status_url_present": dispatch_result.get("status_url_present"),
        "block_plan_file_id": dispatch_result.get("block_plan_file_id"),
    }


def _failed_scenario_status_row(
    site_name: str,
    block_plan_file_id: str,
    base_row: dict[str, Any],
    status: str,
) -> dict[str, Any] | None:
    if status in RAYCON_IN_PROGRESS_STATUSES:
        return {
            **base_row,
            "site": site_name,
            "skipped": f"raycon recovery job {status}",
            "block_plan_file_id": block_plan_file_id,
            "raycon_run_id": base_row["raycon_run_id"],
            "json_modified": base_row["json_modified"],
        }
    if status == "completed":
        return {
            **base_row,
            "alert": (
                "raycon recovery completed but failed raycon_scenario.json "
                "is still visible in M1"
            ),
            "block_plan_file_id": block_plan_file_id,
        }
    return None


def _handle_failed_scenario(
    site_summary: dict[str, Any],
    site_name: str,
    block_plan: dict[str, Any],
    m1_folder_id: str,
    scenario: dict[str, Any],
    report_fields: dict[str, str],
    *,
    dispatch_state: dict[str, dict[str, Any]] | None,
    dry_run: bool,
    redispatch_after: timedelta,
    retry_failed_scenario: bool = True,
) -> dict[str, Any]:
    base_row = _failed_scenario_base_row(site_name, scenario, report_fields)
    reason = base_row["alert"].removeprefix("raycon run failed: ")
    block_plan_file_id = str(block_plan.get("id", "")).strip()
    if dispatch_state is None or not block_plan_file_id:
        return base_row

    if not retry_failed_scenario:
        base_row["block_plan_file_id"] = block_plan_file_id
        base_row["dispatch_skipped"] = "callback_terminal_status"
        return base_row

    status_result = _poll_dispatch_state_status(block_plan_file_id, dispatch_state)
    status = str(status_result.get("status", "") or "").strip().lower()
    status_row = _failed_scenario_status_row(
        site_name, block_plan_file_id, base_row, status
    )
    if status_row is not None:
        return status_row

    dispatch_result = _dispatch_raycon_job(
        site_summary,
        block_plan,
        m1_folder_id,
        dispatch_state,
        dry_run=dry_run,
        redispatch_after=redispatch_after,
    )
    if dispatch_result.get("dispatched"):
        return {
            **base_row,
            **_failed_scenario_dispatch_row(site_name, reason, dispatch_result),
        }
    if dispatch_result.get("dispatch_error"):
        return {
            "site": site_name,
            "error": f"raycon failed-scenario retry: {dispatch_result['dispatch_error']}",
            "previous_failure": reason,
        }
    if dispatch_result.get("dispatch_skipped"):
        base_row["dispatch_skipped"] = dispatch_result.get("dispatch_skipped")
    return base_row


def _find_existing_dd_report(
    gc: GoogleClient, site_folder_id: str
) -> dict[str, Any] | None:
    """Return the most recently modified DD Report Doc in the site folder.

    Thin wrapper over ``dd_republish.find_existing_dd_report``. Kept here
    so existing tests that patch ``scripts.raycon_followup._find_existing_dd_report``
    continue to work.
    """
    return find_existing_dd_report(gc, site_folder_id)


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
    drive_modified_time: str = "",
    now: datetime | None = None,
    force_after: timedelta = DD_REPUBLISH_FORCE_AFTER,
    failure_event_recorder: Any = None,
) -> dict[str, Any]:
    """Regenerate the DD Report on top of an existing one when RayCon answers.

    Thin RayCon-flavored wrapper over
    :func:`due_diligence_reporter.dd_republish.maybe_republish_dd_report`
    (Rec. 3). Preserves the legacy decision strings the existing tests
    and on-call runbooks rely on (``republished``, ``deduped``, etc.)
    while delegating the actual idempotence + pipeline call to the
    shared helper so vendor SIR + Building Inspection arrivals dedup
    against the same store.

    Failures here MUST NOT crash the caller — the RayCon Scenario Doc
    publish has already succeeded by the time we get here, and a
    republish error should not undo that.
    """
    now = now or datetime.now(UTC)

    # Composite fingerprint mirrors the SIR/BI shape (file_id:modifiedTime).
    # If RayCon recomputes the same run_id but writes fresh content (new
    # _drive_modified_time), the modifiedTime suffix changes and we
    # republish; otherwise dedup holds. Without this suffix a real content
    # change gets silently skipped for up to DD_REPUBLISH_FORCE_AFTER.
    raycon_run_id = raycon_run_id.strip()
    drive_modified_time = (drive_modified_time or "").strip()
    base_key = raycon_run_id or f"unknown@{now.date().isoformat()}"
    run_key = f"{base_key}:{drive_modified_time}" if drive_modified_time else base_key

    drive_folder_url = str(site_summary.get("drive_folder_url", "")).strip()

    # Pre-rec3 "skipped_no_drive_folder" / "skipped_bad_drive_url"
    # branches surfaced as distinct decision strings; the shared helper
    # collapses them into ``skip_bad_input``. Map back to the legacy
    # strings so existing log greps don't break.
    if not drive_folder_url:
        return {"dd_report_republish": "skipped_no_drive_folder"}
    if not extract_folder_id_from_url(drive_folder_url):
        return {"dd_report_republish": "skipped_bad_drive_url"}

    outcome = maybe_republish_dd_report(
        gc,
        site_summary=site_summary,
        reason=REASON_RAYCON,
        content_fingerprint=run_key,
        settings=settings,
        system_prompt=system_prompt,
        shared_cache=shared_cache,
        republish_state=republish_state,
        dry_run=dry_run,
        now=now,
        force_after=force_after,
        existing_report_finder=_find_existing_dd_report,
        # Plumb the module-level ``process_site_pipeline`` reference so
        # tests that patch ``scripts.raycon_followup.process_site_pipeline``
        # still observe the call. Without this, the helper imports the
        # symbol directly from ``due_diligence_reporter.report_pipeline``
        # and bypasses the patch.
        pipeline_runner=process_site_pipeline,
        failure_event_recorder=failure_event_recorder,
    )

    # Translate the helper's decision strings back to the legacy strings
    # raycon_followup callers (and tests) expect. The ``raycon_run_id``
    # field on the row is the bare run id (without the modifiedTime
    # suffix) so on-call grepping by run still works after the
    # composite-fingerprint fix.
    legacy_run_id = base_key
    if outcome.decision == "republish":
        payload: dict[str, Any] = {
            "dd_report_republish": "republished",
            "raycon_run_id": legacy_run_id,
            "pipeline_status": outcome.pipeline_status,
            "doc_url": outcome.doc_url,
        }
        if outcome.failure_event is not None:
            payload["republish_failure_event"] = outcome.failure_event
        return payload
    if outcome.decision == "skip_no_prior_report":
        return {"dd_report_republish": "skipped_no_existing_report"}
    if outcome.decision == "skip_no_diff":
        return {
            "dd_report_republish": "deduped",
            "raycon_run_id": legacy_run_id,
            "last_republish": outcome.last_republish or None,
        }
    if outcome.decision == "skip_dry_run":
        existing = _find_existing_dd_report(
            gc, extract_folder_id_from_url(drive_folder_url) or ""
        )
        return {
            "dd_report_republish": "would_republish",
            "raycon_run_id": legacy_run_id,
            "existing_dd_report_id": (existing or {}).get("id"),
        }
    if outcome.decision == "failed":
        failed_payload: dict[str, Any] = {
            "dd_report_republish": "failed",
            "reason": outcome.error,
        }
        if outcome.failure_event is not None:
            failed_payload["republish_failure_event"] = outcome.failure_event
        return failed_payload
    # skip_bad_input fallback (e.g. unknown reason — shouldn't happen
    # since we hard-code REASON_RAYCON above).
    return {"dd_report_republish": outcome.decision, "reason": outcome.error}


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
    retry_failed_scenarios: bool = True,
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

    m1_files = gc.list_files_in_folder(m1_folder_id)

    try:
        scenario = read_raycon_scenario_from_m1(
            gc,
            drive_folder_url,
            m1_folder_id=m1_folder_id,
            m1_files=m1_files,
        )
    except RayConSchemaError as e:
        logger.error("[%s] %s", site_name, e)
        return {"site": site_name, "error": f"schema error: {e}"}

    block_plan = _find_block_plan(gc, m1_folder_id, m1_files=m1_files)
    if block_plan is None and scenario is None:
        return {"site": site_name, "skipped": "no block plan in M1"}

    if scenario is None:
        assert block_plan is not None
        now = datetime.now(UTC)
        bp_modified = _parse_iso(str(block_plan.get("modifiedTime", "")))
        is_stuck = (
            bp_modified is not None and (now - bp_modified) > alert_after
        )
        block_plan_file_id = str(block_plan.get("id", "")).strip()
        status_result: dict[str, Any] = {}
        if dispatch_state is not None and block_plan_file_id:
            status_result = _poll_dispatch_state_status(
                block_plan_file_id,
                dispatch_state,
            )
        status = str(status_result.get("status", "") or "").strip().lower()
        if status in RAYCON_TERMINAL_STATUSES and status != "completed":
            return {
                "site": site_name,
                "alert": f"raycon job terminal status: {status}",
                "block_plan_file_id": block_plan_file_id,
            }
        if status == "completed":
            return {
                "site": site_name,
                "alert": "raycon completed but raycon_scenario.json is not visible in M1",
                "block_plan_file_id": block_plan_file_id,
            }
        if status in RAYCON_IN_PROGRESS_STATUSES:
            progress_row: dict[str, Any] = {
                "site": site_name,
                "skipped": f"raycon job {status}",
                "block_plan_file_id": block_plan_file_id,
            }
            if is_stuck:
                progress_row["alert"] = f"raycon job still {status} after {alert_after}"
            return progress_row

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
            dispatch_row: dict[str, Any] = {
                "site": site_name,
                "dispatched": True,
                "job_id": dispatch_result.get("job_id"),
                "raycon_run_id": dispatch_result.get("raycon_run_id"),
                "status": dispatch_result.get("status"),
                "status_url_present": dispatch_result.get("status_url_present"),
                "block_plan_file_id": dispatch_result.get("block_plan_file_id"),
            }
            if bp_modified is not None:
                dispatch_row["block_plan_modified"] = bp_modified.isoformat()
            return dispatch_row

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
    # state would render an empty/zero-dollar scenario as authoritative.
    # Surface the failure as an alert row instead
    # so EDU Ops sees it in Chat and we don't pollute the site's M1 folder.
    if raycon_payload_failed(scenario):
        report_fields = raycon_scenario_to_report_fields(scenario)
        if block_plan is None:
            return _failed_scenario_base_row(site_name, scenario, report_fields)
        return _handle_failed_scenario(
            site_summary,
            site_name,
            block_plan,
            m1_folder_id,
            scenario,
            report_fields,
            dispatch_state=dispatch_state,
            dry_run=dry_run,
            redispatch_after=redispatch_after,
            retry_failed_scenario=retry_failed_scenarios,
        )

    # Scenario JSON is here and the run succeeded — publish the report
    # Doc if missing or stale.
    site_id = str(site_summary.get("id") or site_summary.get("site_id") or "").strip()
    site_address = str(site_summary.get("address") or site_summary.get("site_address") or "").strip()
    if not site_id or not site_address:
        return {
            "site": site_name,
            "error": "missing Rhodes site identity/address for RayCon scenario publish",
        }

    published = _find_published_doc(gc, m1_folder_id, site_name, m1_files=m1_files)
    json_modified = scenario.get("_drive_modified_time", "")
    doc_modified = (published or {}).get("modifiedTime", "") if published else ""
    json_modified_dt = _parse_iso(str(json_modified))
    if json_modified_dt is None:
        return {
            "site": site_name,
            "error": "raycon_scenario.json missing Drive modifiedTime",
        }
    doc_modified_dt = _parse_iso(str(doc_modified))

    if published is not None and doc_modified_dt is not None and doc_modified_dt >= json_modified_dt:
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

    published_row: dict[str, Any] = {
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
            drive_modified_time = str(
                scenario.get("_drive_modified_time", "")
            ).strip()
            republish_result = dd_republish_callback(
                gc=gc,
                site_summary=site_summary,
                raycon_run_id=raycon_run_id,
                drive_modified_time=drive_modified_time,
                dry_run=dry_run,
            )
        except Exception as e:
            logger.exception(
                "DD Report republish callback raised for site=%s",
                site_name,
            )
            republish_result = {"dd_report_republish": "failed", "reason": str(e)}
        if republish_result:
            published_row.update(republish_result)

    return published_row


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
    # Rec. 2: RayCon callback receiver. When --site-id is set, scope the
    # run to a single site instead of sweeping the full portfolio. The
    # GitHub Actions workflow passes these from the RayCon callback's
    # workflow_dispatch inputs.
    parser.add_argument(
        "--site-id",
        dest="site_id",
        default=None,
        help=(
            "Site Drive folder id to scope the run to (RayCon callback). "
            "When set, only that site is processed. Unknown ids exit cleanly "
            "with a logged warning so the cron safety net can pick the run up."
        ),
    )
    parser.add_argument(
        "--run-id",
        dest="run_id",
        default=None,
        help="RayCon run UUID — observability only, logged for cross-system correlation.",
    )
    parser.add_argument(
        "--status",
        dest="raycon_status",
        default=None,
        help="RayCon job status (succeeded|failed|validation_failed) — observability only.",
    )
    args = parser.parse_args(argv)

    # Reject malformed callback inputs (charset + length cap) before they
    # reach logging or downstream filters. The workflow_dispatch surface
    # is HTTP-reachable via the GitHub PAT path, so RayCon-side bugs (or
    # a stale token's misuse) shouldn't be able to wedge log
    # ingestion or smuggle log-injection sequences through.
    args.site_id = _validate_callback_input("site_id", args.site_id)
    args.run_id = _validate_callback_input("run_id", args.run_id)
    args.raycon_status = _validate_callback_input(
        "status", args.raycon_status, max_len=32
    )

    if args.site_id or args.run_id or args.raycon_status:
        logger.info(
            "RayCon callback dispatch: site_id=%s run_id=%s status=%s",
            args.site_id or "(none)",
            args.run_id or "(none)",
            args.raycon_status or "(none)",
        )

    settings = get_settings()
    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )
    if not settings.google_drive_root_folder_id:
        logger.error("GOOGLE_DRIVE_ROOT_FOLDER_ID is required for RayCon follow-up")
        return 1

    summaries = _load_site_summaries(
        gc,
        settings.google_drive_root_folder_id,
        target_site_id=args.site_id,
    )
    summaries = [s for s in summaries if _site_filter(s, args.site)]

    # Rec. 2: when the RayCon callback supplies a site_id, scope the run
    # to that single site. Unknown ids exit cleanly (return 0) so the
    # 5-minute cron can recover on its next tick — same fallback shape
    # the rest of the system uses.
    if args.site_id:
        target_id = str(args.site_id).strip()
        scoped = [s for s in summaries if _site_id_matches(s, target_id)]
        if not scoped:
            logger.warning(
                "RayCon callback site_id=%s did not match any active site; "
                "exiting 0 so cron picks it up on the next tick.",
                target_id,
            )
            return 0
        logger.info(
            "RayCon callback scoping run to single site: id=%s title=%s",
            target_id,
            scoped[0].get("title", "(unnamed)"),
        )
        summaries = scoped

    alert_after = timedelta(minutes=args.alert_after_minutes)
    redispatch_after = timedelta(minutes=args.redispatch_after_minutes)
    event_run_id = _raycon_followup_run_id(args.run_id)

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
        drive_modified_time: str = "",
    ) -> dict[str, Any]:
        nonlocal _shared_cache, _system_prompt
        if _system_prompt is None:
            prompt_path = _project_root / "docs" / "prompts" / "prompt_v4.md"
            if not prompt_path.exists():
                logger.error(
                    "DD Report republish: system prompt missing at %s", prompt_path
                )
                error = f"system prompt missing at {prompt_path}"
                outcome = RepublishOutcome(
                    decision="failed",
                    reason=REASON_RAYCON,
                    site_id=str(site_summary.get("id") or "").strip(),
                    fingerprint=raycon_run_id,
                    error=error,
                )
                failure_payload: dict[str, Any] = {
                    "dd_report_republish": "failed",
                    "reason": error,
                }
                if not dry_run:
                    failure_payload["republish_failure_event"] = (
                        record_dd_republish_failure_event(
                            outcome,
                            site_summary,
                            settings,
                        )
                    )
                return failure_payload
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
            drive_modified_time=drive_modified_time,
            failure_event_recorder=record_dd_republish_failure_event,
        )

    results: list[dict[str, Any]] = []
    retry_failed_scenarios = not bool(args.run_id or args.raycon_status)
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
                retry_failed_scenarios=retry_failed_scenarios,
            )
        except Exception as e:
            logger.exception("Unhandled error for site '%s'", site_summary.get("title"))
            row = {"site": site_summary.get("title"), "error": str(e)}
        row = _with_site_context(row, site_summary)
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

    alert_state_changed = False
    dedup_state = _load_alert_state() if alerts or errors else {}
    notification_failures: list[dict[str, Any]] = []

    if alerts:
        alert_groups = (
            (
                "failed_scenario",
                "RayCon scenario follow-up: failed scenarios",
                [row for row in alerts if _is_failed_scenario_alert(row)],
            ),
            (
                "stuck_site",
                "RayCon scenario follow-up: stuck sites",
                [row for row in alerts if not _is_failed_scenario_alert(row)],
            ),
        )
        for alert_type, heading, grouped_alerts in alert_groups:
            if not grouped_alerts:
                continue
            fresh_alerts = _fresh_dedup_alerts(grouped_alerts, dedup_state)
            if fresh_alerts:
                notification_failures.extend(
                    _notify_raycon_followup_rows(
                        fresh_alerts,
                        settings,
                        run_id=event_run_id,
                        alert_type=alert_type,
                        message_field="alert",
                        heading=heading,
                    )
                )
                new_state = _mark_notified_alerts(fresh_alerts, dedup_state)
                alert_state_changed = alert_state_changed or new_state != dedup_state
                dedup_state = new_state
            suppressed = len(grouped_alerts) - len(fresh_alerts)
            if suppressed:
                logger.info(
                    "Suppressed %d %s alert(s) within %s dedup window",
                    suppressed,
                    alert_type,
                    ALERT_DEDUP_WINDOW,
                )

    if errors:
        for row in errors:
            row["alert_dedup_key"] = _error_alert_dedup_key(row)
        fresh_errors = _fresh_dedup_alerts(errors, dedup_state)
        if fresh_errors:
            notification_failures.extend(
                _notify_raycon_followup_rows(
                    fresh_errors,
                    settings,
                    run_id=event_run_id,
                    alert_type="error",
                    message_field="error",
                    heading="RayCon scenario follow-up: errors",
                )
            )
            new_state = _mark_notified_alerts(fresh_errors, dedup_state)
            alert_state_changed = alert_state_changed or new_state != dedup_state
            dedup_state = new_state
        suppressed = len(errors) - len(fresh_errors)
        if suppressed:
            logger.info(
                "Suppressed %d RayCon error alert(s) within %s dedup window",
                suppressed,
                ALERT_DEDUP_WINDOW,
            )

    if alert_state_changed:
        _save_alert_state(dedup_state)

    if notification_failures:
        for row in notification_failures:
            logger.error(
                "RayCon follow-up notification delivery failed: %s",
                json.dumps(
                    {
                        "site": row.get("site"),
                        "site_id": row.get("site_id"),
                        "owner_assigned": _row_has_site_owner(row),
                        "raycon_followup_event": row.get("raycon_followup_event"),
                    },
                    default=str,
                ),
            )

    logger.info(
        "Run complete: published=%d dispatched=%d alerts=%d errors=%d total_sites=%d",
        len(published),
        len(dispatched),
        len(alerts),
        len(errors),
        len(results),
    )
    return 0 if not errors and not notification_failures else 1


if __name__ == "__main__":
    sys.exit(main())
