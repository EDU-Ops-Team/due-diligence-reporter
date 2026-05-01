#!/usr/bin/env python3
"""delete_dashboard_slugs.py — one-off DELETE pass for a fixed slug list.

Used to clean up dashboard rows that the title-driven ``reconcile_dashboard``
classifies as RENAME-SUSPECT (so it skips them) but a human has confirmed are
actually safe to drop. Examples: leftover duplicate rows after the Rebl
canonical-slug migration where the canonical sibling already exists.

Pass slugs via the ``--slugs`` argument (comma-separated) or stdin (newline-
separated). Default is dry-run; use ``--apply`` to actually issue DELETE.
Reason header is configurable via ``--reason``.

Run:
    uv run python scripts/delete_dashboard_slugs.py \\
        --slugs "alpha-school-chicago-350-gems-full-school,alpha-school-lombard-835" \\
        --reason "dashboard-cleanup-post-rebl-migration"

Env (loaded from .env):
    DASHBOARD_PUBLISH_URL    (default https://dd-dashboard-three.vercel.app)
    DASHBOARD_PUBLISH_SECRET (required for --apply)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

import requests  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("delete_dashboard_slugs")

_DEFAULT_BASE_URL = "https://dd-dashboard-three.vercel.app"


def _parse_slugs(raw: str) -> list[str]:
    out: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        s = chunk.strip()
        if s:
            out.append(s)
    return out


def _delete(base_url: str, slug: str, secret: str, reason: str, *, timeout: int = 20) -> tuple[bool, str]:
    """DELETE one slug. Returns (success, note). 404 is treated as success."""
    url = f"{base_url}/api/sites/{slug}/publish"
    try:
        r = requests.delete(
            url,
            headers={
                "Authorization": f"Bearer {secret}",
                "X-Reconcile-Reason": reason,
            },
            timeout=timeout,
        )
    except requests.RequestException as e:
        return False, f"network error: {e}"
    if r.status_code == 200:
        return True, "deleted"
    if r.status_code == 404:
        return True, "already absent (404)"
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slugs", default="", help="Comma- or newline-separated slug list")
    parser.add_argument("--reason", default="manual-cleanup", help="X-Reconcile-Reason header")
    parser.add_argument("--apply", action="store_true", help="Actually issue DELETE; default is dry-run")
    args = parser.parse_args(argv)

    raw = args.slugs
    if not raw and not sys.stdin.isatty():
        raw = sys.stdin.read()
    slugs = _parse_slugs(raw)

    if not slugs:
        logger.error("No slugs provided. Use --slugs or pipe via stdin.")
        return 2

    base_url = (os.environ.get("DASHBOARD_PUBLISH_URL") or _DEFAULT_BASE_URL).rstrip("/")
    secret = os.environ.get("DASHBOARD_PUBLISH_SECRET", "")

    if args.apply and not secret:
        logger.error("--apply requires DASHBOARD_PUBLISH_SECRET")
        return 3

    logger.info(
        "delete_dashboard_slugs mode=%s base_url=%s reason=%r count=%d",
        "APPLY" if args.apply else "DRY_RUN",
        base_url,
        args.reason,
        len(slugs),
    )

    if not args.apply:
        for slug in slugs:
            logger.info("DRY_RUN would DELETE %s", slug)
        return 0

    deleted, failed = 0, 0
    for i, slug in enumerate(slugs):
        ok, note = _delete(base_url, slug, secret, args.reason)
        if ok:
            deleted += 1
            logger.info("[%d/%d] %s: %s", i + 1, len(slugs), slug, note)
        else:
            failed += 1
            logger.warning("[%d/%d] %s: %s", i + 1, len(slugs), slug, note)
        # Modest spacing — each DELETE commits to GitHub which Vercel watches.
        if i + 1 < len(slugs):
            time.sleep(1.0)

    logger.info("Done: %d deleted, %d failed of %d", deleted, failed, len(slugs))
    return 0 if failed == 0 else 4


if __name__ == "__main__":
    sys.exit(main())
