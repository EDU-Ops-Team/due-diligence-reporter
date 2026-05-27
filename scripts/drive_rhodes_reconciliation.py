#!/usr/bin/env python3
"""Reconcile existing M1 Drive files into Rhodes document records."""
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
from due_diligence_reporter.drive_rhodes_reconciliation import (  # noqa: E402
    run_drive_rhodes_reconciliation,
)
from due_diligence_reporter.google_client import GoogleClient  # noqa: E402
from due_diligence_reporter.rhodes import RhodesClient, list_rhodes_site_records  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("drive_rhodes_reconciliation")


def main(*, dry_run: bool = False, site: str = "") -> None:
    settings = get_settings()
    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )
    rhodes = RhodesClient()
    site_records = list_rhodes_site_records(client=rhodes)
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

    result = run_drive_rhodes_reconciliation(
        gc,
        site_records=site_records,
        dry_run=dry_run,
        rhodes_client=rhodes,
    )

    print(
        "Drive Rhodes reconciliation: "
        f"sites={result['sites_scanned']} "
        f"recognized_files={result['recognized_files']} "
        f"registered={result['registered']} "
        f"already_registered={result['already_registered']} "
        f"would_register={result['would_register']} "
        f"skipped={result['skipped']} "
        f"errors={result['errors']}"
    )
    for row in result["rows"]:
        print(
            "  "
            f"{row.get('site_title') or row.get('site_id')}: "
            f"{row.get('drive_file_name') or '-'} "
            f"({row.get('ddr_doc_type') or '-'}) -> "
            f"{row.get('status')}:{row.get('reason') or '-'}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--site", default="", help="Optional site id/name/address substring")
    args = parser.parse_args()
    main(dry_run=args.dry_run, site=args.site)
