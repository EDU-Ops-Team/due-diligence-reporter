from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from due_diligence_reporter.report_pipeline import PipelineResult
from due_diligence_reporter.rhodes import RhodesError
from scripts import daily_dd_check


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.get_client_config_path.return_value = "/fake/client.json"
    settings.get_token_file_path.return_value = "/fake/token.json"
    settings.oauth_port = 0
    settings.google_scopes = []
    settings.google_chat_webhook_url = ""
    return settings


def test_daily_dd_check_uses_rhodes_records_and_passes_site_address(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(daily_dd_check, "get_settings", MagicMock(return_value=settings))

    gc = MagicMock()
    monkeypatch.setattr(
        daily_dd_check.GoogleClient,
        "from_oauth_config",
        MagicMock(return_value=gc),
    )
    monkeypatch.setattr(
        daily_dd_check,
        "list_rhodes_site_records",
        MagicMock(
            return_value=[
                {
                    "id": "SITE1",
                    "title": "Alpha Keller",
                    "address": "123 Main St, Keller, TX",
                    "drive_folder_url": "https://drive.google.com/drive/folders/root",
                    "p1_assignee_email": "owner@example.com",
                    "p1_assignee_name": "Owner One",
                    "created_date": "2026-05-01T00:00:00Z",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        daily_dd_check,
        "list_shared_folders_once",
        MagicMock(return_value={"sir": []}),
    )
    process = MagicMock(return_value=PipelineResult(site_title="Alpha Keller", status="report_exists"))
    monkeypatch.setattr(daily_dd_check, "process_site_pipeline", process)
    monkeypatch.setattr(daily_dd_check, "post_pipeline_result", MagicMock())

    daily_dd_check.main()

    assert gc.list_subfolders.call_count == 0
    kwargs = process.call_args.kwargs
    assert kwargs["site_address"] == "123 Main St, Keller, TX"
    assert kwargs["site_id"] == "SITE1"
    assert kwargs["p1_email"] == "owner@example.com"
    assert kwargs["p1_name"] == "Owner One"
    assert process.call_args.args[1] == "Alpha Keller"
    assert "123 Main St" in process.call_args.args[3]


def test_daily_dd_check_fails_when_rhodes_roster_unavailable(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(daily_dd_check, "get_settings", MagicMock(return_value=settings))
    monkeypatch.setattr(
        daily_dd_check.GoogleClient,
        "from_oauth_config",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        daily_dd_check,
        "list_rhodes_site_records",
        MagicMock(side_effect=RhodesError("unavailable")),
    )

    with pytest.raises(SystemExit) as exc:
        daily_dd_check.main()

    assert exc.value.code == 1
