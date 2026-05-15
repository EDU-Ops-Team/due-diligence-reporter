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
