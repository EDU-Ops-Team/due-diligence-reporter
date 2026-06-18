from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_env_example_lists_current_rhodes_and_model_requirements() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_CAPACITY_MODEL",
        "LOCATIONOS_MCP_API_KEY",
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


def test_raycon_rebuild_package_documents_guarded_proof_input() -> None:
    text = (ROOT / "docs" / "reference" / "RayCon-DDR-Rebuild-Package.md").read_text(
        encoding="utf-8"
    )

    assert "require_raycon_git_commit" in text
    assert "--require-raycon-git-commit" in text
    assert "RayCon `/version`" in text
    assert "before Drive, Rhodes, Alpha Capacity" in text


def test_raycon_rebuild_package_blocks_no_capacity_auto_dispatch() -> None:
    text = (ROOT / "docs" / "reference" / "RayCon-DDR-Rebuild-Package.md").read_text(
        encoding="utf-8"
    )
    how_it_works = (ROOT / "docs" / "process" / "HOW-IT-WORKS.md").read_text(
        encoding="utf-8"
    )

    assert "dispatch_skipped=capacity_analysis_not_available" in text
    assert "instead of sending a no-capacity request" in text
    assert "does not dispatch a no-capacity RayCon job" in how_it_works
