#!/usr/bin/env python3
"""Sweep Rhodes sites for core DDR source docs and republish existing DDRs."""
# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

from due_diligence_reporter.config import get_settings  # noqa: E402
from due_diligence_reporter.dd_republish import (  # noqa: E402
    load_state as load_republish_state,
    save_state as save_republish_state,
)
from due_diligence_reporter.google_client import GoogleClient  # noqa: E402
from due_diligence_reporter.report_pipeline import (  # noqa: E402
    list_shared_folders_once,
    process_site_pipeline,
)
from due_diligence_reporter.rhodes import list_rhodes_site_records  # noqa: E402
from due_diligence_reporter.vendor_doc_sweep import run_vendor_doc_republish_sweep  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("vendor_doc_republish_sweep")


def main(*, dry_run: bool = False, site: str = "") -> None:
    settings = get_settings()
    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )

    prompt_path = _project_root / "docs" / "prompts" / "prompt_v4.md"
    system_prompt = prompt_path.read_text(encoding="utf-8")
    shared_cache = list_shared_folders_once(gc)
    republish_state = load_republish_state()
    site_records = list_rhodes_site_records()
    if site.strip():
        needle = site.strip().lower()
        site_records = [
            record
            for record in site_records
            if needle
            in " ".join(
                str(record.get(key) or "").lower()
                for key in ("id", "site_id", "title", "name", "slug", "address")
            )
        ]

    result = run_vendor_doc_republish_sweep(
        gc,
        settings=settings,
        system_prompt=system_prompt,
        shared_cache=shared_cache,
        republish_state=republish_state,
        site_records=site_records,
        dry_run=dry_run,
        pipeline_runner=process_site_pipeline,
    )
    if not dry_run:
        save_republish_state(republish_state)

    print(
        "Vendor doc republish sweep: "
        f"sites={result['sites_scanned']} "
        f"events={result['source_events']} "
        f"republished={result['republished']} "
        f"skipped={result['skipped']} "
        f"errors={result['errors']}"
    )
    for row in result["rows"]:
        print(
            "  "
            f"{row.get('site_title') or row.get('site_id')}: "
            f"{row.get('republish_reason') or row.get('reason') or '-'} -> "
            f"{row.get('dd_report_republish') or row.get('status')}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--site", default="", help="Optional site id/name/address substring")
    args = parser.parse_args()
    main(dry_run=args.dry_run, site=args.site)
