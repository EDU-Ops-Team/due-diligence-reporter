"""Manifest persistence and redaction checks for DD pipeline runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from .pipeline_contracts import PipelineRun

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_MANIFEST_DIR = PROJECT_ROOT / ".ddr-runs"

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "access_token",
    "refresh_token",
    "app_password",
    "password",
    "authorization",
    "credential",
    "secret",
)


def local_manifest_path(run_id: str) -> Path:
    return RUN_MANIFEST_DIR / f"{run_id}.json"


def persist_run_manifest(run: PipelineRun, *, root: Path | None = None) -> Path:
    """Persist a run manifest locally and return its path."""
    base = root or RUN_MANIFEST_DIR
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{run.run_id}.json"
    path.write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
    run.manifest_path = str(path)
    return path


def load_run_manifest(run_id: str, *, root: Path | None = None) -> dict[str, Any]:
    path = (root or RUN_MANIFEST_DIR) / f"{run_id}.json"
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def manifest_has_secret_like_value(payload: dict[str, Any]) -> bool:
    """Return True if a manifest payload appears to contain secret material."""
    return _contains_secret(payload)


def _contains_secret(value: Any, key_path: str = "") -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
                if child not in (None, "", [], {}):
                    return True
            if _contains_secret(child, f"{key_path}.{lowered}"):
                return True
    elif isinstance(value, list):
        return any(_contains_secret(item, key_path) for item in value)
    elif isinstance(value, str):
        return _looks_like_secret(value)
    return False


def _looks_like_secret(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) < 20:
        return False
    secret_prefixes = ("sk-", "xoxb-", "ya29.", "ghp_", "github_pat_")
    return stripped.startswith(secret_prefixes)
