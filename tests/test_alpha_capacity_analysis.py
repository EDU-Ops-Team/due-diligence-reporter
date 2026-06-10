"""Tests for hosted Alpha Capacity Analysis wiring."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from due_diligence_reporter import alpha_capacity_analysis as aca
from due_diligence_reporter.alpha_capacity_analysis import (
    AlphaCapacitySkill,
    alpha_capacity_analysis_filename,
    generate_alpha_capacity_analysis_artifact,
    load_alpha_capacity_skill,
    normalize_alpha_capacity_payload,
    run_alpha_capacity_analysis,
)

_HOSTED_SKILL_TEXT = """---
name: alpha-capacity-analysis
metadata:
  scorecard:
    themeId: test-capacity-building-fit
  version: '2.0'
---

# Alpha Schools Capacity Analysis
"""


@pytest.fixture()
def hosted_capacity_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    skill_dir = tmp_path / "Ops-Skills" / "skills" / "alpha-capacity-analysis"
    reference_dir = skill_dir / "references"
    reference_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_HOSTED_SKILL_TEXT, encoding="utf-8")
    (reference_dir / "microschool-ruleset.md").write_text(
        "# Microschool Ruleset\n42 SF/student",
        encoding="utf-8",
    )
    (reference_dir / "250plus-ruleset.md").write_text(
        "# 250+ Ruleset\n35-50 SF/student",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPS_SKILLS_REPO_PATH", str(tmp_path / "Ops-Skills"))
    return skill_dir


def _skill() -> AlphaCapacitySkill:
    return AlphaCapacitySkill(
        version="2.0",
        source="test-skill",
        scorecard_theme_id="test-capacity-building-fit",
        skill_text="# Skill",
        microschool_reference_source="micro-ref",
        microschool_ruleset="# Microschool",
        plus250_reference_source="250-ref",
        plus250_ruleset="# 250+",
    )


class _FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.kwargs: dict[str, Any] | None = None
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )
        self._content = json.dumps(payload)

    def _create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self._content),
                )
            ]
        )


class _FakeResponsesClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.kwargs: dict[str, Any] | None = None
        self.responses = SimpleNamespace(create=self._create)
        self._content = json.dumps(payload)

    def _create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return SimpleNamespace(output_text=self._content)


def test_load_alpha_capacity_skill_reads_hosted_skill_and_references(
    hosted_capacity_skill: Path,
) -> None:
    skill = load_alpha_capacity_skill()

    assert skill.version == "2.0"
    assert skill.scorecard_theme_id == "test-capacity-building-fit"
    assert "SKILL.md" in skill.source
    assert "Microschool Ruleset" in skill.microschool_ruleset
    assert "250+ Ruleset" in skill.plus250_ruleset


def test_run_alpha_capacity_analysis_normalizes_model_json() -> None:
    raw_payload = {
        "status": "success",
        "ruleset": "Microschool",
        "fastest_open": {
            "capacity_students": 36,
            "classroom_count": 4,
            "basis": "Strict room schedule.",
        },
        "max_capacity": {
            "capacity_students": 54,
            "classroom_count": 6,
            "basis": "Merged-room max plan.",
        },
        "assumptions": ["Uses Block Plan capacities only."],
    }
    client = _FakeClient(raw_payload)

    result = run_alpha_capacity_analysis(
        site_name="Alpha Keller",
        site_address="123 Main St, Keller, TX",
        block_plan_content="Block Plan says strict 36 and max 54 students.",
        total_building_sf=8400,
        block_plan_file_id="bp123",
        client=client,
        model="test-model",
        skill=_skill(),
    )

    assert result["status"] == "success"
    assert result["source_label"] == "Alpha Capacity Analysis"
    assert result["strict"]["capacity_students"] == 36
    assert result["max"]["capacity_students"] == 54
    assert result["report_data_fields"] == {
        "exec.fastest_open_capacity": "36",
        "exec.max_capacity_capacity": "54",
    }
    assert client.kwargs is not None
    assert client.kwargs["model"] == "test-model"
    assert client.kwargs["response_format"] == {"type": "json_object"}


def test_run_alpha_capacity_analysis_defaults_blank_capacity_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_CAPACITY_MODEL", "")
    raw_payload = {
        "status": "success",
        "strict": {"capacity_students": 36, "basis": "Strict room schedule."},
        "max": {"capacity_students": 54, "basis": "Merged-room max plan."},
    }
    client = _FakeClient(raw_payload)

    result = run_alpha_capacity_analysis(
        site_name="Alpha Keller",
        site_address="123 Main St, Keller, TX",
        block_plan_content="Block Plan says strict 36 and max 54 students.",
        client=client,
        model="",
        skill=_skill(),
    )

    assert result["status"] == "success"
    assert client.kwargs is not None
    assert client.kwargs["model"] == "gpt-4o"


def test_run_alpha_capacity_analysis_uses_pdf_file_when_text_is_empty() -> None:
    raw_payload = {
        "status": "success",
        "ruleset": "Microschool",
        "strict": {"capacity_students": 32, "basis": "Attached PDF room schedule."},
        "max": {"capacity_students": 48, "basis": "Attached PDF max plan."},
    }
    client = _FakeResponsesClient(raw_payload)

    result = run_alpha_capacity_analysis(
        site_name="Alpha Keller",
        site_address="123 Main St, Keller, TX",
        block_plan_content="",
        block_plan_file_bytes=b"%PDF-block-plan",
        block_plan_file_name="Alpha Keller Block Plan.pdf",
        total_building_sf=8400,
        block_plan_file_id="bp123",
        client=client,
        model="test-model",
        skill=_skill(),
    )

    assert result["status"] == "success"
    assert result["strict"]["capacity_students"] == 32
    assert result["max"]["capacity_students"] == 48
    assert client.kwargs is not None
    assert client.kwargs["model"] == "test-model"
    assert client.kwargs["text"] == {"format": {"type": "json_object"}}
    user_content = client.kwargs["input"][1]["content"]
    system_content = client.kwargs["input"][0]["content"][0]["text"]
    assert "attached Block Plan PDF page evidence" in system_content
    assert "do not rely only on lossy text extraction" in system_content
    assert user_content[0]["type"] == "input_text"
    assert "Block Plan PDF attached: yes" in user_content[0]["text"]
    assert user_content[1]["type"] == "input_file"
    assert user_content[1]["filename"] == "Alpha Keller Block Plan.pdf"
    assert user_content[1]["file_data"].startswith("data:application/pdf;base64,")


def test_run_alpha_capacity_analysis_falls_back_to_explicit_schedule_counts() -> None:
    raw_payload = {
        "status": "insufficient_evidence",
        "ruleset": "Microschool",
        "strict": {"capacity_students": None},
        "max": {"capacity_students": None},
        "open_items": ["Confirm total building SF and natural-light assumptions."],
        "room_inventory": [{"name": "Classroom 01", "net_leasable_sf": 378}],
    }
    client = _FakeClient(raw_payload)

    result = run_alpha_capacity_analysis(
        site_name="Alpha Miami Beach 300 71st 3rd",
        site_address="300 71st St, Miami Beach, FL",
        block_plan_content=(
            "LL 1,397 SF 40 / 70 STUDENTS\n"
            "L3 851 SF 24 / 42 STUDENTS\n"
            "L2 1,747 SF 50 / 87 STUDENTS"
        ),
        total_building_sf=None,
        block_plan_file_id="bp123",
        client=client,
        model="test-model",
        skill=_skill(),
    )

    assert result["status"] == "success"
    assert result["strict"]["capacity_students"] == 114
    assert result["max"]["capacity_students"] == 199
    assert result["report_data_fields"] == {
        "exec.fastest_open_capacity": "114",
        "exec.max_capacity_capacity": "199",
    }
    assert result["capacity_source_detail"] == {
        "source": "explicit_block_plan_schedule",
        "student_count_pairs": [
            {"strict": 40, "max": 70},
            {"strict": 24, "max": 42},
            {"strict": 50, "max": 87},
        ],
    }
    assert result["model_status_before_schedule_fallback"] == "insufficient_evidence"
    assert result["open_items"] == [
        "Confirm total building SF and natural-light assumptions."
    ]
    assert result["room_inventory"] == [
        {"name": "Classroom 01", "net_leasable_sf": 378}
    ]


def test_explicit_capacity_pairs_preserve_intentional_duplicate_rows() -> None:
    assert aca._explicit_capacity_pairs(
        "L1 600 SF 24 / 42 STUDENTS\nL2 600 SF 24 / 42 STUDENTS"
    ) == [(24, 42), (24, 42)]


def test_explicit_capacity_pairs_handle_pdf_text_glued_to_next_level() -> None:
    assert aca._explicit_capacity_pairs(
        "LL1,397 SF40 / 70 STUDENTSL3851 SF24 / 42 STUDENTS\n"
        "L21,747 SF50 / 87 STUDENTS"
    ) == [(40, 70), (24, 42), (50, 87)]


def test_explicit_capacity_pairs_collapse_repeated_full_schedule() -> None:
    assert aca._explicit_capacity_pairs(
        "LL 40 / 70 STUDENTS\n"
        "L1 24 / 42 STUDENTS\n"
        "LL 40 / 70 STUDENTS\n"
        "L1 24 / 42 STUDENTS"
    ) == [(40, 70), (24, 42)]


def test_normalize_alpha_capacity_payload_requires_both_scenarios() -> None:
    result = normalize_alpha_capacity_payload(
        {"strict": {"capacity_students": 36}},
        skill=_skill(),
        site_name="Alpha Keller",
        site_address="123 Main St",
        total_building_sf=8400,
        block_plan_file_id="bp123",
        model="test-model",
    )

    assert result["status"] == "insufficient_evidence"
    assert "report_data_fields" not in result
    assert result["strict"]["capacity_students"] == 36
    assert result["max"]["capacity_students"] is None
    assert result["open_items"]


def test_generate_alpha_capacity_analysis_artifact_uploads_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, Any] = {}

    def fake_run(**_kwargs: Any) -> dict[str, Any]:
        called.update(_kwargs)
        return {
            "status": "success",
            "source_label": "Alpha Capacity Analysis",
            "strict": {"capacity_students": 36},
            "max": {"capacity_students": 54},
            "report_data_fields": {
                "exec.fastest_open_capacity": "36",
                "exec.max_capacity_capacity": "54",
            },
        }

    monkeypatch.setattr(aca, "run_alpha_capacity_analysis", fake_run)
    gc = MagicMock()
    gc.upload_file_to_folder.return_value = {
        "id": "cap-json-123",
        "webViewLink": "https://drive.google.com/file/d/cap-json-123/view",
    }

    result = generate_alpha_capacity_analysis_artifact(
        gc,
        m1_folder_id="m1-folder-id",
        site_name="Alpha Keller",
        site_address="123 Main St",
        block_plan_content="Block Plan text",
        total_building_sf=8400,
        block_plan_file_id="block123456789",
        block_plan_file_bytes=b"%PDF-block-plan",
        block_plan_file_name="Block Plan.pdf",
    )

    assert result["status"] == "success"
    assert called["block_plan_file_bytes"] == b"%PDF-block-plan"
    assert called["block_plan_file_name"] == "Block Plan.pdf"
    assert result["capacity_analysis_file_id"] == "cap-json-123"
    assert result["capacity_analysis"]["strict"]["capacity_students"] == 36
    kwargs = gc.upload_file_to_folder.call_args.kwargs
    assert kwargs["folder_id"] == "m1-folder-id"
    assert kwargs["mime_type"] == "application/json"
    assert kwargs["file_name"] == "Alpha Capacity Analysis - Alpha Keller - block1234567.json"
    uploaded_payload = json.loads(kwargs["file_bytes"].decode("utf-8"))
    assert uploaded_payload["max"]["capacity_students"] == 54


def test_alpha_capacity_analysis_filename_sanitizes_site_name() -> None:
    assert alpha_capacity_analysis_filename(
        site_name='Alpha: "Keller" / Test',
        block_plan_file_id="abc123456789xyz",
    ) == "Alpha Capacity Analysis - Alpha Keller Test - abc123456789.json"


def test_apply_alpha_capacity_analysis_skill_routes_without_drive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from due_diligence_reporter import server

    called: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> dict[str, Any]:
        called.update(kwargs)
        return {"status": "success", "strict": {"capacity_students": 36}, "max": {"capacity_students": 54}}

    monkeypatch.setattr(server, "run_alpha_capacity_analysis", fake_run)

    result = asyncio.run(
        server.apply_alpha_capacity_analysis_skill(
            site_name="Alpha Keller",
            site_address="123 Main St",
            block_plan_content="Block Plan text",
            block_plan_file_id="bp123",
            total_building_sf=8400,
        )
    )

    assert result["status"] == "success"
    assert called["site_name"] == "Alpha Keller"
    assert called["site_address"] == "123 Main St"
    assert called["total_building_sf"] == 8400
    assert called["block_plan_file_id"] == "bp123"


def test_apply_alpha_capacity_analysis_skill_downloads_pdf_for_drive_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from due_diligence_reporter import server

    called: dict[str, Any] = {}
    gc = MagicMock()
    gc.download_file_bytes.return_value = b"%PDF-block-plan"

    def fake_generate(_gc: Any, **kwargs: Any) -> dict[str, Any]:
        called["gc"] = _gc
        called.update(kwargs)
        return {"status": "success", "capacity_analysis_file_id": "cap-json"}

    monkeypatch.setattr(server, "_make_google_client", lambda: gc)
    monkeypatch.setattr(server, "_get_or_create_m1_folder", lambda _gc, _folder_id: {"id": "m1-folder"})
    monkeypatch.setattr(server, "generate_alpha_capacity_analysis_artifact", fake_generate)

    result = asyncio.run(
        server.apply_alpha_capacity_analysis_skill(
            site_name="Alpha Keller",
            site_address="123 Main St",
            block_plan_content="",
            drive_folder_url="https://drive.google.com/drive/folders/site-folder",
            block_plan_file_id="bp123",
            total_building_sf=8400,
        )
    )

    assert result["status"] == "success"
    gc.download_file_bytes.assert_called_once_with("bp123")
    assert called["gc"] is gc
    assert called["m1_folder_id"] == "m1-folder"
    assert called["block_plan_file_bytes"] == b"%PDF-block-plan"
    assert called["block_plan_file_id"] == "bp123"
