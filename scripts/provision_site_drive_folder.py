#!/usr/bin/env python3
"""provision_site_drive_folder.py — One-shot helper that creates a Drive
folder for sites whose Wrike Site Record currently has no Google Folder
set, pre-creates the M1 subfolder, and writes the new folder URL back
to Wrike's "Google Folder" custom field.

The inbox scanner refuses to route SIR / Building Inspection / ISP /
Block Plan uploads when a site has no Drive folder URL — it flags them
for manual review instead. Running this brings those sites back into
the automated path.

For each requested site title:

  1. Look up the active Site Record in Wrike (substring match on title).
     If multiple match the same input, the run aborts so an operator can
     disambiguate.
  2. If the record already has a non-empty Google Folder custom field,
     skip — nothing to do.
  3. Create a Drive folder named after the site title under
     ``GOOGLE_DRIVE_ROOT_FOLDER_ID``.
  4. Create an ``M1`` subfolder inside it (the scanner will create one
     on demand otherwise; pre-creating makes it visible immediately).
  5. PUT the new ``webViewLink`` onto the Site Record's ``customFields``
     so subsequent ``site-roster-sync`` runs don't keep treating the
     site as unprovisioned.

Run:
    uv run python scripts/provision_site_drive_folder.py \\
        "Alpha School Littleton 7018" \\
        "Alpha School Austin 500 S Congress" \\
        ...

    --dry-run  log what would happen, but don't create folders or
               write back to Wrike. Default is False — this script
               makes real changes when you run it without the flag.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import requests

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

from due_diligence_reporter.config import get_settings  # noqa: E402
from due_diligence_reporter.google_client import GoogleClient  # noqa: E402
from due_diligence_reporter.wrike import (  # noqa: E402
    WRIKE_API_BASE_URL,
    WRIKE_CUSTOM_FIELDS,
    WRIKE_TIMEOUT_SECONDS,
    _get_active_status_ids,
    _get_all_site_records,
    _wrike_headers,
    extract_google_folder_from_record,
    filter_active_site_records,
    load_wrike_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("provision_site_drive_folder")


def _resolve_site_record(records: list[dict[str, Any]], title_query: str) -> dict[str, Any]:
    """Return the active Site Record whose title matches ``title_query`` exactly,
    or — if no exact hit — the unique record whose title contains the query.
    Aborts (raises) if nothing matches or if the substring match is ambiguous.
    """
    target = title_query.strip()
    target_lower = target.lower()

    exact = [r for r in records if (r.get("title") or "").strip().lower() == target_lower]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise SystemExit(
            f"Multiple active records match '{title_query}' exactly — disambiguate by ID."
        )

    contains = [r for r in records if target_lower in (r.get("title") or "").lower()]
    if len(contains) == 0:
        raise SystemExit(f"No active site record matches '{title_query}'")
    if len(contains) > 1:
        titles = "\n  - ".join((r.get("title") or "").strip() for r in contains)
        raise SystemExit(
            f"Ambiguous query '{title_query}' — matches multiple active sites:\n  - {titles}"
        )
    return contains[0]


def _write_drive_folder_to_wrike(
    *, record_id: str, drive_url: str, access_token: str, dry_run: bool
) -> None:
    """PUT the new Drive folder URL onto the Wrike Site Record's Google
    Folder custom field. Other custom fields are left untouched — Wrike
    merges single-field updates by default.
    """
    folder_field_id = WRIKE_CUSTOM_FIELDS["google_folder"]
    if not folder_field_id:
        raise SystemExit("WRIKE_CUSTOM_FIELDS['google_folder'] is not configured")

    if dry_run:
        logger.info(
            "DRY-RUN: would PUT /folders/%s with customFields[google_folder]=%s",
            record_id,
            drive_url,
        )
        return

    url = f"{WRIKE_API_BASE_URL}/folders/{record_id}"
    body = {
        "customFields": [
            {"id": folder_field_id, "value": drive_url},
        ],
    }
    resp = requests.put(
        url,
        headers={**_wrike_headers(access_token), "Content-Type": "application/json"},
        json=body,
        timeout=WRIKE_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    logger.info("Wrike record %s: google_folder set to %s", record_id, drive_url)


def _provision_one_site(
    gc: GoogleClient,
    *,
    record: dict[str, Any],
    drive_root_id: str,
    access_token: str,
    dry_run: bool,
) -> tuple[bool, str | None]:
    """Return ``(provisioned, drive_url_or_none)`` for one site record."""
    title = (record.get("title") or "").strip()
    record_id = record.get("id")
    if not record_id:
        logger.warning("%s: record has no id, skipping", title)
        return False, None

    existing = extract_google_folder_from_record(record)
    if existing:
        logger.info("%s: already has Google Folder (%s), skipping", title, existing)
        return False, existing

    if dry_run:
        logger.info(
            "DRY-RUN: would create Drive folder '%s' under %s, pre-create M1 subfolder, "
            "and write URL onto Wrike record %s",
            title,
            drive_root_id,
            record_id,
        )
        return True, None

    # 1. Site folder.
    site_folder = gc.create_folder(drive_root_id, title)
    site_folder_id = site_folder.get("id")
    site_folder_url = site_folder.get("webViewLink")
    if not site_folder_id or not site_folder_url:
        logger.error("%s: create_folder returned no id/webViewLink: %s", title, site_folder)
        return False, None
    logger.info("%s: created Drive folder %s (%s)", title, site_folder_id, site_folder_url)

    # 2. M1 subfolder. The scanner would auto-create this on first upload, but
    # pre-creating makes the canonical layout visible immediately.
    m1 = gc.create_folder(site_folder_id, "M1")
    logger.info(
        "%s: pre-created M1 subfolder %s (%s)",
        title,
        m1.get("id"),
        m1.get("webViewLink"),
    )

    # 3. Wrike write-back.
    _write_drive_folder_to_wrike(
        record_id=record_id,
        drive_url=site_folder_url,
        access_token=access_token,
        dry_run=False,
    )

    return True, site_folder_url


def main(*, site_titles: list[str], dry_run: bool) -> int:
    settings = get_settings()
    drive_root_id = settings.google_drive_root_folder_id
    if not drive_root_id:
        raise SystemExit("GOOGLE_DRIVE_ROOT_FOLDER_ID not set in env")

    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )
    wrike_cfg = load_wrike_config()
    all_records = _get_all_site_records(cfg=wrike_cfg)
    active_status_ids = _get_active_status_ids(access_token=wrike_cfg.access_token)
    active = filter_active_site_records(all_records, active_status_ids)

    # Resolve each title to a record up front so we abort cleanly on
    # ambiguity / typos before any folder gets created.
    resolved: list[tuple[str, dict[str, Any]]] = []
    for title in site_titles:
        rec = _resolve_site_record(active, title)
        resolved.append((title, rec))
        logger.info("Resolved '%s' -> %s (%s)", title, rec.get("title"), rec.get("id"))

    provisioned = 0
    skipped_existing = 0
    for original_query, rec in resolved:
        ok, _url = _provision_one_site(
            gc,
            record=rec,
            drive_root_id=drive_root_id,
            access_token=wrike_cfg.access_token,
            dry_run=dry_run,
        )
        if ok:
            provisioned += 1
        else:
            skipped_existing += 1

    verb = "would provision" if dry_run else "provisioned"
    logger.info(
        "Done: %s %d site(s); skipped %d already-set",
        verb,
        provisioned,
        skipped_existing,
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "site_titles",
        nargs="+",
        help="Wrike site titles (substring match on active records).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve records and log proposed actions, but don't call Drive or Wrike.",
    )
    args = parser.parse_args()
    sys.exit(main(site_titles=args.site_titles, dry_run=args.dry_run))
