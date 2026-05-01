#!/usr/bin/env python3
"""
wrike_search.py — One-off Wrike record lookup.

Used to triage dashboard reconciliation orphans by listing every Wrike
record whose title contains any of the supplied keywords (case-insensitive),
or whose permalinkId matches any of the supplied IDs.

Usage:
    uv run python scripts/wrike_search.py "chicago,lombard,minneapolis"
    WRIKE_PERMALINK_IDS=1791910698 uv run python scripts/wrike_search.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

from due_diligence_reporter.wrike import (  # noqa: E402
    _get_active_status_ids,
    _get_all_site_records,
    is_record_active,
    load_wrike_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("wrike_search")


def main() -> int:
    keyword_arg = sys.argv[1] if len(sys.argv) > 1 else ""
    keywords = [k.strip().lower() for k in keyword_arg.split(",") if k.strip()]
    permalink_ids = {
        p.strip()
        for p in os.environ.get("WRIKE_PERMALINK_IDS", "").split(",")
        if p.strip()
    }

    if not keywords and not permalink_ids:
        logger.error("Pass keywords as 'a,b,c' or set WRIKE_PERMALINK_IDS env var")
        return 2

    cfg = load_wrike_config()
    records = _get_all_site_records(cfg=cfg)
    active_ids = _get_active_status_ids(access_token=cfg.access_token)

    logger.info(
        "Searching %d Wrike records (keywords=%s, permalink_ids=%s)",
        len(records),
        keywords or "<none>",
        sorted(permalink_ids) or "<none>",
    )

    hits = 0
    for rec in records:
        title = (rec.get("title") or "").strip()
        title_lc = title.lower()
        permalink = str(rec.get("permalinkId") or rec.get("id") or "")

        kw_match = any(k in title_lc for k in keywords) if keywords else False
        id_match = permalink in permalink_ids if permalink_ids else False
        if not (kw_match or id_match):
            continue

        active = is_record_active(rec, active_ids)
        status_id = rec.get("customStatusId") or "?"
        logger.info(
            "  [%s] permalink=%s id=%s status_id=%s\n      title=%r",
            "ACTIVE" if active else "INACT",
            permalink or "?",
            rec.get("id") or "?",
            status_id,
            title,
        )
        hits += 1

    logger.info("Done: %d matching record(s)", hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
