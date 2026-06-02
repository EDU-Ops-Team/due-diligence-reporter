"""Tests for hosted ease-of-conversion skill wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from due_diligence_reporter.server import _format_skill_document, apply_e_occupancy_skill

_HOSTED_SKILL_TEXT = """---
name: ease-of-conversion
description: Test hosted skill
metadata:
  scorecard:
    themeId: test-site-due-diligence
---

# AI-First SIR Skill
"""

_HOSTED_REFERENCE_TEXT = """# Site Evaluator

## E-Occupancy Rating Bands

- GREEN (80-100): Strong candidate
- YELLOW (60-79): Viable with known challenges
- ORANGE (40-59): Significant barriers
- RED (0-39): Fatal flaws likely
"""


@pytest.fixture(autouse=True)
def _hosted_ease_conversion_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    skill_dir = tmp_path / "Ops-Skills" / "skills" / "ease-of-conversion"
    reference_dir = skill_dir / "references"
    reference_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_HOSTED_SKILL_TEXT, encoding="utf-8")
    (reference_dir / "site-eval-brainlift.md").write_text(
        _HOSTED_REFERENCE_TEXT,
        encoding="utf-8",
    )
    monkeypatch.setenv("OPS_SKILLS_REPO_PATH", str(tmp_path / "Ops-Skills"))
    return skill_dir


def test_ease_conversion_uses_hosted_orange_band() -> None:
    result = asyncio.run(apply_e_occupancy_skill("warehouse with hvac", stories=1))

    assert result["status"] == "success"
    assert result["final_score"] == 58
    assert result["zone"] == "ORANGE"
    assert result["ease_conversion_skill_version"] == "unversioned"
    assert result["ease_conversion_scorecard_theme_id"] == "test-site-due-diligence"
    assert result["report_data_fields"]["q2.e_occupancy_zone"] == "ORANGE"
    assert (
        result["report_data_fields"]["q2.e_occupancy_scorecard_theme_id"]
        == "test-site-due-diligence"
    )


def test_ease_conversion_uses_hosted_green_band() -> None:
    result = asyncio.run(apply_e_occupancy_skill("1-story office", stories=1))

    assert result["status"] == "success"
    assert result["final_score"] == 92
    assert result["zone"] == "GREEN"
    assert result["report_data_fields"]["q2.e_occupancy_skill_version"] == "unversioned"


def test_ease_conversion_document_format_includes_provenance() -> None:
    result = asyncio.run(apply_e_occupancy_skill("warehouse with hvac", stories=1))

    doc_text = _format_skill_document(
        skill_name="E-Occupancy",
        site_name="Alpha Test",
        date="06/02/2026",
        data=result,
    )

    assert "Zone: ORANGE" in doc_text
    assert "Skill Version: unversioned" in doc_text
    assert "Skill Source:" in doc_text
    assert "Reference Source:" in doc_text
    assert "Scorecard Theme: test-site-due-diligence" in doc_text
