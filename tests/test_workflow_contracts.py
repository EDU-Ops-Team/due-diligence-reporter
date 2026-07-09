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
        "ad-hoc-ddr-run.yml",
        "reprocess-mislabeled.yml",
        "drive-rhodes-reconciliation.yml",
        "portfolio-automation-gaps.yml",
        "m2-direct-dd-events.yml",
    ):
        shell = "\n".join(_run_blocks(_workflow_text(workflow)))
        assert "${{ inputs.apply }}" not in shell
        assert "${{ inputs.site }}" not in shell
        assert "${{ inputs.run_id }}" not in shell
        assert "${{ inputs.since }}" not in shell
        assert "${{ inputs.max_results }}" not in shell
        assert "${{ inputs.max_events }}" not in shell
        assert "${{ inputs.max_sites }}" not in shell
        assert "${{ inputs.include_clean }}" not in shell
        assert "${{ inputs.trigger_remediation }}" not in shell
        assert "${{ inputs.run_source_watch }}" not in shell
        assert "${{ inputs.mode }}" not in shell
        assert "${{ inputs.address }}" not in shell
        assert "${{ inputs.site_id }}" not in shell
        assert "${{ inputs.slug }}" not in shell
        assert "${{ inputs.drive_folder_url }}" not in shell
        assert "${{ inputs.notify }}" not in shell
        assert "${{ inputs.sor_write_mode }}" not in shell
        assert "${{ inputs.mcp_write_completed }}" not in shell
        assert "${{ inputs.document_first_on_sor_blocker }}" not in shell
        assert "${{ inputs.apply_source_sweep }}" not in shell
        assert "${{ inputs.source_type }}" not in shell
        assert "${{ inputs.fingerprint }}" not in shell


def test_workflows_do_not_use_generic_ops_skill_chat_webhook() -> None:
    for workflow_path in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = workflow_path.read_text(encoding="utf-8")
        assert "secrets.GOOGLE_CHAT_WEBHOOK_URL" not in text, workflow_path.name


def test_publish_to_mcp_hive_never_packages_generated_secret_files() -> None:
    text = _workflow_text("publish-to-mcp-hive.yml")

    assert "LOCATIONOS_MCP_API_KEY" in text
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
        '".m2_direct_dd_state.json"',
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
        '"Ad-Hoc DDR Run"',
        '"M2 Direct DD Events"',
    ):
        assert workflow in text


def test_long_running_mutating_workflows_have_timeouts() -> None:
    assert "timeout-minutes: 60" in _workflow_text("inbox-scan.yml")
    assert "timeout-minutes: 60" in _workflow_text("vendor-doc-republish-sweep.yml")
    assert "timeout-minutes: 60" in _workflow_text("drive-rhodes-reconciliation.yml")
    assert "timeout-minutes: 60" in _workflow_text("ad-hoc-ddr-run.yml")
    assert "timeout-minutes: 60" in _workflow_text("m2-direct-dd-events.yml")


def test_ad_hoc_ddr_workflow_dispatch_uses_runner_and_opt_in_notifications() -> None:
    text = _workflow_text("ad-hoc-ddr-run.yml")
    shell = "\n".join(_run_blocks(text))

    assert "name: Ad-Hoc DDR Run" in text
    assert "mode:" in text
    assert "source-republish" in text
    assert "resume-mcp-write" in text
    assert "INPUT_MODE: ${{ inputs.mode }}" in text
    assert "INPUT_NOTIFY: ${{ inputs.notify }}" in text
    assert "INPUT_RUN_ID: ${{ inputs.run_id }}" in text
    assert "INPUT_SOR_WRITE_MODE: ${{ inputs.sor_write_mode }}" in text
    assert "INPUT_MCP_WRITE_COMPLETED: ${{ inputs.mcp_write_completed }}" in text
    assert (
        "INPUT_DOCUMENT_FIRST_ON_SOR_BLOCKER: ${{ inputs.document_first_on_sor_blocker }}"
        in text
    )
    assert "INPUT_APPLY_SOURCE_SWEEP: ${{ inputs.apply_source_sweep }}" in text
    assert "Generation secrets not required for this ad-hoc DDR mode" in text
    assert 'if [ "$INPUT_MODE" = "resume-mcp-write" ]; then' in shell
    assert 'ARGS=("$INPUT_MODE" --run-id "$INPUT_RUN_ID")' in shell
    assert 'echo "site is required for $INPUT_MODE"' in shell
    assert 'ARGS=("$INPUT_MODE" --site "$INPUT_SITE")' in shell
    assert 'if [ "$INPUT_MODE" != "resume-mcp-write" ]; then' in shell
    assert 'if [ "${INPUT_NOTIFY:-false}" = "true" ]; then' in shell
    assert "ARGS+=(--notify)" in shell
    assert 'if [ "${INPUT_SOR_WRITE_MODE:-api}" != "api" ]; then' in shell
    assert 'ARGS+=(--sor-write-mode "$INPUT_SOR_WRITE_MODE")' in shell
    assert 'if [ "${INPUT_MCP_WRITE_COMPLETED:-false}" = "true" ]; then' in shell
    assert "ARGS+=(--mcp-write-completed)" in shell
    assert 'if [ "${INPUT_DOCUMENT_FIRST_ON_SOR_BLOCKER:-true}" = "false" ]; then' in shell
    assert "ARGS+=(--no-document-first-on-sor-blocker)" in shell
    assert 'if [ "${INPUT_APPLY_SOURCE_SWEEP:-false}" = "true" ]; then' in shell
    assert "ARGS+=(--apply)" in shell
    assert "ARGS+=(--dry-run)" in shell
    assert 'uv run ddr run-site "${ARGS[@]}" | tee ad-hoc-ddr-run.json' in shell
    assert "actions/upload-artifact@v4" in text
    assert "DD_REPUBLISH_STATE_STORE" in text
    assert "GCP_FIRESTORE_SERVICE_ACCOUNT_JSON missing" not in text


def test_drive_rhodes_reconciliation_uploads_dashboard_telemetry() -> None:
    text = _workflow_text("drive-rhodes-reconciliation.yml")

    assert "--telemetry-output reports/telemetry/drive-rhodes-reconciliation-telemetry.json" in text
    assert '--run-id "drive-rhodes-reconciliation-${{ github.run_id }}"' in text
    assert "Upload reconciliation telemetry artifact" in text
    assert "name: drive-rhodes-reconciliation-telemetry" in text
    assert "reports/telemetry/drive-rhodes-reconciliation-telemetry.json" in text


def test_vendor_doc_republish_scheduled_runs_are_gated_by_repo_variable() -> None:
    text = _workflow_text("vendor-doc-republish-sweep.yml")

    assert (
        "if: ${{ github.event_name != 'schedule' || "
        "vars.VENDOR_DOC_REPUBLISH_SWEEP_ENABLED == 'true' }}"
    ) in text


def test_m2_direct_dd_scheduled_runs_are_gated_by_repo_variable() -> None:
    text = _workflow_text("m2-direct-dd-events.yml")

    assert (
        "if: ${{ github.event_name != 'schedule' || "
        "vars.M2_DIRECT_DD_EVENTS_ENABLED == 'true' }}"
    ) in text


def test_incoming_source_workflows_require_aerie_secret_for_handoffs() -> None:
    vendor_text = _workflow_text("vendor-doc-republish-sweep.yml")
    m2_text = _workflow_text("m2-direct-dd-events.yml")
    m2_shell = "\n".join(_run_blocks(m2_text))

    assert "AERIE_API_KEY: ${{ secrets.AERIE_API_KEY }}" in vendor_text
    assert "AERIE_API_KEY missing" in vendor_text
    assert "AERIE_API_KEY: ${{ secrets.AERIE_API_KEY }}" in m2_text
    assert 'require_secret AERIE_API_KEY "AERIE_API_KEY missing"' in m2_shell


def test_scheduled_dd_workflows_use_repo_cli_surfaces() -> None:
    daily_text = _workflow_text("daily-dd-check.yml")
    daily_shell = "\n".join(_run_blocks(daily_text))
    vendor_text = _workflow_text("vendor-doc-republish-sweep.yml")
    vendor_shell = "\n".join(_run_blocks(vendor_text))
    m2_text = _workflow_text("m2-direct-dd-events.yml")
    m2_shell = "\n".join(_run_blocks(m2_text))

    assert 'uv run ddr daily-check "${ARGS[@]}"' in daily_shell
    assert "scripts/daily_dd_check.py" not in daily_shell
    assert 'uv run ddr source-sweep "${args[@]}"' in vendor_shell
    assert "scripts/vendor_doc_republish_sweep.py" not in vendor_shell
    assert 'uv run ddr "${ARGS[@]}" | tee m2-poll-events.json' in m2_shell
    assert "ARGS=(m2 poll-events" in m2_shell
    assert "ARGS=(m2 source-watch)" in m2_shell
    assert 'uv run ddr "${ARGS[@]}" | tee m2-execute-ready.json' in m2_shell
    assert "ARGS=(m2 execute-ready" in m2_shell


def test_active_runtime_surfaces_do_not_mention_braintrust() -> None:
    for path in (
        ROOT / "src" / "due_diligence_reporter" / "adhoc_runner.py",
        ROOT / ".github" / "workflows" / "daily-dd-check.yml",
        ROOT / ".github" / "workflows" / "vendor-doc-republish-sweep.yml",
        ROOT / ".github" / "workflows" / "ad-hoc-ddr-run.yml",
        ROOT / ".github" / "workflows" / "m2-direct-dd-events.yml",
        ROOT / "docs" / "prompts" / "prompt_v4.md",
    ):
        assert "braintrust" not in path.read_text(encoding="utf-8").lower(), path


def test_portfolio_gap_snapshot_triggers_aadp_remediation_without_oauth() -> None:
    text = _workflow_text("portfolio-automation-gaps.yml")

    assert "LOCATIONOS_MCP_API_KEY" in text
    assert "RHODES_API_KEY" in text
    assert (
        "name: Build portfolio gap snapshot\n"
        "        env:\n"
        "          LOCATIONOS_MCP_API_KEY: ${{ secrets.LOCATIONOS_MCP_API_KEY }}\n"
        "          RHODES_API_KEY: ${{ secrets.RHODES_API_KEY }}"
    ) in text
    assert "DDR_GOOGLE_CHAT_WEBHOOK_URL" in text
    assert "secrets.GOOGLE_CHAT_WEBHOOK_URL" not in text
    assert "portfolio-gaps" in text
    assert "portfolio-automation-gaps.json" in text
    assert "EDU-Ops-Team/alpha-analysis-downstream-processing" in text
    assert "trilogy-group/" not in text
    assert "AADP_REMEDIATION_REPO_TOKEN" in text
    assert "AADP_DRIVE_PARENT_FOLDER_ID" in text
    assert (
        "name: Trigger AADP remediation for correctable gaps\n"
        "        if: ${{ github.event_name != 'workflow_dispatch' || inputs.trigger_remediation != 'false' }}\n"
        "        env:\n"
        "          AADP_DRIVE_PARENT_FOLDER_ID: ${{ vars.AADP_DRIVE_PARENT_FOLDER_ID || secrets.AADP_DRIVE_PARENT_FOLDER_ID }}\n"
        "          GCP_FIRESTORE_SERVICE_ACCOUNT_JSON: ${{ secrets.GCP_FIRESTORE_SERVICE_ACCOUNT_JSON }}\n"
        "          PIPELINE_STATUS_FIRESTORE_COLLECTION: alphaAnalysisPipelineStatus\n"
        "          PIPELINE_STATUS_FIRESTORE_DATABASE: edu-ops-email-router\n"
        "          PIPELINE_STATUS_FIRESTORE_PROJECT_ID: ap-automation-464623\n"
        "          PIPELINE_STATUS_STORE: firestore\n"
        "          LOCATIONOS_MCP_API_KEY: ${{ secrets.LOCATIONOS_MCP_API_KEY }}\n"
        "          RHODES_API_KEY: ${{ secrets.RHODES_API_KEY }}"
    ) in text
    assert "AADP telemetry not persisted" in text
    assert "scripts/run_aadp_portfolio_gap_remediation.py" in text
    assert "post_portfolio_gap_summary.py" in text
    assert "--result-output portfolio-automation-gaps-notification.json" in text
    assert "Build dashboard telemetry artifact" in text
    assert "if: ${{ always() }}" in text
    assert "scripts/build_portfolio_gap_telemetry.py" in text
    assert "--output reports/telemetry/portfolio-automation-gaps-telemetry.json" in text
    assert "reports/telemetry/portfolio-automation-gaps-telemetry.json" in text
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
        "ad-hoc-ddr-run.yml",
    ):
        text = _workflow_text(workflow)

        assert "DD_REPUBLISH_STATE_STORE" in text
        assert "DD_REPUBLISH_STATE_FIRESTORE_PROJECT_ID" in text
        assert "DD_REPUBLISH_STATE_FIRESTORE_DATABASE" in text
        assert "DD_REPUBLISH_STATE_FIRESTORE_COLLECTION" in text
        assert "GCP_FIRESTORE_SERVICE_ACCOUNT_JSON" in text
        assert "No Firestore service account configured" in text
        assert "GCP_FIRESTORE_SERVICE_ACCOUNT_JSON missing" not in text


def test_m2_direct_dd_workflow_uses_firestore_event_queue_and_state_store() -> None:
    text = _workflow_text("m2-direct-dd-events.yml")
    shell = "\n".join(_run_blocks(text))

    assert "GCP_FIRESTORE_SERVICE_ACCOUNT_JSON missing" in text
    assert "[ -n \"${{ secrets.GCP_FIRESTORE_SERVICE_ACCOUNT_JSON }}\" ]" not in shell
    assert "require_secret GCP_FIRESTORE_SERVICE_ACCOUNT_JSON" in shell
    assert "GCP_FIRESTORE_SERVICE_ACCOUNT_JSON: ${{ secrets.GCP_FIRESTORE_SERVICE_ACCOUNT_JSON }}" in text
    assert "M2_DD_EVENT_FIRESTORE_PROJECT_ID" in text
    assert "M2_DD_EVENT_FIRESTORE_DATABASE" in text
    assert "M2_DD_EVENT_FIRESTORE_COLLECTION" in text
    assert "m2DirectDdEvents" in text
    assert "M2_DD_STATE_STORE" in text
    assert "M2_DD_STATE_FIRESTORE_PROJECT_ID" in text
    assert "M2_DD_STATE_FIRESTORE_DATABASE" in text
    assert "M2_DD_STATE_FIRESTORE_COLLECTION" in text
    assert "ddrM2DirectDdState" in text
    assert "target_site_id:" in text
    assert "target_event_id:" in text
    assert "source_event_limit:" in text
    assert 'INPUT_SOURCE_EVENT_LIMIT: ${{ inputs.source_event_limit }}' in text
    assert 'ARGS+=(--source-event-limit "$SOURCE_EVENT_LIMIT")' in shell
    assert 'TARGET_SITE_ID="${INPUT_TARGET_SITE_ID:-}"' in shell
    assert 'TARGET_EVENT_ID="${INPUT_TARGET_EVENT_ID:-}"' in shell
    assert 'ARGS+=(--site-id "$TARGET_SITE_ID")' in shell
    assert 'ARGS+=(--event-id "$TARGET_EVENT_ID")' in shell
    assert "m2-execute-ready.json" in text


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


def test_raycon_followup_workflow_is_job_disabled() -> None:
    text = _workflow_text("raycon-followup.yml")

    assert "if: ${{ false }}" in text
    assert "RayCon is no longer an active DDR dependency" in text


def test_raycon_followup_passes_alpha_capacity_model_override() -> None:
    text = _workflow_text("raycon-followup.yml")

    assert "OPENAI_CAPACITY_MODEL: ${{ vars.OPENAI_CAPACITY_MODEL || 'gpt-4o' }}" in text
    assert 'echo "OPENAI_CAPACITY_MODEL=${OPENAI_CAPACITY_MODEL}" >> .env' in text


def test_raycon_followup_workflow_dispatch_can_require_raycon_git_commit() -> None:
    text = _workflow_text("raycon-followup.yml")
    shell = "\n".join(_run_blocks(text))

    assert "require_raycon_git_commit:" in text
    assert "Expected RayCon /version git_commit before processing jobs" in text
    assert (
        "INPUT_REQUIRE_RAYCON_GIT_COMMIT: ${{ inputs.require_raycon_git_commit }}"
        in text
    )
    assert "${{ inputs.require_raycon_git_commit }}" not in shell
    assert (
        'if [ -n "${INPUT_REQUIRE_RAYCON_GIT_COMMIT:-}" ]; then\n'
        '            ARGS+=(--require-raycon-git-commit "$INPUT_REQUIRE_RAYCON_GIT_COMMIT")\n'
        "          fi"
    ) in shell


def test_alpha_capacity_workflows_fail_fast_without_openai_key() -> None:
    for workflow in (
        "inbox-scan.yml",
        "raycon-followup.yml",
        "vendor-doc-republish-sweep.yml",
        "daily-dd-check.yml",
        "ad-hoc-ddr-run.yml",
        "m2-direct-dd-events.yml",
        "publish-to-mcp-hive.yml",
    ):
        text = _workflow_text(workflow)

        assert "OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}" in text
        assert "OPENAI_CAPACITY_MODEL: ${{ vars.OPENAI_CAPACITY_MODEL || 'gpt-4o' }}" in text
        assert "OPENAI_API_KEY missing" in text


def test_locationos_workflows_accept_preferred_or_legacy_secret() -> None:
    for workflow in (
        "daily-dd-check.yml",
        "drive-rhodes-reconciliation.yml",
        "portfolio-automation-gaps.yml",
        "publish-to-mcp-hive.yml",
        "raycon-followup.yml",
        "vendor-doc-republish-sweep.yml",
        "ad-hoc-ddr-run.yml",
        "m2-direct-dd-events.yml",
    ):
        text = _workflow_text(workflow)

        assert "secrets.LOCATIONOS_MCP_API_KEY || secrets.RHODES_API_KEY" in text
        assert "LOCATIONOS_MCP_API_KEY or RHODES_API_KEY missing" in text

    for workflow in (
        "daily-dd-check.yml",
        "drive-rhodes-reconciliation.yml",
        "inbox-scan.yml",
        "portfolio-automation-gaps.yml",
        "publish-to-mcp-hive.yml",
        "raycon-followup.yml",
        "vendor-doc-republish-sweep.yml",
        "ad-hoc-ddr-run.yml",
        "m2-direct-dd-events.yml",
    ):
        text = _workflow_text(workflow)

        assert "LOCATIONOS_MCP_API_KEY: ${{ secrets.LOCATIONOS_MCP_API_KEY }}" in text
        assert 'echo "LOCATIONOS_MCP_API_KEY=${LOCATIONOS_TOKEN}" >> .env' in text
