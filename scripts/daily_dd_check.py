#!/usr/bin/env python3
"""
daily_dd_check.py — Standalone daily cron script for DD report readiness checking.

Scans every active Wrike Site Record that has a Google Drive folder, checks for
SIR + ISP documents, and:
  - Posts a Google Chat alert if required documents are missing.
  - Triggers a DD report generation via Claude API when all docs are present and
    no report exists yet.
  - Checks the generated report for completeness and sends an email if ready.

Run:
    uv run python scripts/daily_dd_check.py

Environment (from .env):
    WRIKE_ACCESS_TOKEN, GOOGLE_CLIENT_CONFIG, GOOGLE_TOKEN_FILE,
    ANTHROPIC_API_KEY, GOOGLE_CHAT_WEBHOOK_URL, DD_REPORT_EMAIL_RECIPIENTS,
    EMAIL_SENDER, EMAIL_APP_PASSWORD, DD_TEMPLATE_V3_GOOGLE_DOC_ID,
    GOOGLE_DRIVE_ROOT_FOLDER_ID, OPENAI_API_KEY
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure project src is on path when running as a script
_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

from due_diligence_reporter.config import get_settings  # noqa: E402
from due_diligence_reporter.google_client import GoogleClient  # noqa: E402
from due_diligence_reporter.report_pipeline import (  # noqa: E402
    PipelineResult,
    list_shared_folders_once,
    post_pipeline_result,
    process_site_pipeline,
)
from due_diligence_reporter.utils import build_site_match_terms as _build_site_match_terms  # noqa: E402
from due_diligence_reporter.wrike import (  # noqa: E402
    _get_active_status_ids,
    _get_all_site_records,
    extract_address_from_record,
    extract_google_folder_from_record,
    extract_p1_email_from_record,
    extract_p1_from_record,
    filter_active_site_records,
    load_wrike_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("daily_dd_check")


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def main(site_filter: str | None = None) -> None:
    settings = get_settings()
    wrike_cfg = load_wrike_config()

    # Load the agent system prompt
    prompt_path = _project_root / "docs" / "prompts" / "prompt_v3.md"
    if not prompt_path.exists():
        logger.error("System prompt not found at %s — aborting", prompt_path)
        sys.exit(1)
    system_prompt = prompt_path.read_text(encoding="utf-8")

    # Init Google client
    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )

    # Fetch all Wrike site records, then filter to active sites in DD stages
    logger.info("Fetching all Wrike site records...")
    all_records = _get_all_site_records(cfg=wrike_cfg)
    active_status_ids = _get_active_status_ids(access_token=wrike_cfg.access_token)
    active_records = filter_active_site_records(all_records, active_status_ids)
    logger.info("Found %d site records (%d active in DD stages)", len(all_records), len(active_records))

    # Pre-fetch shared folder file lists once for all sites
    logger.info("Listing shared Drive folders (SIR, ISP, Building Inspection)...")
    shared_cache = list_shared_folders_once(gc)
    logger.info(
        "Shared folder files: SIR=%d, ISP=%d, Inspection=%d",
        len(shared_cache.get("sir", [])),
        len(shared_cache.get("isp", [])),
        len(shared_cache.get("building_inspection", [])),
    )

    results: list[PipelineResult] = []
    skipped = len(all_records) - len(active_records)

    for record in active_records:
        site_title = record.get("title", "Unknown")

        if site_filter and site_filter.lower() not in site_title.lower():
            continue

        drive_folder_url = extract_google_folder_from_record(record)

        if not drive_folder_url:
            logger.debug("Skipping '%s' — no Drive folder URL", site_title)
            continue

        address = extract_address_from_record(record)
        match_terms = _build_site_match_terms(site_title, address)
        p1_email = extract_p1_email_from_record(record)
        p1_profile = extract_p1_from_record(record) or {}
        p1_name = p1_profile.get("name")
        logger.info("Checking site: %s (match terms: %s, p1: %s)", site_title, match_terms, p1_email)

        try:
            result = process_site_pipeline(
                gc, site_title, drive_folder_url, match_terms,
                shared_cache, system_prompt, settings,
                p1_email=p1_email, site_address=address, p1_name=p1_name,
            )
        except Exception as e:
            logger.exception("Unexpected pipeline failure for '%s'", site_title)
            result = PipelineResult(site_title=site_title, status="error", error=str(e))
        results.append(result)

        # Post each result to Google Chat
        post_pipeline_result(
            settings.google_chat_webhook_url, result, drive_folder_url,
        )

    # Summary — use ASCII-safe markers to avoid encoding errors on Windows
    print("\n" + "=" * 60)
    print(f"Daily DD Check -- {len(results)} sites processed, {skipped} skipped (inactive or wrong stage)")
    print("=" * 60)
    for r in results:
        if r.status == "report_created":
            print(f"  [OK] {r.site_title} -- report created ({r.pending_count} pending fields)")
        elif r.status == "waiting_on_docs":
            print(f"  [..] {r.site_title} -- waiting on: {', '.join(r.missing_docs)}")
        elif r.status == "report_exists":
            print(f"  [--] {r.site_title} -- report already exists")
        elif r.status == "report_incomplete":
            print(f"  [!!] {r.site_title} -- report incomplete ({len(r.unresolved_tokens)} unfilled tokens)")
        elif r.status == "generation_failed":
            print(f"  [XX] {r.site_title} -- generation failed: {r.error}")
        else:
            print(f"  [??] {r.site_title} -- {r.status}")
    print("=" * 60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Daily DD readiness check and report generation")
    parser.add_argument("--site", type=str, default=None, help="Run for a single site (substring match on title)")
    args = parser.parse_args()
    main(site_filter=args.site)
