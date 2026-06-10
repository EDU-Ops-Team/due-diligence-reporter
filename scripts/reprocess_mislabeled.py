#!/usr/bin/env python3
"""
reprocess_mislabeled.py — One-off backfill: clear DD-Processed from emails
that were wrongly skipped by the buggy internal-sender heuristic.

Background: Before the X-Original-Sender fix landed, the catch-up sweep
(run 25070313003) labeled ~40 Group-routed external emails (CDS, regulators,
vendors, Docusign, etc.) as DD-Processed because their visible From: was
the auth.permitting@trilogy.com group address.

This script:
  1. Searches for emails labeled DD-Processed that contain a PDF and are
     in :inbox (the original scan's universe).
  2. For each, fetches headers and inspects the X-Original-Sender header.
  3. If X-Original-Sender exists and its domain is NOT in the internal
     domain list, the email was misclassified — remove DD-Processed so the
     next scan picks it up.
  4. Optionally applies an audit label (DD-Reprocessed-2026-04-28) so we can
     track exactly which emails were touched.

Run:
    uv run python scripts/reprocess_mislabeled.py            # dry-run by default
    uv run python scripts/reprocess_mislabeled.py --apply    # actually unlabel
    uv run python scripts/reprocess_mislabeled.py --apply --since 2026-04-20

Environment (from .env): same as scan_inbox.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Ensure project src is on path when running as a script
_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

from due_diligence_reporter.config import get_settings  # noqa: E402
from due_diligence_reporter.google_client import GoogleClient  # noqa: E402
from due_diligence_reporter.inbox_scanner import (  # noqa: E402
    _extract_email_metadata,
    _is_internal_sender,
    _parse_sender_email,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("reprocess_mislabeled")


AUDIT_LABEL = "DD-Reprocessed-2026-04-28"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually remove labels (default is dry-run).",
    )
    p.add_argument(
        "--since",
        default=None,
        help=(
            "Only reprocess emails received after this date (YYYY-MM-DD). "
            "Defaults to 14 days ago."
        ),
    )
    p.add_argument(
        "--max-results",
        type=int,
        default=200,
        help="Max emails to inspect (default 200).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()
    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )

    since = args.since
    if not since:
        since = (datetime.now(UTC) - timedelta(days=14)).strftime("%Y/%m/%d")
    else:
        # Gmail accepts YYYY/MM/DD
        since = since.replace("-", "/")

    processed_label = settings.inbox_processed_label

    # Search for already-labeled emails that match the original scan universe.
    query = (
        f"label:{processed_label} has:attachment filename:pdf "
        f"after:{since} -category:promotions -category:social"
    )
    logger.info("Reprocess query: %s (apply=%s)", query, args.apply)

    messages = gc.gmail_search(query, max_results=args.max_results)
    logger.info("Found %d candidates labeled %s", len(messages), processed_label)

    processed_label_id = gc.gmail_get_or_create_label(processed_label)
    audit_label_id = gc.gmail_get_or_create_label(AUDIT_LABEL)

    n_inspected = 0
    n_misclassified = 0
    n_already_external = 0  # had no X-Original-Sender (legitimately processed)
    n_correctly_internal = 0
    examples_to_unlabel: list[dict[str, str]] = []

    for stub in messages:
        msg_id = stub["id"]
        try:
            meta = _extract_email_metadata(gc, msg_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch %s: %s", msg_id, exc)
            continue

        n_inspected += 1
        # Only consider emails that have an X-Original-Sender (i.e., they
        # arrived via a Google Group). Direct-to-trilogy mail has no
        # X-Original-Sender and the prior label is correct.
        if not meta.original_sender.strip():
            n_already_external += 1
            continue

        # If the original sender's domain is internal, the prior label is
        # still correct.
        if _is_internal_sender(meta.original_sender, settings):
            n_correctly_internal += 1
            continue

        # Mismatch! The email was Group-routed from an external sender but
        # got labeled DD-Processed because the visible From: was internal.
        n_misclassified += 1
        original_email = _parse_sender_email(meta.original_sender)
        logger.info(
            "MISCLASSIFIED %s | from='%s' x-orig='%s' subject='%s'",
            msg_id,
            meta.sender,
            original_email,
            meta.subject,
        )
        examples_to_unlabel.append(
            {
                "id": msg_id,
                "subject": meta.subject,
                "original_sender": original_email,
            }
        )

        if args.apply:
            try:
                gc.gmail_modify_labels(
                    msg_id,
                    add_labels=[audit_label_id],
                    remove_labels=[processed_label_id],
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to unlabel %s: %s", msg_id, exc)

    logger.info("=" * 60)
    logger.info("Inspected:               %d", n_inspected)
    logger.info("No X-Original-Sender:    %d (kept labeled)", n_already_external)
    logger.info("Internal X-Orig-Sender:  %d (kept labeled)", n_correctly_internal)
    logger.info("MISCLASSIFIED:           %d", n_misclassified)
    logger.info("Apply mode:              %s", args.apply)
    logger.info("=" * 60)

    if examples_to_unlabel and not args.apply:
        logger.info("Sample misclassified (first 10):")
        for ex in examples_to_unlabel[:10]:
            logger.info("  %s | %s | %s", ex["id"], ex["original_sender"], ex["subject"])
        logger.info("Re-run with --apply to actually clear DD-Processed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
