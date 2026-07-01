#!/usr/bin/env python3
"""Compatibility wrapper for the repo-owned M2 source document sweep CLI."""
# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

from due_diligence_reporter.config import get_settings  # noqa: E402
from due_diligence_reporter.dd_republish_state_store import (  # noqa: E402
    build_dd_republish_state_store,
)
from due_diligence_reporter.google_client import GoogleClient  # noqa: E402
from due_diligence_reporter.m2_pipeline import (  # noqa: E402
    M2EventQueueError,
    build_m2_event_queue_from_env,
    emit_source_available_event,
    source_available_event_from_observation,
)
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
    republish_state_store = build_dd_republish_state_store()
    republish_state = republish_state_store.load()
    source_event_emitter = _source_event_emitter(dry_run=dry_run)
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
        source_event_emitter=source_event_emitter,
    )
    if not dry_run:
        republish_state_store.save(republish_state)

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


def _source_event_emitter(
    *,
    dry_run: bool,
) -> Callable[[Mapping[str, Any], Mapping[str, Any]], None] | None:
    try:
        event_queue = build_m2_event_queue_from_env()
    except M2EventQueueError as exc:
        logger.info("M2 source event queue not configured for source sweep: %s", exc)
        return None

    def emit(site_summary: Mapping[str, Any], source_observation: Mapping[str, Any]) -> None:
        source_event = source_available_event_from_observation(
            site=site_summary,
            observation=source_observation,
            producer={
                "workflow": "vendor-doc-republish-sweep",
                "artifact_type": "drive_source_observation",
            },
        )
        emit_source_available_event(
            event_queue,
            site=source_event["site"],
            source_type=source_event["source_type"],
            document=source_event["document"],
            producer=source_event["producer"],
            fingerprint=source_event["fingerprint"],
            event_id=source_event["event_id"],
            created_at=source_event["created_at"],
            apply=not dry_run,
        )

    return emit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--site", default="", help="Optional site id/name/address substring")
    args = parser.parse_args()
    main(dry_run=args.dry_run, site=args.site)
