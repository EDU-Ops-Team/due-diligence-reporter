from __future__ import annotations

import json

from due_diligence_reporter import ddr_cli


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
                "missing_required_documents": 1,
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
                        "missing_current_milestone_documents",
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
    assert (
        "Missing current-milestone docs (Acquiring Property): "
        "propertyConditionAssessment, floorPlan"
    ) in out
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
