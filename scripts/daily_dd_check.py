#!/usr/bin/env python3
"""Standalone daily cron script for DD report readiness checking.

Scans the configured Google Drive root folder for site folders, checks each
folder for first-round DDR readiness, and triggers report generation when a SIR
is present and no report exists yet.

Run:
    uv run python scripts/daily_dd_check.py

Environment:
    GOOGLE_CLIENT_CONFIG, GOOGLE_TOKEN_FILE, ANTHROPIC_API_KEY,
    GOOGLE_CHAT_WEBHOOK_URL, DD_REPORT_EMAIL_RECIPIENTS, EMAIL_SENDER,
    EMAIL_APP_PASSWORD, GOOGLE_DRIVE_ROOT_FOLDER_ID, OPENAI_API_KEY
"""
# ruff: noqa: E402

from __future__ import annotations

import logging
import sys
from pathlib import Path

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
from due_diligence_reporter.utils import (
    build_site_match_terms as _build_site_match_terms,  # noqa: E402
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("daily_dd_check")


def _folder_url(folder: dict[str, object]) -> str:
    link = folder.get("webViewLink")
    if isinstance(link, str) and link.strip():
        return link.strip()
    folder_id = str(folder.get("id") or "").strip()
    return f"https://drive.google.com/drive/folders/{folder_id}" if folder_id else ""


def main(site_filter: str | None = None) -> None:
    settings = get_settings()
    if not settings.google_drive_root_folder_id:
        logger.error("GOOGLE_DRIVE_ROOT_FOLDER_ID is required for the daily sweep")
        sys.exit(1)

    prompt_path = _project_root / "docs" / "prompts" / "prompt_v4.md"
    if not prompt_path.exists():
        logger.error("System prompt not found at %s - aborting", prompt_path)
        sys.exit(1)
    system_prompt = prompt_path.read_text(encoding="utf-8")

    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )

    logger.info("Listing site folders under Drive root %s", settings.google_drive_root_folder_id)
    site_folders = gc.list_subfolders(settings.google_drive_root_folder_id)
    logger.info("Found %d site folders", len(site_folders))

    logger.info("Listing shared Drive folders (SIR, ISP, Building Inspection)")
    shared_cache = list_shared_folders_once(gc)

    results: list[PipelineResult] = []
    skipped = 0

    for folder in site_folders:
        site_title = str(folder.get("name") or "").strip()
        drive_folder_url = _folder_url(folder)

        if not site_title or not drive_folder_url:
            skipped += 1
            continue
        if site_filter and site_filter.lower() not in site_title.lower():
            continue

        match_terms = _build_site_match_terms(site_title, None)
        logger.info("Checking site folder: %s (match terms: %s)", site_title, match_terms)

        try:
            result = process_site_pipeline(
                gc,
                site_title,
                drive_folder_url,
                match_terms,
                shared_cache,
                system_prompt,
                settings,
            )
        except Exception as e:
            logger.exception("Unexpected pipeline failure for '%s'", site_title)
            result = PipelineResult(site_title=site_title, status="error", error=str(e))
        results.append(result)

        post_pipeline_result(settings.google_chat_webhook_url, result, drive_folder_url)

    print("\n" + "=" * 60)
    print(f"Daily DD Check -- {len(results)} sites processed, {skipped} skipped")
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
        if r.run_id:
            print(
                "       "
                f"run_id={r.run_id} "
                f"failed_step={r.failed_step or '-'} "
                f"quality={r.quality_score}/{r.quality_band or '-'} "
                f"manifest={r.manifest_path or '-'}"
            )
    print("=" * 60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Daily DD readiness check and report generation")
    parser.add_argument("--site", type=str, default=None, help="Run for a single site folder")
    args = parser.parse_args()
    main(site_filter=args.site)
