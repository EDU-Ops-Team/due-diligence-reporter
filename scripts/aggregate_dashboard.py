#!/usr/bin/env python3
"""aggregate_dashboard.py — Roll up per-site dashboard payloads into sites.json.

Walks the Drive "All Locations" tree (``GOOGLE_DRIVE_ROOT_FOLDER_ID``),
reads every ``*.dashboard.json`` published by the reporter, merges them
into a single ``sites.json`` manifest, and writes it to configurable
targets:

1. Local filesystem at ``DASHBOARD_OUTPUT_PATH`` (always).
2. Drive folder at ``DASHBOARD_DRIVE_FOLDER_ID`` (if set).
3. GitHub repo at ``DASHBOARD_REPO`` via ``gh`` CLI (if set).

Can be invoked inline after a reporter run (single site) or on a cron
(sweep everything). Safe to re-run — writes are idempotent.

Usage::

    uv run python scripts/aggregate_dashboard.py             # sweep all sites
    uv run python scripts/aggregate_dashboard.py --dry-run   # no writes, print plan
    uv run python scripts/aggregate_dashboard.py --no-push   # skip GitHub push
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

# Ensure project src is on path when running as a script
_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env")

from due_diligence_reporter.config import Settings, get_settings  # noqa: E402
from due_diligence_reporter.dashboard_aggregate import (  # noqa: E402
    CandidatePayload,
    MergeResult,
    merge_payloads,
    slug_from_filename,
)
from due_diligence_reporter.google_client import GoogleClient  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("aggregate_dashboard")

DASHBOARD_FILE_SUFFIX = ".dashboard.json"


# ─────────────────────────────────────────────────────────────────────────────
# Drive discovery
# ─────────────────────────────────────────────────────────────────────────────


def collect_candidates(
    gc: GoogleClient,
    *,
    root_folder_id: str,
    site_filter: str | None = None,
) -> list[CandidatePayload]:
    """Walk one level deep under ``root_folder_id`` and load every payload."""
    candidates: list[CandidatePayload] = []

    try:
        site_folders = gc.list_subfolders(root_folder_id)
    except Exception as e:
        logger.error("Failed to list site folders under %s: %s", root_folder_id, e)
        return []

    logger.info("Found %d site folders under All Locations", len(site_folders))

    needle = (site_filter or "").strip().lower()
    for folder in site_folders:
        folder_name = folder.get("name", "")
        folder_id = folder.get("id", "")
        if not folder_id:
            continue
        if needle and needle not in folder_name.lower():
            continue

        try:
            files = gc.list_files_in_folder(folder_id)
        except Exception as e:
            logger.warning("Could not list files for %s (%s): %s", folder_name, folder_id, e)
            continue

        payload_files = [f for f in files if f.get("name", "").endswith(DASHBOARD_FILE_SUFFIX)]
        if not payload_files:
            logger.info("No dashboard payloads in %s — skipping", folder_name)
            continue

        for f in payload_files:
            filename = f.get("name", "")
            file_id = f.get("id", "")
            slug = slug_from_filename(filename)
            try:
                raw = gc.download_file_bytes(file_id)
                payload = json.loads(raw.decode("utf-8"))
            except Exception as e:
                logger.warning(
                    "Failed to load %s/%s (id=%s): %s",
                    folder_name, filename, file_id, e,
                )
                continue

            candidates.append(CandidatePayload(
                slug=slug,
                site_folder_name=folder_name,
                file_id=file_id,
                modified_time=f.get("modifiedTime", ""),
                payload=payload,
            ))

    logger.info("Loaded %d dashboard payloads total", len(candidates))
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# Writers
# ─────────────────────────────────────────────────────────────────────────────


def write_local(manifest_bytes: bytes, output_path: Path) -> Path:
    """Write sites.json to a local filesystem path. Creates parent dirs."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(manifest_bytes)
    logger.info("Wrote manifest locally: %s (%d bytes)", output_path, len(manifest_bytes))
    return output_path


def upload_to_drive(
    gc: GoogleClient,
    *,
    folder_id: str,
    manifest_bytes: bytes,
    filename: str = "sites.json",
) -> str:
    """Upsert sites.json into a Drive folder. Trashes any prior copy."""
    try:
        files = gc.list_files_in_folder(folder_id)
    except Exception as e:
        logger.warning("Drive list failed, skipping trash-sweep: %s", e)
        files = []

    for f in files:
        if f.get("name") == filename:
            file_id = f.get("id")
            if not file_id:
                continue
            try:
                gc.drive_service.files().update(
                    fileId=file_id,
                    body={"trashed": True},
                    supportsAllDrives=True,
                ).execute()
                logger.info("Trashed prior manifest in Drive: id=%s", file_id)
            except Exception as e:
                logger.warning("Failed to trash prior manifest: %s", e)

    uploaded = gc.upload_file_to_folder(
        folder_id=folder_id,
        file_name=filename,
        file_bytes=manifest_bytes,
        mime_type="application/json",
    )
    url = uploaded.get("webViewLink", "")
    logger.info("Uploaded manifest to Drive: %s", url)
    return url


def push_to_github(
    *,
    repo: str,
    branch: str,
    repo_path: str,
    manifest_bytes: bytes,
    commit_message: str,
) -> str:
    """Clone, overwrite, commit, push. Returns the short commit SHA or ''."""
    if not shutil.which("gh") or not shutil.which("git"):
        logger.warning("gh and/or git not available on PATH — skipping GitHub push")
        return ""

    with tempfile.TemporaryDirectory(prefix="dashboard-push-") as tmp:
        workdir = Path(tmp) / "repo"
        clone = subprocess.run(
            ["gh", "repo", "clone", repo, str(workdir), "--", "--depth", "1", "--branch", branch],
            capture_output=True, text=True,
        )
        if clone.returncode != 0:
            logger.error("gh clone failed for %s@%s: %s", repo, branch, clone.stderr.strip())
            return ""

        target = workdir / repo_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(manifest_bytes)

        # Short-circuit if nothing changed.
        status = subprocess.run(
            ["git", "-C", str(workdir), "status", "--porcelain"],
            capture_output=True, text=True,
        )
        if not status.stdout.strip():
            logger.info("Manifest unchanged on %s — no commit needed", repo)
            return ""

        env = os.environ.copy()
        env.setdefault("GIT_AUTHOR_NAME", "dd-dashboard-bot")
        env.setdefault("GIT_AUTHOR_EMAIL", "dd-dashboard-bot@users.noreply.github.com")
        env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
        env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])

        for cmd in (
            ["git", "-C", str(workdir), "add", repo_path],
            ["git", "-C", str(workdir), "commit", "-m", commit_message],
            ["git", "-C", str(workdir), "push", "origin", branch],
        ):
            r = subprocess.run(cmd, capture_output=True, text=True, env=env)
            if r.returncode != 0:
                logger.error("command failed: %s — %s", " ".join(cmd), r.stderr.strip())
                return ""

        sha = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True,
        )
        commit_sha = sha.stdout.strip()
        logger.info("Pushed manifest to %s@%s at %s", repo, branch, commit_sha)
        return commit_sha


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def run(
    *,
    dry_run: bool = False,
    no_push: bool = False,
    site_filter: str | None = None,
    settings: Settings | None = None,
) -> MergeResult:
    settings = settings or get_settings()

    root_folder_id = settings.google_drive_root_folder_id
    if not root_folder_id:
        logger.error("GOOGLE_DRIVE_ROOT_FOLDER_ID is not configured — aborting")
        sys.exit(2)

    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
    )

    candidates = collect_candidates(
        gc, root_folder_id=root_folder_id, site_filter=site_filter,
    )
    result = merge_payloads(candidates)

    logger.info(
        "Merge: %d site(s) kept, %d duplicate(s) resolved, %d invalid skipped",
        result.manifest["site_count"],
        len(result.duplicates_resolved),
        len(result.skipped_invalid),
    )
    for note in result.duplicates_resolved:
        logger.info("  duplicate: %s", note)
    for note in result.skipped_invalid:
        logger.info("  skipped: %s", note)

    if dry_run:
        logger.info("Dry run — not writing manifest")
        print(result.manifest_bytes.decode("utf-8"))
        return result

    output_path = _project_root / settings.dashboard_output_path
    write_local(result.manifest_bytes, output_path)

    if settings.dashboard_drive_folder_id:
        try:
            upload_to_drive(
                gc,
                folder_id=settings.dashboard_drive_folder_id,
                manifest_bytes=result.manifest_bytes,
            )
        except Exception as e:
            logger.warning("Drive upload failed (local + git targets still valid): %s", e)

    if settings.dashboard_repo and not no_push:
        commit_message = (
            f"Update sites.json — {result.manifest['site_count']} site(s)\n"
            f"generated_at: {result.manifest['generated_at']}"
        )
        push_to_github(
            repo=settings.dashboard_repo,
            branch=settings.dashboard_repo_branch,
            repo_path=settings.dashboard_repo_path,
            manifest_bytes=result.manifest_bytes,
            commit_message=commit_message,
        )
    elif no_push:
        logger.info("--no-push set — skipping GitHub push")
    else:
        logger.info("DASHBOARD_REPO not set — skipping GitHub push")

    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print manifest to stdout, write nothing.")
    parser.add_argument("--no-push", action="store_true", help="Skip GitHub push even if DASHBOARD_REPO is set.")
    parser.add_argument("--site", default=None, help="Case-insensitive substring filter on site folder name.")
    args = parser.parse_args(argv)

    run(dry_run=args.dry_run, no_push=args.no_push, site_filter=args.site)


if __name__ == "__main__":
    main()
