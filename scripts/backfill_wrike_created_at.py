#!/usr/bin/env python3
"""
backfill_wrike_created_at.py — One-shot backfill of `wrike_created_at`
onto every row in the DD Dashboard's sites.json.

Clones the dashboard repo via `gh` CLI, patches sites.json with Wrike
createdDate for each site, commits, and pushes back. Also writes the
result to the local filesystem path configured by settings (if any)
so the next aggregator run doesn't overwrite.

For each entry in dd-dashboard/client/public/sites.json:
  1. Look up the Wrike folder by site_name.
  2. Pull its `createdDate` (ISO 8601).
  3. Write it back onto the row as `wrike_created_at`.

Run:
    uv run python scripts/backfill_wrike_created_at.py --dry-run  # no writes
    uv run python scripts/backfill_wrike_created_at.py            # write + push
    uv run python scripts/backfill_wrike_created_at.py --no-push  # write locally only

Env (from .env):
    WRIKE_API_TOKEN, WRIKE_ROOT_FOLDER_ID (standard pipeline env)
    DASHBOARD_REPO (defaults to EDU-Ops-Team/dd-dashboard)
    DASHBOARD_REPO_PATH (defaults to client/public/sites.json)
    DASHBOARD_REPO_BRANCH (defaults to main)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

from due_diligence_reporter.wrike import (  # noqa: E402
    build_site_summary,
    find_site_record,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("backfill_wrike_created_at")

DEFAULT_REPO = os.environ.get("DASHBOARD_REPO", "EDU-Ops-Team/dd-dashboard")
DEFAULT_BRANCH = os.environ.get("DASHBOARD_REPO_BRANCH", "main")
# Hard-coded because .env may set DASHBOARD_REPO_PATH to the aggregator's
# target ("public/sites.json") which is a different layout than this repo.
# The dashboard we're patching keeps sites.json at client/public/.
DEFAULT_PATH = "client/public/sites.json"


def _clone_and_read(
    *, repo: str, branch: str, repo_path: str, workdir: Path
) -> tuple[Path, dict]:
    """Shallow-clone the dashboard repo and load sites.json.

    Returns (workdir, payload).
    """
    if not shutil.which("gh") or not shutil.which("git"):
        raise RuntimeError("gh and/or git not available on PATH")

    target = workdir / "repo"
    r = subprocess.run(
        [
            "gh", "repo", "clone", repo, str(target),
            "--", "--depth", "1", "--branch", branch,
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"gh clone failed: {r.stderr.strip()}")

    sites_file = target / repo_path
    if not sites_file.exists():
        raise RuntimeError(f"sites.json not found at {sites_file}")

    payload = json.loads(sites_file.read_text(encoding="utf-8"))
    return target, payload


def _patch_payload(payload: dict) -> tuple[int, int, int]:
    """Mutate payload in place. Returns (updated, skipped, failed)."""
    sites = payload.get("sites", [])
    logger.info("Loaded %d sites", len(sites))

    updated = 0
    skipped = 0
    failed = 0

    for row in sites:
        slug = row.get("slug", "?")
        name = row.get("site_name") or row.get("marketing_name") or ""
        if row.get("wrike_created_at"):
            logger.info("[%s] already has wrike_created_at \u2014 skip", slug)
            skipped += 1
            continue
        if not name:
            logger.warning("[%s] no site_name \u2014 skip", slug)
            failed += 1
            continue
        try:
            record = find_site_record(site_name_or_id=name)
        except Exception as e:
            logger.warning("[%s] Wrike lookup failed for '%s': %s", slug, name, e)
            failed += 1
            continue
        if not record:
            logger.warning("[%s] no Wrike record for '%s'", slug, name)
            failed += 1
            continue
        summary = build_site_summary(record)
        created = summary.get("created_date") or ""
        if not created:
            logger.warning("[%s] Wrike record has no createdDate", slug)
            failed += 1
            continue
        row["wrike_created_at"] = created
        logger.info("[%s] %s  \u2192  %s", slug, name[:60], created)
        updated += 1

    return updated, skipped, failed


def _commit_and_push(
    *, workdir: Path, repo_path: str, branch: str, message: str
) -> str:
    """Git add/commit/push inside the cloned repo. Returns short SHA."""
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "dd-dashboard-bot")
    env.setdefault(
        "GIT_AUTHOR_EMAIL", "dd-dashboard-bot@users.noreply.github.com"
    )
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])

    status = subprocess.run(
        ["git", "-C", str(workdir), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    if not status.stdout.strip():
        logger.info("No changes to commit \u2014 sites.json unchanged")
        return ""

    for cmd in (
        ["git", "-C", str(workdir), "add", repo_path],
        ["git", "-C", str(workdir), "commit", "-m", message],
        ["git", "-C", str(workdir), "push", "origin", branch],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError(
                f"command failed: {' '.join(cmd)} \u2014 {r.stderr.strip()}"
            )

    sha = subprocess.run(
        ["git", "-C", str(workdir), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True,
    )
    return sha.stdout.strip()


def backfill(*, dry_run: bool, no_push: bool) -> int:
    repo = DEFAULT_REPO
    branch = DEFAULT_BRANCH
    repo_path = DEFAULT_PATH

    with tempfile.TemporaryDirectory(prefix="backfill-wrike-") as tmp:
        tmp_path = Path(tmp)
        try:
            workdir, payload = _clone_and_read(
                repo=repo, branch=branch, repo_path=repo_path, workdir=tmp_path
            )
        except Exception as e:
            logger.error("Clone/read failed: %s", e)
            return 2

        updated, skipped, failed = _patch_payload(payload)
        logger.info(
            "Summary: updated=%d  skipped=%d  failed=%d  total=%d",
            updated, skipped, failed, len(payload.get("sites", [])),
        )

        if dry_run:
            logger.info("Dry-run: not writing sites.json")
            return 0

        if updated == 0:
            logger.info("No updates to write")
            return 0

        sites_file = workdir / repo_path
        sites_file.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        logger.info("Patched sites.json in clone: %s", sites_file)

        if no_push:
            logger.info("--no-push: skipping git push")
            # Copy out so user can inspect after temp dir is cleaned
            out = _project_root / "sites.backfilled.json"
            out.write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
            logger.info("Wrote copy to %s", out)
            return 0

        try:
            sha = _commit_and_push(
                workdir=workdir,
                repo_path=repo_path,
                branch=branch,
                message=f"Backfill wrike_created_at for {updated} sites",
            )
        except Exception as e:
            logger.error("Push failed: %s", e)
            return 3

        if sha:
            logger.info("Pushed to %s@%s at %s", repo, branch, sha)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch + compute updates but don't write or push",
    )
    parser.add_argument(
        "--no-push", action="store_true",
        help="Write patched sites.json locally but don't push to GitHub",
    )
    args = parser.parse_args()
    return backfill(dry_run=args.dry_run, no_push=args.no_push)


if __name__ == "__main__":
    sys.exit(main())
