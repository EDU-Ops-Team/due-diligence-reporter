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
    _normalize_drive_folder_url,
    _unwrap_html_anchor,
    get_raycon_job_status,
    post_raycon_folder_ping,
    post_raycon_job,
    raycon_payload_failed,
    raycon_payload_status,
    raycon_scenario_to_report_fields,
    read_raycon_scenario_from_m1,
)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


class TestNormalizeDriveFolderUrl:
    """RayCon /v1/jobs validators reject HTML-anchor-wrapped folder URLs.

    Wrike's rich-text Google Folder field can store an anchor; we must
    rebuild a canonical /drive/folders/<id> URL before dispatch.
    """

    def test_plain_folder_url_passes_through_canonicalized(self) -> None:
        out = _normalize_drive_folder_url(
            "https://drive.google.com/drive/folders/abc123abc123"
        )
        assert out == "https://drive.google.com/drive/folders/abc123abc123"

    def test_u0_prefixed_folder_url_canonicalized(self) -> None:
        out = _normalize_drive_folder_url(
            "https://drive.google.com/drive/u/0/folders/abc123abc123"
        )
        assert out == "https://drive.google.com/drive/folders/abc123abc123"

    def test_html_anchor_unwrapped(self) -> None:
        out = _normalize_drive_folder_url(
            '<a href="https://drive.google.com/drive/folders/abc123abc123">Site</a>'
        )
        assert out == "https://drive.google.com/drive/folders/abc123abc123"

    def test_open_id_format_canonicalized(self) -> None:
        out = _normalize_drive_folder_url(
            "https://drive.google.com/open?id=abc123abc123"
        )
        assert out == "https://drive.google.com/drive/folders/abc123abc123"

    def test_file_url_returns_none(self) -> None:
        # /file/d/<id> is not a folder URL — caller will raise a clear
        # "fix the Wrike field" error rather than dispatch a doomed POST.
        out = _normalize_drive_folder_url(
            "https://drive.google.com/file/d/abc123abc123/view"
        )
        assert out is None

    def test_garbage_returns_none(self) -> None:
        assert _normalize_drive_folder_url("") is None
        assert _normalize_drive_folder_url("not a url") is None


class TestUnwrapHtmlAnchor:
    def test_anchor_unwrapped(self) -> None:
        assert _unwrap_html_anchor(
            '<a href="https://drive.google.com/file/d/xyz/view">BP</a>'
        ) == "https://drive.google.com/file/d/xyz/view"

    def test_amp_entities_decoded(self) -> None:
        assert _unwrap_html_anchor('<a href="a?b=1&amp;c=2">x</a>') == "a?b=1&c=2"

    def test_plain_url_passes_through(self) -> None:
        url = "https://drive.google.com/file/d/xyz/view"
        assert _unwrap_html_anchor(url) == url


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


def _accepted_job_response(status_code: int = 202, **overrides: object):
    body = {
        "status": "queued",
        "job_id": "job-123",
        "raycon_run_id": None,
        "idempotency_key": "block_plan|S-123|bp-123",
        "retry_after_seconds": 30,
        "status_url": "https://raycon.test/v1/jobs/status/job-123?token=opaque",
        "cached": False,
    }
    body.update(overrides)
    return _ok_response(status_code=status_code, body=body)


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
        response = _accepted_job_response()
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=response,
        ) as mock_post:
            result = post_raycon_job(total_building_sf=8400, **_REQUIRED_KW)

        assert result == {
            "status": "queued",
            "job_id": "job-123",
            "raycon_run_id": None,
            "idempotency_key": "block_plan|S-123|bp-123",
            "retry_after_seconds": 30,
            "status_url": "https://raycon.test/v1/jobs/status/job-123?token=opaque",
            "cached": False,
        }
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
            return_value=_accepted_job_response(),
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
            return_value=_accepted_job_response(),
        ) as mock_post:
            result = post_raycon_job(total_building_sf=8400, **_REQUIRED_KW)

        assert result["status"] == "queued"
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

    def test_total_building_sf_omitted_drops_field(self) -> None:
        # RayCon's validator rejects 0 ("Number must be greater than 0") and
        # null on `total_building_sf`. When the Wrike record lacks a value,
        # drop the field entirely so the payload validates.
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=_accepted_job_response(),
        ) as mock_post:
            post_raycon_job(**_REQUIRED_KW)
        body = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        assert "total_building_sf" not in body
        # Spec §1.2 ordering preserved for the fields we *do* send.
        assert list(body.keys()) == [
            "schema_version",
            "site_id",
            "site_name",
            "address",
            "drive_folder_url",
            "m1_folder_id",
            "block_plan_file_id",
            "block_plan_url",
            "callback_marker",
            "requested_at",
        ]

    def test_total_building_sf_zero_drops_field(self) -> None:
        # Same guarantee when the caller passes 0 explicitly: the validator
        # rejects 0, so we treat 0 the same as missing and omit the field.
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=_accepted_job_response(),
        ) as mock_post:
            post_raycon_job(total_building_sf=0, **_REQUIRED_KW)
        body = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        assert "total_building_sf" not in body

    def test_positive_sf_sent_in_canonical_position(self) -> None:
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=_accepted_job_response(),
        ) as mock_post:
            post_raycon_job(total_building_sf=8400, **_REQUIRED_KW)
        body = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        assert body["total_building_sf"] == 8400
        # Field sits between block_plan_url and callback_marker per spec.
        keys = list(body.keys())
        assert keys.index("total_building_sf") == keys.index("block_plan_url") + 1
        assert keys.index("callback_marker") == keys.index("total_building_sf") + 1

    def test_drive_folder_url_html_anchor_normalized(self) -> None:
        # Live regression (2026-05-05): NYC 156 William and Dallas 4152 Cole
        # had Wrike Google Folder fields stored as HTML anchors. RayCon's
        # validator rejected the raw anchor with 400. We must unwrap to a
        # canonical /drive/folders/<id> URL before sending.
        kw = dict(_REQUIRED_KW)
        kw["drive_folder_url"] = (
            '<a href="https://drive.google.com/drive/folders/'
            '13L4rDu9mW4UNPzBjLgZvadsrY8Cnl3zJ">Site Folder</a>'
        )
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=_accepted_job_response(),
        ) as mock_post:
            post_raycon_job(total_building_sf=8400, **kw)
        body = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        assert body["drive_folder_url"] == (
            "https://drive.google.com/drive/folders/"
            "13L4rDu9mW4UNPzBjLgZvadsrY8Cnl3zJ"
        )

    def test_drive_folder_url_open_id_format_normalized(self) -> None:
        # /open?id=<id> is a valid Drive folder URL shape; rebuild it as
        # the canonical /drive/folders/<id> form for RayCon.
        kw = dict(_REQUIRED_KW)
        kw["drive_folder_url"] = (
            "https://drive.google.com/open?id=13L4rDu9mW4UNPzBjLgZvadsrY8Cnl3zJ"
        )
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=_accepted_job_response(),
        ) as mock_post:
            post_raycon_job(total_building_sf=8400, **kw)
        body = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        assert body["drive_folder_url"].endswith(
            "/drive/folders/13L4rDu9mW4UNPzBjLgZvadsrY8Cnl3zJ"
        )

    def test_drive_folder_url_unparseable_raises_clear_error(self) -> None:
        # When Wrike has a value with no recoverable folder ID (e.g. the PM
        # pasted a /file/d/<id> URL by mistake), we must raise a clear
        # message naming Wrike as the fix-it surface, *before* we burn a
        # RayCon dispatch on a guaranteed-400.
        kw = dict(_REQUIRED_KW)
        kw["drive_folder_url"] = (
            "https://drive.google.com/file/d/13L4rDu9mW4UNPzBjLgZvadsrY8Cnl3zJ/view"
        )
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=_accepted_job_response(),
        ) as mock_post:
            with pytest.raises(ValueError, match="Google Folder custom field in Wrike"):
                post_raycon_job(total_building_sf=8400, **kw)
        # Critically: we never made the network call.
        assert mock_post.call_count == 0

    def test_4xx_error_logs_response_body_and_includes_in_exception(self, caplog) -> None:
        # Regression: RayCon returns 400 with a JSON body explaining *why*
        # the payload failed validation. The previous code called
        # raise_for_status() without capturing that body, leaving cron logs
        # showing only "400 Client Error" with no diagnostic. We must log
        # the body and surface it on the raised exception.
        bad_response = MagicMock()
        bad_response.status_code = 400
        bad_response.text = (
            '{"ok":false,"error":{"code":"VALIDATION_ERROR",'
            '"message":"Number must be greater than 0",'
            '"path":"total_building_sf"}}'
        )
        bad_response.raise_for_status.side_effect = requests.HTTPError(
            "400 Client Error: Bad Request for url: https://raycon.test/v1/jobs",
            response=bad_response,
        )
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=bad_response,
        ), caplog.at_level("ERROR", logger="due_diligence_reporter.raycon_client"):
            with pytest.raises(requests.HTTPError) as excinfo:
                post_raycon_job(total_building_sf=8400, **_REQUIRED_KW)

        # Exception message carries the RayCon response body so the cron
        # log line and any upstream alert sees the validation reason.
        assert "VALIDATION_ERROR" in str(excinfo.value)
        assert "total_building_sf" in str(excinfo.value)
        # And the structured log captured the same body.
        assert any(
            "VALIDATION_ERROR" in record.getMessage()
            and "status=400" in record.getMessage()
            for record in caplog.records
        )

    def test_signature_matches_byte_exact_payload(self) -> None:
        # Regression: signing must happen over the exact bytes posted; if
        # we serialized with one separator scheme and posted with another
        # (e.g. via requests' json= kwarg), RayCon would reject every call.
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=_accepted_job_response(),
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
        flaky.text = ""
        flaky.raise_for_status.side_effect = requests.HTTPError(response=flaky)
        ok = _accepted_job_response()
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            side_effect=[flaky, ok],
        ) as mock_post:
            result = post_raycon_job(total_building_sf=8400, **_REQUIRED_KW)
        assert result["status"] == "queued"
        assert mock_post.call_count == 2

    def test_retries_on_connection_error_then_succeeds(self) -> None:
        ok = _accepted_job_response()
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            side_effect=[requests.ConnectionError("network down"), ok],
        ) as mock_post:
            result = post_raycon_job(total_building_sf=8400, **_REQUIRED_KW)
        assert result["status"] == "queued"
        assert mock_post.call_count == 2

    def test_non_202_success_status_raises(self) -> None:
        response = _ok_response(status_code=200, body={"status": "accepted"})
        response.text = '{"status":"accepted"}'
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=response,
        ):
            with pytest.raises(requests.HTTPError, match="expected 202 Accepted"):
                post_raycon_job(total_building_sf=8400, **_REQUIRED_KW)

    def test_empty_202_body_raises_schema_error(self) -> None:
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
            with pytest.raises(RayConSchemaError, match="must include JSON metadata"):
                post_raycon_job(total_building_sf=8400, **_REQUIRED_KW)


class TestGetRayConJobStatus:
    def test_gets_signed_status_url_without_auth_headers(self) -> None:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "status": "completed",
            "job_id": "job-123",
            "raycon_run_id": "rc-123",
            "result_filename": "raycon_scenario.json",
            "drive_file": {"id": "drive-json-123"},
        }
        with patch(
            "due_diligence_reporter.raycon_client.requests.get",
            return_value=response,
        ) as mock_get:
            result = get_raycon_job_status(
                "https://raycon.test/v1/jobs/status/job-123?token=opaque"
            )

        assert result["status"] == "completed"
        mock_get.assert_called_once_with(
            "https://raycon.test/v1/jobs/status/job-123?token=opaque",
            timeout=60,
        )

    def test_non_object_status_response_raises_schema_error(self) -> None:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = []
        with patch(
            "due_diligence_reporter.raycon_client.requests.get",
            return_value=response,
        ):
            with pytest.raises(RayConSchemaError, match="JSON object"):
                get_raycon_job_status("https://raycon.test/status?token=opaque")


# ---------------------------------------------------------------------------
# post_raycon_folder_ping
# ---------------------------------------------------------------------------


_PING_REQUIRED_KW: dict[str, object] = {
    "site_id": "S-123",
    "site_name": "Test Site",
    "address": "100 Main St, Austin, TX",
    "drive_folder_url": "https://drive.google.com/drive/folders/parent-abc",
    "m1_folder_id": "m1-xyz",
}


class TestPostRayConFolderPing:
    """Per-doc folder ping: lighter body, same /v1/jobs URL.

    Sent on every classified upload (CDS SIR, Worksmith inspection, ISP,
    Block Plan). RayCon walks the folder server-side and decides whether
    the document set is complete. Idempotent on RayCon's side.
    """

    def _fake_settings(self):
        settings = MagicMock()
        settings.raycon_jobs_url = "https://raycon.test/v1/jobs"
        settings.raycon_webhook_secret = "shared-secret"
        settings.raycon_api_key = ""
        return settings

    def test_happy_path_sends_minimal_body_and_hmac(self) -> None:
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=_ok_response(),
        ) as mock_post:
            result = post_raycon_folder_ping(
                doc_type="sir",
                file_id="file-1",
                file_url="https://drive.google.com/file/d/file-1/view",
                **_PING_REQUIRED_KW,
            )

        assert result == {"status": "accepted"}
        kwargs = mock_post.call_args.kwargs
        # Same endpoint as post_raycon_job.
        assert mock_post.call_args.args[0] == "https://raycon.test/v1/jobs"
        assert "json" not in kwargs
        body_bytes = kwargs["data"]
        body = json.loads(body_bytes.decode("utf-8"))

        # Required fields present.
        assert body["schema_version"] == "1.0"
        assert body["site_id"] == "S-123"
        assert body["site_name"] == "Test Site"
        assert body["address"] == "100 Main St, Austin, TX"
        assert body["drive_folder_url"].endswith("/parent-abc")
        assert body["m1_folder_id"] == "m1-xyz"
        assert body["event"] == "folder_updated"
        assert body["callback_marker"] == "raycon_scenario.json"
        assert body["requested_at"].endswith("Z")

        # Informational hints included.
        assert body["doc_type"] == "sir"
        assert body["file_id"] == "file-1"
        assert body["file_url"].endswith("/view")

        # NO Block Plan handle on the wire — that's how RayCon
        # distinguishes a folder ping from a job dispatch.
        assert "block_plan_file_id" not in body
        assert "block_plan_url" not in body
        assert "total_building_sf" not in body

        # HMAC matches.
        sig_header = kwargs["headers"]["X-RayCon-Signature"]
        expected = hmac.new(b"shared-secret", body_bytes, hashlib.sha256).hexdigest()
        assert sig_header == f"sha256={expected}"

    def test_optional_hint_fields_omitted_when_not_provided(self) -> None:
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=_ok_response(),
        ) as mock_post:
            post_raycon_folder_ping(**_PING_REQUIRED_KW)

        body = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        # Optional hints not on the wire when caller didn't supply them.
        assert "doc_type" not in body
        assert "file_id" not in body
        assert "file_url" not in body
        # Required fields still present.
        assert body["site_id"] == "S-123"
        assert body["m1_folder_id"] == "m1-xyz"

    def test_missing_required_field_raises(self) -> None:
        kw = dict(_PING_REQUIRED_KW)
        kw["drive_folder_url"] = ""
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ):
            with pytest.raises(ValueError) as excinfo:
                post_raycon_folder_ping(**kw)
        assert "drive_folder_url" in str(excinfo.value)
        assert "folder_ping" in str(excinfo.value)

    def test_missing_secret_skips_signature_header(self) -> None:
        settings = self._fake_settings()
        settings.raycon_webhook_secret = ""
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=settings,
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=_ok_response(),
        ) as mock_post:
            post_raycon_folder_ping(**_PING_REQUIRED_KW)

        headers = mock_post.call_args.kwargs["headers"]
        assert "X-RayCon-Signature" not in headers
        assert "X-RayCon-API-Key" not in headers
        assert headers["Content-Type"] == "application/json"

    def test_empty_response_body_returns_default_status(self) -> None:
        empty = MagicMock()
        empty.status_code = 202
        empty.raise_for_status.return_value = None
        empty.json.side_effect = ValueError("no body")
        with patch(
            "due_diligence_reporter.raycon_client.get_settings",
            return_value=self._fake_settings(),
        ), patch(
            "due_diligence_reporter.raycon_client.requests.post",
            return_value=empty,
        ):
            result = post_raycon_folder_ping(**_PING_REQUIRED_KW)
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

    def test_missing_scenarios_return_blank_fields(self) -> None:
        # Empty scenario dicts mean RayCon has not returned usable scenario
        # values yet. Keep fields blank so placeholders can show pending data.
        fields = raycon_scenario_to_report_fields({"schema_version": "1.0"})
        assert fields["exec.fastest_open_capex"] == ""
        assert fields["exec.max_capacity_capex"] == ""
        assert fields["exec.fastest_open_open_date"] == ""
        assert fields["exec.max_capacity_open_date"] == ""
        for row_key, _label in RAYCON_BREAKDOWN_ROWS:
            assert fields[f"exec.cost_{row_key}_fastest_open"] == ""
            assert fields[f"exec.cost_{row_key}_max_capacity"] == ""

    def test_non_dict_scenarios_return_blank_fields(self) -> None:
        # When RayCon explicitly sends a non-dict (e.g. null), the else branch
        # produces empty strings — distinct from the $0 case above.
        fields = raycon_scenario_to_report_fields(
            {"schema_version": "1.0", "fastest_open": None, "max_capacity": None}
        )
        # None falls through `payload.get(...) or {}` to {} which IS a dict,
        # so verify the actual behavior: empty dict path → $0, blank date.
        assert fields["exec.fastest_open_capex"] == ""
        assert fields["exec.fastest_open_open_date"] == ""

    def test_explicit_zero_cost_remains_zero(self) -> None:
        fields = raycon_scenario_to_report_fields(
            {
                "schema_version": "1.0",
                "fastest_open": {
                    "grand_total": 0,
                    "categories": [{"category": "Demolition", "subtotal": 0}],
                },
                "max_capacity": {"grand_total": 0},
            }
        )
        assert fields["exec.fastest_open_capex"] == "$0"
        assert fields["exec.cost_demolition_fastest_open"] == "$0"
        assert fields["exec.max_capacity_capex"] == "$0"


# ---------------------------------------------------------------------------
# v1.1 envelope: scenarios under analysis.*, top-level status, validation
# ---------------------------------------------------------------------------


class TestRayConPayloadEnvelope:
    """RayCon's production v1.1 payload nests scenarios under ``analysis.``
    and adds a top-level ``status`` + ``validation`` block. DDR has to
    accept both the v1.1 envelope and the v1.0 flat shape, and must never
    publish a successful-looking Doc for a failed run."""

    def test_status_helper_lowercases_and_defaults_blank(self) -> None:
        assert raycon_payload_status({"status": "FAILED"}) == "failed"
        assert raycon_payload_status({"status": "  Completed  "}) == "completed"
        assert raycon_payload_status({}) == ""
        assert raycon_payload_status({"status": None}) == ""

    def test_failed_helper_detects_failed_status(self) -> None:
        assert raycon_payload_failed({"status": "failed"}) is True
        assert raycon_payload_failed({"status": "error"}) is True
        assert raycon_payload_failed({"status": "completed"}) is False
        assert raycon_payload_failed({}) is False

    def test_failed_helper_detects_validation_passed_false(self) -> None:
        # Even if status is missing/optimistic, validation.passed=false
        # is authoritative — don't publish a successful-looking Doc.
        payload = {"status": "completed", "validation": {"passed": False}}
        assert raycon_payload_failed(payload) is True

    def test_failed_helper_treats_missing_validation_as_not_failed(self) -> None:
        # Legacy flat payloads have no validation block; absence ≠ failure.
        assert raycon_payload_failed({"fastest_open": {}}) is False

    def test_envelope_payload_maps_scenarios_under_analysis(self) -> None:
        """v1.1 envelope: scenarios live under ``analysis.fastest_open`` /
        ``analysis.max_capacity``. The mapper must read from there and
        produce the same exec.* keys it does for the flat shape."""
        payload = {
            "schema_version": "1.0",
            "status": "completed",
            "raycon_run_id": "rc_2026_05_05_abc",
            "analysis": {
                "fastest_open": {
                    "grand_total": 412000,
                    "timeline_weeks": 14,
                    "soft_costs": 32000,
                    "gc_fee": 28000,
                    "contingency": 18000,
                    "furniture": 24000,
                    "categories": [
                        {"category": "Demolition", "subtotal": 12000},
                        {"category": "MEP / Fire / Life Safety", "subtotal": 86000},
                    ],
                },
                "max_capacity": {
                    "grand_total": 587000,
                    "timeline_weeks": 22,
                    "categories": [],
                },
            },
            "validation": {"passed": True, "errors": [], "warnings": []},
            "provenance": {
                "selected_block_plan": {"id": "bp-file-456"},
            },
        }
        fields = raycon_scenario_to_report_fields(payload)
        assert fields["exec.fastest_open_capex"] == "$412,000"
        assert fields["exec.max_capacity_capex"] == "$587,000"
        assert fields["exec.fastest_open_open_date"]  # 14 weeks out, non-empty
        assert fields["exec.cost_demolition_fastest_open"] == "$12,000"
        assert fields["exec.cost_mep_fire_life_safety_fastest_open"] == "$86,000"
        assert fields["exec.cost_soft_costs_fastest_open"] == "$32,000"
        assert fields["exec.cost_grand_total_fastest_open"] == "$412,000"
        # Traceability fields populated
        assert fields["exec.raycon_status"] == "completed"
        assert fields["exec.raycon_run_id"] == "rc_2026_05_05_abc"
        assert fields["exec.raycon_block_plan_used"] == "bp-file-456"
        assert fields["exec.raycon_failure_reason"] == ""

    def test_envelope_prefers_analysis_over_top_level_when_both_present(self) -> None:
        """If RayCon ever (mistakenly) sends both, we trust the envelope so
        we don't accidentally read a stale top-level mirror."""
        payload = {
            "schema_version": "1.0",
            "fastest_open": {"grand_total": 999999},  # should be ignored
            "analysis": {
                "fastest_open": {"grand_total": 100000, "timeline_weeks": 10},
                "max_capacity": {"grand_total": 200000, "timeline_weeks": 20},
            },
        }
        fields = raycon_scenario_to_report_fields(payload)
        assert fields["exec.fastest_open_capex"] == "$100,000"

    def test_failed_status_blanks_all_scenario_fields(self) -> None:
        """Failed run: $0 is the wrong default — it would render as a real
        zero-cost scenario in the dashboard. We emit blanks instead and
        surface the failure reason for the published Doc."""
        payload = {
            "schema_version": "1.0",
            "status": "failed",
            "analysis": {"fastest_open": None, "max_capacity": None},
            "validation": {
                "passed": False,
                "errors": [
                    "Real estate spreadsheet did not resolve a usable microschool tier for this address (status: no_address_match)."
                ],
            },
        }
        fields = raycon_scenario_to_report_fields(payload)
        assert fields["exec.fastest_open_capex"] == ""
        assert fields["exec.max_capacity_capex"] == ""
        assert fields["exec.fastest_open_open_date"] == ""
        assert fields["exec.max_capacity_open_date"] == ""
        for row_key, _ in RAYCON_BREAKDOWN_ROWS:
            assert fields[f"exec.cost_{row_key}_fastest_open"] == ""
            assert fields[f"exec.cost_{row_key}_max_capacity"] == ""
        assert fields["exec.raycon_status"] == "failed"
        assert "no_address_match" in fields["exec.raycon_failure_reason"]

    def test_failed_via_validation_passed_false_blanks_scenarios(self) -> None:
        """Status optimistic but validation says no — still treat as failure."""
        payload = {
            "schema_version": "1.0",
            "status": "completed",  # RayCon optimistic
            "analysis": {
                "fastest_open": {"grand_total": 100, "timeline_weeks": 4},
                "max_capacity": {"grand_total": 200, "timeline_weeks": 8},
            },
            "validation": {"passed": False, "errors": ["missing inputs"]},
        }
        fields = raycon_scenario_to_report_fields(payload)
        # validation overrides optimistic status — scenario fields blanked.
        assert fields["exec.fastest_open_capex"] == ""
        assert fields["exec.max_capacity_capex"] == ""
        assert fields["exec.raycon_failure_reason"] == "missing inputs"

    def test_failure_reason_falls_back_to_analysis_summary(self) -> None:
        """When validation.errors is empty, surface analysis.summary so the
        published Doc still has *something* to explain why we failed."""
        payload = {
            "status": "failed",
            "analysis": {"summary": "Block Plan rooms inconsistent with SIR."},
            "validation": {"passed": False, "errors": []},
        }
        fields = raycon_scenario_to_report_fields(payload)
        assert (
            fields["exec.raycon_failure_reason"]
            == "Block Plan rooms inconsistent with SIR."
        )

    def test_block_plan_used_falls_back_to_provenance(self) -> None:
        """v1.1 envelope reports the consumed Block Plan id under
        ``provenance.selected_block_plan.id`` rather than echoing
        ``block_plan_file_id`` at the top level."""
        payload = {
            "status": "completed",
            "analysis": {"fastest_open": {}, "max_capacity": {}},
            "provenance": {"selected_block_plan": {"id": "prov-bp-789"}},
        }
        fields = raycon_scenario_to_report_fields(payload)
        assert fields["exec.raycon_block_plan_used"] == "prov-bp-789"

    def test_legacy_flat_payload_still_works(self) -> None:
        """Back-compat: spec v1.0 flat payloads (no envelope) keep working.
        This is the contract the original /v1/chat tests captured."""
        payload = {
            "schema_version": "1.0",
            "fastest_open": {
                "grand_total": 250000,
                "timeline_weeks": 8,
                "categories": [{"category": "Demolition", "subtotal": 5000}],
            },
            "max_capacity": {"grand_total": 400000, "timeline_weeks": 16},
        }
        fields = raycon_scenario_to_report_fields(payload)
        assert fields["exec.fastest_open_capex"] == "$250,000"
        assert fields["exec.max_capacity_capex"] == "$400,000"
        assert fields["exec.cost_demolition_fastest_open"] == "$5,000"
        # No envelope means no failure, no traceability values.
        assert fields["exec.raycon_status"] == ""
        assert fields["exec.raycon_failure_reason"] == ""

    def test_real_payload_pbg_failed_run(self) -> None:
        """Regression test built from the real RayCon payload for
        Alpha Palm Beach Gardens (rc_20260505195427_ab6414fec5). RayCon
        returned ``status: failed`` because the address didn't resolve to a
        microschool tier; DDR must blank scenarios and surface the reason."""
        payload = {
            "schema_version": "1.0",
            "raycon_run_id": "rc_20260505195427_ab6414fec5",
            "status": "failed",
            "site": {
                "site_id": "PBG-live-verify",
                "site_name": "Alpha Palm Beach Gardens",
                "total_building_sf": 5560,
            },
            "analysis": {
                "summary": "RayCon could not complete scenario pricing for Alpha Palm Beach Gardens. See validation errors.",
                "rooms": [{"name": "1 ENTRY", "type": "lobby", "sqft": 180}],
                "fastest_open": None,
                "max_capacity": None,
                "ray_review": None,
            },
            "provenance": {
                "selected_block_plan": {"id": "14C7o_nwNqM9O-TsHzLYTqbBhHad6rKsg"},
            },
            "validation": {
                "passed": False,
                "errors": [
                    "Real estate spreadsheet did not resolve a usable microschool tier for this address (status: no_address_match)."
                ],
                "warnings": [],
            },
        }
        fields = raycon_scenario_to_report_fields(payload)
        assert fields["exec.raycon_status"] == "failed"
        assert fields["exec.raycon_run_id"] == "rc_20260505195427_ab6414fec5"
        assert (
            fields["exec.raycon_block_plan_used"]
            == "14C7o_nwNqM9O-TsHzLYTqbBhHad6rKsg"
        )
        assert "no_address_match" in fields["exec.raycon_failure_reason"]
        assert fields["exec.fastest_open_capex"] == ""
        assert fields["exec.max_capacity_capex"] == ""
