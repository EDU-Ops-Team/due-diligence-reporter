"""Shared helpers for loading hosted Ops-Skills files."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import get_settings

logger = logging.getLogger(__name__)

ARCHIVED_SKILLS_DIRNAME = "archived-skills"


@dataclass(frozen=True)
class _SkillCandidate:
    """One probe location for a skill file, with its provenance."""

    path: Path
    archived: bool = False


@dataclass(frozen=True)
class OpsSkillFile:
    """Text loaded from an Ops-Skills file and its provenance."""

    source: str
    text: str
    archived: bool = False


class OpsSkillLoadError(RuntimeError):
    """Raised when a hosted Ops-Skills file cannot be loaded."""


def load_ops_skill_file(skill_id: str, relative_path: str = "SKILL.md") -> OpsSkillFile:
    """Load a file from a hosted Ops-Skills skill.

    Git checkouts are read from origin/main when possible so a local stale or
    dirty worktree does not silently downgrade the runtime skill contract.

    Skills retired from skills/ into archived-skills/ still load (Ops-Skills
    archives superseded skills instead of deleting them), but with a warning:
    an archived skill has a replacement the caller should migrate to.
    """

    for candidate in _skill_file_candidates(skill_id, relative_path):
        loaded = _read_skill_candidate(candidate.path)
        if loaded is not None:
            source, text = loaded
            archived = candidate.archived
            if archived:
                logger.warning(
                    "Loaded Ops-Skills %s from %s: the skill is archived "
                    "(superseded); migrate this caller to its replacement skill.",
                    skill_id,
                    source,
                )
            return OpsSkillFile(source=source, text=text, archived=archived)

    raise OpsSkillLoadError(
        f"Could not load Ops-Skills {skill_id}/{relative_path}. Set "
        "OPS_SKILLS_REPO_PATH to the Ops-Skills repo root or install the "
        "Ops Skills Codex plugin cache."
    )


def _skill_file_candidates(skill_id: str, relative_path: str) -> list[_SkillCandidate]:
    settings = get_settings()
    configured = settings.ops_skills_repo_path.strip()
    candidates: list[_SkillCandidate] = []

    if configured:
        candidates.extend(_expand_skill_path(Path(configured), skill_id, relative_path))

    repo_root = Path(__file__).resolve().parents[2]
    workspace_root = repo_root.parent
    candidates.extend(_expand_skill_path(workspace_root / "Ops-Skills", skill_id, relative_path))
    candidates.extend(_expand_skill_path(workspace_root / "ops-skills", skill_id, relative_path))

    plugin_cache_root = (
        Path.home()
        / ".codex"
        / "plugins"
        / "cache"
        / "ops-skills"
        / "ops-skills"
        / "0.1.0"
    )
    candidates.append(
        _SkillCandidate(plugin_cache_root / "skills" / skill_id / relative_path)
    )
    candidates.append(
        _SkillCandidate(
            plugin_cache_root / ARCHIVED_SKILLS_DIRNAME / skill_id / relative_path,
            archived=True,
        )
    )

    return _dedupe_candidates(candidates)


def _expand_skill_path(
    path: Path, skill_id: str, relative_path: str
) -> list[_SkillCandidate]:
    """Probe locations for one configured root, active before archived.

    Roots pointing directly at a skill file or a single skill directory get
    no archived fallback: the caller pinned an exact location, and there is
    no reliable way to derive its archived sibling.
    """
    if path.name == Path(relative_path).name:
        return [_SkillCandidate(path)]
    if path.name == skill_id:
        return [_SkillCandidate(path / relative_path)]
    if path.name == "skills":
        return [
            _SkillCandidate(path / skill_id / relative_path),
            _SkillCandidate(
                path.parent / ARCHIVED_SKILLS_DIRNAME / skill_id / relative_path,
                archived=True,
            ),
        ]
    return [
        _SkillCandidate(path / "skills" / skill_id / relative_path),
        _SkillCandidate(path / skill_id / relative_path),
        _SkillCandidate(
            path / ARCHIVED_SKILLS_DIRNAME / skill_id / relative_path,
            archived=True,
        ),
    ]


def _dedupe_candidates(candidates: list[_SkillCandidate]) -> list[_SkillCandidate]:
    seen: set[str] = set()
    deduped: list[_SkillCandidate] = []
    for candidate in candidates:
        key = str(candidate.path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _read_skill_candidate(path: Path) -> tuple[str, str] | None:
    git_blob = _read_git_origin_main_blob(path)
    if git_blob is not None:
        return git_blob
    if path.exists():
        return str(path), path.read_text(encoding="utf-8")
    return None


def _read_git_origin_main_blob(path: Path) -> tuple[str, str] | None:
    repo_root = _find_git_repo_root(path)
    if repo_root is None:
        return None

    try:
        rel = path.relative_to(repo_root).as_posix()
    except ValueError:
        return None

    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={repo_root.as_posix()}",
                "-C",
                str(repo_root),
                "show",
                f"origin/main:{rel}",
            ],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    stdout = completed.stdout or ""
    if completed.returncode != 0 or not stdout.strip():
        return None
    return f"{repo_root} origin/main:{rel}", stdout


def _find_git_repo_root(path: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None
