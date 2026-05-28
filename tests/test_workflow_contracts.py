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
        "drive-rhodes-reconciliation.yml",
        "portfolio-automation-gaps.yml",
    ):
        shell = "\n".join(_run_blocks(_workflow_text(workflow)))
        assert "${{ inputs.site }}" not in shell
        assert "${{ inputs.since }}" not in shell
        assert "${{ inputs.max_results }}" not in shell
        assert "${{ inputs.max_sites }}" not in shell
        assert "${{ inputs.include_clean }}" not in shell


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


def test_publish_to_mcp_hive_cancels_stale_mutating_runs() -> None:
    text = _workflow_text("publish-to-mcp-hive.yml")

    assert "actions: write" in text
    assert "Cancel stale mutating workflow runs" in text
    assert 'select(.headSha != \\"${CURRENT_SHA}\\")' in text
    for workflow in (
        '"Inbox Scan"',
        '"Vendor Doc Republish Sweep"',
        '"Daily DD Check"',
        '"RayCon Follow-up"',
        '"Drive Rhodes Reconciliation"',
    ):
        assert workflow in text


def test_long_running_mutating_workflows_have_timeouts() -> None:
    assert "timeout-minutes: 60" in _workflow_text("inbox-scan.yml")
    assert "timeout-minutes: 60" in _workflow_text("vendor-doc-republish-sweep.yml")
    assert "timeout-minutes: 60" in _workflow_text("drive-rhodes-reconciliation.yml")


def test_portfolio_gap_snapshot_is_read_only_rhodes_workflow() -> None:
    text = _workflow_text("portfolio-automation-gaps.yml")

    assert "RHODES_API_KEY" in text
    assert "portfolio-gaps" in text
    assert "portfolio-automation-gaps.json" in text
    assert "OAUTH_CLIENT_ID" not in text
    assert "OAUTH_REFRESH_TOKEN" not in text


def test_inbox_scan_can_enable_firestore_retry_state_without_required_secret() -> None:
    text = _workflow_text("inbox-scan.yml")

    assert "RHODES_RETRY_STATE_STORE" in text
    assert "RHODES_RETRY_STATE_FIRESTORE_PROJECT_ID" in text
    assert "RHODES_RETRY_STATE_FIRESTORE_DATABASE" in text
    assert "RHODES_RETRY_STATE_FIRESTORE_COLLECTION" in text
    assert "GCP_FIRESTORE_SERVICE_ACCOUNT_JSON" in text
    assert "No Firestore service account configured" in text
    assert "GCP_FIRESTORE_SERVICE_ACCOUNT_JSON missing" not in text


def test_dd_republish_workflows_can_enable_firestore_state_without_required_secret() -> None:
    for workflow in (
        "inbox-scan.yml",
        "raycon-followup.yml",
        "vendor-doc-republish-sweep.yml",
    ):
        text = _workflow_text(workflow)

        assert "DD_REPUBLISH_STATE_STORE" in text
        assert "DD_REPUBLISH_STATE_FIRESTORE_PROJECT_ID" in text
        assert "DD_REPUBLISH_STATE_FIRESTORE_DATABASE" in text
        assert "DD_REPUBLISH_STATE_FIRESTORE_COLLECTION" in text
        assert "GCP_FIRESTORE_SERVICE_ACCOUNT_JSON" in text
        assert "No Firestore service account configured" in text
        assert "GCP_FIRESTORE_SERVICE_ACCOUNT_JSON missing" not in text


def test_raycon_followup_can_enable_firestore_runtime_state_without_required_secret() -> None:
    text = _workflow_text("raycon-followup.yml")

    assert "RAYCON_RUNTIME_STATE_STORE" in text
    assert "RAYCON_RUNTIME_STATE_FIRESTORE_PROJECT_ID" in text
    assert "RAYCON_RUNTIME_STATE_FIRESTORE_DATABASE" in text
    assert "RAYCON_RUNTIME_STATE_DISPATCH_FIRESTORE_COLLECTION" in text
    assert "RAYCON_RUNTIME_STATE_ALERT_FIRESTORE_COLLECTION" in text
    assert "GCP_FIRESTORE_SERVICE_ACCOUNT_JSON" in text
    assert "No Firestore service account configured" in text
    assert "GCP_FIRESTORE_SERVICE_ACCOUNT_JSON missing" not in text
