"""Tests for the Opening Plan v2 skill MCP tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from due_diligence_reporter.server import (
    _OPENING_PLAN_SKILL_DIR,
    _build_opening_plan_prompt,
    _load_opening_plan_skill_files,
)


# ---------------------------------------------------------------------------
# Skill file loading
# ---------------------------------------------------------------------------


class TestLoadOpeningPlanSkillFiles:
    def test_skill_dir_exists(self):
        assert _OPENING_PLAN_SKILL_DIR.is_dir(), (
            f"Skill directory not found: {_OPENING_PLAN_SKILL_DIR}"
        )

    def test_all_required_files_exist(self):
        expected = [
            _OPENING_PLAN_SKILL_DIR / "SKILL.md",
            _OPENING_PLAN_SKILL_DIR / "references" / "field-mapping.md",
            _OPENING_PLAN_SKILL_DIR / "references" / "template-content.md",
            _OPENING_PLAN_SKILL_DIR / "references" / "executive-mindset.md",
        ]
        for path in expected:
            assert path.exists(), f"Missing skill file: {path}"

    def test_load_returns_all_four_keys(self):
        files = _load_opening_plan_skill_files()
        assert set(files.keys()) == {"skill", "field_mapping", "template_content", "executive_mindset"}

    def test_skill_file_is_non_empty(self):
        files = _load_opening_plan_skill_files()
        assert len(files["skill"]) > 1000, "SKILL.md appears too short"

    def test_field_mapping_contains_auto_and_derive(self):
        files = _load_opening_plan_skill_files()
        assert "AUTO" in files["field_mapping"]
        assert "DERIVE" in files["field_mapping"]
        assert "ENRICH" in files["field_mapping"]

    def test_template_content_contains_permit_paths(self):
        files = _load_opening_plan_skill_files()
        assert "Permit Paths" in files["template_content"] or "permit" in files["template_content"].lower()

    def test_executive_mindset_mentions_andy(self):
        files = _load_opening_plan_skill_files()
        assert "Andy" in files["executive_mindset"]


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


class TestBuildOpeningPlanPrompt:
    def _skill_files(self) -> dict[str, str]:
        return {
            "skill": "SKILL CONTENT",
            "field_mapping": "FIELD MAPPING CONTENT",
            "template_content": "TEMPLATE CONTENT",
            "executive_mindset": "EXECUTIVE MINDSET CONTENT",
        }

    def test_sir_content_included(self):
        prompt = _build_opening_plan_prompt(
            self._skill_files(), "Alpha Austin", "123 Main St, Austin TX", "SIR TEXT HERE"
        )
        assert "SIR TEXT HERE" in prompt

    def test_site_name_and_address_included(self):
        prompt = _build_opening_plan_prompt(
            self._skill_files(), "Alpha Austin", "123 Main St, Austin TX", "sir"
        )
        assert "Alpha Austin" in prompt
        assert "123 Main St, Austin TX" in prompt

    def test_all_reference_files_included(self):
        prompt = _build_opening_plan_prompt(
            self._skill_files(), "Alpha Austin", "123 Main St", "sir"
        )
        assert "FIELD MAPPING CONTENT" in prompt
        assert "TEMPLATE CONTENT" in prompt
        assert "EXECUTIVE MINDSET CONTENT" in prompt

    def test_pass1_only_instruction_present(self):
        prompt = _build_opening_plan_prompt(
            self._skill_files(), "Alpha Austin", "123 Main St", "sir"
        )
        assert "Pass 1" in prompt
        assert "Do NOT launch research agents" in prompt

    def test_optional_school_approval_included_when_provided(self):
        prompt = _build_opening_plan_prompt(
            self._skill_files(), "Alpha Austin", "123 Main St", "sir",
            school_approval_data="SCHOOL APPROVAL JSON DATA"
        )
        assert "SCHOOL APPROVAL JSON DATA" in prompt

    def test_optional_school_approval_absent_when_not_provided(self):
        prompt = _build_opening_plan_prompt(
            self._skill_files(), "Alpha Austin", "123 Main St", "sir"
        )
        assert "SCHOOL APPROVAL" not in prompt

    def test_optional_building_inspection_included_when_provided(self):
        prompt = _build_opening_plan_prompt(
            self._skill_files(), "Alpha Austin", "123 Main St", "sir",
            building_inspection_content="BUILDING INSPECTION TEXT"
        )
        assert "BUILDING INSPECTION TEXT" in prompt

    def test_target_open_date_included_when_provided(self):
        prompt = _build_opening_plan_prompt(
            self._skill_files(), "Alpha Austin", "123 Main St", "sir",
            target_open_date="08/12/2026"
        )
        assert "08/12/2026" in prompt

    def test_target_open_date_absent_when_not_provided(self):
        prompt = _build_opening_plan_prompt(
            self._skill_files(), "Alpha Austin", "123 Main St", "sir"
        )
        assert "Target Open Date" not in prompt


# ---------------------------------------------------------------------------
# apply_opening_plan_skill (async MCP tool)
# ---------------------------------------------------------------------------


class TestApplyOpeningPlanSkill:
    """Tests for the apply_opening_plan_skill async MCP tool.

    The tool uses asyncio.to_thread internally; tests call asyncio.run() to
    drive the coroutine synchronously, matching the pattern used elsewhere
    in this test suite.
    """

    import asyncio as _asyncio

    def _call(self, **kwargs):
        import asyncio
        from due_diligence_reporter.server import apply_opening_plan_skill
        return asyncio.run(apply_opening_plan_skill(**kwargs))

    def test_missing_site_name_returns_error(self):
        result = self._call(site_name="", site_address="123 Main", sir_content="SIR")
        assert result["status"] == "error"
        assert "required" in result["message"].lower()

    def test_missing_sir_content_returns_error(self):
        result = self._call(site_name="Alpha Test", site_address="123 Main", sir_content="")
        assert result["status"] == "error"

    def test_missing_anthropic_key_returns_error(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            result = self._call(
                site_name="Alpha Test", site_address="123 Main", sir_content="SIR TEXT"
            )
        assert result["status"] == "error"
        assert "ANTHROPIC_API_KEY" in result["error"]

    def test_successful_generation_no_publish(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="# Opening Plan\n\nFull plan content here.")]

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic") as mock_anthropic_mod:
                mock_client = MagicMock()
                mock_client.messages.create.return_value = mock_response
                mock_anthropic_mod.return_value = mock_client

                result = self._call(
                    site_name="Alpha Test",
                    site_address="123 Main St",
                    sir_content="SIR CONTENT HERE",
                )

        assert result["status"] == "success"
        assert "Opening Plan" in result["plan_content"]
        assert result["doc_url"] == ""

    def test_auto_publish_when_drive_url_provided(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="# Opening Plan content")]

        mock_gc = MagicMock()
        mock_gc.list_subfolders.return_value = [{"name": "M1 - Permitting", "id": "m1_folder_id"}]
        mock_gc.create_document.return_value = {
            "id": "new_doc_id",
            "webViewLink": "https://docs.google.com/document/d/new_doc_id",
        }

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic") as mock_anthropic_mod:
                mock_client = MagicMock()
                mock_client.messages.create.return_value = mock_response
                mock_anthropic_mod.return_value = mock_client

                with patch(
                    "due_diligence_reporter.server._make_google_client",
                    return_value=mock_gc,
                ):
                    result = self._call(
                        site_name="Alpha Test",
                        site_address="123 Main St",
                        sir_content="SIR CONTENT",
                        drive_folder_url="https://drive.google.com/drive/folders/abc123",
                    )

        assert result["status"] == "success"
        assert result["doc_url"] == "https://docs.google.com/document/d/new_doc_id"
        mock_gc.create_document.assert_called_once()
        call_kwargs = mock_gc.create_document.call_args
        assert "Opening Plan - Alpha Test" in str(call_kwargs)
        assert call_kwargs.kwargs.get("folder_id") == "m1_folder_id"
