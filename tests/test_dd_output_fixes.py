"""Tests for DD report output fixes."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from due_diligence_reporter.google_client import GoogleClient
from due_diligence_reporter.server import (
    MATTERBOT_BASE_URL,
)
from due_diligence_reporter.utils import (
    escape_drive_query_literal,
    find_text_index_in_doc,
    sanitize_http_url,
)
from due_diligence_reporter.wrike import classify_comment_to_section


# ---------------------------------------------------------------------------
# Scope of Work stray numbers — consecutive newline collapse
# ---------------------------------------------------------------------------

class TestScopeOfWorkNewlineCollapse:
    """Verify that consecutive newlines in scope_of_work are collapsed."""

    def test_collapse_double_newlines(self) -> None:
        text = "Item 1\n\nItem 2\n\n\nItem 3"
        result = re.sub(r"\n{2,}", "\n", text)
        assert result == "Item 1\nItem 2\nItem 3"

    def test_single_newlines_preserved(self) -> None:
        text = "Item 1\nItem 2\nItem 3"
        result = re.sub(r"\n{2,}", "\n", text)
        assert result == text

    def test_no_newlines(self) -> None:
        text = "Single line"
        result = re.sub(r"\n{2,}", "\n", text)
        assert result == text


# ---------------------------------------------------------------------------
# find_text_index_in_doc
# ---------------------------------------------------------------------------

class TestFindTextIndex:
    def test_finds_placeholder(self) -> None:
        body = {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {
                                "textRun": {
                                    "content": "Hello {{q2.floorplan_image}} world",
                                },
                                "startIndex": 10,
                            }
                        ]
                    }
                }
            ]
        }
        idx = find_text_index_in_doc(body, "{{q2.floorplan_image}}")
        assert idx == 16  # 10 + 6 (offset of "{{" in the string)

    def test_returns_none_when_not_found(self) -> None:
        body = {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {
                                "textRun": {"content": "No placeholder here"},
                                "startIndex": 0,
                            }
                        ]
                    }
                }
            ]
        }
        assert find_text_index_in_doc(body, "{{missing}}") is None

    def test_empty_body(self) -> None:
        assert find_text_index_in_doc({}, "{{anything}}") is None


# ---------------------------------------------------------------------------
# Fix 4: PDF mimeType preference — tested via logic check
# ---------------------------------------------------------------------------

class TestPdfMimePreference:
    """Verify that the PDF-preference logic selects PDFs over Google Docs."""

    def test_prefer_pdf_over_gdoc(self) -> None:
        matches = [
            {"name": "ISP.pdf", "mimeType": "application/vnd.google-apps.document", "id": "1"},
            {"name": "ISP.pdf", "mimeType": "application/pdf", "id": "2"},
        ]
        pdf_matches = [f for f in matches if f.get("mimeType") == "application/pdf"]
        best = pdf_matches[0] if pdf_matches else matches[0]
        assert best["id"] == "2"
        assert best["mimeType"] == "application/pdf"

    def test_fallback_to_first_when_no_pdf(self) -> None:
        matches = [
            {"name": "ISP", "mimeType": "application/vnd.google-apps.document", "id": "1"},
        ]
        pdf_matches = [f for f in matches if f.get("mimeType") == "application/pdf"]
        best = pdf_matches[0] if pdf_matches else matches[0]
        assert best["id"] == "1"


# ---------------------------------------------------------------------------
# Fix 5: Comment classification
# ---------------------------------------------------------------------------

class TestCommentClassification:
    def test_zoning_comment(self) -> None:
        assert classify_comment_to_section("Zoning variance required for this location") == "q1"

    def test_pre_app_comment(self) -> None:
        assert classify_comment_to_section("Pre-app meeting notes from city planner") == "q1"

    def test_permit_comment(self) -> None:
        assert classify_comment_to_section("Permit timeline is 6-8 weeks") == "q1"

    def test_building_comment(self) -> None:
        assert classify_comment_to_section("HVAC system needs full replacement") == "q2"

    def test_inspection_comment(self) -> None:
        assert classify_comment_to_section("Building inspection scheduled for Monday") == "q2"

    def test_cost_comment(self) -> None:
        assert classify_comment_to_section("Budget estimate came in at $250k") == "q3"

    def test_timeline_comment(self) -> None:
        assert classify_comment_to_section("Timeline pushed back, target date is Q3") == "q4"

    def test_general_comment(self) -> None:
        assert classify_comment_to_section("Great location, team is excited") == "general"

    def test_empty_comment(self) -> None:
        assert classify_comment_to_section("") == "general"


# ---------------------------------------------------------------------------
# MatterBot integration
# ---------------------------------------------------------------------------

class TestGenerateMarketingPack:
    """Tests for the generate_marketing_pack MCP tool."""

    def test_matterbot_base_url_is_set(self) -> None:
        assert MATTERBOT_BASE_URL.startswith("https://")
        assert "matterbot" in MATTERBOT_BASE_URL

    @patch("due_diligence_reporter.server.requests.get")
    def test_successful_trigger(self, mock_get: MagicMock) -> None:
        import asyncio
        from due_diligence_reporter.server import generate_marketing_pack

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = f"{MATTERBOT_BASE_URL}/api/batch/generate-marketing-pack/abc123?space_name=Test"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = asyncio.run(generate_marketing_pack(
            space_sid="abc123", space_name="Test Site",
        ))

        assert result["status"] == "success"
        assert "triggered" in result["message"]
        mock_get.assert_called_once()
        call_url = mock_get.call_args[0][0]
        assert "abc123" in call_url

    def test_empty_space_sid_returns_error(self) -> None:
        import asyncio
        from due_diligence_reporter.server import generate_marketing_pack

        result = asyncio.run(generate_marketing_pack(space_sid="", space_name="Test"))
        assert result["status"] == "error"
        assert "space_sid" in result["message"]

    def test_empty_space_name_returns_error(self) -> None:
        import asyncio
        from due_diligence_reporter.server import generate_marketing_pack

        result = asyncio.run(generate_marketing_pack(space_sid="abc", space_name=""))
        assert result["status"] == "error"
        assert "space_name" in result["message"]

    def test_invalid_tier_returns_error(self) -> None:
        from due_diligence_reporter.server import generate_marketing_pack

        result = asyncio.run(generate_marketing_pack(
            space_sid="abc", space_name="Test", tier="ultra",
        ))
        assert result["status"] == "error"
        assert "tier" in result["message"]

    def test_invalid_space_sid_returns_error(self) -> None:
        from due_diligence_reporter.server import generate_marketing_pack

        result = asyncio.run(generate_marketing_pack(
            space_sid="../abc", space_name="Test Site",
        ))

        assert result["status"] == "error"
        assert "space_sid" in result["message"]


class TestCheckReportCompleteness:
    def test_rejects_invalid_can_we_answer(self) -> None:
        from due_diligence_reporter.server import check_report_completeness

        gc = MagicMock()
        gc.export_google_doc_as_text.return_value = (
            "Can we do this?\n"
            "YES Education Regulatory Approval: Not required "
            "Occupancy path: Has E-Occupancy Zoning: Permitted by right\n"
        )

        with patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=gc,
        ):
            result = asyncio.run(check_report_completeness("doc123"))

        assert result["status"] == "success"
        assert result["ready_to_send"] is False
        assert result["invalid_can_we_answer"] == "YES"
        assert "Can we do this?" in result["summary"]

    def test_accepts_canonical_can_we_answer(self) -> None:
        from due_diligence_reporter.server import check_report_completeness

        gc = MagicMock()
        gc.export_google_doc_as_text.return_value = (
            "Can we do this?\n"
            "Yes see notes Education Regulatory Approval: Not required "
            "Occupancy path: Has E-Occupancy Zoning: Permitted by right\n"
        )

        with patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=gc,
        ):
            result = asyncio.run(check_report_completeness("doc123"))

        assert result["status"] == "success"
        assert result["ready_to_send"] is True
        assert result["invalid_can_we_answer"] is None

    @patch("due_diligence_reporter.server.requests.get")
    def test_optional_params_passed_correctly(self, mock_get: MagicMock) -> None:
        import asyncio
        from due_diligence_reporter.server import generate_marketing_pack

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "http://test"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        asyncio.run(generate_marketing_pack(
            space_sid="abc123", space_name="Test Site",
            tier="premium", max_rooms=5, room_types="classroom,commons",
        ))

        call_kwargs = mock_get.call_args
        params = call_kwargs[1]["params"]
        assert params["tier"] == "premium"
        assert params["max_rooms"] == 5
        assert params["room_types"] == "classroom,commons"

    @patch("due_diligence_reporter.server.requests.get")
    def test_timeout_returns_error(self, mock_get: MagicMock) -> None:
        import asyncio
        from due_diligence_reporter.server import generate_marketing_pack
        import requests as _requests

        mock_get.side_effect = _requests.Timeout("timed out")

        result = asyncio.run(generate_marketing_pack(
            space_sid="abc123", space_name="Test Site",
        ))

        assert result["status"] == "error"
        assert "timeout" in result["error"].lower()

    @patch("due_diligence_reporter.server.requests.get")
    def test_http_error_returns_error(self, mock_get: MagicMock) -> None:
        import asyncio
        from due_diligence_reporter.server import generate_marketing_pack
        import requests as _requests

        mock_get.side_effect = _requests.ConnectionError("refused")

        result = asyncio.run(generate_marketing_pack(
            space_sid="abc123", space_name="Test Site",
        ))

        assert result["status"] == "error"
        assert "failed" in result["error"].lower()

    @patch("due_diligence_reporter.server.requests.get")
    def test_generate_marketing_pack_uses_to_thread(self, mock_get: MagicMock) -> None:
        from due_diligence_reporter.server import generate_marketing_pack

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com/trigger"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        async def run_inline(func: Any, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        with patch(
            "due_diligence_reporter.server.asyncio.to_thread",
            new=AsyncMock(side_effect=run_inline),
        ) as mock_to_thread:
            result = asyncio.run(generate_marketing_pack(space_sid="abc123", space_name="Test Site"))

        assert result["status"] == "success"
        mock_to_thread.assert_awaited_once()


class TestEmailEscaping:
    @patch("due_diligence_reporter.server.send_email")
    @patch("due_diligence_reporter.server.get_settings")
    def test_send_dd_report_email_escapes_html_content(
        self,
        mock_get_settings: MagicMock,
        mock_send_email: MagicMock,
    ) -> None:
        from due_diligence_reporter.server import send_dd_report_email

        mock_get_settings.return_value = MagicMock(
            email_sender="sender@example.com",
            email_app_password="secret",
            dd_report_email_recipients="ops@example.com",
        )

        result = asyncio.run(send_dd_report_email(
            site_name="<b>Alpha</b>",
            report_url="https://example.com/report",
            key_findings="<script>alert(1)</script>",
        ))

        assert result["status"] == "success"
        html_body = mock_send_email.call_args.kwargs["html_body"]
        assert "&lt;b&gt;Alpha&lt;/b&gt;" in html_body
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_body
        assert "<script>alert(1)</script>" not in html_body
        assert 'href="https://example.com/report"' in html_body

    def test_sanitize_http_url_rejects_javascript(self) -> None:
        assert sanitize_http_url("javascript:alert(1)") is None

    @patch("due_diligence_reporter.server.get_settings")
    def test_send_dd_report_email_rejects_invalid_url(
        self,
        mock_get_settings: MagicMock,
    ) -> None:
        from due_diligence_reporter.server import send_dd_report_email

        mock_get_settings.return_value = MagicMock(
            email_sender="sender@example.com",
            email_app_password="secret",
            dd_report_email_recipients="ops@example.com",
        )

        result = asyncio.run(send_dd_report_email(
            site_name="Alpha",
            report_url="javascript:alert(1)",
            key_findings="Safe",
        ))

        assert result["status"] == "error"
        assert result["error"] == "Invalid report_url"

    @patch("due_diligence_reporter.server.send_email")
    @patch("due_diligence_reporter.server.get_settings")
    def test_send_dd_report_email_uses_to_thread(
        self,
        mock_get_settings: MagicMock,
        mock_send_email: MagicMock,
    ) -> None:
        from due_diligence_reporter.server import send_dd_report_email

        mock_get_settings.return_value = MagicMock(
            email_sender="sender@example.com",
            email_app_password="secret",
            dd_report_email_recipients="ops@example.com",
        )

        async def run_inline(func: Any, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        with patch(
            "due_diligence_reporter.server.asyncio.to_thread",
            new=AsyncMock(side_effect=run_inline),
        ) as mock_to_thread:
            result = asyncio.run(send_dd_report_email(
                site_name="Alpha",
                report_url="https://example.com/report",
                key_findings="Looks fine",
            ))

        assert result["status"] == "success"
        mock_send_email.assert_called_once()
        mock_to_thread.assert_awaited_once()


class TestDriveQueryEscaping:
    def test_escape_drive_query_literal(self) -> None:
        assert escape_drive_query_literal(r"O'Brien\ISP.pdf") == r"O\'Brien\\ISP.pdf"

    def test_file_exists_uses_escaped_query(self) -> None:
        files_resource = MagicMock()
        files_resource.list.return_value.execute.return_value = {"files": []}
        drive_service = MagicMock()
        drive_service.files.return_value = files_resource

        client = GoogleClient.__new__(GoogleClient)
        client.drive_service = drive_service

        client.file_exists_in_folder("folder123", r"O'Brien\ISP.pdf")

        query = files_resource.list.call_args.kwargs["q"]
        assert r"name='O\'Brien\\ISP.pdf'" in query


class TestConfiguredModels:
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_FILENAME_MODEL": "gpt-custom-mini"})
    def test_filename_classifier_uses_configured_model(self) -> None:
        from due_diligence_reporter.classifier import classify_by_filename_llm

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"doc_type":"sir","confidence":0.9}'))]
        )

        with patch("openai.OpenAI", return_value=mock_client):
            doc_type, confidence = classify_by_filename_llm("Alpha Keller SIR.pdf")

        assert doc_type == "sir"
        assert confidence == 0.9
        assert mock_client.chat.completions.create.call_args.kwargs["model"] == "gpt-custom-mini"


class TestAsyncOffloading:
    def test_get_cost_estimate_uses_to_thread(self) -> None:
        from due_diligence_reporter.server import get_cost_estimate

        async def run_inline(func: Any, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        with patch(
            "due_diligence_reporter.server.asyncio.to_thread",
            new=AsyncMock(side_effect=run_inline),
        ) as mock_to_thread, patch(
            "due_diligence_reporter.server._call_pricing_api",
            side_effect=[
                {"data": {"rooms": []}},
                {"data": {"rooms": []}},
            ],
        ):
            result = asyncio.run(get_cost_estimate(total_building_sf=1000, classroom_count=2))

        assert result["status"] == "success"
        mock_to_thread.assert_awaited_once()

    def test_save_skill_report_uses_to_thread(self) -> None:
        from due_diligence_reporter.server import save_skill_report

        gc = MagicMock()
        gc.list_subfolders.return_value = [{"id": "m1-folder", "name": "M1 Property Acquired"}]
        gc.create_document.return_value = {"id": "doc123", "webViewLink": "https://example.com/doc123"}

        async def run_inline(func: Any, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        with patch(
            "due_diligence_reporter.server.asyncio.to_thread",
            new=AsyncMock(side_effect=run_inline),
        ) as mock_to_thread, patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=gc,
        ):
            result = asyncio.run(save_skill_report(
                skill_name="E-Occupancy",
                site_name="Alpha",
                drive_folder_url="https://drive.google.com/drive/folders/folder123",
                skill_data={"final_score": 90},
            ))

        assert result["status"] == "success"
        gc.create_document.assert_called_once()
        mock_to_thread.assert_awaited_once()

    def test_create_dd_report_uses_to_thread(self) -> None:
        from due_diligence_reporter.server import create_dd_report

        gc = MagicMock()
        gc.copy_document.return_value = {
            "id": "doc123",
            "webViewLink": "https://docs.google.com/document/d/doc123",
        }
        gc.get_document.return_value = {"body": {}}
        gc.upload_file_to_folder.return_value = {"webViewLink": ""}

        async def run_inline(func: Any, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        with patch(
            "due_diligence_reporter.server.asyncio.to_thread",
            new=AsyncMock(side_effect=run_inline),
        ) as mock_to_thread, patch(
            "due_diligence_reporter.server.get_settings",
            return_value=MagicMock(dd_template_v2_google_doc_id="template123"),
        ), patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=gc,
        ), patch(
            "due_diligence_reporter.server.normalize_report_data",
            return_value=({}, [], [], {}),
        ), patch(
            "due_diligence_reporter.server.compute_deltas",
        ), patch(
            "due_diligence_reporter.server.build_replace_all_text_requests",
            return_value=[],
        ):
            result = asyncio.run(create_dd_report(
                site_name="Alpha",
                drive_folder_url="https://drive.google.com/drive/folders/folder123",
                report_data={},
            ))

        assert result["status"] == "success"
        gc.copy_document.assert_called_once()
        mock_to_thread.assert_awaited_once()
