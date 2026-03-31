"""Tests for the report pipeline module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from due_diligence_reporter.report_pipeline import (
    PipelineResult,
    check_site_readiness_direct,
    match_site_in_shared_cache,
    process_site_pipeline,
)


# ---------------------------------------------------------------------------
# match_site_in_shared_cache
# ---------------------------------------------------------------------------


class TestMatchSiteInSharedCache:
    """Test matching logic against pre-fetched shared folder file lists."""

    def _make_cache(self) -> dict:
        return {
            "sir": [
                {"name": "Mar 01 2026 - Alpha Keller SIR.pdf", "id": "sir1"},
                {"name": "Feb 20 2026 - Alpha Boca Raton SIR.pdf", "id": "sir2"},
            ],
            "isp": [
                {"name": "Alpha Keller ISP.pdf", "id": "isp1"},
            ],
            "building_inspection": [
                {"name": "Feb 26 2026 - Alpha Keller Building Inspection Report.pdf", "id": "bi1"},
            ],
        }

    def test_matches_by_full_title(self):
        cache = self._make_cache()
        result = match_site_in_shared_cache(["Alpha Keller"], cache)
        assert result["sir"] is not None
        assert result["sir"]["id"] == "sir1"
        assert result["isp"] is not None
        assert result["building_inspection"] is not None

    def test_matches_by_city_name(self):
        cache = self._make_cache()
        result = match_site_in_shared_cache(["Keller"], cache)
        assert result["sir"] is not None
        assert result["isp"] is not None

    def test_no_match_returns_none(self):
        cache = self._make_cache()
        result = match_site_in_shared_cache(["Alpha Southlake"], cache)
        assert result["sir"] is None
        assert result["isp"] is None
        assert result["building_inspection"] is None

    def test_case_insensitive(self):
        cache = self._make_cache()
        result = match_site_in_shared_cache(["alpha keller"], cache)
        assert result["sir"] is not None

    def test_empty_match_terms(self):
        cache = self._make_cache()
        result = match_site_in_shared_cache([], cache)
        assert result["sir"] is None
        assert result["isp"] is None
        assert result["building_inspection"] is None

    def test_partial_match_boca_raton(self):
        cache = self._make_cache()
        result = match_site_in_shared_cache(["Boca Raton"], cache)
        assert result["sir"] is not None
        assert result["sir"]["id"] == "sir2"
        # No ISP or BI for Boca Raton in the cache
        assert result["isp"] is None
        assert result["building_inspection"] is None


# ---------------------------------------------------------------------------
# process_site_pipeline
# ---------------------------------------------------------------------------


def _make_settings():
    settings = MagicMock()
    settings.email_sender = ""
    settings.email_app_password = ""
    settings.dd_report_email_recipients = ""
    settings.google_chat_webhook_url = ""
    return settings


class TestProcessSitePipeline:
    """Test the full single-site pipeline."""

    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_missing_docs(self, mock_readiness):
        """Returns waiting_on_docs with correct missing list."""
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": False,
            "inspection_found": False,
            "report_exists": False,
        }

        gc = MagicMock()
        result = process_site_pipeline(
            gc, "Alpha Keller", "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller", "Keller"], {}, "system prompt", _make_settings(),
        )

        assert result.status == "waiting_on_docs"
        assert "ISP" in result.missing_docs
        assert "Building Inspection" in result.missing_docs
        assert "SIR" not in result.missing_docs

    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_report_exists(self, mock_readiness):
        """Returns report_exists when report already present."""
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": True,
            "inspection_found": True,
            "report_exists": True,
        }

        gc = MagicMock()
        result = process_site_pipeline(
            gc, "Alpha Keller", "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"], {}, "system prompt", _make_settings(),
        )

        assert result.status == "report_exists"

    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_all_present_generates_report(self, mock_readiness, mock_agent, mock_completeness):
        """Triggers agent and returns report_created when all docs present."""
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": True,
            "inspection_found": True,
            "report_exists": False,
        }
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc123",
            "doc_url": "https://docs.google.com/document/d/doc123",
        }

        # Mock the async completeness check — asyncio.run() will call the coroutine
        async def fake_completeness(doc_id):
            return {"ready_to_send": True, "pending_section_count": 0}

        mock_completeness.side_effect = fake_completeness

        gc = MagicMock()
        result = process_site_pipeline(
            gc, "Alpha Keller", "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"], {}, "system prompt", _make_settings(),
        )

        assert result.status == "report_created"
        assert result.doc_id == "doc123"
        assert result.doc_url == "https://docs.google.com/document/d/doc123"
        mock_agent.assert_called_once()

    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_agent_failure(self, mock_readiness, mock_agent):
        """Returns generation_failed when agent fails."""
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": True,
            "inspection_found": True,
            "report_exists": False,
        }
        mock_agent.return_value = {
            "success": False,
            "error": "ANTHROPIC_API_KEY not set",
        }

        gc = MagicMock()
        result = process_site_pipeline(
            gc, "Alpha Keller", "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"], {}, "system prompt", _make_settings(),
        )

        assert result.status == "generation_failed"
        assert result.error == "ANTHROPIC_API_KEY not set"

    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_readiness_error(self, mock_readiness):
        """Returns error when readiness check throws."""
        mock_readiness.side_effect = RuntimeError("Drive API error")

        gc = MagicMock()
        result = process_site_pipeline(
            gc, "Alpha Keller", "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"], {}, "system prompt", _make_settings(),
        )

        assert result.status == "error"
        assert "Drive API error" in result.error

    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_readiness_payload_error(self, mock_readiness):
        """Treats readiness payload errors as pipeline errors."""
        mock_readiness.return_value = {
            "sir_found": False,
            "isp_found": False,
            "inspection_found": False,
            "report_exists": False,
            "error": "bad_url",
        }

        gc = MagicMock()
        result = process_site_pipeline(
            gc, "Alpha Keller", "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"], {}, "system prompt", _make_settings(),
        )

        assert result.status == "error"
        assert result.error == "bad_url"

    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_agent_exception_becomes_generation_failed(self, mock_readiness, mock_agent):
        """Raised agent exceptions degrade to generation_failed."""
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": True,
            "inspection_found": True,
            "report_exists": False,
        }
        mock_agent.side_effect = RuntimeError("Anthropic timeout")

        gc = MagicMock()
        result = process_site_pipeline(
            gc, "Alpha Keller", "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"], {}, "system prompt", _make_settings(),
        )

        assert result.status == "generation_failed"
        assert result.error == "Anthropic timeout"

    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_completeness_payload_error_returns_error(
        self,
        mock_readiness,
        mock_agent,
        mock_completeness,
    ):
        """Treats completeness payload errors as pipeline errors."""
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": True,
            "inspection_found": True,
            "report_exists": False,
        }
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc123",
            "doc_url": "https://docs.google.com/document/d/doc123",
        }

        async def fake_completeness(doc_id):
            return {
                "status": "error",
                "error": "check_report_completeness failed",
                "message": "export broke",
            }

        mock_completeness.side_effect = fake_completeness

        gc = MagicMock()
        result = process_site_pipeline(
            gc, "Alpha Keller", "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"], {}, "system prompt", _make_settings(),
        )

        assert result.status == "error"
        assert result.doc_id == "doc123"
        assert "export broke" in (result.error or "")


# ---------------------------------------------------------------------------
# PipelineResult dataclass
# ---------------------------------------------------------------------------


class TestPipelineResult:
    def test_defaults(self):
        r = PipelineResult(site_title="Alpha Keller", status="waiting_on_docs")
        assert r.missing_docs == []
        assert r.doc_id is None
        assert r.doc_url is None
        assert r.unresolved_tokens == []
        assert r.pending_count == 0
        assert r.error is None

    def test_with_all_fields(self):
        r = PipelineResult(
            site_title="Alpha Keller",
            status="report_created",
            doc_id="abc",
            doc_url="https://docs.google.com/document/d/abc",
            pending_count=2,
        )
        assert r.doc_id == "abc"
        assert r.pending_count == 2
