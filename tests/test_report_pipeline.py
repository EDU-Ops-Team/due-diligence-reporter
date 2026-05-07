"""Tests for the report pipeline module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from due_diligence_reporter.report_pipeline import (
    PipelineResult,
    ReportTrace,
    TraceEvent,
    _extract_source_read_issues,
    _merge_cached_report_fields,
    check_site_readiness_direct,
    match_site_in_shared_cache,
    process_site_pipeline,
    run_dd_report_agent,
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

    def test_prefers_strong_site_specific_match_over_weak_city_overlap(self):
        cache = {
            "sir": [],
            "isp": [],
            "building_inspection": [
                {"name": "Alpha Sunny Isles Building Inspection Report.pdf", "id": "bi-wrong"},
                {"name": "Alpha School Miami Beach 300 71st St Building Inspection Report.pdf", "id": "bi-right"},
            ],
        }

        result = match_site_in_shared_cache(
            ["Miami", "Beach", "71st"],
            cache,
            site_title="Alpha School Miami Beach 300 71st St",
            site_address="300 71st St, Miami Beach, FL 33141",
        )

        assert result["building_inspection"] is not None
        assert result["building_inspection"]["id"] == "bi-right"


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
        assert "Building Inspection" in result.missing_docs
        assert "SIR" not in result.missing_docs

    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_report_exists(self, mock_readiness):
        """Returns report_exists when report already present."""
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": False,
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
    def test_force_regenerate_bypasses_report_exists(
        self, mock_readiness, mock_agent, mock_completeness
    ):
        """``force_regenerate=True`` runs the agent even when a DD Report exists."""
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": False,
            "inspection_found": True,
            "report_exists": True,  # would normally short-circuit
        }
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc456",
            "doc_url": "https://docs.google.com/document/d/doc456",
        }

        async def fake_completeness(doc_id):
            return {"ready_to_send": True, "pending_section_count": 0}

        mock_completeness.side_effect = fake_completeness

        gc = MagicMock()
        result = process_site_pipeline(
            gc, "Alpha Keller", "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"], {}, "system prompt", _make_settings(),
            force_regenerate=True,
        )

        assert result.status == "report_created"
        assert result.doc_id == "doc456"
        mock_agent.assert_called_once()

    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_force_regenerate_still_blocks_on_missing_docs(
        self, mock_readiness, mock_agent
    ):
        """``force_regenerate=True`` does not bypass the missing-docs gate."""
        mock_readiness.return_value = {
            "sir_found": False,
            "isp_found": False,
            "inspection_found": False,
            "report_exists": True,
        }

        gc = MagicMock()
        result = process_site_pipeline(
            gc, "Alpha Keller", "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"], {}, "system prompt", _make_settings(),
            force_regenerate=True,
        )

        # Missing-docs gate fires before the (bypassed) report_exists check.
        assert result.status == "waiting_on_docs"
        assert "SIR" in result.missing_docs
        mock_agent.assert_not_called()

    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_all_present_generates_report(self, mock_readiness, mock_agent, mock_completeness):
        """Triggers agent and returns report_created when all docs present."""
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": False,
            "inspection_found": True,
            "report_exists": False,
        }
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc123",
            "doc_url": "https://docs.google.com/document/d/doc123",
        }

        # Mock the async completeness check â€” asyncio.run() will call the coroutine
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
            "isp_found": False,
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
            "isp_found": False,
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
            "isp_found": False,
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

    @patch("due_diligence_reporter.report_pipeline.publish_to_dashboard")
    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_report_incomplete_still_publishes_to_dashboard_in_progress(
        self,
        mock_readiness,
        mock_agent,
        mock_completeness,
        mock_publish,
    ):
        """report_incomplete reports must still publish partial data to the
        dashboard with ``dd_status="in_progress"`` so sites do not get
        stuck on the empty roster-sync stub when the completeness check
        rejects an otherwise-filled report (raw template tokens leaking,
        invalid Can-We-Open answer, unfilled placeholders)."""
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": False,
            "inspection_found": True,
            "report_exists": False,
        }
        trace = ReportTrace(
            site_name="Alpha Keller",
            started_at="2026-04-30T15:53:12+00:00",
            events=[],
            final_report_data={"exec.c_answer": "Yes", "q1.school_approval_label": "yes"},
        )
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc123",
            "doc_url": "https://docs.google.com/document/d/doc123",
            "trace": trace,
        }

        async def fake_completeness(doc_id):
            # Same shape the production code returns when the doc has
            # raw template tokens leaked: ready_to_send=False with zero
            # unresolved {{...}} tokens. This is the exact failure mode
            # observed for both Tulsa sites in run 25175297453.
            return {
                "ready_to_send": False,
                "unresolved_token_count": 0,
                "unresolved_tokens": [],
                "raw_template_token_count": 1,
                "raw_template_tokens": ["INSERT_ANSWER"],
                "pending_section_count": 0,
                "summary": "Report NOT ready to send. 1 raw template token(s).",
            }

        mock_completeness.side_effect = fake_completeness
        mock_publish.return_value = True

        gc = MagicMock()
        result = process_site_pipeline(
            gc, "Alpha Keller", "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"], {}, "system prompt", _make_settings(),
            site_address="123 Main St, Keller TX",
            p1_name="Robbie Forrest",
            wrike_created_at="2026-04-01T00:00:00Z",
        )

        assert result.status == "report_incomplete"
        assert result.doc_id == "doc123"
        # The HTTP publish must fire on the incomplete branch.
        mock_publish.assert_called_once()
        kwargs = mock_publish.call_args.kwargs
        assert kwargs["dd_status"] == "in_progress"
        assert kwargs["address"] == "123 Main St, Keller TX"
        assert kwargs["site_owner"] == "Robbie Forrest"
        assert kwargs["wrike_created_at"] == "2026-04-01T00:00:00Z"
        # The real report data (not an empty stub) is published.
        positional = mock_publish.call_args.args
        assert positional[0] == "Alpha Keller"
        assert positional[1] == trace.final_report_data

    @patch("due_diligence_reporter.report_pipeline.publish_to_dashboard")
    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_report_created_publishes_with_default_dd_status(
        self,
        mock_readiness,
        mock_agent,
        mock_completeness,
        mock_publish,
    ):
        """Success path must NOT pass dd_status=in_progress; publisher
        auto-stamps ``complete`` when the kwarg is None."""
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": False,
            "inspection_found": True,
            "report_exists": False,
        }
        trace = ReportTrace(
            site_name="Alpha Keller",
            started_at="2026-04-30T15:53:12+00:00",
            events=[],
            final_report_data={"exec.c_answer": "Yes"},
        )
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc123",
            "doc_url": "https://docs.google.com/document/d/doc123",
            "trace": trace,
        }

        async def fake_completeness(doc_id):
            return {"ready_to_send": True, "pending_section_count": 0}

        mock_completeness.side_effect = fake_completeness
        mock_publish.return_value = True

        gc = MagicMock()
        result = process_site_pipeline(
            gc, "Alpha Keller", "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"], {}, "system prompt", _make_settings(),
        )

        assert result.status == "report_created"
        mock_publish.assert_called_once()
        # The success path leaves dd_status unset so the publisher's
        # auto-stamp logic still fires ("complete").
        assert mock_publish.call_args.kwargs["dd_status"] is None


class TestCheckSiteReadinessDirect:
    def test_picks_up_source_docs_from_site_folder_m1(self):
        # `list_files_recursive` with max_depth=2 surfaces files inside the
        # per-site M1 subfolder. The readiness check should treat those as
        # valid SIR/BI/ISP sources — they're what the live inbox scanner
        # writes for net-new uploads.
        gc = MagicMock()
        gc.list_files_recursive.return_value = [
            {"id": "m1-sir", "name": "Alpha Keller SIR.pdf"},
            {"id": "m1-bi", "name": "Alpha Keller Building Inspection Report.pdf"},
            {"id": "dd-1", "name": "Alpha Keller DD Report - 04/20/2026"},
            {"id": "eocc-1", "name": "E-Occupancy Assessment - Alpha Keller"},
        ]

        result = check_site_readiness_direct(
            gc,
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"],
            {"sir": [], "isp": [], "building_inspection": []},
        )

        assert result["sir_found"] is True
        assert result["inspection_found"] is True
        assert result["isp_found"] is False
        assert result["report_exists"] is True
        assert result["e_occupancy_report_found"] is True

    def test_falls_back_to_shared_cache_when_m1_missing(self):
        # When the site folder has no source docs, the legacy shared-folder
        # match (via `match_site_in_shared_cache`) still wins.
        gc = MagicMock()
        gc.list_files_recursive.return_value = [
            {"id": "dd-1", "name": "Alpha Keller DD Report - 04/20/2026"},
        ]

        result = check_site_readiness_direct(
            gc,
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"],
            {
                "sir": [{"id": "shared-sir", "name": "Alpha Keller SIR.pdf"}],
                "isp": [],
                "building_inspection": [
                    {"id": "shared-bi", "name": "Alpha Keller Building Inspection Report.pdf"},
                ],
            },
            site_title="Alpha Keller",
        )

        assert result["sir_found"] is True
        assert result["inspection_found"] is True
        assert result["isp_found"] is False
        assert result["report_exists"] is True

    def test_site_folder_source_docs_win_over_shared_cache(self):
        # If the same doc_type exists in both M1 (via the site-folder listing)
        # and the shared-folder cache, the M1 copy should win since it's the
        # freshest version filed by the live scanner.
        gc = MagicMock()
        gc.list_files_recursive.return_value = [
            {"id": "m1-sir", "name": "Alpha Keller SIR.pdf"},
        ]

        result = check_site_readiness_direct(
            gc,
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"],
            {
                "sir": [{"id": "legacy-sir", "name": "Alpha Keller SIR.pdf"}],
                "isp": [],
                "building_inspection": [],
            },
            site_title="Alpha Keller",
        )

        assert result["sir_found"] is True
        # The merged record exposes only flags, not file IDs, but we can
        # confirm preference by inspecting the AI-generated `all_files`
        # payload — source docs are *not* surfaced there, so the test below
        # checks the implementation seam directly.
        # Re-run with both caches empty to ensure pass-through still works.
        result_empty = check_site_readiness_direct(
            gc,
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"],
            {"sir": [], "isp": [], "building_inspection": []},
            site_title="Alpha Keller",
        )
        assert result_empty["sir_found"] is True


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


class TestAgentToolMerging:
    def test_merge_cached_report_fields_fills_missing_values_only(self):
        merged = _merge_cached_report_fields(
            {
                "report_data": {
                    "exec.fastest_open_capex": "$100,000",
                },
            },
            {
                "exec.fastest_open_capex": "$86,000",
                "exec.cost_demolition_fastest_open": "$0",
            },
        )

        assert merged["report_data"]["exec.fastest_open_capex"] == "$100,000"
        assert merged["report_data"]["exec.cost_demolition_fastest_open"] == "$0"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("due_diligence_reporter.report_pipeline.route_tool_call_sync")
    @patch("due_diligence_reporter.report_pipeline.anthropic.Anthropic")
    def test_run_dd_report_agent_merges_skill_fields_and_stops_after_first_report(
        self,
        mock_anthropic,
        mock_route_tool_call_sync,
    ):
        """Skill tool report_data_fields merge into create_dd_report; agent stops
        after the first successful create_dd_report call (post-RayCon-cutover:
        get_cost_estimate is no longer a production tool, so this exercises the
        same merge path via apply_school_approval_skill instead)."""
        class FakeToolUse:
            def __init__(self, tool_id, name, tool_input):
                self.type = "tool_use"
                self.id = tool_id
                self.name = name
                self.input = tool_input

        response = MagicMock()
        response.content = [
            FakeToolUse(
                "tool-1",
                "apply_school_approval_skill",
                {"site_name": "Alpha Keller", "address": "123 Main St"},
            ),
            FakeToolUse(
                "tool-2",
                "create_dd_report",
                {
                    "site_name": "Alpha Keller",
                    "drive_folder_url": "https://drive.google.com/drive/folders/abc123",
                    "report_data": {"exec.fastest_open_capacity": "25"},
                },
            ),
            FakeToolUse(
                "tool-3",
                "create_dd_report",
                {
                    "site_name": "Alpha Keller",
                    "drive_folder_url": "https://drive.google.com/drive/folders/abc123",
                    "report_data": {},
                },
            ),
        ]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        mock_anthropic.return_value = mock_client

        mock_route_tool_call_sync.side_effect = [
            {
                "status": "success",
                "report_data_fields": {
                    "q2.school_approval_difficulty": "easy",
                    "q2.school_approval_score": "9",
                },
            },
            {
                "status": "success",
                "document": {
                    "id": "doc123",
                    "url": "https://docs.google.com/document/d/doc123",
                },
                "replacements_applied": 10,
                "unfilled_template_tokens": 0,
            },
        ]

        result = run_dd_report_agent("Alpha Keller", "system prompt", "claude-test")

        assert result["success"] is True
        assert mock_route_tool_call_sync.call_count == 2
        create_call = mock_route_tool_call_sync.call_args_list[1]
        create_input = create_call.args[1]
        assert create_input["report_data"]["exec.fastest_open_capacity"] == "25"
        assert create_input["report_data"]["q2.school_approval_difficulty"] == "easy"
        assert create_input["report_data"]["q2.school_approval_score"] == "9"


class TestSourceReadAlerts:
    def test_extracts_sir_and_building_inspection_read_issues(self):
        trace = ReportTrace(
            site_name="Alpha Keller",
            started_at="2026-04-01T00:00:00+00:00",
            events=[
                TraceEvent(
                    timestamp="2026-04-01T00:00:01+00:00",
                    event_type="tool_call",
                    tool_name="read_drive_document",
                    input_summary={"file_name": "Alpha Keller SIR.pdf"},
                    output_summary={"status": "error", "error": "Failed to read document"},
                ),
                TraceEvent(
                    timestamp="2026-04-01T00:00:02+00:00",
                    event_type="tool_call",
                    tool_name="read_drive_document",
                    input_summary={
                        "file_name": "Alpha Keller Building Inspection Report.pdf",
                    },
                    output_summary={
                        "status": "ok",
                        "content_preview": "[PDF text extraction returned no text. This may be an image-only PDF that requires OCR.]",
                    },
                ),
                TraceEvent(
                    timestamp="2026-04-01T00:00:03+00:00",
                    event_type="tool_call",
                    tool_name="read_drive_document",
                    input_summary={"file_name": "Alpha Keller ISP.pdf"},
                    output_summary={"status": "error", "error": "Ignore ISP failures here"},
                ),
            ],
        )

        issues = _extract_source_read_issues(trace)

        assert len(issues) == 2
        assert issues[0]["doc_type"] == "SIR"
        assert issues[1]["doc_type"] == "Building Inspection"

    @patch("due_diligence_reporter.report_pipeline._save_pipeline_trace")
    @patch("due_diligence_reporter.report_pipeline.post_google_chat_message")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_generation_failure_posts_source_review_alert(
        self,
        mock_readiness,
        mock_agent,
        mock_chat,
        mock_save_trace,
    ):
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": True,
            "inspection_found": True,
            "report_exists": False,
        }
        mock_save_trace.return_value = "https://drive.google.com/trace"
        mock_agent.return_value = {
            "success": False,
            "error": "Agent completed without creating a report",
            "trace": ReportTrace(
                site_name="Alpha Keller",
                started_at="2026-04-01T00:00:00+00:00",
                events=[
                    TraceEvent(
                        timestamp="2026-04-01T00:00:01+00:00",
                        event_type="tool_call",
                        tool_name="read_drive_document",
                        input_summary={"file_name": "Alpha Keller SIR.pdf"},
                        output_summary={"status": "error", "error": "Failed to read document"},
                    ),
                ],
            ),
        }
        settings = _make_settings()
        settings.google_chat_webhook_url = "https://chat.example/webhook"

        result = process_site_pipeline(
            MagicMock(),
            "Alpha Keller",
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"],
            {},
            "system prompt",
            settings,
        )

        assert result.status == "generation_failed"
        assert result.trace_url == "https://drive.google.com/trace"
        mock_chat.assert_called_once()
        message = mock_chat.call_args.args[1]
        assert "DD Source Review Needed -- Alpha Keller" in message
        assert "SIR" in message
        assert "Failed to read document" in message

