"""Tests for the dashboard_readiness client and its scanner integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from due_diligence_reporter import dashboard_readiness
from due_diligence_reporter.dashboard_readiness import (
    DOC_TYPE_TO_FIELD,
    edits_from_uploads,
    field_for_doc_type,
    mark_readiness_complete,
)


class TestFieldMapping:
    def test_known_doc_types_map_to_fields(self) -> None:
        assert field_for_doc_type("sir") == "cds_sir_status"
        assert field_for_doc_type("building_inspection") == "building_inspection_status"
        assert field_for_doc_type("block_plan") == "block_plan_status"

    def test_unknown_doc_type_returns_none(self) -> None:
        # ISP is intentionally not in the map (no Portfolio column).
        assert field_for_doc_type("isp") is None
        assert field_for_doc_type("dd_report") is None
        assert field_for_doc_type("") is None
        assert field_for_doc_type("unknown") is None


class TestEditsFromUploads:
    def test_translates_uploads_to_edits(self) -> None:
        uploads = [
            {"doc_type": "sir", "site_title": "Austin"},
            {"doc_type": "building_inspection", "site_title": "Austin"},
            {"doc_type": "block_plan", "site_title": "Chicago 350"},
        ]
        edits = edits_from_uploads(uploads)
        assert {(e["slug"], e["fieldPath"]) for e in edits} == {
            ("austin", "cds_sir_status"),
            ("austin", "building_inspection_status"),
            ("chicago-350", "block_plan_status"),
        }

    def test_dedupes_duplicates(self) -> None:
        uploads = [
            {"doc_type": "sir", "site_title": "Austin"},
            {"doc_type": "sir", "site_title": "Austin"},
        ]
        edits = edits_from_uploads(uploads)
        assert len(edits) == 1

    def test_skips_unmapped_doc_types(self) -> None:
        uploads = [
            {"doc_type": "isp", "site_title": "Austin"},
            {"doc_type": "dd_report", "site_title": "Austin"},
            {"doc_type": "unknown", "site_title": "Austin"},
        ]
        assert edits_from_uploads(uploads) == []

    def test_skips_dry_run_entries(self) -> None:
        uploads = [
            {"doc_type": "sir", "site_title": "Austin", "dry_run": True},
            {"doc_type": "block_plan", "site_title": "Austin"},
        ]
        edits = edits_from_uploads(uploads)
        assert edits == [{"slug": "austin", "fieldPath": "block_plan_status"}]

    def test_skips_entries_missing_site_title(self) -> None:
        uploads = [{"doc_type": "sir", "site_title": ""}]
        assert edits_from_uploads(uploads) == []

    def test_handles_empty_or_invalid_input(self) -> None:
        assert edits_from_uploads([]) == []
        assert edits_from_uploads([None, "not a dict", 42]) == []  # type: ignore[list-item]


class TestMarkReadinessComplete:
    def test_empty_edits_short_circuits(self) -> None:
        result = mark_readiness_complete([])
        assert result == {"applied": 0, "skipped": [], "ok": True, "reason": None}

    @patch.dict("os.environ", {"DASHBOARD_PUBLISH_ENABLED": "0"}, clear=False)
    def test_disabled_when_publish_flag_off(self) -> None:
        result = mark_readiness_complete(
            [{"slug": "austin", "fieldPath": "cds_sir_status"}]
        )
        assert result["ok"] is True
        assert result["applied"] == 0
        assert "disabled" in (result["reason"] or "").lower()

    @patch.dict("os.environ", {"DASHBOARD_PUBLISH_ENABLED": "1"}, clear=True)
    def test_missing_token_fails_gracefully(self) -> None:
        # No INBOX_SCANNER_TOKEN in env.
        result = mark_readiness_complete(
            [{"slug": "austin", "fieldPath": "cds_sir_status"}]
        )
        assert result["ok"] is False
        assert "INBOX_SCANNER_TOKEN" in (result["reason"] or "")

    @patch.dict(
        "os.environ",
        {"INBOX_SCANNER_TOKEN": "test-token", "DASHBOARD_PUBLISH_ENABLED": "1"},
        clear=False,
    )
    @patch.object(dashboard_readiness.requests, "post")
    def test_happy_path_posts_with_bearer(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"applied": 2, "skipped": []}
        mock_post.return_value = mock_resp

        edits = [
            {"slug": "austin", "fieldPath": "cds_sir_status"},
            {"slug": "austin", "fieldPath": "block_plan_status"},
        ]
        result = mark_readiness_complete(edits)

        assert result == {"applied": 2, "skipped": [], "ok": True, "reason": None}
        assert mock_post.call_count == 1
        call_args = mock_post.call_args
        assert call_args.kwargs["headers"]["Authorization"] == "Bearer test-token"
        body = call_args.kwargs["json"]
        assert body["editedBy"] == "inbox-scanner"
        assert all(e["value"] == "complete" for e in body["edits"])
        assert len(body["edits"]) == 2

    @patch.dict(
        "os.environ",
        {"INBOX_SCANNER_TOKEN": "test-token", "DASHBOARD_PUBLISH_ENABLED": "1"},
        clear=False,
    )
    @patch.object(dashboard_readiness.requests, "post")
    def test_filters_disallowed_field_paths(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"applied": 1, "skipped": []}
        mock_post.return_value = mock_resp

        result = mark_readiness_complete(
            [
                {"slug": "austin", "fieldPath": "cds_sir_status"},
                {"slug": "austin", "fieldPath": "c_zoning"},  # disallowed
                {"slug": "austin", "fieldPath": ""},  # empty
                {"slug": "", "fieldPath": "cds_sir_status"},  # empty slug
            ]
        )

        # Only one valid edit should reach the wire.
        assert result["ok"] is True
        body = mock_post.call_args.kwargs["json"]
        assert len(body["edits"]) == 1
        assert body["edits"][0]["fieldPath"] == "cds_sir_status"

    @patch.dict(
        "os.environ",
        {"INBOX_SCANNER_TOKEN": "test-token", "DASHBOARD_PUBLISH_ENABLED": "1"},
        clear=False,
    )
    @patch.object(dashboard_readiness.requests, "post")
    def test_http_error_is_swallowed(self, mock_post: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "internal server error"
        mock_post.return_value = mock_resp

        result = mark_readiness_complete(
            [{"slug": "austin", "fieldPath": "cds_sir_status"}]
        )
        assert result["ok"] is False
        assert "500" in (result["reason"] or "")

    @patch.dict(
        "os.environ",
        {"INBOX_SCANNER_TOKEN": "test-token", "DASHBOARD_PUBLISH_ENABLED": "1"},
        clear=False,
    )
    @patch.object(dashboard_readiness.requests, "post")
    def test_network_exception_is_swallowed(self, mock_post: MagicMock) -> None:
        import requests as _requests

        mock_post.side_effect = _requests.ConnectionError("DNS failure")
        result = mark_readiness_complete(
            [{"slug": "austin", "fieldPath": "cds_sir_status"}]
        )
        assert result["ok"] is False
        assert "DNS" in (result["reason"] or "")

    def test_lidar_status_is_allowlisted_even_though_unmapped(self) -> None:
        """Future-proof: callers may set lidar_status directly even though no
        doc_type maps to it. The endpoint accepts it, so the client must too."""
        with patch.dict(
            "os.environ",
            {"INBOX_SCANNER_TOKEN": "t", "DASHBOARD_PUBLISH_ENABLED": "1"},
            clear=False,
        ), patch.object(dashboard_readiness.requests, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"applied": 1, "skipped": []}
            mock_post.return_value = mock_resp

            result = mark_readiness_complete(
                [{"slug": "austin", "fieldPath": "lidar_status"}]
            )
            assert result["ok"] is True
            body = mock_post.call_args.kwargs["json"]
            assert body["edits"][0]["fieldPath"] == "lidar_status"


class TestDocTypeToFieldCoverage:
    """Sanity check: every key in the map matches the dashboard endpoint's allowlist."""

    def test_all_mapped_fields_are_dashboard_allowlisted(self) -> None:
        DASHBOARD_ALLOWED = {
            "cds_sir_status",
            "building_inspection_status",
            "block_plan_status",
            "lidar_status",
        }
        for doc_type, field in DOC_TYPE_TO_FIELD.items():
            assert field in DASHBOARD_ALLOWED, (
                f"doc_type {doc_type!r} maps to {field!r} which is not in "
                f"the dashboard's auto-readiness allowlist"
            )
