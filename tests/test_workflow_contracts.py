from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _workflow_text(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def _run_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if stripped.startswith("run: |"):
            indent = len(line) - len(stripped)
            block: list[str] = []
            i += 1
            while i < len(lines):
                next_line = lines[i]
                next_stripped = next_line.lstrip()
                next_indent = len(next_line) - len(next_stripped)
                if next_stripped and next_indent <= indent:
                    break
                block.append(next_line)
                i += 1
            blocks.append("\n".join(block))
            continue
        i += 1
    return blocks


def test_workflow_dispatch_site_inputs_are_not_interpolated_in_shell() -> None:
    for workflow in (
        "daily-dd-check.yml",
        "vendor-doc-republish-sweep.yml",
        "reprocess-mislabeled.yml",
    ):
        shell = "\n".join(_run_blocks(_workflow_text(workflow)))
        assert "${{ inputs.site }}" not in shell
        assert "${{ inputs.since }}" not in shell
        assert "${{ inputs.max_results }}" not in shell


def test_publish_to_mcp_hive_never_packages_generated_secret_files() -> None:
    text = _workflow_text("publish-to-mcp-hive.yml")

    assert "RHODES_API_KEY" in text
    assert "RHODES_MCP_URL" in text
    assert "ANTHROPIC_API_KEY" in text

    zip_block = next(
        block for block in _run_blocks(text) if "due-diligence-reporter-mcp.zip" in block
    )
    for excluded in (
        '".env"',
        '"credentials/*"',
        '".gcp-saved-tokens.json"',
        '".dd_republish_state.json"',
        '".rhodes_registration_retry_state.json"',
        '".raycon_dispatch_state.json"',
        '".raycon_followup_alerts.json"',
    ):
        assert excluded in zip_block

    assert "grep -q ' .env$'" in zip_block
