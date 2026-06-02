"""Tests for school approval state-history guidance."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from due_diligence_reporter.server import _format_skill_document, apply_school_approval_skill

_HOSTED_SKILL_TEXT = """---
name: school-approval
description: Test hosted skill
version: 9.9.9
---

# Score Addresses for Education Approval

```json
{
  "rules_version": "9.9.9"
}
```

## Baseline Score Table

| State | Score | Archetype | Approval Type | Gating | Timeline (days) |
|---|---|---|---|---|---|
| TX | 95 | MINIMAL | NONE | No | 7 |
| CA | 73 | NOTIFICATION | REGISTRATION_SIMPLE | No | 14 |
| OK | 88 | MINIMAL | NONE | No | 7 |
| OR | 65 | APPROVAL_REQUIRED | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes | 90 |
| GA | 78 | NOTIFICATION | REGISTRATION_SIMPLE | No | 14 |
"""


@pytest.fixture(autouse=True)
def _hosted_school_approval_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    skill_dir = tmp_path / "Ops-Skills" / "skills" / "school-approval"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_HOSTED_SKILL_TEXT, encoding="utf-8")
    monkeypatch.setenv("OPS_SKILLS_REPO_PATH", str(tmp_path / "Ops-Skills"))
    return skill_dir


def test_school_approval_exec_status_not_required_for_tx() -> None:
    result = asyncio.run(apply_school_approval_skill("TX"))

    assert result["status"] == "success"
    assert result["rules_version"] == "9.9.9"
    assert result["school_approval_skill_version"] == "9.9.9"
    assert result["exec_c_edreg_status"] == "Not required"
    assert result["alpha_is_operating_in_state"] is True
    assert result["report_data_fields"]["q1.school_approval_exec_status"] == "Not required"
    assert result["report_data_fields"]["q1.school_approval_rules_version"] == "9.9.9"


def test_school_approval_exec_status_required_and_have_done_for_ca() -> None:
    result = asyncio.run(apply_school_approval_skill("CA"))

    assert result["status"] == "success"
    assert result["approval_type"] == "REGISTRATION_SIMPLE"
    assert result["gating"] is False
    assert result["timeline_days"] == 14
    assert result["exec_c_edreg_status"] == "Required and have done"
    assert result["alpha_is_operating_in_state"] is True
    assert "currently operates in CA" in result["alpha_state_reference"]
    assert result["report_data_fields"]["q1.school_approval_exec_status"] == "Required and have done"


def test_school_approval_exec_status_required_have_not_done_for_or() -> None:
    result = asyncio.run(apply_school_approval_skill("OR"))

    assert result["status"] == "success"
    assert result["exec_c_edreg_status"] == "Required have not done"
    assert result["alpha_has_worked_in_state"] is True
    assert result["alpha_is_operating_in_state"] is False
    assert "Oregon" in result["alpha_state_reference"]
    assert result["report_data_fields"]["q1.school_approval_exec_status"] == "Required have not done"


def test_school_approval_uses_address_when_state_is_not_provided() -> None:
    result = asyncio.run(apply_school_approval_skill(address="421 E 11th St, Tulsa, OK 74120"))

    assert result["status"] == "success"
    assert result["state"] == "OK"
    assert result["archetype"] == "MINIMAL"
    assert result["approval_type"] == "NONE"
    assert result["timeline_days"] == 7


def test_school_approval_address_parser_prefers_postal_state_over_words() -> None:
    result = asyncio.run(
        apply_school_approval_skill(address="Can we open in 421 E 11th St, Tulsa, OK 74120?")
    )

    assert result["status"] == "success"
    assert result["state"] == "OK"


def test_school_approval_document_format_includes_state_reference() -> None:
    result = asyncio.run(apply_school_approval_skill("GA"))

    doc_text = _format_skill_document(
        skill_name="School Approval",
        site_name="Alpha Test",
        date="04/22/2026",
        data=result,
    )

    assert "Executive Status: Required and have done" in doc_text
    assert "Alpha State Reference:" in doc_text
    assert "Rules Version: 9.9.9" in doc_text
    assert "Skill Version: 9.9.9" in doc_text
