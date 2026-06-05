"""Tests for DD report output fixes."""

from __future__ import annotations

import asyncio
import io
import os
import re
import zipfile
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from due_diligence_reporter.google_client import GoogleClient
from due_diligence_reporter.server import (
    MATTERBOT_BASE_URL,
    _validate_document_site_context,
)
from due_diligence_reporter.utils import (
    escape_drive_query_literal,
    find_text_index_in_doc,
    sanitize_http_url,
)


def _build_docx_bytes(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        archive.writestr(
            "word/document.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{text}</w:t></w:r></w:p>
  </w:body>
</w:document>""",
        )
    return buffer.getvalue()

# ---------------------------------------------------------------------------
# Scope of Work stray numbers â€” consecutive newline collapse
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
# Fix 4: PDF mimeType preference â€” tested via logic check
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
# DOCX extraction and site validation
# ---------------------------------------------------------------------------


class TestReadDriveDocumentDocx:
    def test_docx_returns_readable_text(self) -> None:
        from due_diligence_reporter.server import read_drive_document

        gc = MagicMock()
        gc.drive_service.files.return_value.get.return_value.execute.return_value = {
            "id": "docx-1",
            "name": "School Approval.docx",
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        gc.download_file_bytes.return_value = _build_docx_bytes("Alpha Miami Beach approval path")

        with patch("due_diligence_reporter.server._make_google_client", return_value=gc):
            result = asyncio.run(read_drive_document("docx-1", "School Approval.docx"))

        assert result["status"] == "success"
        assert result["source_usable"] is True
        assert result["unreadable"] is False
        assert "Alpha Miami Beach approval path" in result["text"]

    def test_broken_docx_returns_structured_warning(self) -> None:
        from due_diligence_reporter.server import read_drive_document

        gc = MagicMock()
        gc.drive_service.files.return_value.get.return_value.execute.return_value = {
            "id": "docx-2",
            "name": "Broken School Approval.docx",
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        gc.download_file_bytes.return_value = b"not-a-zip-file"

        with patch("due_diligence_reporter.server._make_google_client", return_value=gc):
            result = asyncio.run(read_drive_document("docx-2", "Broken School Approval.docx"))

        assert result["status"] == "success"
        assert result["source_usable"] is False
        assert result["unreadable"] is True
        assert result["source_quality_warnings"]
        assert "could not be parsed as a DOCX file" in result["text"]


class TestDocumentSiteValidation:
    def test_rejects_mismatched_building_inspection_content(self) -> None:
        usable, warnings, verified = _validate_document_site_context(
            "Building Inspection Report.pdf",
            "Alpha Sunny Isles 17701 Collins Ave facility condition assessment",
            site_title="Alpha School Miami Beach 300 71st St",
            site_address="300 71st St, Miami Beach, FL 33141",
            doc_type="building_inspection",
        )

        assert usable is False
        assert verified is False
        assert warnings
        assert "excluded from this run" in warnings[0]

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
            "Can this school be open in time for the current school year?\n"
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
        assert "Can this school be open in time for the current school year?" in result["summary"]

    def test_accepts_canonical_can_we_answer(self) -> None:
        from due_diligence_reporter.server import check_report_completeness

        gc = MagicMock()
        # Canonical answer is the binary plain-English "Yes" / "No" — the
        # literal answer to "Can this be a school by [date]?". The publisher
        # derives Go / No Go separately into `dd_recommendation`.
        gc.export_google_doc_as_text.return_value = (
            "Can this school be open in time for the current school year?\n"
            "Yes Education Regulatory Approval: Not required "
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

    def test_accepts_rendered_can_we_answer_phrase(self) -> None:
        from due_diligence_reporter.server import check_report_completeness

        gc = MagicMock()
        gc.export_google_doc_as_text.return_value = (
            "Can this school be open in time for the current school year (8/12 or 9/8)?\n"
            "No, because:\n"
            "Education Regulatory Approval: Not required "
            "Occupancy path: Has E-Occupancy Zoning: Use Permit Required (public)\n"
        )

        with patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=gc,
        ):
            result = asyncio.run(check_report_completeness("doc123"))

        assert result["status"] == "success"
        assert result["ready_to_send"] is True
        assert result["invalid_can_we_answer"] is None

    def test_rejects_raw_template_tokens(self) -> None:
        from due_diligence_reporter.server import check_report_completeness

        gc = MagicMock()
        gc.export_google_doc_as_text.return_value = (
            "Can this school be open in time for the current school year?\n"
            "No Education Regulatory Approval: Required have not done "
            "Occupancy path: Change of use required, needs work Zoning: Use Permit Required (Public approval)\n"
            "Build Scenarios\n"
            "exec.cost_demolition_fastest_open exec.max_capacity_capex\n"
        )

        with patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=gc,
        ):
            result = asyncio.run(check_report_completeness("doc123"))

        assert result["status"] == "success"
        assert result["ready_to_send"] is False
        assert result["raw_template_token_count"] == 2
        assert "exec.cost_demolition_fastest_open" in result["raw_template_tokens"]
        assert "raw template token" in result["summary"]


class TestListDriveDocumentsFiltering:
    def test_rejects_drive_root_folder_url(self) -> None:
        from due_diligence_reporter.server import list_drive_documents

        result = asyncio.run(list_drive_documents(
            "https://drive.google.com/drive/folders/root",
            "Alpha Miami Beach 300 71st St",
        ))

        assert result["status"] == "error"
        assert result["error"] == "Invalid folder URL"
        assert "Google Drive root" in result["message"]

    def test_returns_site_folder_report_and_source_artifacts(self) -> None:
        from due_diligence_reporter.server import list_drive_documents

        gc = MagicMock()
        gc.list_files_recursive.return_value = [
            {"id": "site-sir", "name": "Alpha Keller SIR.pdf"},
            {"id": "opening-plan", "name": "Opening Plan - Alpha Keller"},
            {"id": "eocc", "name": "E-Occupancy Assessment - Alpha Keller"},
            {"id": "trace", "name": "Alpha Keller DD Report Trace - 2026-04-20.json"},
            {"id": "dd-report", "name": "Alpha Keller DD Report - 2026-04-20"},
            {"id": "lease", "name": "Lease Draft.pdf"},
        ]

        with patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=gc,
        ), patch(
            "due_diligence_reporter.server._find_site_docs_in_shared_folders",
            return_value={
                "sir": {"id": "shared-sir", "name": "Alpha Keller SIR.pdf", "doc_type": "sir"},
                "isp": None,
                "building_inspection": None,
            },
        ):
            result = asyncio.run(list_drive_documents(
                "https://drive.google.com/drive/folders/folder123",
                "Alpha Keller",
                "123 Main St, Keller TX",
            ))

        assert result["status"] == "success"
        site_files = {file_info["name"]: file_info for file_info in result["site_folder_files"]}
        assert "Lease Draft.pdf" not in site_files
        assert "Alpha Keller DD Report - 2026-04-20" not in site_files
        assert "Alpha Keller DD Report Trace - 2026-04-20.json" not in site_files
        assert site_files["Alpha Keller SIR.pdf"]["doc_type"] == "sir"
        assert site_files["Opening Plan - Alpha Keller"]["doc_type"] == "opening_plan_report"
        assert site_files["E-Occupancy Assessment - Alpha Keller"]["doc_type"] == "e_occupancy_report"
        assert result["shared_folder_files"][0]["reference_origin"] == "shared_source"

    def test_missing_site_name_lists_drive_folder_without_shared_search(self) -> None:
        from due_diligence_reporter.server import list_drive_documents

        gc = MagicMock()
        gc.list_files_recursive.return_value = [
            {"id": "site-sir", "name": "Alpha Beethoven_SIR.docx"},
            {"id": "school-approval", "name": "Alpha Beethoven_school-approval.docx"},
        ]

        with patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=gc,
        ), patch(
            "due_diligence_reporter.server._find_site_docs_in_shared_folders",
        ) as find_shared:
            result = asyncio.run(list_drive_documents(
                "https://drive.google.com/drive/folders/folder123",
                "",
            ))

        assert result["status"] == "success"
        assert result["shared_folder_files"] == []
        assert find_shared.call_count == 0
        assert [f["doc_type"] for f in result["site_folder_files"]] == [
            "sir",
            "school_approval_report",
        ]


class TestReportNormalizationDefaults:
    def test_normalize_report_replacements_uses_supplied_prepared_by(self) -> None:
        from due_diligence_reporter.server import _normalize_report_replacements

        replacements, _, _, _, _ = _normalize_report_replacements(
            report_data={"meta": {"prepared_by": "Jane Owner"}},
            site_name="Alpha Atlanta 345",
            report_date="04/02/2026",
            drive_folder_url="https://drive.google.com/drive/folders/folder123",
        )

        assert replacements["meta.prepared_by"] == "Jane Owner"

    def test_normalize_report_replacements_uses_canonical_site_name(self) -> None:
        from due_diligence_reporter.server import _normalize_report_replacements

        replacements, _, _, _, _ = _normalize_report_replacements(
            report_data={"meta": {"site_name": "Alpha Los Angeles"}},
            site_name="Alpha Los Angeles 5400 Beethoven St",
            report_date="05/26/2026",
            drive_folder_url="https://drive.google.com/drive/folders/folder123",
        )

        assert replacements["meta.site_name"] == "Alpha Los Angeles 5400 Beethoven St"

    def test_normalize_report_replacements_overrides_root_drive_folder_url(self) -> None:
        from due_diligence_reporter.server import _normalize_report_replacements

        replacements, _, _, _, _ = _normalize_report_replacements(
            report_data={
                "meta": {
                    "drive_folder_url": "https://drive.google.com/drive/folders/root",
                },
            },
            site_name="Alpha Miami Beach 300 71st St",
            report_date="06/01/2026",
            drive_folder_url="https://drive.google.com/drive/folders/site-root",
        )

        assert replacements["meta.drive_folder_url"].endswith("/site-root")

    def test_normalize_report_replacements_fills_two_scenario_gap_labels(self) -> None:
        from due_diligence_reporter.server import _normalize_report_replacements

        replacements, _, _, _, _ = _normalize_report_replacements(
            report_data={
                "exec": {
                    "c_answer": "Conditional",
                    "e_mvp_capacity": "25",
                    "e_mvp_cost": "$401,000",
                    "f_mvp_ready": "10/27",
                },
            },
            site_name="Alpha Atlanta 345",
            report_date="04/02/2026",
            drive_folder_url="https://drive.google.com/drive/folders/folder123",
        )

        # Date 10/27 (Oct 2027) is past both school year deadlines — deterministic "No"
        assert replacements["exec.c_answer"] == "No"
        assert replacements["exec.max_capacity_capacity"] == "[Not found - Max Capacity scenario not extracted]"
        assert replacements["exec.max_capacity_capex"] == "[Not found - Max Capacity scenario not extracted]"
        assert "exec.max_value_capacity" not in replacements

    def test_normalize_report_replacements_marks_failed_raycon_tokens(self) -> None:
        from due_diligence_reporter.server import _normalize_report_replacements

        replacements, _, _, _, _ = _normalize_report_replacements(
            report_data={
                "exec": {
                    "raycon_status": "failed",
                    "raycon_failure_reason": "capacity_not_defensible",
                    "fastest_open_capex": "$401,000",
                    "cost_grand_total_fastest_open": "$401,000",
                },
            },
            site_name="Alpha Tulsa 6940 S Utica Ave",
            report_date="05/28/2026",
            drive_folder_url="https://drive.google.com/drive/folders/folder123",
        )

        assert (
            replacements["exec.fastest_open_capex"]
            == "[Not found - RayCon validation failed]"
        )
        assert (
            replacements["exec.cost_grand_total_fastest_open"]
            == "[Not found - RayCon validation failed]"
        )

    def test_normalize_report_replacements_preserves_verification_open_items(self) -> None:
        from due_diligence_reporter.google_doc_builder import VERIFICATION_OPEN_ITEMS_KEY
        from due_diligence_reporter.server import _normalize_report_replacements

        replacements, _, _, _, _ = _normalize_report_replacements(
            report_data={
                "verification": {
                    "open_items": "- Confirm zoning path with Planning",
                },
            },
            site_name="Alpha Atlanta 345",
            report_date="04/02/2026",
            drive_folder_url="https://drive.google.com/drive/folders/folder123",
        )

        assert replacements[VERIFICATION_OPEN_ITEMS_KEY] == (
            "- Confirm zoning path with Planning"
        )

    def test_normalize_report_replacements_preserves_citations_block(self) -> None:
        from due_diligence_reporter.google_doc_builder import CITATIONS_BLOCK_KEY
        from due_diligence_reporter.server import _normalize_report_replacements

        replacements, _, _, _, _ = _normalize_report_replacements(
            report_data={
                "exec": {
                    "citations_block": "SIR -- zoning table lists school use as permitted",
                },
            },
            site_name="Alpha Atlanta 345",
            report_date="04/02/2026",
            drive_folder_url="https://drive.google.com/drive/folders/folder123",
        )

        assert replacements[CITATIONS_BLOCK_KEY] == (
            "SIR -- zoning table lists school use as permitted"
        )

    def test_inject_report_defaults_sets_missing_p1_label(self) -> None:
        from due_diligence_reporter.report_schema import MISSING_P1_ASSIGNEE_LABEL
        from due_diligence_reporter.server import _inject_report_defaults

        enriched, rebl_resolution = _inject_report_defaults(
            {"meta": {"prepared_by": "DD Report Agent"}}
        )

        assert enriched["p1_assignee_name"] == MISSING_P1_ASSIGNEE_LABEL
        assert rebl_resolution.resolution_status == "missing_address"

    def test_normalize_report_replacements_uses_missing_p1_label(self) -> None:
        from due_diligence_reporter.report_schema import MISSING_P1_ASSIGNEE_LABEL
        from due_diligence_reporter.server import _normalize_report_replacements

        replacements, _, unfilled, _, _ = _normalize_report_replacements(
            report_data={"meta": {"prepared_by": "DD Report Agent"}},
            site_name="Alpha Los Angeles 5400 Beethoven St",
            report_date="05/26/2026",
            drive_folder_url="https://drive.google.com/drive/folders/folder123",
        )

        assert replacements["meta.prepared_by"] == MISSING_P1_ASSIGNEE_LABEL
        assert "meta.prepared_by" not in unfilled

    @patch("due_diligence_reporter.server.resolve_address")
    def test_inject_report_defaults_adds_rebl_fields(
        self,
        mock_resolve_address: MagicMock,
    ) -> None:
        from due_diligence_reporter.rebl import ReblResolution
        from due_diligence_reporter.server import _inject_report_defaults

        mock_resolve_address.return_value = ReblResolution(
            address_submitted="123 Main St, Austin, TX 78701",
            resolution_status="resolved",
            site_id="123-main-st-austin-tx",
            url="https://rebl3.vercel.app/site/123-main-st-austin-tx",
        )

        enriched, rebl_resolution = _inject_report_defaults(
            {"site": {"address": "123 Main St, Austin, TX 78701"}}
        )

        assert enriched["meta"]["rebl_site_id"] == "123-main-st-austin-tx"
        assert enriched["sources"]["rebl_link"] == "https://rebl3.vercel.app/site/123-main-st-austin-tx"
        assert rebl_resolution.site_id == "123-main-st-austin-tx"

    @patch("due_diligence_reporter.server.resolve_address")
    def test_normalize_report_replacements_resolves_rebl_from_site_address_param(
        self,
        mock_resolve_address: MagicMock,
    ) -> None:
        from due_diligence_reporter.rebl import ReblResolution
        from due_diligence_reporter.server import _normalize_report_replacements

        mock_resolve_address.return_value = ReblResolution(
            address_submitted="5400 Beethoven St, Los Angeles, CA 90066",
            resolution_status="resolved",
            site_id="5400-beethoven-st-los-angeles-ca",
            url="https://rebl3.vercel.app/site/5400-beethoven-st-los-angeles-ca",
        )

        replacements, _, unfilled, _, rebl_resolution = _normalize_report_replacements(
            report_data={"meta": {"prepared_by": "Devin Bates"}},
            site_name="Alpha Los Angeles 5400 Beethoven St",
            report_date="05/26/2026",
            drive_folder_url="https://drive.google.com/drive/folders/folder123",
            site_address="5400 Beethoven St, Los Angeles, CA 90066",
        )

        assert replacements["meta.rebl_site_id"] == "5400-beethoven-st-los-angeles-ca"
        assert replacements["sources.rebl_link"].endswith("/5400-beethoven-st-los-angeles-ca")
        assert "meta.rebl_site_id" not in unfilled
        assert rebl_resolution.site_id == "5400-beethoven-st-los-angeles-ca"

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

        import requests as _requests

        from due_diligence_reporter.server import generate_marketing_pack

        mock_get.side_effect = _requests.Timeout("timed out")

        result = asyncio.run(generate_marketing_pack(
            space_sid="abc123", space_name="Test Site",
        ))

        assert result["status"] == "error"
        assert "timeout" in result["error"].lower()

    @patch("due_diligence_reporter.server.requests.get")
    def test_http_error_returns_error(self, mock_get: MagicMock) -> None:
        import asyncio

        import requests as _requests

        from due_diligence_reporter.server import generate_marketing_pack

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


class TestGlobalEmailCC:
    """Tests for global_cc dedup logic in send_email()."""

    def _call_send_email(self, recipients: list[str], global_cc: str) -> list[str]:
        """Run send_email() with SMTP mocked; return the final recipients list."""
        from due_diligence_reporter.utils import send_email

        captured: list[str] = []

        def fake_sendmail(sender: str, rcpts: list[str], msg: str) -> None:
            captured.extend(rcpts)

        with patch("due_diligence_reporter.utils.smtplib.SMTP_SSL") as mock_ssl:
            mock_server = MagicMock()
            mock_ssl.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_ssl.return_value.__exit__ = MagicMock(return_value=False)
            mock_server.sendmail.side_effect = fake_sendmail
            send_email(
                sender="bot@example.com",
                app_password="pass",
                recipients=recipients,
                subject="Test",
                html_body="<p>hi</p>",
                global_cc=global_cc,
            )
        return captured

    def test_global_cc_added_when_not_in_recipients(self) -> None:
        rcpts = self._call_send_email(["a@x.com"], "thomas.barrow@trilogy.com")
        assert "thomas.barrow@trilogy.com" in rcpts

    def test_global_cc_deduped_case_insensitive(self) -> None:
        rcpts = self._call_send_email(["Thomas.Barrow@Trilogy.com"], "thomas.barrow@trilogy.com")
        assert rcpts.count("thomas.barrow@trilogy.com") == 0  # not added again
        assert len([r for r in rcpts if r.lower() == "thomas.barrow@trilogy.com"]) == 1

    def test_global_cc_empty_string_no_change(self) -> None:
        rcpts = self._call_send_email(["a@x.com"], "")
        assert rcpts == ["a@x.com"]

    def test_global_cc_multiple_addresses(self) -> None:
        rcpts = self._call_send_email(["a@x.com"], "b@x.com, c@x.com")
        assert "b@x.com" in rcpts
        assert "c@x.com" in rcpts


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


class TestGoogleClientDocumentCreation:
    def test_create_document_removes_actual_parent_when_moving_to_target_folder(self) -> None:
        docs_documents = MagicMock()
        docs_documents.create.return_value = "create-request"
        docs_service = MagicMock()
        docs_service.documents.return_value = docs_documents

        files_resource = MagicMock()
        files_resource.get.side_effect = ["parents-request", "final-request"]
        files_resource.update.return_value = "update-request"
        drive_service = MagicMock()
        drive_service.files.return_value = files_resource

        client = GoogleClient.__new__(GoogleClient)
        client.docs_service = docs_service
        client.drive_service = drive_service

        final_metadata = {
            "id": "doc123",
            "name": "Alpha DD Report",
            "webViewLink": "https://docs.google.com/document/d/doc123",
        }
        with patch("due_diligence_reporter.google_client._google_api_execute") as execute:
            execute.side_effect = [
                {"documentId": "doc123"},
                {"parents": ["my-drive-root-id"]},
                {"id": "doc123", "parents": ["m1-folder"]},
                final_metadata,
            ]

            result = client.create_document(
                name="Alpha DD Report",
                folder_id="m1-folder",
                text_content="",
            )

        assert result == final_metadata
        update_kwargs = files_resource.update.call_args.kwargs
        assert update_kwargs["fileId"] == "doc123"
        assert update_kwargs["addParents"] == "m1-folder"
        assert update_kwargs["removeParents"] == "my-drive-root-id"
        assert update_kwargs["supportsAllDrives"] is True


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
        gc.upload_file_to_folder.assert_not_called()
        mock_to_thread.assert_awaited_once()

    def test_create_dd_report_uses_to_thread(self) -> None:
        from due_diligence_reporter.server import create_dd_report

        gc = MagicMock()
        gc.list_subfolders.return_value = [{
            "id": "m1-folder",
            "name": "M1 - Acquire Property",
            "webViewLink": "https://drive.google.com/drive/folders/m1-folder",
        }]
        gc.list_files_in_folder.return_value = []
        gc.create_document.return_value = {
            "id": "doc123",
            "webViewLink": "https://docs.google.com/document/d/doc123",
        }
        gc.get_document.return_value = {"body": {}, "revisionId": "rev-doc123"}
        gc.upload_file_to_folder.return_value = {"webViewLink": ""}
        gc.update_file_app_properties.return_value = {
            "id": "doc123",
            "modifiedTime": "2026-04-02T10:01:00+00:00",
            "appProperties": {},
        }

        async def run_inline(func: Any, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        with patch(
            "due_diligence_reporter.server.asyncio.to_thread",
            new=AsyncMock(side_effect=run_inline),
        ) as mock_to_thread, patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=gc,
        ), patch(
            "due_diligence_reporter.server._normalize_report_replacements",
            return_value=({}, [], [], {}, MagicMock()),
        ), patch(
            "due_diligence_reporter.server.build_dd_report_doc",
            return_value={"applied": 0, "found_tokens": [], "not_found_tokens": []},
        ):
            result = asyncio.run(create_dd_report(
                site_name="Alpha",
                drive_folder_url="https://drive.google.com/drive/folders/folder123",
                report_data={},
            ))

        assert result["status"] == "success"
        gc.create_document.assert_called_once()
        assert gc.create_document.call_args.kwargs["folder_id"] == "m1-folder"
        gc.upload_file_to_folder.assert_not_called()
        mock_to_thread.assert_awaited_once()
        assert result["document"]["role"] == "active"
        gc.update_file_app_properties.assert_called_once()

    def test_create_dd_report_rejects_drive_root_folder_url(self) -> None:
        from due_diligence_reporter.server import create_dd_report

        result = asyncio.run(create_dd_report(
            site_name="Alpha Miami Beach 300 71st St",
            drive_folder_url="https://drive.google.com/drive/folders/root",
            report_data={},
        ))

        assert result["status"] == "error"
        assert result["error"] == "Invalid folder URL"
        assert "Google Drive root" in result["message"]

    def test_create_dd_report_rebuilds_existing_same_day_doc(self) -> None:
        from due_diligence_reporter.server import create_dd_report

        gc = MagicMock()
        gc.list_subfolders.return_value = [{
            "id": "m1-folder",
            "name": "M1 - Acquire Property",
            "webViewLink": "https://drive.google.com/drive/folders/m1-folder",
        }]
        gc.list_files_in_folder.return_value = [{
            "id": "doc-existing",
            "name": "Alpha DD Report - 04/02/2026",
            "webViewLink": "https://docs.google.com/document/d/doc-existing",
            "modifiedTime": "2026-04-02T10:00:00+00:00",
        }]
        gc.get_file_metadata.return_value = {
            "id": "doc-existing",
            "name": "Alpha DD Report - 04/02/2026",
            "webViewLink": "https://docs.google.com/document/d/doc-existing",
            "modifiedTime": "2026-04-02T10:00:00+00:00",
            "appProperties": {
                "ddrAutomationRevisionId": "rev-before",
            },
        }
        gc.get_document.side_effect = [
            {"body": {}, "revisionId": "rev-before"},
            {"body": {}, "revisionId": "rev-after"},
        ]
        gc.update_file_app_properties.return_value = {
            "id": "doc-existing",
            "modifiedTime": "2026-04-02T10:01:00+00:00",
            "appProperties": {},
        }
        gc.docs_service = MagicMock()
        gc.drive_service = MagicMock()
        gc.upload_file_to_folder.return_value = {"webViewLink": ""}

        async def run_inline(func: Any, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        with patch(
            "due_diligence_reporter.server.asyncio.to_thread",
            new=AsyncMock(side_effect=run_inline),
        ), patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=gc,
        ), patch(
            "due_diligence_reporter.server._clear_document_body",
        ) as mock_clear, patch(
            "due_diligence_reporter.server.datetime",
        ) as mock_datetime, patch(
            "due_diligence_reporter.server._normalize_report_replacements",
            return_value=({}, [], [], {}, MagicMock()),
        ), patch(
            "due_diligence_reporter.server.build_dd_report_doc",
            return_value={"applied": 0, "found_tokens": [], "not_found_tokens": []},
        ):
            mock_datetime.now.return_value = datetime(2026, 4, 2, 10, 1, tzinfo=UTC)
            result = asyncio.run(create_dd_report(
                site_name="Alpha",
                drive_folder_url="https://drive.google.com/drive/folders/folder123",
                report_data={},
            ))

        assert result["status"] == "success"
        assert result["document"]["id"] == "doc-existing"
        assert result["document"]["role"] == "active"
        gc.create_document.assert_not_called()
        gc.upload_file_to_folder.assert_not_called()
        mock_clear.assert_called_once_with(gc, doc_id="doc-existing")
        gc.update_file_app_properties.assert_called_once()

    def test_create_dd_report_creates_candidate_when_existing_doc_has_no_watermark(self) -> None:
        from due_diligence_reporter.server import create_dd_report

        gc = MagicMock()
        gc.list_subfolders.return_value = [{
            "id": "m1-folder",
            "name": "M1 - Acquire Property",
            "webViewLink": "https://drive.google.com/drive/folders/m1-folder",
        }]
        gc.list_files_in_folder.return_value = [{
            "id": "doc-existing",
            "name": "Alpha DD Report - 04/02/2026",
            "webViewLink": "https://docs.google.com/document/d/doc-existing",
            "modifiedTime": "2026-04-03T10:00:00+00:00",
        }]
        gc.get_file_metadata.return_value = {
            "id": "doc-existing",
            "name": "Alpha DD Report - 04/02/2026",
            "webViewLink": "https://docs.google.com/document/d/doc-existing",
            "modifiedTime": "2026-04-03T10:00:00+00:00",
            "appProperties": {},
        }
        gc.create_document.return_value = {
            "id": "doc-candidate",
            "webViewLink": "https://docs.google.com/document/d/doc-candidate",
        }
        gc.get_document.return_value = {"body": {}, "revisionId": "rev-candidate"}
        gc.update_file_app_properties.return_value = {
            "id": "doc-candidate",
            "modifiedTime": "2026-04-03T10:01:00+00:00",
            "appProperties": {},
        }
        gc.docs_service = MagicMock()
        gc.drive_service = MagicMock()

        async def run_inline(func: Any, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        with patch(
            "due_diligence_reporter.server.asyncio.to_thread",
            new=AsyncMock(side_effect=run_inline),
        ), patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=gc,
        ), patch(
            "due_diligence_reporter.server._clear_document_body",
        ) as mock_clear, patch(
            "due_diligence_reporter.server._normalize_report_replacements",
            return_value=({}, [], [], {}, MagicMock()),
        ), patch(
            "due_diligence_reporter.server.build_dd_report_doc",
            return_value={"applied": 0, "found_tokens": [], "not_found_tokens": []},
        ):
            result = asyncio.run(create_dd_report(
                site_name="Alpha",
                drive_folder_url="https://drive.google.com/drive/folders/folder123",
                report_data={},
            ))

        assert result["status"] == "success"
        assert result["document"]["id"] == "doc-candidate"
        assert result["document"]["role"] == "candidate"
        assert result["document"]["source_doc_id"] == "doc-existing"
        assert result["republish_guard"]["status"] == "blocked"
        assert result["republish_guard"]["reason"] == "missing_automation_revision"
        mock_clear.assert_not_called()
        gc.create_document.assert_called_once()
        assert "Candidate" in gc.create_document.call_args.kwargs["name"]
        gc.update_file_app_properties.assert_called_once()

    def test_create_dd_report_creates_candidate_when_active_revision_changed(self) -> None:
        from due_diligence_reporter.server import create_dd_report

        gc = MagicMock()
        gc.list_subfolders.return_value = [{
            "id": "m1-folder",
            "name": "M1 - Acquire Property",
            "webViewLink": "https://drive.google.com/drive/folders/m1-folder",
        }]
        gc.list_files_in_folder.return_value = [{
            "id": "doc-existing",
            "name": "Alpha DD Report - 04/02/2026",
            "webViewLink": "https://docs.google.com/document/d/doc-existing",
            "modifiedTime": "2026-04-03T10:00:00+00:00",
        }]
        gc.get_file_metadata.return_value = {
            "id": "doc-existing",
            "name": "Alpha DD Report - 04/02/2026",
            "webViewLink": "https://docs.google.com/document/d/doc-existing",
            "modifiedTime": "2026-04-03T10:00:00+00:00",
            "appProperties": {
                "ddrAutomationRevisionId": "rev-before",
            },
        }
        gc.get_document.side_effect = [
            {"body": {}, "revisionId": "rev-human-edit"},
            {"body": {}, "revisionId": "rev-candidate"},
        ]
        gc.create_document.return_value = {
            "id": "doc-candidate",
            "webViewLink": "https://docs.google.com/document/d/doc-candidate",
        }
        gc.update_file_app_properties.return_value = {
            "id": "doc-candidate",
            "modifiedTime": "2026-04-03T10:01:00+00:00",
            "appProperties": {},
        }
        gc.docs_service = MagicMock()
        gc.drive_service = MagicMock()

        async def run_inline(func: Any, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        with patch(
            "due_diligence_reporter.server.asyncio.to_thread",
            new=AsyncMock(side_effect=run_inline),
        ), patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=gc,
        ), patch(
            "due_diligence_reporter.server._clear_document_body",
        ) as mock_clear, patch(
            "due_diligence_reporter.server._normalize_report_replacements",
            return_value=({}, [], [], {}, MagicMock()),
        ), patch(
            "due_diligence_reporter.server.build_dd_report_doc",
            return_value={"applied": 0, "found_tokens": [], "not_found_tokens": []},
        ):
            result = asyncio.run(create_dd_report(
                site_name="Alpha",
                drive_folder_url="https://drive.google.com/drive/folders/folder123",
                report_data={},
            ))

        assert result["status"] == "success"
        assert result["document"]["id"] == "doc-candidate"
        assert result["document"]["role"] == "candidate"
        assert result["republish_guard"]["status"] == "blocked"
        assert result["republish_guard"]["reason"] == "content_revision_changed"
        mock_clear.assert_not_called()
        gc.create_document.assert_called_once()

    def test_create_dd_report_moves_legacy_root_report_to_m1(self) -> None:
        from due_diligence_reporter.server import create_dd_report

        gc = MagicMock()
        gc.list_subfolders.return_value = [{
            "id": "m1-folder",
            "name": "M1 - Acquire Property",
            "webViewLink": "https://drive.google.com/drive/folders/m1-folder",
        }]
        gc.list_files_in_folder.side_effect = [
            [],
            [{
                "id": "doc-existing",
                "name": "Alpha DD Report - 04/02/2026",
                "webViewLink": "https://docs.google.com/document/d/doc-existing",
                "modifiedTime": "2026-04-02T10:00:00+00:00",
            }],
        ]
        gc.get_file_metadata.return_value = {
            "id": "doc-existing",
            "name": "Alpha DD Report - 04/02/2026",
            "webViewLink": "https://docs.google.com/document/d/doc-existing",
            "modifiedTime": "2026-04-02T10:00:00+00:00",
            "appProperties": {
                "ddrAutomationRevisionId": "rev-before",
            },
        }
        gc.get_document.side_effect = [
            {"body": {}, "revisionId": "rev-before"},
            {"body": {}, "revisionId": "rev-after"},
        ]
        gc.update_file_app_properties.return_value = {
            "id": "doc-existing",
            "modifiedTime": "2026-04-02T10:01:00+00:00",
            "appProperties": {},
        }
        gc.docs_service = MagicMock()
        gc.drive_service = MagicMock()

        async def run_inline(func: Any, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        with patch(
            "due_diligence_reporter.server.asyncio.to_thread",
            new=AsyncMock(side_effect=run_inline),
        ), patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=gc,
        ), patch(
            "due_diligence_reporter.server._clear_document_body",
        ) as mock_clear, patch(
            "due_diligence_reporter.server.datetime",
        ) as mock_datetime, patch(
            "due_diligence_reporter.server._normalize_report_replacements",
            return_value=({}, [], [], {}, MagicMock()),
        ), patch(
            "due_diligence_reporter.server.build_dd_report_doc",
            return_value={"applied": 0, "found_tokens": [], "not_found_tokens": []},
        ):
            mock_datetime.now.return_value = datetime(2026, 4, 2, 10, 1, tzinfo=UTC)
            result = asyncio.run(create_dd_report(
                site_name="Alpha",
                drive_folder_url="https://drive.google.com/drive/folders/site-root",
                report_data={},
            ))

        assert result["status"] == "success"
        assert result["document"]["folder_id"] == "m1-folder"
        assert result["document"]["role"] == "active"
        gc.move_file_to_folder.assert_called_once_with("doc-existing", "m1-folder")
        gc.create_document.assert_not_called()
        mock_clear.assert_called_once_with(gc, doc_id="doc-existing")
        gc.update_file_app_properties.assert_called_once()


