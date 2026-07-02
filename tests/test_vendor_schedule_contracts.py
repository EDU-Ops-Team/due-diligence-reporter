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


def test_vendor_schedule_uses_bounded_source_sweep() -> None:
    text = _workflow_text("vendor-doc-republish-sweep.yml")
    shell = "\n".join(_run_blocks(text))

    assert "max_sites:" in text
    assert "INPUT_MAX_SITES: ${{ inputs.max_sites }}" in text
    assert (
        "SCHEDULE_MAX_SITES: ${{ vars.VENDOR_DOC_REPUBLISH_SWEEP_MAX_SITES || '5' }}"
        in text
    )
    assert 'if [ -z "$max_sites" ] && [ "${GITHUB_EVENT_NAME:-}" = "schedule" ]; then' in shell
    assert 'args+=(--max-sites "$max_sites")' in shell
