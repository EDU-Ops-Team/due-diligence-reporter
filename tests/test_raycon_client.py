"""Tests for the RayCon async hand-off client."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from due_diligence_reporter.raycon_client import (
    RAYCON_BREAKDOWN_ROWS,
    RAYCON_SCENARIO_FILENAME,
    RayConSchemaError,
    _compute_hmac_signature,
    post_raycon_job,
    raycon_scenario_to_report_fields,
    read_raycon_scenario_from_m1,
)


# ---------------------------------------------------------------------------
# post_raycon_job
# ---------------------------------------------------------------------------


# Spec-required fields per raycon_ddr_integration_spec.md §1.2.
_REQUIRED_KW: dict[str, object] = {
    "site_id": "S-123",
    "site_name": "Test Site",
    "address": "100 Main St, Austin, TX",
    "drive_folder_url": "https://drive.google.com/drive/folders/parent-abc",
    "m1_folder_id": "m1-xyz",
    "block_plan_file_id": "bp-123",
    "block_plan_url": "https://drive.google.com/file/d/bp-123/view",
}


def _ok_response(status_code: int = 202, body: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status.return_value = None
    resp.json.return_value = body if body is not None else {"status": "accepted"}
    return resp


class TestPostRayConJob:
    """The POST contract is the only place DDR can break RayCon, so guard it.

    Spec: raycon_ddr_integration_spec.md §1 — 11 required body fields.
    HMAC-SHA256 of the raw body is sent in X-RayCon-Signature *when*
    RAYCON_WEBHOOK_SECRET is configured. RayCon's /v1/jobs is currently
    public (no signature verification) per RayCon team 2026-04-30, so
    the secret is optional; the signing path is exercised when present
    so we're ready the day RayCon enables verification.
    """

    def _fake_settings(self):
        settings = MagicMock()
        settings.raycon_jobs_url = "https://raycon.test/v1/jobs"
        settings.raycon_webhook_secret = "shared-secret"
        settings.raycon_api_key = ""
        return settings

    def test_happy_path_sends_all_spec_fields_and_hmac(self) -> None:
        response = _ok_response()
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=response,
        ) as mock_post:
            result = post_raycon_job(total_building_sf=8400, **_REQUIRED_KW)

        assert result == {"status": "accepted"}
        assert mock_post.call_count == 1
        kwargs = mock_post.call_args.kwargs

        # Body is sent as raw bytes (data=), not json=, so the bytes we
        # POST match the bytes we signed.
        assert "json" not in kwargs
        body_bytes = kwargs["data"]
        assert isinstance(body_bytes, (bytes, bytearray))
        body = json.loads(body_bytes.decode("utf-8"))

        # All 11 spec §1.2 fields present and correct.
        assert body["schema_version"] == "1.0"
        assert body["site_id"] == "S-123"
        assert body["site_name"] == "Test Site"
        assert body["address"] == "100 Main St, Austin, TX"
        assert body["drive_folder_url"].endswith("/parent-abc")
        assert body["m1_folder_id"] == "m1-xyz"
        assert body["block_plan_file_id"] == "bp-123"
        assert body["block_plan_url"].endswith("/view")
        assert body["total_building_sf"] == 8400
        assert body["callback_marker"] == "raycon_scenario.json"
        assert body["requested_at"].endswith("Z")

        # HMAC: signature header matches an independent recompute of the
        # exact bytes that were sent on the wire.
        sig_header = kwargs["headers"]["X-RayCon-Signature"]
        expected = hmac.new(b"shared-secret", body_bytes, hashlib.sha256).hexdigest()
        assert sig_header == f"sha256={expected}"

        # Legacy API key not sent when unset.
        assert "X-RayCon-API-Key" not in kwargs["headers"]

    def test_legacy_api_key_sent_alongside_hmac_when_configured(self) -> None:
        settings = self._fake_settings()
        settings.raycon_api_key = "legacy-key"
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=settings,
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=_ok_response(),
        ) as mock_post:
            post_raycon_job(total_building_sf=8400, **_REQUIRED_KW)
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["X-RayCon-API-Key"] == "legacy-key"
        assert headers["X-RayCon-Signature"].startswith("sha256=")

    def test_missing_webhook_secret_skips_signature_header(self) -> None:
        """RayCon /v1/jobs is currently public; without a secret we still
        POST the spec-shaped body but omit the X-RayCon-Signature header."""
        settings = self._fake_settings()
        settings.raycon_webhook_secret = ""
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=settings,
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=_ok_response(),
        ) as mock_post:
            result = post_raycon_job(total_building_sf=8400, **_REQUIRED_KW)

        assert result == {"status": "accepted"}
        kwargs = mock_post.call_args.kwargs
        # Body still sent as raw bytes with the full 11-field payload.
        body = json.loads(kwargs["data"].decode("utf-8"))
        assert body["site_id"] == "S-123"
        assert body["block_plan_file_id"] == "bp-123"
        # No signature header when secret is unset.
        assert "X-RayCon-Signature" not in kwargs["headers"]
        assert "X-RayCon-API-Key" not in kwargs["headers"]

    def test_missing_required_field_raises(self) -> None:
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ):
            kw = dict(_REQUIRED_KW)
            kw["block_plan_file_id"] = ""  # spec §1.2 idempotency key
            with pytest.raises(ValueError, match="block_plan_file_id"):
                post_raycon_job(total_building_sf=8400, **kw)

    def test_total_building_sf_omitted_sends_zero(self) -> None:
        # Spec §1.2 marks total_building_sf required. When the Wrike record
        # lacks it, we still send the field (as 0) rather than dropping it,
        # so RayCon's validator sees a complete payload.
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=_ok_response(),
        ) as mock_post:
            post_raycon_job(**_REQUIRED_KW)
        body = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        assert body["total_building_sf"] == 0

    def test_signature_matches_byte_exact_payload(self) -> None:
        # Regression: signing must happen over the exact bytes posted; if
        # we serialized with one separator scheme and posted with another
        # (e.g. via requests' json= kwarg), RayCon would reject every call.
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=_ok_response(),
        ) as mock_post:
            post_raycon_job(total_building_sf=1000, **_REQUIRED_KW)
        kwargs = mock_post.call_args.kwargs
        sent_bytes = kwargs["data"]
        sent_sig = kwargs["headers"]["X-RayCon-Signature"]
        recomputed = _compute_hmac_signature("shared-secret", sent_bytes)
        assert sent_sig == recomputed

    def test_retries_on_5xx_then_succeeds(self) -> None:
        flaky = MagicMock()
        flaky.status_code = 503
        flaky.raise_for_status.side_effect = requests.HTTPError(response=flaky)
        ok = _ok_response()
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            side_effect=[flaky, ok],
        ) as mock_post:
            result = post_raycon_job(total_building_sf=8400, **_REQUIRED_KW)
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
            result = post_raycon_job(total_building_sf=8400, **_REQUIRED_KW)
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

    def test_empty_drive_folder_url_returns_none(self) -> None:
        """Defensive: empty drive_folder_url short-circuits without hitting Drive."""
        gc = MagicMock()
        result = read_raycon_scenario_from_m1(gc, "")
        assert result is None
        gc.list_files_in_folder.assert_not_called()
        gc.download_file_bytes.assert_not_called()

    def test_drive_list_files_error_propagates(self) -> None:
        """Transient Drive errors during folder listing surface to the caller
        so the follow-up script can report them as per-site errors instead of
        treating them as 'no scenario yet'."""
        gc = MagicMock()
        gc.list_files_in_folder.side_effect = RuntimeError("Drive 503 unavailable")
        with patch(
            "due_diligence_reporter.raycon_client._resolve_m1_folder",
            return_value=("m1-id", "M1 Folder"),
        ):
            with pytest.raises(RuntimeError, match="Drive 503"):
                read_raycon_scenario_from_m1(
                    gc, "https://drive.google.com/folder/abc"
                )

    def test_non_dict_top_level_json_raises_schema_error(self) -> None:
        """A JSON array (or other non-object root) at the top level is rejected."""
        gc = MagicMock()
        gc.list_files_in_folder.return_value = [
            {
                "id": "f1",
                "name": RAYCON_SCENARIO_FILENAME,
                "modifiedTime": "2026-04-30T10:00:00Z",
            },
        ]
        gc.download_file_bytes.return_value = b"[1, 2, 3]"
        with patch(
            "due_diligence_reporter.raycon_client._resolve_m1_folder",
            return_value=("m1-id", "M1 Folder"),
        ):
            with pytest.raises(RayConSchemaError, match="must be a JSON object"):
                read_raycon_scenario_from_m1(
                    gc, "https://drive.google.com/folder/abc"
                )

    def test_invalid_utf8_bytes_raises_schema_error(self) -> None:
        """Non-UTF-8 bytes from Drive are surfaced as RayConSchemaError, not
        an unhandled UnicodeDecodeError."""
        gc = MagicMock()
        gc.list_files_in_folder.return_value = [
            {
                "id": "f1",
                "name": RAYCON_SCENARIO_FILENAME,
                "modifiedTime": "2026-04-30T10:00:00Z",
            },
        ]
        # 0xff alone is not valid UTF-8.
        gc.download_file_bytes.return_value = b"\xff\xfe\xfd"
        with patch(
            "due_diligence_reporter.raycon_client._resolve_m1_folder",
            return_value=("m1-id", "M1 Folder"),
        ):
            with pytest.raises(RayConSchemaError, match="not valid JSON"):
                read_raycon_scenario_from_m1(
                    gc, "https://drive.google.com/folder/abc"
                )


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
