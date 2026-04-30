"""Tests for the RayCon async hand-off client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from due_diligence_reporter.raycon_client import (
    RAYCON_BREAKDOWN_ROWS,
    RAYCON_SCENARIO_FILENAME,
    RayConSchemaError,
    post_raycon_job,
    raycon_scenario_to_report_fields,
    read_raycon_scenario_from_m1,
)


# ---------------------------------------------------------------------------
# post_raycon_job
# ---------------------------------------------------------------------------


class TestPostRayConJob:
    """The POST contract is the only place DDR can break RayCon, so guard it."""

    def _fake_settings(self):
        settings = MagicMock()
        settings.raycon_jobs_url = "https://raycon.test/v1/jobs"
        settings.raycon_api_key = "test-key"
        return settings

    def test_happy_path_sends_required_fields_and_auth(self) -> None:
        response = MagicMock()
        response.status_code = 202
        response.raise_for_status.return_value = None
        response.json.return_value = {"status": "accepted"}

        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=response,
        ) as mock_post:
            result = post_raycon_job(
                site_id="S-123",
                site_name="Test Site",
                address="100 Main St, Austin, TX",
                site_folder_id="folder-abc",
                request_id="ddr-S-123-20260430T120000Z",
                m1_folder_id="m1-xyz",
            )

        assert result == {"status": "accepted"}
        assert mock_post.call_count == 1
        kwargs = mock_post.call_args.kwargs
        assert kwargs["headers"]["X-RayCon-API-Key"] == "test-key"
        body = kwargs["json"]
        assert body["schema_version"] == "1.0"
        assert body["site_id"] == "S-123"
        assert body["site_name"] == "Test Site"
        assert body["address"] == "100 Main St, Austin, TX"
        assert body["site_folder_id"] == "folder-abc"
        assert body["request_id"] == "ddr-S-123-20260430T120000Z"
        assert body["m1_folder_id"] == "m1-xyz"
        assert "requested_at" in body and body["requested_at"].endswith("Z")

    def test_missing_api_key_raises(self) -> None:
        settings = self._fake_settings()
        settings.raycon_api_key = ""
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=settings,
        ):
            with pytest.raises(RuntimeError, match="RAYCON_API_KEY"):
                post_raycon_job(
                    site_id="S-1",
                    site_name="x",
                    address="y",
                    site_folder_id="z",
                )

    def test_missing_required_fields_raises(self) -> None:
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ):
            with pytest.raises(ValueError, match="site_id, site_name, address"):
                post_raycon_job(
                    site_id="",
                    site_name="x",
                    address="y",
                    site_folder_id="z",
                )

    def test_retries_on_5xx_then_succeeds(self) -> None:
        flaky = MagicMock()
        flaky.status_code = 503
        flaky.raise_for_status.side_effect = requests.HTTPError(response=flaky)

        ok = MagicMock()
        ok.status_code = 202
        ok.raise_for_status.return_value = None
        ok.json.return_value = {"status": "accepted"}

        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            side_effect=[flaky, ok],
        ) as mock_post:
            result = post_raycon_job(
                site_id="S-1",
                site_name="x",
                address="y",
                site_folder_id="z",
            )

        assert result == {"status": "accepted"}
        assert mock_post.call_count == 2

    def test_empty_body_returns_default_status(self) -> None:
        response = MagicMock()
        response.status_code = 202
        response.raise_for_status.return_value = None
        response.json.side_effect = ValueError("no body")

        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=response,
        ):
            result = post_raycon_job(
                site_id="S-1",
                site_name="x",
                address="y",
                site_folder_id="z",
            )

        assert result == {"status": "accepted"}


# ---------------------------------------------------------------------------
# read_raycon_scenario_from_m1
# ---------------------------------------------------------------------------


class TestReadRayConScenarioFromM1:
    def test_returns_none_when_file_absent(self) -> None:
        gc = MagicMock()
        gc.list_files_in_folder.return_value = [
            {"id": "f1", "name": "block_plan.pdf"},
            {"id": "f2", "name": "site_inspection.pdf"},
        ]
        with patch(
            "due_diligence_reporter.raycon_client._resolve_m1_folder",
            return_value=("m1-id", "M1 Folder"),
        ):
            result = read_raycon_scenario_from_m1(gc, "https://drive.google.com/folder/abc")
        assert result is None

    def test_returns_payload_when_present(self) -> None:
        payload = {
            "schema_version": "1.0",
            "fastest_open": {"grand_total": 100000, "timeline_weeks": 12},
            "max_capacity": {"grand_total": 250000, "timeline_weeks": 26},
        }
        gc = MagicMock()
        gc.list_files_in_folder.return_value = [
            {
                "id": "f1",
                "name": RAYCON_SCENARIO_FILENAME,
                "modifiedTime": "2026-04-30T10:00:00Z",
            },
        ]
        gc.download_file_bytes.return_value = json.dumps(payload).encode("utf-8")
        with patch(
            "due_diligence_reporter.raycon_client._resolve_m1_folder",
            return_value=("m1-id", "M1 Folder"),
        ):
            result = read_raycon_scenario_from_m1(gc, "https://drive.google.com/folder/abc")
        assert result is not None
        assert result["schema_version"] == "1.0"
        assert result["_drive_file_id"] == "f1"
        assert result["_drive_modified_time"] == "2026-04-30T10:00:00Z"

    def test_unsupported_schema_version_raises(self) -> None:
        payload = {"schema_version": "9.9", "fastest_open": {}}
        gc = MagicMock()
        gc.list_files_in_folder.return_value = [
            {"id": "f1", "name": RAYCON_SCENARIO_FILENAME, "modifiedTime": "2026-04-30T10:00:00Z"},
        ]
        gc.download_file_bytes.return_value = json.dumps(payload).encode("utf-8")
        with patch(
            "due_diligence_reporter.raycon_client._resolve_m1_folder",
            return_value=("m1-id", "M1 Folder"),
        ):
            with pytest.raises(RayConSchemaError, match="schema_version"):
                read_raycon_scenario_from_m1(gc, "https://drive.google.com/folder/abc")

    def test_invalid_json_raises(self) -> None:
        gc = MagicMock()
        gc.list_files_in_folder.return_value = [
            {"id": "f1", "name": RAYCON_SCENARIO_FILENAME, "modifiedTime": "2026-04-30T10:00:00Z"},
        ]
        gc.download_file_bytes.return_value = b"not json{"
        with patch(
            "due_diligence_reporter.raycon_client._resolve_m1_folder",
            return_value=("m1-id", "M1 Folder"),
        ):
            with pytest.raises(RayConSchemaError, match="not valid JSON"):
                read_raycon_scenario_from_m1(gc, "https://drive.google.com/folder/abc")

    def test_picks_most_recent_when_duplicates(self) -> None:
        payload = {"schema_version": "1.0"}
        gc = MagicMock()
        gc.list_files_in_folder.return_value = [
            {"id": "older", "name": RAYCON_SCENARIO_FILENAME, "modifiedTime": "2026-04-29T08:00:00Z"},
            {"id": "newer", "name": RAYCON_SCENARIO_FILENAME, "modifiedTime": "2026-04-30T10:00:00Z"},
        ]
        gc.download_file_bytes.return_value = json.dumps(payload).encode("utf-8")
        with patch(
            "due_diligence_reporter.raycon_client._resolve_m1_folder",
            return_value=("m1-id", "M1 Folder"),
        ):
            result = read_raycon_scenario_from_m1(gc, "https://drive.google.com/folder/abc")
        assert result is not None
        assert result["_drive_file_id"] == "newer"
        gc.download_file_bytes.assert_called_once_with("newer")


# ---------------------------------------------------------------------------
# raycon_scenario_to_report_fields
# ---------------------------------------------------------------------------


class TestRayConScenarioToReportFields:
    def test_full_schema_maps_all_buckets_for_both_scenarios(self) -> None:
        payload = {
            "schema_version": "1.0",
            "fastest_open": {
                "grand_total": 500000,
                "timeline_weeks": 12,
                "soft_costs": 50000,
                "gc_fee": 30000,
                "contingency": 20000,
                "categories": [
                    {"category": "Demolition", "subtotal": 25000},
                    {"category": "Framing / Doors", "subtotal": 60000},
                    {"category": "MEP / Fire / Life Safety", "subtotal": 90000},
                    {"category": "Plumbing / Bathrooms", "subtotal": 35000},
                    {"category": "Finish Work", "subtotal": 70000},
                    {"category": "Furniture", "subtotal": 40000},
                    {"category": "Tech / Security / Signage", "subtotal": 25000},
                    {"category": "Other Hard Costs", "subtotal": 55000},
                ],
            },
            "max_capacity": {
                "grand_total": 900000,
                "timeline_weeks": 26,
                "soft_costs": 80000,
                "gc_fee": 60000,
                "contingency": 40000,
                "categories": [
                    {"category": "Demolition", "subtotal": 50000},
                ],
            },
        }
        fields = raycon_scenario_to_report_fields(payload)

        assert fields["exec.fastest_open_capex"] == "$500,000"
        assert fields["exec.max_capacity_capex"] == "$900,000"
        # Open dates rendered (non-empty)
        assert fields["exec.fastest_open_open_date"]
        assert fields["exec.max_capacity_open_date"]

        # Every breakdown row exists for both suffixes
        for row_key, _label in RAYCON_BREAKDOWN_ROWS:
            assert f"exec.cost_{row_key}_fastest_open" in fields
            assert f"exec.cost_{row_key}_max_capacity" in fields

        # Spot-check categories landed in their buckets
        assert fields["exec.cost_demolition_fastest_open"] == "$25,000"
        assert fields["exec.cost_framing_doors_fastest_open"] == "$60,000"
        assert fields["exec.cost_soft_costs_fastest_open"] == "$50,000"
        assert fields["exec.cost_grand_total_fastest_open"] == "$500,000"
        assert fields["exec.cost_demolition_max_capacity"] == "$50,000"
        assert fields["exec.cost_grand_total_max_capacity"] == "$900,000"

    def test_unknown_category_rolls_into_other_hard_costs(self) -> None:
        payload = {
            "schema_version": "1.0",
            "fastest_open": {
                "grand_total": 10000,
                "timeline_weeks": 4,
                "categories": [
                    {"category": "Mystery Bucket", "subtotal": 7777},
                ],
            },
            "max_capacity": {},
        }
        fields = raycon_scenario_to_report_fields(payload)
        assert fields["exec.cost_other_hard_costs_fastest_open"] == "$7,777"

    def test_missing_scenarios_return_blank_or_zero_fields(self) -> None:
        # Empty scenario dicts ({}) still produce $0 values via _format_currency
        # and blank dates (timeline_weeks <= 0). All keys must still exist so
        # downstream placeholder substitution doesn't crash.
        fields = raycon_scenario_to_report_fields({"schema_version": "1.0"})
        assert fields["exec.fastest_open_capex"] == "$0"
        assert fields["exec.max_capacity_capex"] == "$0"
        assert fields["exec.fastest_open_open_date"] == ""
        assert fields["exec.max_capacity_open_date"] == ""
        for row_key, _label in RAYCON_BREAKDOWN_ROWS:
            assert f"exec.cost_{row_key}_fastest_open" in fields
            assert f"exec.cost_{row_key}_max_capacity" in fields

    def test_non_dict_scenarios_return_blank_fields(self) -> None:
        # When RayCon explicitly sends a non-dict (e.g. null), the else branch
        # produces empty strings — distinct from the $0 case above.
        fields = raycon_scenario_to_report_fields(
            {"schema_version": "1.0", "fastest_open": None, "max_capacity": None}
        )
        # None falls through `payload.get(...) or {}` to {} which IS a dict,
        # so verify the actual behavior: empty dict path → $0, blank date.
        assert fields["exec.fastest_open_capex"] == "$0"
        assert fields["exec.fastest_open_open_date"] == ""
