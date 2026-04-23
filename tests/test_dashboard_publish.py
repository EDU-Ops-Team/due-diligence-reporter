"""Unit tests for dashboard_publish."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from due_diligence_reporter.dashboard_publish import (
    build_dashboard_filename,
    publish_site_record,
)
from due_diligence_reporter.site_record import SiteRecord


@pytest.fixture
def record() -> SiteRecord:
    return SiteRecord.from_replacements(
        {
            "meta.site_name": "Palm Beach Gardens",
            "meta.city_state_zip": "Palm Beach Gardens, FL 33410",
            "exec.c_answer": "Yes",
            "exec.fastest_open_capacity": "180",
            "sources.sir_link": "https://drive.google.com/sir",
            "sources.block_plan_link": "https://drive.google.com/block-plan",
        },
        site_name="Palm Beach Gardens",
        report_date="04/22/26",
        drive_folder_url="https://drive.google.com/drive/folders/ABC",
        dd_report_url="https://docs.google.com/document/d/XYZ",
    )


class TestBuildDashboardFilename:
    def test_suffix(self) -> None:
        assert build_dashboard_filename("palm-beach-gardens") == "palm-beach-gardens.dashboard.json"


class TestPublishSiteRecord:
    def test_happy_path_new_upload(self, record: SiteRecord) -> None:
        gc = MagicMock()
        gc.list_files_in_folder.return_value = []
        gc.upload_file_to_folder.return_value = {
            "id": "file-123",
            "name": "palm-beach-gardens.dashboard.json",
            "webViewLink": "https://drive.google.com/file/d/file-123/view",
        }

        result = publish_site_record(gc, folder_id="folder-ABC", record=record)

        gc.list_files_in_folder.assert_called_once_with("folder-ABC")
        gc.drive_service.files().update.assert_not_called()
        gc.upload_file_to_folder.assert_called_once()

        call = gc.upload_file_to_folder.call_args
        assert call.kwargs["folder_id"] == "folder-ABC"
        assert call.kwargs["file_name"] == "palm-beach-gardens.dashboard.json"
        assert call.kwargs["mime_type"] == "application/json"

        payload = json.loads(call.kwargs["file_bytes"].decode("utf-8"))
        assert payload["slug"] == "palm-beach-gardens"
        assert payload["classification"]["label"] == "yes"
        assert payload["sources"]["sir"] == "https://drive.google.com/sir"
        assert payload["sources"]["block_plan"] == "https://drive.google.com/block-plan"

        assert result["file_id"] == "file-123"
        assert result["file_name"] == "palm-beach-gardens.dashboard.json"
        assert result["replaced_count"] == 0

    def test_upsert_trashes_existing_after_upload(self, record: SiteRecord) -> None:
        gc = MagicMock()
        gc.list_files_in_folder.return_value = [
            {"id": "old-1", "name": "palm-beach-gardens.dashboard.json"},
            {"id": "other", "name": "Palm Beach Gardens DD Report - 04/22/2026"},
        ]
        gc.upload_file_to_folder.return_value = {
            "id": "file-new",
            "name": "palm-beach-gardens.dashboard.json",
            "webViewLink": "https://drive.google.com/file/d/file-new/view",
        }

        result = publish_site_record(gc, folder_id="folder-ABC", record=record)

        gc.upload_file_to_folder.assert_called_once()
        trashed_ids = [
            call.kwargs["fileId"]
            for call in gc.drive_service.files().update.call_args_list
            if call.kwargs.get("body") == {"trashed": True}
        ]
        assert trashed_ids == ["old-1"]
        assert result["replaced_count"] == 1

    def test_list_failure_does_not_block_upload(self, record: SiteRecord) -> None:
        gc = MagicMock()
        gc.list_files_in_folder.side_effect = RuntimeError("Drive is grumpy")
        gc.upload_file_to_folder.return_value = {
            "id": "file-new",
            "name": "palm-beach-gardens.dashboard.json",
            "webViewLink": "https://drive.google.com/file/d/file-new/view",
        }

        result = publish_site_record(gc, folder_id="folder-ABC", record=record)
        gc.upload_file_to_folder.assert_called_once()
        gc.drive_service.files().update.assert_not_called()
        assert result["replaced_count"] == 0

    def test_trash_failure_does_not_block_upload(self, record: SiteRecord) -> None:
        gc = MagicMock()
        gc.list_files_in_folder.return_value = [
            {"id": "old-1", "name": "palm-beach-gardens.dashboard.json"},
        ]
        gc.drive_service.files().update.side_effect = RuntimeError("trash denied")
        gc.upload_file_to_folder.return_value = {
            "id": "file-new",
            "name": "palm-beach-gardens.dashboard.json",
            "webViewLink": "https://drive.google.com/file/d/file-new/view",
        }

        result = publish_site_record(gc, folder_id="folder-ABC", record=record)
        gc.upload_file_to_folder.assert_called_once()
        assert result["replaced_count"] == 0

    def test_upload_failure_preserves_existing_payload(self, record: SiteRecord) -> None:
        gc = MagicMock()
        gc.list_files_in_folder.return_value = [
            {"id": "old-1", "name": "palm-beach-gardens.dashboard.json"},
        ]
        gc.upload_file_to_folder.side_effect = RuntimeError("upload died")

        with pytest.raises(RuntimeError, match="upload died"):
            publish_site_record(gc, folder_id="folder-ABC", record=record)

        gc.drive_service.files().update.assert_not_called()

    def test_payload_is_deterministic_json(self, record: SiteRecord) -> None:
        gc1 = MagicMock()
        gc1.list_files_in_folder.return_value = []
        gc1.upload_file_to_folder.return_value = {"id": "a", "webViewLink": ""}
        gc2 = MagicMock()
        gc2.list_files_in_folder.return_value = []
        gc2.upload_file_to_folder.return_value = {"id": "b", "webViewLink": ""}

        publish_site_record(gc1, folder_id="f", record=record)
        publish_site_record(gc2, folder_id="f", record=record)

        bytes1 = gc1.upload_file_to_folder.call_args.kwargs["file_bytes"]
        bytes2 = gc2.upload_file_to_folder.call_args.kwargs["file_bytes"]
        assert bytes1 == bytes2
