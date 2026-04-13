#!/usr/bin/env python3
"""
scan_inbox.py — Scan edu.ops@trilogy.com inbox for DD documents.

Finds emails with PDF attachments (SIR, Building Inspection, ISP), classifies
them by filename using the three-tier classifier (regex → GPT-4o-mini), and
uploads to the correct shared Drive folder by doc_type only (no site matching).

Phase 2: Pipeline trigger for newly-uploaded sites. This stays disabled unless
uploads carry site identity. Today the filename classifier does not match files
to Wrike site records, so report generation falls to the daily sweep instead.

Run:
    uv run python scripts/scan_inbox.py
    uv run python scripts/scan_inbox.py --dry-run
    uv run python scripts/scan_inbox.py --scan-only

Environment (from .env):
    WRIKE_ACCESS_TOKEN, GOOGLE_CLIENT_CONFIG, GOOGLE_TOKEN_FILE,
    OPENAI_API_KEY, GOOGLE_CHAT_WEBHOOK_URL, ANTHROPIC_API_KEY,
    SIR_FOLDER_ID, ISP_FOLDER_ID, BUILDING_INSPECTION_FOLDER_ID,
    DD_TEMPLATE_V3_GOOGLE_DOC_ID, GOOGLE_DRIVE_ROOT_FOLDER_ID,
    EMAIL_SENDER, EMAIL_APP_PASSWORD, DD_REPORT_EMAIL_RECIPIENTS
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

# Ensure project src is on path when running as a script
_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv

load_dotenv(_project_root / ".env")

from due_diligence_reporter.config import get_settings
from due_diligence_reporter.google_client import GoogleClient
from due_diligence_reporter.inbox_scanner import (
    build_scan_summary,
    has_site_identity,
    scan_inbox,
)
from due_diligence_reporter.report_pipeline import (
    list_shared_folders_once,
    post_pipeline_result,
    process_site_pipeline,
)
from due_diligence_reporter.server import _build_site_match_terms
from due_diligence_reporter.utils import (
    escape_html_text,
    post_google_chat_message,
    sanitize_http_url,
    send_email,
)
from due_diligence_reporter.wrike import (
    _get_active_status_ids,
    _get_all_site_records,
    extract_address_from_record,
    extract_google_folder_from_record,
    extract_p1_email_from_record,
    filter_active_site_records,
    load_wrike_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("scan_inbox")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _extract_unique_sites_from_uploads(
    uploads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate upload results by site_title, returning one entry per site."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for u in uploads:
        title = u.get("site_title")
        if not title or title in seen:
            continue
        seen.add(title)
        unique.append(u)
    return unique


def _find_record_by_title_or_id(
    site_records: list[dict[str, Any]],
    title: str | None,
    site_id: str | None,
) -> dict[str, Any] | None:
    """Find a Wrike site record by title or ID."""
    for record in site_records:
        if site_id and record.get("id") == site_id:
            return record
        if title and record.get("title", "").lower() == title.lower():
            return record
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def main(dry_run: bool = False, scan_only: bool = False) -> None:
    settings = get_settings()

    # Init Google client
    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )

    # Fetch Wrike site records when available. Inbox filing should continue even if Wrike is down.
    site_records: list[dict[str, Any]] = []
    try:
        logger.info("Fetching Wrike site records...")
        wrike_cfg = load_wrike_config()
        all_records = _get_all_site_records(cfg=wrike_cfg)
        active_status_ids = _get_active_status_ids(access_token=wrike_cfg.access_token)
        site_records = filter_active_site_records(all_records, active_status_ids)
        logger.info("Found %d site records (%d active)", len(all_records), len(site_records))
    except Exception as e:
        logger.error("Wrike lookup failed; continuing with inbox scan only: %s", e)

    # ── Phase 1: Inbox scan ──────────────────────────────────────────────────
    results = scan_inbox(gc, site_records, settings, dry_run=dry_run)

    # Build summary
    summary = build_scan_summary(results)
    print("\n" + "=" * 60)
    print(summary)
    print("=" * 60)

    # Post to Google Chat if any uploads or alerts
    if settings.google_chat_webhook_url and (
        results["attachments_uploaded"] > 0
        or results.get("low_confidence")
        or results.get("errors")
    ):
        try:
            post_google_chat_message(settings.google_chat_webhook_url, summary)
        except Exception as e:
            logger.error("Failed to post Google Chat summary: %s", e)

    # ── SIR arrival notifications ────────────────────────────────────────────
    sir_notification_recipients = [
        r.strip()
        for r in settings.sir_notification_recipients.split(",")
        if r.strip()
    ]

    sir_uploads = [u for u in results.get("uploads", []) if u.get("doc_type") == "sir"]
    if sir_uploads and sir_notification_recipients and settings.email_sender and settings.email_app_password:
        for sir in sir_uploads:
            site = sir.get("site_title") or "Unknown Site"
            drive_link = sanitize_http_url(sir.get("drive_link", ""))
            filename = sir.get("drive_filename", sir.get("original_filename", ""))
            safe_site = escape_html_text(site)
            safe_filename = escape_html_text(filename)
            if drive_link:
                link_html = (
                    f'<p><a href="{drive_link}" style="font-size:16px;font-weight:bold;">'
                    "View SIR in Google Drive</a></p>"
                )
            else:
                link_html = "<p>SIR link unavailable.</p>"
            html_body = f"""<html><body>
<h2>SIR Received — {safe_site}</h2>
<p>A new Site Investigation Report has been uploaded for <strong>{safe_site}</strong>.</p>
<p><strong>File:</strong> {safe_filename}</p>
{link_html}
<p style="color:#888;font-size:12px;">Sent automatically by the Alpha DD Reporter inbox scanner.</p>
</body></html>"""
            try:
                send_email(
                    sender=settings.email_sender,
                    app_password=settings.email_app_password,
                    recipients=sir_notification_recipients,
                    subject=f"SIR Received — {site}",
                    html_body=html_body,
                )
                logger.info("SIR arrival email sent for '%s' to %s", site, sir_notification_recipients)
            except Exception as e:
                logger.error("Failed to send SIR arrival email for '%s': %s", site, e)

    # ── Phase 2: Pipeline for newly-uploaded sites ───────────────────────────
    if scan_only or dry_run:
        if scan_only:
            logger.info("--scan-only flag set, skipping pipeline phase")
        if dry_run:
            logger.info("--dry-run mode, skipping pipeline phase")
        return

    uploads = results.get("uploads", [])
    if not uploads:
        logger.info("No uploads — skipping pipeline phase")
        return
    if not has_site_identity(uploads):
        logger.info("Uploads lack site identity — skipping pipeline phase until matching exists")
        return
    if not site_records:
        logger.info("Wrike site records unavailable — skipping pipeline phase")
        return
    template_id = settings.dd_template_v3_google_doc_id or settings.dd_template_v2_google_doc_id
    if not template_id or not settings.google_drive_root_folder_id:
        logger.info("DD report generation settings missing — skipping pipeline phase")
        return

    unique_sites = _extract_unique_sites_from_uploads(uploads)
    logger.info("Pipeline phase: %d unique site(s) received new uploads", len(unique_sites))

    # Load the agent system prompt
    prompt_path = _project_root / "docs" / "prompts" / "prompt_v3.md"
    if not prompt_path.exists():
        logger.error("System prompt not found at %s — aborting pipeline phase", prompt_path)
        return
    system_prompt = prompt_path.read_text(encoding="utf-8")

    # Pre-fetch shared folder file lists once (freshly, since we just uploaded)
    logger.info("Refreshing shared Drive folder cache...")
    shared_cache = list_shared_folders_once(gc)

    for site_info in unique_sites:
        site_title = site_info["site_title"]
        site_id = site_info.get("matched_site_id")

        # Look up the full Wrike record (already filtered to active sites)
        record = _find_record_by_title_or_id(site_records, site_title, site_id)
        if not record:
            logger.warning("No Wrike record found for '%s' — skipping pipeline", site_title)
            continue

        drive_folder_url = extract_google_folder_from_record(record)
        if not drive_folder_url:
            logger.warning("No Drive folder URL for '%s' — skipping pipeline", site_title)
            continue

        address = extract_address_from_record(record)
        match_terms = _build_site_match_terms(site_title, address)
        p1_email = extract_p1_email_from_record(record)

        logger.info("Running pipeline for '%s' (match terms: %s, p1: %s)", site_title, match_terms, p1_email)
        result = process_site_pipeline(
            gc, site_title, drive_folder_url, match_terms,
            shared_cache, system_prompt, settings,
            p1_email=p1_email, site_address=address,
        )

        # Post each result to Google Chat
        post_pipeline_result(
            settings.google_chat_webhook_url, result, drive_folder_url,
        )

        # Print result
        print(f"  Pipeline: {site_title} -> {result.status}")
        if result.missing_docs:
            print(f"    Missing: {', '.join(result.missing_docs)}")
        if result.doc_url:
            print(f"    Report: {result.doc_url}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scan inbox for DD documents and upload to Drive")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify and match without uploading or marking emails",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Run inbox scan only, skip readiness check and report pipeline",
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run, scan_only=args.scan_only)
