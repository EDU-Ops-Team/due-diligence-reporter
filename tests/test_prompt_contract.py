from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "docs" / "prompts"
PROMPT_PATH = PROMPTS_DIR / "prompt_v4.md"
OLD_PROMPT_PATH = PROMPTS_DIR / ("prompt_" + "v" + "3.md")


def _prompt_text() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def test_prompt_v4_is_ascii_and_compact() -> None:
    text = _prompt_text()

    assert PROMPT_PATH.exists()
    assert not OLD_PROMPT_PATH.exists()
    text.encode("ascii")
    assert len(text.split()) < 2500


def test_prompt_v4_excludes_stale_workflow_rules() -> None:
    text = _prompt_text()
    retired_system = "W" + "rike"
    old_report_version = "V" + "3"
    stale_phrases = [
        "Always** call `send_dd_report_email`",
        "Every DD Report answers four questions",
        "How to Use Me",
        "Footnote Citations",
        "RayCon API",
        "dashboard publishing",
        "sources.trace_link",
        "Source Quality Notes",
        "Lease Conditions",
        "Trade-Offs and Deficiencies",
        old_report_version,
        retired_system,
        retired_system.lower(),
        "[1]",
    ]

    for phrase in stale_phrases:
        assert phrase not in text


def test_prompt_v4_keeps_first_round_contract() -> None:
    text = _prompt_text()
    required_phrases = [
        "Version:** 4.0.0",
        "Last Updated:** 2026-06-09",
        "V4 prompt contract",
        "first-round DDR",
        "current school year (8/12 or 9/8)",
        "lookup_rhodes_site_owner",
        "returned `drive_folder_url`",
        "linked/provisioned in Rhodes",
        "REBL Site ID",
        "create_dd_report",
        "verification.open_items",
        "exec.citations_block",
        "Source Notes",
        "one answer line",
        "support fact on its own plain line",
        "Never pack support facts",
        "Source notes render after the Referenced Reports",
        "does not render in",
        "After `create_dd_report` returns a document, stop.",
        "apply_opening_plan_skill",
        "Opening Plan is a normal DDR enrichment step",
        "still call the tool and let it return",
        "Always call",
    ]

    for phrase in required_phrases:
        assert phrase in text
