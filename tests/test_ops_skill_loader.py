"""Tests for the hosted Ops-Skills loader."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from due_diligence_reporter.ops_skill_loader import (
    OpsSkillLoadError,
    load_ops_skill_file,
)

_SKILL_TEXT = """---
name: alpha-phasing-plan
description: Test skill
---

# Body
"""


def _write_skill(root: Path, directory: str, skill_id: str) -> Path:
    skill_dir = root / directory / skill_id
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_SKILL_TEXT, encoding="utf-8")
    return skill_dir


def test_loads_active_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "Ops-Skills"
    _write_skill(repo, "skills", "alpha-phasing-plan")
    monkeypatch.setenv("OPS_SKILLS_REPO_PATH", str(repo))

    loaded = load_ops_skill_file("alpha-phasing-plan")

    assert loaded.text == _SKILL_TEXT
    assert loaded.archived is False


def test_falls_back_to_archived_skill_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = tmp_path / "Ops-Skills"
    _write_skill(repo, "archived-skills", "alpha-phasing-plan")
    monkeypatch.setenv("OPS_SKILLS_REPO_PATH", str(repo))

    with caplog.at_level(logging.WARNING, logger="due_diligence_reporter.ops_skill_loader"):
        loaded = load_ops_skill_file("alpha-phasing-plan")

    assert loaded.text == _SKILL_TEXT
    assert loaded.archived is True
    assert any(
        "archived" in record.getMessage() and "alpha-phasing-plan" in record.getMessage()
        for record in caplog.records
    )


def test_prefers_active_over_archived_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "Ops-Skills"
    active_dir = _write_skill(repo, "skills", "alpha-phasing-plan")
    archived_dir = repo / "archived-skills" / "alpha-phasing-plan"
    archived_dir.mkdir(parents=True)
    (archived_dir / "SKILL.md").write_text("archived copy", encoding="utf-8")
    monkeypatch.setenv("OPS_SKILLS_REPO_PATH", str(repo))

    loaded = load_ops_skill_file("alpha-phasing-plan")

    assert loaded.archived is False
    assert loaded.text == _SKILL_TEXT
    assert str(active_dir) in loaded.source


def test_skills_dir_root_falls_back_to_archived_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "Ops-Skills"
    _write_skill(repo, "archived-skills", "alpha-phasing-plan")
    monkeypatch.setenv("OPS_SKILLS_REPO_PATH", str(repo / "skills"))

    loaded = load_ops_skill_file("alpha-phasing-plan")

    assert loaded.archived is True
    assert loaded.text == _SKILL_TEXT


def test_archived_flag_ignores_ancestor_directory_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "archived-skills" / "Ops-Skills"
    _write_skill(repo, "skills", "alpha-phasing-plan")
    monkeypatch.setenv("OPS_SKILLS_REPO_PATH", str(repo))

    loaded = load_ops_skill_file("alpha-phasing-plan")

    assert loaded.archived is False


def test_raises_when_skill_missing_everywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "Ops-Skills"
    repo.mkdir()
    monkeypatch.setenv("OPS_SKILLS_REPO_PATH", str(repo))
    # Keep the machine-level Codex plugin cache out of the fallback chain.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    # A skill id that no fallback root (workspace sibling checkout, plugin
    # cache) can satisfy, so the raise path is hermetic on dev machines.
    with pytest.raises(OpsSkillLoadError):
        load_ops_skill_file("nonexistent-skill-for-loader-test")
