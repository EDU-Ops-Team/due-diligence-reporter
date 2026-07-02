from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

from due_diligence_reporter import adhoc_runner
from due_diligence_reporter.report_pipeline import PipelineResult


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        google_chat_webhook_url="https://chat.example/hook",
        get_client_config_path=lambda: "/fake/client.json",
        get_token_file_path=lambda: "/fake/token.json",
        oauth_port=0,
        google_scopes=[],
    )


def test_run_site_source_sweep_defaults_to_dry_run() -> None:
    parser = adhoc_runner.build_parser()

    args = parser.parse_args(["source-sweep", "--site", "Alpha Keller"])

    assert args.command == "run-site"
    assert args.mode == "source-sweep"
    assert args.dry_run is True


def test_run_site_source_sweep_apply_disables_dry_run() -> None:
    parser = adhoc_runner.build_parser()

    args = parser.parse_args(["source-sweep", "--site", "Alpha Keller", "--apply"])

    assert args.dry_run is False


def test_run_site_source_sweep_runs_without_prior_ddr(monkeypatch) -> None:
    monkeypatch.setattr(
        "due_diligence_reporter.config.get_settings",
        MagicMock(return_value=_settings()),
    )
    monkeypatch.setattr(adhoc_runner, "_make_google_client", MagicMock(return_value="gc"))
    monkeypatch.setattr(adhoc_runner, "_load_prompt", MagicMock(return_value="prompt"))
    monkeypatch.setattr(
        "due_diligence_reporter.report_pipeline.list_shared_folders_once",
        MagicMock(return_value={}),
    )
    store = MagicMock()
    store.load.return_value = {}
    monkeypatch.setattr(
        "due_diligence_reporter.dd_republish_state_store.build_dd_republish_state_store",
        MagicMock(return_value=store),
    )
    monkeypatch.setattr(
        "due_diligence_reporter.rhodes.list_rhodes_site_records",
        MagicMock(return_value=[{"id": "SITE1", "title": "Alpha Keller"}]),
    )
    sweep = MagicMock(
        return_value={
            "errors": 0,
            "republished": 0,
            "rows": [],
        }
    )
    monkeypatch.setattr(
        "due_diligence_reporter.vendor_doc_sweep.run_vendor_doc_republish_sweep",
        sweep,
    )
    args = adhoc_runner.build_parser().parse_args([
        "source-sweep",
        "--site",
        "Alpha Keller",
    ])

    exit_code, payload = adhoc_runner.run_site_command(args)

    assert exit_code == 0
    assert payload["mode"] == "source-sweep"
    assert sweep.call_args.kwargs["run_without_existing_report"] is True


def test_run_site_mcp_write_completed_requires_mcp_assisted_mode() -> None:
    parser = adhoc_runner.build_parser()
    args = parser.parse_args([
        "force-regenerate",
        "--site",
        "Alpha Keller",
        "--mcp-write-completed",
    ])

    exit_code, payload = adhoc_runner.run_site_command(args)

    assert exit_code == 2
    assert payload["status"] == "error"
    assert "--mcp-write-completed requires --sor-write-mode mcp-assisted" in payload[
        "message"
    ]


def test_source_sweep_filter_matches_site_id_and_drive_url() -> None:
    record = {
        "id": "SITE1",
        "title": "Alpha Keller",
        "drive_folder_url": "https://drive.google.com/drive/folders/site-folder",
    }

    assert adhoc_runner._record_matches_any(record, ["site1"])
    assert adhoc_runner._record_matches_any(record, ["site-folder"])
    assert not adhoc_runner._record_matches_any(record, ["different-site"])


def test_force_regenerate_suppresses_notifications_and_calls_pipeline(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DDR_GOOGLE_CHAT_WEBHOOK_URL", "https://chat.example/hook")
    monkeypatch.setenv("DD_REPORT_EMAIL_RECIPIENTS", "ops@example.com")
    monkeypatch.setattr(
        "due_diligence_reporter.config.get_settings",
        MagicMock(return_value=_settings()),
    )
    monkeypatch.setattr(adhoc_runner, "_make_google_client", MagicMock(return_value="gc"))
    monkeypatch.setattr(adhoc_runner, "_load_prompt", MagicMock(return_value="prompt"))
    monkeypatch.setattr(
        adhoc_runner,
        "_site_context",
        MagicMock(
            return_value={
                "site_title": "Alpha Keller",
                "site_address": "123 Main St, Keller, TX",
                "drive_folder_url": "https://drive.google.com/drive/folders/site",
                "site_id": "SITE1",
                "p1_name": "Owner One",
                "p1_email": "owner@example.com",
                "site_created_at": "2026-06-01T00:00:00Z",
                "rhodes_status": "found",
                "rhodes_message": "",
            }
        ),
    )
    monkeypatch.setattr(
        "due_diligence_reporter.report_pipeline.list_shared_folders_once",
        MagicMock(return_value={"sir": []}),
    )
    post_pipeline_result = MagicMock()
    monkeypatch.setattr(
        "due_diligence_reporter.report_pipeline.post_pipeline_result",
        post_pipeline_result,
    )
    process_site_pipeline = MagicMock(
        return_value=PipelineResult(
            site_title="Alpha Keller",
            status="report_created",
            doc_id="DOC1",
            doc_url="https://docs.example/doc",
            run_id="run-1",
            manifest_path=".ddr-runs/run-1.json",
        )
    )
    monkeypatch.setattr(
        "due_diligence_reporter.report_pipeline.process_site_pipeline",
        process_site_pipeline,
    )
    args = adhoc_runner.build_parser().parse_args([
        "force-regenerate",
        "--site",
        "Alpha Keller",
    ])

    exit_code, payload = adhoc_runner.run_site_command(args)

    assert exit_code == 0
    assert os.environ["DDR_GOOGLE_CHAT_WEBHOOK_URL"] == ""
    assert os.environ["DD_REPORT_EMAIL_RECIPIENTS"] == ""
    assert payload["status"] == "report_created"
    assert payload["notifications"] == "suppressed"
    assert payload["document_first_on_sor_blocker"] is True
    assert payload["run_id"] == "run-1"
    assert process_site_pipeline.call_args.kwargs["force_regenerate"] is True
    assert process_site_pipeline.call_args.kwargs["site_id"] == "SITE1"
    assert (
        process_site_pipeline.call_args.kwargs["document_first_on_sor_blocker"] is True
    )
    assert process_site_pipeline.call_args.kwargs["launch_context"] == {
        "schema_version": "ddr_run_site_launch.v1",
        "mode": "force-regenerate",
        "site": "Alpha Keller",
        "address": "123 Main St, Keller, TX",
        "site_id": "SITE1",
        "slug": "",
        "drive_folder_url": "https://drive.google.com/drive/folders/site",
        "notify": False,
        "sor_write_mode": "api",
        "mcp_write_completed": False,
        "document_first_on_sor_blocker": True,
        "force_regenerate": True,
    }
    post_pipeline_result.assert_not_called()


def test_force_regenerate_mcp_assisted_surfaces_write_request_and_resume_command(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "due_diligence_reporter.config.get_settings",
        MagicMock(return_value=_settings()),
    )
    monkeypatch.setattr(adhoc_runner, "_make_google_client", MagicMock(return_value="gc"))
    monkeypatch.setattr(adhoc_runner, "_load_prompt", MagicMock(return_value="prompt"))
    monkeypatch.setattr(
        adhoc_runner,
        "_site_context",
        MagicMock(
            return_value={
                "site_title": "Alpha Keller",
                "site_address": "123 Main St, Keller, TX",
                "drive_folder_url": "https://drive.google.com/drive/folders/site",
                "site_id": "SITE1",
                "p1_name": "Owner One",
                "p1_email": "owner@example.com",
                "site_created_at": "2026-06-01T00:00:00Z",
                "rhodes_status": "found",
                "rhodes_message": "",
            }
        ),
    )
    monkeypatch.setattr(
        "due_diligence_reporter.report_pipeline.list_shared_folders_once",
        MagicMock(return_value={}),
    )
    request = {
        "server": "locationos",
        "tool": "updateDueDiligence",
        "arguments": {"siteId": "SITE1", "status": "complete"},
    }
    process_site_pipeline = MagicMock(
        return_value=PipelineResult(
            site_title="Alpha Keller",
            status="locationos_mcp_write_required",
            run_id="run-1",
            manifest_path=".ddr-runs/run-1.json",
            locationos_mcp_resume={
                "schema_version": "locationos_mcp_resume.v1",
                "source_run_id": "run-1",
                "site_id": "SITE1",
                "site_title": "Alpha Keller",
            },
            rhodes_due_diligence_update={
                "status": "failed",
                "reason": "rhodes_error",
                "error": "Error: elicitation_unsupported",
                "locationos_mcp_write_request": request,
            },
        )
    )
    monkeypatch.setattr(
        "due_diligence_reporter.report_pipeline.process_site_pipeline",
        process_site_pipeline,
    )
    args = adhoc_runner.build_parser().parse_args([
        "force-regenerate",
        "--site",
        "Alpha Keller",
        "--sor-write-mode",
        "mcp-assisted",
        "--no-document-first-on-sor-blocker",
    ])

    exit_code, payload = adhoc_runner.run_site_command(args)

    assert exit_code == 0
    assert payload["status"] == "locationos_mcp_write_required"
    assert payload["sor_write_mode"] == "mcp-assisted"
    assert payload["document_first_on_sor_blocker"] is False
    assert payload["locationos_mcp_write_request"]["arguments"] == {
        "siteId": "SITE1",
        "status": "complete",
    }
    assert payload["mcp_resume_command"] == [
        "uv",
        "run",
        "ddr",
        "run-site",
        "resume-mcp-write",
        "--run-id",
        "run-1",
    ]
    assert "--mcp-write-completed" not in payload["mcp_resume_command"]
    assert process_site_pipeline.call_args.kwargs["due_diligence_write_mode"] == (
        "mcp_assisted"
    )
    assert (
        process_site_pipeline.call_args.kwargs["locationos_mcp_write_completed"] is False
    )
    assert (
        process_site_pipeline.call_args.kwargs["document_first_on_sor_blocker"] is False
    )


def test_result_payload_surfaces_manual_check_warnings() -> None:
    payload = adhoc_runner._result_payload(
        PipelineResult(
            site_title="Alpha Keller",
            status="report_created",
            doc_url="https://docs.google.com/document/d/doc123",
            rhodes_due_diligence_update={
                "status": "failed",
                "reason": "readback_failed",
            },
            rhodes_report_event={
                "status": "failed",
                "severity": "warning",
                "warning": "Rhodes event note was not verified; manually confirm.",
            },
        ),
        mode="first-publish",
        notify=False,
        sor_write_mode="api",
        mcp_write_completed=False,
    )

    assert payload["warnings"] == [
        (
            "DD Report Google Doc created; Rhodes/LocationOS dueDiligence "
            "write or readback is pending manual verification."
        ),
        "Rhodes event note was not verified; manually confirm.",
    ]


def test_result_payload_surfaces_source_packet() -> None:
    source_packet = {
        "status": "blocked",
        "m2_source_packet_complete": False,
        "supporting_documents": [
            {
                "source_type": "outdoor_play_space_report",
                "title": "Outdoor Play Space Report",
                "registration_status": "registered",
            }
        ],
        "dd_field_updates": [
            {
                "field": "play_area_score",
                "locationos_key": "playAreaScore",
                "write_status": "pending",
                "readback_status": "pending",
                "source_titles": ["Outdoor Play Space Report"],
            }
        ],
        "source_note_lines": [
            "play_area_score -> 1 -> Outdoor Play Space Report",
        ],
        "open_items": ["play_area_score: write not completed"],
    }

    payload = adhoc_runner._result_payload(
        PipelineResult(
            site_title="Alpha Keller",
            status="report_created",
            source_packet=source_packet,
        ),
        mode="first-publish",
        notify=False,
        sor_write_mode="api",
        mcp_write_completed=False,
    )

    assert payload["source_packet"] == source_packet


def test_resume_mcp_write_calls_manifest_bound_resume(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(
        "due_diligence_reporter.config.get_settings",
        MagicMock(return_value=settings),
    )
    resume_pipeline = MagicMock(
        return_value=PipelineResult(
            site_title="Alpha Keller",
            status="report_created",
            doc_id="DOC1",
            doc_url="https://docs.example/doc",
            run_id="resume-run",
            manifest_path=".ddr-runs/resume-run.json",
            locationos_mcp_resume={
                "schema_version": "locationos_mcp_resume.v1",
                "source_run_id": "source-run",
                "site_id": "SITE1",
                "site_title": "Alpha Keller",
                "drive_folder_url": "https://drive.google.com/drive/folders/site",
            },
        )
    )
    monkeypatch.setattr(
        "due_diligence_reporter.report_pipeline.resume_locationos_mcp_write_from_manifest",
        resume_pipeline,
    )
    args = adhoc_runner.build_parser().parse_args([
        "resume-mcp-write",
        "--run-id",
        "source-run",
    ])

    exit_code, payload = adhoc_runner.run_site_command(args)

    assert exit_code == 0
    assert payload["mode"] == "resume-mcp-write"
    assert payload["status"] == "report_created"
    assert payload["source_run_id"] == "source-run"
    assert payload["sor_write_mode"] == "mcp-assisted"
    assert payload["mcp_write_completed"] is True
    assert payload["locationos_mcp_resume"] == {
        "schema_version": "locationos_mcp_resume.v1",
        "source_run_id": "source-run",
        "site_id": "SITE1",
        "site_title": "Alpha Keller",
    }
    resume_pipeline.assert_called_once_with("source-run", settings=settings)


def test_run_site_notify_posts_pipeline_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        "due_diligence_reporter.config.get_settings",
        MagicMock(return_value=_settings()),
    )
    monkeypatch.setattr(adhoc_runner, "_make_google_client", MagicMock(return_value="gc"))
    monkeypatch.setattr(adhoc_runner, "_load_prompt", MagicMock(return_value="prompt"))
    monkeypatch.setattr(
        adhoc_runner,
        "_site_context",
        MagicMock(
            return_value={
                "site_title": "Alpha Keller",
                "site_address": "",
                "drive_folder_url": "https://drive.google.com/drive/folders/site",
                "site_id": "SITE1",
                "p1_name": "",
                "p1_email": "",
                "site_created_at": "",
                "rhodes_status": "found",
                "rhodes_message": "",
            }
        ),
    )
    monkeypatch.setattr(
        "due_diligence_reporter.report_pipeline.list_shared_folders_once",
        MagicMock(return_value={}),
    )
    result = PipelineResult(site_title="Alpha Keller", status="report_exists")
    monkeypatch.setattr(
        "due_diligence_reporter.report_pipeline.process_site_pipeline",
        MagicMock(return_value=result),
    )
    post_pipeline_result = MagicMock()
    monkeypatch.setattr(
        "due_diligence_reporter.report_pipeline.post_pipeline_result",
        post_pipeline_result,
    )
    args = adhoc_runner.build_parser().parse_args([
        "first-publish",
        "--site",
        "Alpha Keller",
        "--notify",
    ])

    exit_code, payload = adhoc_runner.run_site_command(args)

    assert exit_code == 0
    assert payload["notifications"] == "enabled"
    post_pipeline_result.assert_called_once_with(
        "https://chat.example/hook",
        result,
        "https://drive.google.com/drive/folders/site",
    )


def test_source_republish_rejects_unknown_source_type(monkeypatch) -> None:
    monkeypatch.setattr(
        "due_diligence_reporter.config.get_settings",
        MagicMock(return_value=_settings()),
    )
    monkeypatch.setattr(adhoc_runner, "_site_context", MagicMock(return_value={}))
    monkeypatch.setattr(adhoc_runner, "_make_google_client", MagicMock(return_value="gc"))
    monkeypatch.setattr(
        "due_diligence_reporter.report_pipeline.list_shared_folders_once",
        MagicMock(return_value={}),
    )
    args = adhoc_runner.build_parser().parse_args([
        "source-republish",
        "--site",
        "Alpha Keller",
        "--source-type",
        "not_a_source",
        "--fingerprint",
        "abc",
    ])

    exit_code, payload = adhoc_runner.run_site_command(args)

    assert exit_code == 2
    assert payload["status"] == "error"
    assert "Unsupported --source-type" in payload["message"]
