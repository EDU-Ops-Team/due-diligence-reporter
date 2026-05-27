from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_env_example_lists_current_rhodes_and_model_requirements() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    for name in (
        "ANTHROPIC_API_KEY",
        "RHODES_API_KEY",
        "RHODES_MCP_URL",
        "INBOX_INTERNAL_SKIP_LABEL",
    ):
        assert name in text


def test_how_it_works_env_section_has_no_stale_required_pricing_secret() -> None:
    text = (ROOT / "docs" / "process" / "HOW-IT-WORKS.md").read_text(encoding="utf-8")

    assert "PRICING_API_KEY` |" not in text
    assert "DD_TEMPLATE_GOOGLE_DOC_ID` |" not in text
    assert "DD_TEMPLATE_V3_GOOGLE_DOC_ID" in text
    assert text.count("GOOGLE_DRIVE_ROOT_FOLDER_ID` |") == 1
