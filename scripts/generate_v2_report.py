#!/usr/bin/env python3
"""
generate_v3_report.py â€” Manual V3 DD report generation for a single site.

Uses docs/prompts/prompt_v3.md and the V3 template.

Run:
    uv run python scripts/generate_v3_report.py --site "Alpha Keller"

Environment (from .env):
    WRIKE_ACCESS_TOKEN, GOOGLE_CLIENT_CONFIG, GOOGLE_TOKEN_FILE,
    ANTHROPIC_API_KEY, DD_TEMPLATE_V3_GOOGLE_DOC_ID,
    GOOGLE_DRIVE_ROOT_FOLDER_ID, OPENAI_API_KEY
"""

from __future__ import annotations

import argparse
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
    list_shared_folders_once,
    post_pipeline_result,
    process_site_pipeline,
    run_dd_report_agent,
)
from due_diligence_reporter.utils import build_site_match_terms as _build_site_match_terms  # noqa: E402
from due_diligence_reporter.wrike import (  # noqa: E402
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
    format="%(asctime)s [%(levelname)s] %(name)s â€” %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("generate_v3_report")


def main(site_filter: str, *, no_email: bool = False, skip_readiness: bool = False) -> None:
    settings = get_settings()
    wrike_cfg = load_wrike_config()

    # Suppress email: blank out settings so pipeline skips, and monkey-patch
    # the tool so the agent's send_dd_report_email call returns a skip message.
    if no_email:
        settings.email_sender = ""
        settings.email_app_password = ""

        from due_diligence_reporter import server as srv

        async def _skip_email(**kwargs: object) -> dict[str, str]:
            logger.info("send_dd_report_email SKIPPED (--no-email)")
            return {"status": "skipped", "message": "Email suppressed by --no-email flag"}

        srv.send_dd_report_email = _skip_email  # type: ignore[assignment]

    # Load the V2 agent system prompt
    prompt_path = _project_root / "docs" / "prompts" / "prompt_v3.md"
    if not prompt_path.exists():
        logger.error("Prompt file not found at %s", prompt_path)
        sys.exit(1)
    system_prompt = prompt_path.read_text(encoding="utf-8")

    # Init Google client
    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )

    # Fetch Wrike site records
    logger.info("Fetching Wrike site records...")
    all_records = _get_all_site_records(cfg=wrike_cfg)
    active_status_ids = _get_active_status_ids(access_token=wrike_cfg.access_token)
    active_records = filter_active_site_records(all_records, active_status_ids)
    logger.info("Found %d active site records", len(active_records))

    # Pre-fetch shared folder file lists
    logger.info("Listing shared Drive folders...")
    shared_cache = list_shared_folders_once(gc)

    # Find the matching site
    matched = [
        r for r in active_records
        if site_filter.lower() in r.get("title", "").lower()
    ]

    if not matched:
        logger.error("No active site matching '%s'", site_filter)
        sys.exit(1)
    if len(matched) > 1:
        titles = [r.get("title", "?") for r in matched]
        logger.error("Multiple sites match '%s': %s", site_filter, titles)
        sys.exit(1)

    record = matched[0]
    site_title = record.get("title", "Unknown")
    drive_folder_url = extract_google_folder_from_record(record)

    if not drive_folder_url:
        logger.error("'%s' has no Drive folder URL in Wrike", site_title)
        sys.exit(1)

    address = extract_address_from_record(record)
    match_terms = _build_site_match_terms(site_title, address)
    p1_email = extract_p1_email_from_record(record)

    logger.info("Generating V3 report for: %s", site_title)

    if skip_readiness:
        logger.info("--skip-readiness: bypassing readiness gate, calling agent directly")
        agent_result = run_dd_report_agent(
            site_title,
            system_prompt,
            settings.anthropic_report_model,
        )

        print(f"\n{'=' * 60}")
        print(f"V3 Report â€” {site_title}")
        print(f"{'=' * 60}")
        if agent_result.get("success"):
            print("  Status: report_created")
            print(f"  Report: {agent_result.get('doc_url', '(no URL)')}")
        else:
            print("  Status: generation_failed")
            print(f"  Error: {agent_result.get('error', 'unknown')}")
        print(f"{'=' * 60}")
        return

    result = process_site_pipeline(
        gc, site_title, drive_folder_url, match_terms,
        shared_cache, system_prompt, settings,
        p1_email=p1_email, site_address=address,
    )

    # Post to Google Chat if configured
    post_pipeline_result(
        settings.google_chat_webhook_url, result, drive_folder_url,
    )

    # Print result
    print(f"\n{'=' * 60}")
    print(f"V3 Report â€” {site_title}")
    print(f"{'=' * 60}")
    print(f"  Status: {result.status}")
    if result.doc_url:
        print(f"  Report: {result.doc_url}")
    if result.missing_docs:
        print(f"  Missing: {', '.join(result.missing_docs)}")
    if result.unresolved_tokens:
        print(f"  Unresolved: {len(result.unresolved_tokens)} tokens")
    if result.error:
        print(f"  Error: {result.error}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a V3 DD report for a single site")
    parser.add_argument("--site", type=str, required=True, help="Site name (substring match)")
    parser.add_argument("--no-email", action="store_true", help="Suppress all email sending")
    parser.add_argument("--skip-readiness", action="store_true", help="Bypass readiness gate, call agent directly")
    args = parser.parse_args()
    main(site_filter=args.site, no_email=args.no_email, skip_readiness=args.skip_readiness)

