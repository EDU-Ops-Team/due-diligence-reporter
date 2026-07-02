from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

from due_diligence_reporter import ddr_cli


def test_ddr_diagnose_points_to_run_site(capsys) -> None:
    exit_code = ddr_cli.main(["diagnose", "--site", "Alpha Keller"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert 'uv run ddr run-site diagnose --site "Alpha Keller"' in out


def test_ddr_run_site_prints_runner_payload(monkeypatch, capsys) -> None:
    def fake_run_site_command(args):
        return 2, {
            "status": "error",
            "mode": args.mode,
            "site": args.site,
        }

    monkeypatch.setattr(ddr_cli, "run_site_command", fake_run_site_command)

    exit_code = ddr_cli.main(["run-site", "diagnose", "--site", "Alpha Keller"])

    out = capsys.readouterr().out
    assert exit_code == 2
    assert json.loads(out) == {
        "mode": "diagnose",
        "site": "Alpha Keller",
        "status": "error",
    }


def test_ddr_daily_check_dispatches_repo_cli_surface(monkeypatch) -> None:
    calls = []

    def fake_daily_check(args):
        calls.append(args.site)
        return 0

    monkeypatch.setattr(ddr_cli, "_run_daily_check", fake_daily_check)

    exit_code = ddr_cli.main(["daily-check", "--site", "Alpha Keller"])

    assert exit_code == 0
    assert calls == ["Alpha Keller"]


def test_ddr_source_sweep_dispatches_repo_cli_surface(monkeypatch) -> None:
    calls = []

    def fake_source_sweep(args):
        calls.append({"site": args.site, "dry_run": args.dry_run})
        return 0

    monkeypatch.setattr(ddr_cli, "_run_source_sweep", fake_source_sweep)

    exit_code = ddr_cli.main(["source-sweep", "--site", "Alpha Keller", "--dry-run"])

    assert exit_code == 0
    assert calls == [{"site": "Alpha Keller", "dry_run": True}]


def test_ddr_daily_check_loads_repo_script_without_importable_scripts(
    monkeypatch,
    tmp_path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    call_path = tmp_path / "daily-call.json"
    (scripts_dir / "daily_dd_check.py").write_text(
        "\n".join([
            "import json",
            "from pathlib import Path",
            f"CALL_PATH = Path({str(call_path)!r})",
            "def main(site_filter=None):",
            "    CALL_PATH.write_text(",
            "        json.dumps({'site_filter': site_filter}),",
            "        encoding='utf-8',",
            "    )",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(ddr_cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setitem(sys.modules, "scripts", ModuleType("scripts"))
    monkeypatch.delitem(sys.modules, "scripts.daily_dd_check", raising=False)

    exit_code = ddr_cli._run_daily_check(SimpleNamespace(site="Alpha Keller"))

    assert exit_code == 0
    assert json.loads(call_path.read_text(encoding="utf-8")) == {
        "site_filter": "Alpha Keller",
    }


def test_ddr_source_sweep_loads_repo_script_without_importable_scripts(
    monkeypatch,
    tmp_path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    call_path = tmp_path / "source-sweep-call.json"
    (scripts_dir / "vendor_doc_republish_sweep.py").write_text(
        "\n".join([
            "import json",
            "from pathlib import Path",
            f"CALL_PATH = Path({str(call_path)!r})",
            "def main(*, dry_run=False, site=''):",
            "    CALL_PATH.write_text(",
            "        json.dumps({'dry_run': dry_run, 'site': site}),",
            "        encoding='utf-8',",
            "    )",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(ddr_cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setitem(sys.modules, "scripts", ModuleType("scripts"))
    monkeypatch.delitem(sys.modules, "scripts.vendor_doc_republish_sweep", raising=False)

    exit_code = ddr_cli._run_source_sweep(
        SimpleNamespace(site="Alpha Keller", dry_run=True)
    )

    assert exit_code == 0
    assert json.loads(call_path.read_text(encoding="utf-8")) == {
        "dry_run": True,
        "site": "Alpha Keller",
    }


def test_ddr_m2_consume_event_writes_local_state(tmp_path, capsys) -> None:
    event_path = tmp_path / "site-ready-event.json"
    state_path = tmp_path / ".m2_direct_dd_state.json"
    event_path.write_text(
        json.dumps(
            {
                "schema_version": "aadp.site_ready_for_ddr.v1",
                "event_id": "evt-cli",
                "status": "pending",
                "ready_for_ddr": True,
                "site": {
                    "id": "SITE1",
                    "name": "Alpha CLI",
                    "address": "123 Main St",
                },
                "drive": {
                    "site_folder_url": "https://drive.google.com/drive/folders/site",
                    "m1_folder_url": "https://drive.google.com/drive/folders/m1",
                },
                "registered_documents": [
                    {
                        "source_type": "sir",
                        "title": "Alpha CLI SIR",
                        "rhodes_doc_type": "siteInvestigationReport",
                        "registration_status": "registered",
                        "readback_status": "verified",
                    },
                    {
                        "source_type": "school_approval_report",
                        "title": "Alpha CLI School Approval Report",
                        "rhodes_doc_type": "regulatoryApproval",
                        "registration_status": "registered",
                        "readback_status": "verified",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = ddr_cli.main([
        "m2",
        "consume-event",
        "--input",
        str(event_path),
        "--state-store",
        str(state_path),
        "--skip-rhodes-readback",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["event_id"] == "evt-cli"
    assert payload["m2_state"] == "waiting_for_capacity_source"
    assert "evt-cli" in state_path.read_text(encoding="utf-8")


def test_ddr_m2_poll_events_dispatches_with_apply(monkeypatch, capsys) -> None:
    calls = []
    queue = object()
    store = object()
    monkeypatch.setattr(ddr_cli, "build_m2_event_queue_from_env", lambda: queue)
    monkeypatch.setattr(ddr_cli, "build_m2_state_store", lambda *args: store)

    def fake_poll(**kwargs):
        calls.append(kwargs)
        return {
            "status": "success",
            "apply": kwargs["apply"],
            "events_found": 0,
            "events_processed": 0,
            "blocked": 0,
            "failed": 0,
            "rows": [],
        }

    monkeypatch.setattr(ddr_cli, "poll_m2_events", fake_poll)

    exit_code = ddr_cli.main([
        "m2",
        "poll-events",
        "--apply",
        "--limit",
        "2",
        "--site-id",
        "SITE2",
        "--event-id",
        "evt-2",
        "--skip-rhodes-readback",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["apply"] is True
    assert calls[0]["event_queue"] is queue
    assert calls[0]["state_store"] is store
    assert calls[0]["limit"] == 2
    assert calls[0]["site_id"] == "SITE2"
    assert calls[0]["event_id"] == "evt-2"
    assert calls[0]["verify_rhodes_readback"] is False


def test_ddr_m2_source_watch_dispatches_canary_filters(monkeypatch, capsys) -> None:
    calls = []

    class Store:
        def load(self):
            return {}

    monkeypatch.setattr(ddr_cli, "build_m2_state_store", lambda *args: Store())

    def fake_open_site_ids(state, **kwargs):
        calls.append(("open", state, kwargs))
        return []

    monkeypatch.setattr(ddr_cli, "open_m2_site_ids", fake_open_site_ids)

    exit_code = ddr_cli.main([
        "m2",
        "source-watch",
        "--site-id",
        "SITE2",
        "--event-id",
        "evt-2",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["open_states_checked"] == 0
    assert payload["filters"] == {"site_id": "SITE2", "event_id": "evt-2"}
    assert calls[0][2] == {"site_id": "SITE2", "event_id": "evt-2"}


def test_ddr_m2_execute_ready_dispatches_with_apply(monkeypatch, capsys) -> None:
    calls = []
    store = object()
    monkeypatch.setattr(ddr_cli, "build_m2_state_store", lambda *args: store)

    def fake_execute(**kwargs):
        calls.append(kwargs)
        return {
            "status": "success",
            "apply": kwargs["apply"],
            "states_checked": 0,
            "executed": 0,
            "completed": 0,
            "blocked": 0,
            "rows": [],
        }

    monkeypatch.setattr(ddr_cli, "execute_ready_m2_states", fake_execute)

    exit_code = ddr_cli.main([
        "m2",
        "execute-ready",
        "--apply",
        "--limit",
        "3",
        "--site-id",
        "SITE2",
        "--event-id",
        "evt-2",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["apply"] is True
    assert calls[0]["state_store"] is store
    assert calls[0]["limit"] == 3
    assert calls[0]["site_id"] == "SITE2"
    assert calls[0]["event_id"] == "evt-2"


def test_ddr_notes_smoke_test_resolves_owner_and_writes_headless_note(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        ddr_cli,
        "lookup_rhodes_site_owner",
        lambda **kwargs: {
            "status": "found",
            "site_id": "SITE1",
            "site_name": "Alpha Keller",
            "site_slug": "alpha-keller",
            "p1_assignee_email": "owner@example.com",
            "p1_assignee_user_id": "OWNER1",
            "p1_dri": {"userId": "OWNER1"},
        },
    )
    calls = []

    def fake_add_note(**kwargs):
        calls.append(kwargs)
        return {
            "status": "created",
            "rhodes_note_id": "NOTE1",
            "owner_notification": "mentioned",
            "mentioned_user_ids": ["OWNER1"],
        }

    monkeypatch.setattr(ddr_cli, "add_rhodes_site_note", fake_add_note)

    exit_code = ddr_cli.main(["notes-smoke-test", "--site", "Alpha Keller"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["site"]["site_id"] == "SITE1"
    assert payload["owner"]["user_id"] == "OWNER1"
    assert calls[0]["site_id"] == "SITE1"
    assert calls[0]["site_slug"] == "alpha-keller"
    assert calls[0]["owner_user_id"] == "OWNER1"
    assert calls[0]["owner_email"] == "owner@example.com"
    assert calls[0]["automation_source"] == "due-diligence-reporter:notes-smoke-test"
    assert "Rhodes note smoke test" in calls[0]["body"]
    assert "Headless note write test completed." in calls[0]["body"]
    assert "Kind: headless_add_note_smoke_test" not in calls[0]["body"]
    assert "P1 owner review:" not in calls[0]["body"]
    assert "owner@example.com" not in calls[0]["body"]
    assert "SITE1" not in calls[0]["body"]


def test_ddr_notes_smoke_test_fails_before_write_without_owner_user_id(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        ddr_cli,
        "lookup_rhodes_site_owner",
        lambda **kwargs: {
            "status": "owner_missing",
            "site_id": "SITE1",
            "site_name": "Alpha Keller",
            "site_slug": "alpha-keller",
            "p1_assignee_email": "",
            "p1_assignee_user_id": "",
            "message": "Rhodes site exists, but p1Dri is not assigned.",
        },
    )

    def fake_add_note(**kwargs):
        raise AssertionError("smoke test should not write without owner user ID")

    monkeypatch.setattr(ddr_cli, "add_rhodes_site_note", fake_add_note)

    exit_code = ddr_cli.main(["notes-smoke-test", "--site", "Alpha Keller"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["status"] == "error"
    assert payload["reason"] == "missing_owner_user_id"
    assert payload["site_id"] == "SITE1"


def test_ddr_status_reads_manifest(monkeypatch, tmp_path, capsys) -> None:
    manifest = {
        "run_id": "run-1",
        "site_title": "Alpha Keller",
        "final_status": "generation_failed",
        "failed_step": "report.generate",
        "next_operator_action": "ddr rerun --run-id run-1 --step report.generate",
        "manifest_path": str(tmp_path / "run-1.json"),
        "quality": {"score": 61, "band": "orange"},
        "steps": [],
    }
    (tmp_path / "run-1.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr("due_diligence_reporter.pipeline_manifest.RUN_MANIFEST_DIR", tmp_path)

    ddr_cli.main(["status", "--run-id", "run-1"])

    out = capsys.readouterr().out
    assert "Run: run-1" in out
    assert "Failed step: report.generate" in out
    assert "Quality: 61 (orange)" in out


def test_ddr_trace_failed_only_filters_successes(monkeypatch, tmp_path, capsys) -> None:
    manifest = {
        "run_id": "run-1",
        "steps": [
            {"step": "readiness.check", "status": "succeeded", "duration_ms": 1},
            {
                "step": "report.generate",
                "status": "failed",
                "duration_ms": 2,
                "error": {"code": "boom", "message": "failed"},
            },
        ],
    }
    (tmp_path / "run-1.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr("due_diligence_reporter.pipeline_manifest.RUN_MANIFEST_DIR", tmp_path)

    ddr_cli.main(["trace", "--run-id", "run-1", "--failed-only"])

    out = capsys.readouterr().out
    assert "report.generate: failed" in out
    assert "readiness.check" not in out


def test_ddr_rerun_report_generate_executes_launch_context(monkeypatch, capsys) -> None:
    manifest = {
        "run_id": "run-1",
        "site_title": "Alpha Keller",
        "steps": [{"step": "report.generate", "status": "failed"}],
        "launch_context": {
            "schema_version": "ddr_run_site_launch.v1",
            "mode": "force-regenerate",
            "site": "Alpha Keller",
            "address": "123 Main St, Keller, TX",
            "site_id": "SITE1",
            "slug": "",
            "drive_folder_url": "https://drive.google.com/drive/folders/site",
            "notify": False,
            "sor_write_mode": "mcp-assisted",
            "mcp_write_completed": False,
            "document_first_on_sor_blocker": True,
            "force_regenerate": True,
        },
    }
    calls = []

    def fake_run_site_command(args):
        calls.append(args)
        return 0, {"status": "report_created", "run_id": "run-2"}

    monkeypatch.setattr(ddr_cli, "load_run_manifest", lambda run_id: manifest)
    monkeypatch.setattr(ddr_cli, "run_site_command", fake_run_site_command)

    exit_code = ddr_cli.main([
        "rerun",
        "--run-id",
        "run-1",
        "--step",
        "report.generate",
        "--max-attempts",
        "1",
    ])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert json.loads(out[out.index("{"):]) == {
        "run_id": "run-2",
        "status": "report_created",
    }
    assert calls[0].mode == "force-regenerate"
    assert calls[0].site == "Alpha Keller"
    assert calls[0].site_id == "SITE1"
    assert calls[0].drive_folder_url == "https://drive.google.com/drive/folders/site"
    assert calls[0].sor_write_mode == "mcp-assisted"
    assert calls[0].document_first_on_sor_blocker is True


def test_ddr_rerun_retries_anthropic_529(monkeypatch) -> None:
    manifest = {
        "run_id": "run-1",
        "site_title": "Alpha Keller",
        "steps": [{"step": "report.generate", "status": "failed"}],
        "launch_context": {
            "schema_version": "ddr_run_site_launch.v1",
            "mode": "first-publish",
            "site": "Alpha Keller",
            "address": "",
            "site_id": "SITE1",
            "drive_folder_url": "",
            "notify": False,
            "sor_write_mode": "api",
            "mcp_write_completed": False,
            "document_first_on_sor_blocker": False,
        },
    }
    calls = []
    sleeps = []

    def fake_run_site_command(args):
        calls.append(args)
        if len(calls) == 1:
            return 1, {
                "status": "generation_failed",
                "failed_step": "report.generate",
                "error": "Anthropic 529 overloaded_error",
            }
        return 0, {"status": "report_created", "run_id": "run-2"}

    monkeypatch.setattr(ddr_cli, "load_run_manifest", lambda run_id: manifest)
    monkeypatch.setattr(ddr_cli, "run_site_command", fake_run_site_command)
    monkeypatch.setattr(ddr_cli.time, "sleep", lambda seconds: sleeps.append(seconds))

    exit_code = ddr_cli.main([
        "rerun",
        "--run-id",
        "run-1",
        "--step",
        "report.generate",
        "--max-attempts",
        "2",
        "--backoff-seconds",
        "5",
    ])

    assert exit_code == 0
    assert len(calls) == 2
    assert sleeps == [5]


def test_ddr_rerun_infers_first_publish_for_legacy_manifest(monkeypatch) -> None:
    manifest = {
        "run_id": "run-1",
        "site_title": "Alpha Boca Raton 5000 T-Rex Ave",
        "site_id": "k175pgrk93nrqx065f5fkhwk8h88sdy3",
        "steps": [{"step": "report.generate", "status": "failed"}],
    }
    calls = []

    def fake_run_site_command(args):
        calls.append(args)
        return 0, {"status": "report_created", "run_id": "run-2"}

    monkeypatch.setattr(ddr_cli, "load_run_manifest", lambda run_id: manifest)
    monkeypatch.setattr(ddr_cli, "run_site_command", fake_run_site_command)

    exit_code = ddr_cli.main([
        "rerun",
        "--run-id",
        "run-1",
        "--step",
        "report.generate",
        "--max-attempts",
        "1",
    ])

    assert exit_code == 0
    assert calls[0].mode == "first-publish"
    assert calls[0].site == "Alpha Boca Raton 5000 T-Rex Ave"
    assert calls[0].site_id == "k175pgrk93nrqx065f5fkhwk8h88sdy3"


def test_ddr_sir_review_add_records_issue(tmp_path, capsys) -> None:
    store = tmp_path / "sir-review-outcomes.jsonl"

    ddr_cli.main([
        "sir-review",
        "add",
        "--site",
        "Alpha Keller",
        "--section",
        "Zoning",
        "--gap-category",
        "AI missed item",
        "--severity",
        "material",
        "--ddr-impact",
        "exec.c_zoning",
        "--evidence-checked",
        "city code",
        "--learning-action",
        "retrieval rule",
        "--status",
        "accepted",
        "--store",
        str(store),
    ])

    out = capsys.readouterr().out
    assert "Recorded SIR review issue" in out
    assert "Alpha Keller" in store.read_text(encoding="utf-8")


def test_ddr_sir_trends_defaults_to_30d(tmp_path, capsys) -> None:
    store = tmp_path / "sir-review-outcomes.jsonl"
    store.write_text(
        "\n".join([
            (
                '{"created_at":"2099-01-01T00:00:00+00:00","site":"Alpha Keller",'
                '"ai_sir":"ai","cds_sir":"cds","section":"Zoning",'
                '"gap_category":"AI missed item","severity":"material",'
                '"ddr_impact":"exec.c_zoning","learning_action":"retrieval rule",'
                '"status":"accepted"}'
            )
        ]),
        encoding="utf-8",
    )

    ddr_cli.main(["sir-trends", "--store", str(store)])

    out = capsys.readouterr().out
    assert "SIR Trends since 30d" in out
    assert "Issues: 1" in out
    assert "AI missed items/SIR: 1.0" in out


def test_ddr_sir_review_queue_lists_ready_pairs(tmp_path, capsys) -> None:
    manifest = {
        "run_id": "run-ready",
        "site_title": "Alpha Keller",
        "started_at": "2026-05-15T00:00:00+00:00",
        "sir_learning_review": {
            "status": "ready_for_review",
            "reason": "AI SIR and CDS/vendor SIR are both present",
            "ai_sir": {"name": "ai.docx", "file_id": "ai-id"},
            "cds_sir": {"name": "cds.pdf", "file_id": "cds-id"},
        },
    }
    (tmp_path / "run-ready.json").write_text(json.dumps(manifest), encoding="utf-8")

    ddr_cli.main(["sir-review", "queue", "--manifest-dir", str(tmp_path)])

    out = capsys.readouterr().out
    assert "SIR Review Queue" in out
    assert "Alpha Keller" in out
    assert "AI SIR: ai.docx (ai-id)" in out
    assert "Reviewed: no" in out


def test_ddr_sir_review_queue_omits_reviewed_pairs_by_default(tmp_path, capsys) -> None:
    manifest = {
        "run_id": "run-ready",
        "site_title": "Alpha Keller",
        "started_at": "2026-05-15T00:00:00+00:00",
        "sir_learning_review": {
            "status": "ready_for_review",
            "ai_sir": {"name": "ai.docx", "file_id": "ai-id"},
            "cds_sir": {"name": "cds.pdf", "file_id": "cds-id"},
        },
    }
    store = tmp_path / "sir-review-outcomes.jsonl"
    store.write_text(
        (
            '{"created_at":"2099-01-01T00:00:00+00:00","site":"Alpha Keller",'
            '"ai_sir":"ai-id","cds_sir":"cds-id","section":"Zoning",'
            '"gap_category":"AI missed item","severity":"material",'
            '"ddr_impact":"exec.c_zoning","learning_action":"retrieval rule",'
            '"status":"accepted"}'
        ),
        encoding="utf-8",
    )
    (tmp_path / "run-ready.json").write_text(json.dumps(manifest), encoding="utf-8")

    ddr_cli.main([
        "sir-review",
        "queue",
        "--manifest-dir",
        str(tmp_path),
        "--store",
        str(store),
    ])

    out = capsys.readouterr().out
    assert "Items: 0" in out


def test_ddr_sir_monthly_summary_prints_decision_template(tmp_path, capsys) -> None:
    store = tmp_path / "sir-review-outcomes.jsonl"
    store.write_text(
        "\n".join([
            (
                '{"created_at":"2099-01-01T00:00:00+00:00","site":"Alpha Keller",'
                '"ai_sir":"ai","cds_sir":"cds","section":"Zoning",'
                '"gap_category":"AI missed item","severity":"material",'
                '"ddr_impact":"exec.c_zoning","learning_action":"retrieval rule",'
                '"status":"accepted"}'
            )
        ]),
        encoding="utf-8",
    )

    ddr_cli.main(["sir-monthly-summary", "--store", str(store)])

    out = capsys.readouterr().out
    assert "# SIR Learning Summary (30d)" in out
    assert "## Monthly Decisions" in out
    assert "retrieval rule: 1" in out


def test_ddr_portfolio_gaps_prints_operator_summary(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        ddr_cli,
        "build_portfolio_automation_gap_snapshot",
        lambda *, max_sites, include_clean: {
            "status": "success",
            "system_of_record": "rhodes",
            "generated_at": "2026-05-28T19:00:00+00:00",
            "max_sites": max_sites,
            "include_clean": include_clean,
            "totals": {
                "sites": 1,
                "sites_with_gaps": 1,
                "missing_p1_dri": 1,
                "missing_drive_folder": 1,
                "open_automation_failures": 1,
                "pending_review_tasks": 1,
            },
            "sites": [
                {
                    "site_id": "SITE1",
                    "site_name": "Alpha Tulsa 6940 S Utica Ave",
                    "owner_routing_status": "missing_owner",
                    "gap_reasons": [
                        "missing_p1_dri",
                        "missing_drive_folder",
                    ],
                    "drive_folder": {
                        "status": "missing",
                        "message": "Rhodes site has no linked Google Drive folder",
                    },
                    "required_documents": {
                        "milestone": {"key": "acquireProperty", "label": "Acquiring Property"},
                        "missing": ["propertyConditionAssessment", "floorPlan"],
                    },
                    "latest_ddr_status": {"status": "republish_failed"},
                    "latest_source_event_fingerprint": (
                        "due-diligence-reporter:raycon_followup_alert:raycon-1"
                    ),
                    "open_automation_failures": [
                        {
                            "kind": "raycon_followup_alert",
                            "mutation_status": "failed",
                            "source_id": "raycon-1",
                        }
                    ],
                    "pending_review_tasks": [
                        {
                            "title": "Assign P1 DRI for Alpha Tulsa",
                            "status": "new",
                            "task_id": "TASK1",
                        }
                    ],
                    "errors": [],
                }
            ],
        },
    )

    ddr_cli.main(["portfolio-gaps", "--max-sites", "25"])

    out = capsys.readouterr().out
    assert "Portfolio Automation Gaps" in out
    assert "Sites with gaps: 1" in out
    assert "Alpha Tulsa 6940 S Utica Ave" in out
    assert "Owner routing: missing_owner" in out
    assert "Missing current-milestone docs" not in out
    assert "propertyConditionAssessment" not in out
    assert "raycon_followup_alert failed raycon-1" in out
    assert "Assign P1 DRI for Alpha Tulsa (new, TASK1)" in out


def test_ddr_portfolio_gaps_can_print_json(monkeypatch, capsys) -> None:
    calls = []

    def fake_snapshot(*, max_sites: int, include_clean: bool) -> dict:
        calls.append({"max_sites": max_sites, "include_clean": include_clean})
        return {
            "status": "success",
            "system_of_record": "rhodes",
            "generated_at": "2026-05-28T19:00:00+00:00",
            "totals": {"sites": 0, "sites_with_gaps": 0},
            "sites": [],
        }

    monkeypatch.setattr(ddr_cli, "build_portfolio_automation_gap_snapshot", fake_snapshot)

    ddr_cli.main(["portfolio-gaps", "--max-sites", "10", "--include-clean", "--json"])

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert calls == [{"max_sites": 10, "include_clean": True}]
    assert payload["system_of_record"] == "rhodes"
    assert payload["sites"] == []


def test_ddr_review_execution_cli_writes_result(tmp_path, capsys) -> None:
    requests_path = tmp_path / "requests.json"
    output_path = tmp_path / "result.json"
    requests_path.write_text(
        json.dumps(
            {
                "schema_version": "review_execution_requests.v1",
                "requests": [
                    {
                        "request_id": "review-request:decision-cli",
                        "decision_id": "decision-cli",
                        "action_id": "ddr:run-cli:step:report.generate",
                        "decision": "approve",
                        "owning_workflow": "ddr",
                        "alert_type": "report_generation_failed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    ddr_cli.main([
        "review-execution",
        "--review-requests",
        str(requests_path),
        "--output",
        str(output_path),
    ])

    out = capsys.readouterr().out
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert "DDR review execution status=needs_review" in out
    assert payload["schema_version"] == "ddr_review_execution_result.v1"
    assert payload["execution"]["source"] == "ddr"
    assert payload["requests"][0]["execution_action"]["source_action_id"] == (
        "ddr:run-cli:step:report.generate"
    )
    assert payload["runs"][0]["workflow_id"] == "ddr"
