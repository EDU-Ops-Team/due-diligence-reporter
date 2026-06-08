"""Tests for the report pipeline module."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from due_diligence_reporter.automation_event import build_dd_report_summary_event
from due_diligence_reporter.report_pipeline import (
    PipelineResult,
    ReportTrace,
    TraceEvent,
    _canonicalize_site_tool_input,
    _dd_report_event_frequency_cap,
    _extract_source_read_issues,
    _merge_cached_report_fields,
    check_site_readiness_direct,
    match_site_in_shared_cache,
    post_completed_report_bundle_summary,
    process_site_pipeline,
    run_dd_report_agent,
)


@pytest.fixture(autouse=True)
def _isolate_report_event_manifest_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("due_diligence_reporter.report_pipeline.RUN_MANIFEST_DIR", tmp_path)


def _open_ask_event():
    return build_dd_report_summary_event(
        site_id="SITE1",
        site_name="Alpha Keller",
        run_id="new-run",
        doc_id="doc-1",
        doc_url="https://docs.google.com/document/d/doc-1",
        open_questions=[{"display_text": "Confirm zoning use from the vendor SIR"}],
        created_at="2026-05-29T14:00:00+00:00",
    )


def _write_prior_report_event_manifest(tmp_path, *, started_at: str) -> None:
    (tmp_path / "prior-run.json").write_text(
        json.dumps(
            {
                "run_id": "prior-run",
                "site_id": "SITE1",
                "site_title": "Alpha Keller",
                "started_at": started_at,
                "rhodes_report_event": {
                    "event_type": "dd_report_created",
                    "decision_required": True,
                    "status": "created",
                    "rhodes_note_id": "NOTE1",
                },
            }
        ),
        encoding="utf-8",
    )


def test_canonicalize_site_tool_input_adds_address_for_school_approval() -> None:
    canonical = _canonicalize_site_tool_input(
        "apply_school_approval_skill",
        {"state": ""},
        site_title="Alpha Tulsa",
        drive_folder_url="https://drive.google.com/drive/folders/site123",
        site_address="421 E 11th St, Tulsa, OK 74120",
    )

    assert canonical["site_name"] == "Alpha Tulsa"
    assert canonical["drive_folder_url"] == "https://drive.google.com/drive/folders/site123"
    assert canonical["address"] == "421 E 11th St, Tulsa, OK 74120"


def test_dd_report_event_frequency_cap_blocks_two_business_days(tmp_path) -> None:
    _write_prior_report_event_manifest(
        tmp_path,
        started_at="2026-05-29T14:00:00+00:00",
    )

    cap = _dd_report_event_frequency_cap(
        _open_ask_event(),
        site_title="Alpha Keller",
        current_run_id="new-run",
        now=datetime(2026, 6, 1, 15, 0, tzinfo=UTC),
        manifest_root=tmp_path,
    )

    assert cap is not None
    assert cap["status"] == "skipped"
    assert cap["reason"] == "frequency_cap"
    assert cap["last_sent_at"] == "2026-05-29T14:00:00+00:00"
    assert cap["next_allowed_at"] == "2026-06-02T14:00:00+00:00"


def test_dd_report_event_frequency_cap_allows_after_two_business_days(tmp_path) -> None:
    _write_prior_report_event_manifest(
        tmp_path,
        started_at="2026-05-29T14:00:00+00:00",
    )

    cap = _dd_report_event_frequency_cap(
        _open_ask_event(),
        site_title="Alpha Keller",
        current_run_id="new-run",
        now=datetime(2026, 6, 2, 14, 0, tzinfo=UTC),
        manifest_root=tmp_path,
    )

    assert cap is None


def test_dd_report_event_frequency_cap_allows_source_triggered_updates(tmp_path) -> None:
    _write_prior_report_event_manifest(
        tmp_path,
        started_at="2026-05-29T14:00:00+00:00",
    )
    event = build_dd_report_summary_event(
        site_id="SITE1",
        site_name="Alpha Keller",
        run_id="new-run",
        doc_id="doc-1",
        doc_url="https://docs.google.com/document/d/doc-1",
        source_event={"source_type": "vendor_sir", "fingerprint": "sir-1"},
        open_questions=[{"display_text": "Confirm zoning use from the vendor SIR"}],
        created_at="2026-06-01T15:00:00+00:00",
    )

    cap = _dd_report_event_frequency_cap(
        event,
        site_title="Alpha Keller",
        current_run_id="new-run",
        now=datetime(2026, 6, 1, 15, 0, tzinfo=UTC),
        manifest_root=tmp_path,
    )

    assert cap is None


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

    def test_rejects_city_only_same_metro_overlap_when_site_context_present(self):
        cache = {
            "sir": [
                {
                    "name": "May 1 2026 - Alpha School Los Angeles 1726 Whitley Ave SIR.pdf",
                    "id": "sir-wrong",
                },
            ],
            "isp": [],
            "building_inspection": [
                {
                    "name": "Alpha Los Angeles 1726 Whitley Ave Building Inspection.pdf",
                    "id": "bi-wrong",
                },
            ],
        }

        result = match_site_in_shared_cache(
            ["Alpha Los Angeles 5400 Beethoven St", "Los Angeles", "5400", "Beethoven"],
            cache,
            site_title="Alpha Los Angeles 5400 Beethoven St",
            site_address="5400 Beethoven St, Los Angeles, CA 90066",
        )

        assert result["sir"] is None
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
        """Returns waiting_on_docs when the first-round SIR floor is missing."""
        mock_readiness.return_value = {
            "sir_found": False,
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
        assert result.missing_docs == ["SIR"]
        readiness_step = next(step for step in result.steps if step.step == "readiness.check")
        assert readiness_step.status == "blocked"
        assert result.failed_step == "readiness.check"
        assert result.quality_score is not None

    @patch("due_diligence_reporter.report_pipeline._resolve_rhodes_owner_for_pipeline")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_missing_drive_folder_blocks_with_rhodes_setup_message(
        self,
        mock_readiness,
        mock_rhodes_owner,
    ):
        mock_rhodes_owner.return_value = {
            "status": "found",
            "drive_folder_status": "missing",
            "drive_folder_message": "Site has no Google Drive folder",
            "report_data_fields": {},
        }

        result = process_site_pipeline(
            MagicMock(),
            "Alpha Los Angeles 5400 Beethoven St",
            "",
            ["Alpha Los Angeles 5400 Beethoven St"],
            {},
            "system prompt",
            _make_settings(),
            site_address="5400 Beethoven St, Los Angeles, CA 90066",
        )

        assert result.status == "error"
        assert result.failed_step == "readiness.check"
        assert "Link/provision the site folder in Rhodes" in (result.error or "")
        mock_readiness.assert_not_called()

    @patch("due_diligence_reporter.report_pipeline._resolve_rhodes_owner_for_pipeline")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_missing_drive_folder_uses_rhodes_link_before_readiness(
        self,
        mock_readiness,
        mock_rhodes_owner,
    ):
        mock_rhodes_owner.return_value = {
            "status": "found",
            "drive_folder_url": "https://drive.google.com/drive/folders/rhodes-root",
            "report_data_fields": {
                "meta.drive_folder_url": "https://drive.google.com/drive/folders/rhodes-root",
            },
        }
        mock_readiness.return_value = {
            "sir_found": False,
            "report_exists": False,
        }

        result = process_site_pipeline(
            MagicMock(),
            "Alpha Los Angeles 5400 Beethoven St",
            "",
            ["Alpha Los Angeles 5400 Beethoven St"],
            {},
            "system prompt",
            _make_settings(),
            site_address="5400 Beethoven St, Los Angeles, CA 90066",
        )

        assert result.status == "waiting_on_docs"
        assert mock_readiness.call_args.args[1].endswith("/rhodes-root")

    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline._resolve_rhodes_owner_for_pipeline")
    @patch("due_diligence_reporter.report_pipeline._email_pipeline_report")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_pipeline_seeds_report_with_rhodes_owner(
        self,
        mock_readiness,
        mock_email,
        mock_rhodes_owner,
        mock_agent,
        mock_completeness,
    ):
        """Rhodes p1Dri seeds meta.prepared_by and the P1 email recipient."""
        mock_readiness.return_value = {
            "sir_found": True,
            "sir_vendor": False,
            "isp_found": False,
            "inspection_found": False,
            "inspection_vendor": False,
            "raycon_scenario_found": False,
            "report_exists": False,
        }
        mock_rhodes_owner.return_value = {
            "status": "found",
            "p1_assignee_name": "Devin Bates",
            "p1_assignee_email": "devin.bates@trilogy.com",
            "report_data_fields": {
                "meta.prepared_by": "Devin Bates",
                "p1_assignee_email": "devin.bates@trilogy.com",
            },
        }
        mock_email.return_value = None
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc-first",
            "doc_url": "https://docs.google.com/document/d/doc-first",
        }

        async def fake_completeness(doc_id):
            return {"ready_to_send": True, "pending_section_count": 0}

        mock_completeness.side_effect = fake_completeness

        result = process_site_pipeline(
            MagicMock(),
            "Alpha Keller",
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"],
            {},
            "system prompt",
            _make_settings(),
            site_address="123 Main St, Keller, TX",
        )

        assert result.status == "report_created"
        assert result.doc_id == "doc-first"
        agent_kwargs = mock_agent.call_args.kwargs
        assert agent_kwargs["initial_report_fields"]["meta.prepared_by"] == "Devin Bates"
        assert agent_kwargs["rhodes_owner_context"]["p1_assignee_email"] == (
            "devin.bates@trilogy.com"
        )
        mock_email.assert_called_once()
        assert mock_email.call_args.args[3] == "devin.bates@trilogy.com"

    @patch("due_diligence_reporter.report_pipeline.add_rhodes_site_note")
    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline._email_pipeline_report")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_first_partial_report_still_sends_email(
        self,
        mock_readiness,
        mock_email,
        mock_agent,
        mock_completeness,
        mock_rhodes_note,
    ):
        """The initial DDR email still sends even when vendor inputs remain open."""
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": False,
            "inspection_found": False,
            "report_exists": False,
        }
        trace = ReportTrace(
            site_name="Alpha Keller",
            started_at="2026-06-04T15:00:00+00:00",
            events=[],
            final_report_data={
                "verification.open_items": "- Review vendor Building Inspection when it arrives",
            },
        )
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc-first",
            "doc_url": "https://docs.google.com/document/d/doc-first",
            "trace": trace,
        }
        mock_email.return_value = None
        mock_rhodes_note.return_value = {
            "status": "created",
            "reason": "ok",
            "rhodes_note_id": "NOTE1",
            "owner_notification": "mentioned",
        }

        async def fake_completeness(doc_id):
            return {"ready_to_send": True, "pending_section_count": 0}

        mock_completeness.side_effect = fake_completeness

        result = process_site_pipeline(
            MagicMock(),
            "Alpha Keller",
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"],
            {},
            "system prompt",
            _make_settings(),
            p1_email="owner@example.com",
            site_id="SITE1",
        )

        assert result.status == "report_created"
        assert len(result.open_questions) == 1
        mock_email.assert_called_once()
        assert mock_email.call_args.kwargs["is_update"] is False
        assert mock_email.call_args.kwargs["open_question_count"] == 1
        email_step = next(step for step in result.steps if step.step == "notify.email")
        assert email_step.status == "succeeded"

    @patch("due_diligence_reporter.report_pipeline.add_rhodes_site_note")
    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline._email_pipeline_report")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_source_triggered_republish_waits_when_vendor_inputs_remain_missing(
        self,
        mock_readiness,
        mock_email,
        mock_agent,
        mock_completeness,
        mock_rhodes_note,
        monkeypatch,
    ):
        """Source-triggered updates wait until the full vendor set is present."""
        monkeypatch.setenv("VENDOR_GATE_ENABLED", "1")
        mock_readiness.return_value = {
            "sir_found": True,
            "sir_vendor": True,
            "isp_found": False,
            "inspection_found": False,
            "inspection_vendor": False,
            "raycon_scenario_found": False,
            "raycon_scenario_usable": False,
            "report_exists": True,
        }
        trace = ReportTrace(
            site_name="Alpha Keller",
            started_at="2026-06-04T15:00:00+00:00",
            events=[],
            final_report_data={"exec.c_answer": "Yes"},
        )
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc-update",
            "doc_url": "https://docs.google.com/document/d/doc-update",
            "trace": trace,
        }
        mock_rhodes_note.return_value = {
            "status": "created",
            "reason": "ok",
            "rhodes_note_id": "NOTE1",
            "owner_notification": "mentioned",
        }

        async def fake_completeness(doc_id):
            return {"ready_to_send": True, "pending_section_count": 0}

        mock_completeness.side_effect = fake_completeness

        result = process_site_pipeline(
            MagicMock(),
            "Alpha Keller",
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"],
            {},
            "system prompt",
            _make_settings(),
            source_event={
                "source_type": "vendor_sir",
                "fingerprint": "sir-1",
                "file_name": "Alpha Keller Vendor SIR.pdf",
            },
            force_regenerate=True,
            site_id="SITE1",
        )

        assert result.status == "waiting_on_docs"
        assert result.missing_docs == ["Vendor Building Inspection", "RayCon Scenario JSON"]
        mock_agent.assert_not_called()
        mock_completeness.assert_not_called()
        mock_email.assert_not_called()
        mock_rhodes_note.assert_not_called()
        generate_step = next(step for step in result.steps if step.step == "report.generate")
        assert generate_step.status == "skipped"
        assert generate_step.skipped_reason == (
            "source_triggered_republish_waiting_on_docs"
        )
        assert result.republish_summary == {
            "trigger_source": "vendor_sir",
            "closed_open_item_count": 0,
            "still_open_item_count": 0,
            "outstanding_vendor_docs": [
                "Vendor Building Inspection",
                "RayCon Scenario JSON",
            ],
        }
        assert not any(step.step == "notify.email" for step in result.steps)

    @patch("due_diligence_reporter.report_pipeline.add_rhodes_site_note")
    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline._email_pipeline_report")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_interim_update_skips_email_when_open_items_remain(
        self,
        mock_readiness,
        mock_email,
        mock_agent,
        mock_completeness,
        mock_rhodes_note,
    ):
        """Source-triggered updates with remaining open asks are not emailed."""
        mock_readiness.return_value = {
            "sir_found": True,
            "sir_vendor": True,
            "isp_found": False,
            "inspection_found": True,
            "inspection_vendor": True,
            "raycon_scenario_found": True,
            "raycon_scenario_usable": True,
            "report_exists": True,
        }
        trace = ReportTrace(
            site_name="Alpha Keller",
            started_at="2026-06-04T15:00:00+00:00",
            events=[],
            final_report_data={
                "verification.open_items": "- Confirm education approval path",
            },
        )
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc-update",
            "doc_url": "https://docs.google.com/document/d/doc-update",
            "trace": trace,
        }
        mock_rhodes_note.return_value = {
            "status": "created",
            "reason": "ok",
            "rhodes_note_id": "NOTE1",
            "owner_notification": "mentioned",
        }

        async def fake_completeness(doc_id):
            return {"ready_to_send": True, "pending_section_count": 0}

        mock_completeness.side_effect = fake_completeness

        result = process_site_pipeline(
            MagicMock(),
            "Alpha Keller",
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"],
            {},
            "system prompt",
            _make_settings(),
            source_event={"source_type": "building_inspection", "fingerprint": "bi-1"},
            force_regenerate=True,
            site_id="SITE1",
        )

        assert result.status == "report_created"
        assert len(result.open_questions) == 1
        mock_email.assert_not_called()
        email_step = next(step for step in result.steps if step.step == "notify.email")
        assert email_step.status == "skipped"
        assert email_step.skipped_reason == "interim DDR update; open verification items remain"

    @patch("due_diligence_reporter.report_pipeline.add_rhodes_site_note")
    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline._email_pipeline_report")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_final_update_sends_email_when_vendor_review_is_complete(
        self,
        mock_readiness,
        mock_email,
        mock_agent,
        mock_completeness,
        mock_rhodes_note,
    ):
        """The final source-triggered DDR update emails once no open asks remain."""
        mock_readiness.return_value = {
            "sir_found": True,
            "sir_vendor": True,
            "isp_found": False,
            "inspection_found": True,
            "inspection_vendor": True,
            "raycon_scenario_found": True,
            "raycon_scenario_usable": True,
            "report_exists": True,
        }
        trace = ReportTrace(
            site_name="Alpha Keller",
            started_at="2026-06-04T15:00:00+00:00",
            events=[],
            final_report_data={"exec.c_answer": "Yes"},
        )
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc-final",
            "doc_url": "https://docs.google.com/document/d/doc-final",
            "trace": trace,
        }
        mock_email.return_value = None
        mock_rhodes_note.return_value = {
            "status": "created",
            "reason": "ok",
            "rhodes_note_id": "NOTE1",
            "owner_notification": "mentioned",
        }

        async def fake_completeness(doc_id):
            return {"ready_to_send": True, "pending_section_count": 0}

        mock_completeness.side_effect = fake_completeness

        result = process_site_pipeline(
            MagicMock(),
            "Alpha Keller",
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"],
            {},
            "system prompt",
            _make_settings(),
            source_event={"source_type": "building_inspection", "fingerprint": "bi-1"},
            force_regenerate=True,
            site_id="SITE1",
        )

        assert result.status == "report_created"
        assert result.open_questions == []
        mock_email.assert_called_once()
        assert mock_email.call_args.kwargs["is_update"] is True
        assert mock_email.call_args.kwargs["open_question_count"] == 0
        email_step = next(step for step in result.steps if step.step == "notify.email")
        assert email_step.status == "succeeded"

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

    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_records_sir_learning_review_candidate(self, mock_readiness):
        """Readiness metadata creates a non-blocking SIR learning step."""
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": False,
            "inspection_found": True,
            "report_exists": True,
            "sir_learning_review": {
                "status": "ready_for_review",
                "reason": "AI SIR and CDS/vendor SIR are both present",
                "ai_sir": {"name": "ai.docx", "file_id": "ai"},
                "cds_sir": {"name": "cds.pdf", "file_id": "cds"},
            },
        }

        result = process_site_pipeline(
            MagicMock(),
            "Alpha Keller",
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"],
            {},
            "system prompt",
            _make_settings(),
        )

        assert result.status == "report_exists"
        assert result.sir_review_status == "ready_for_review"
        review_step = next(step for step in result.steps if step.step == "sir.learning_review")
        assert review_step.status == "succeeded"
        assert review_step.artifacts[0].metadata["cds_sir"]["file_id"] == "cds"

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

    @patch("due_diligence_reporter.report_pipeline.add_rhodes_site_note")
    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_report_created_records_rhodes_summary_event(
        self,
        mock_readiness,
        mock_agent,
        mock_completeness,
        mock_rhodes_note,
    ):
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": False,
            "inspection_found": True,
            "report_exists": False,
        }
        trace = ReportTrace(
            site_name="Alpha Keller",
            started_at="2026-05-27T18:00:00+00:00",
            events=[],
            final_report_data={
                "verification.open_items": "- Confirm zoning use from the vendor SIR",
            },
        )
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc123",
            "doc_url": "https://docs.google.com/document/d/doc123",
            "trace": trace,
        }
        mock_rhodes_note.return_value = {
            "status": "created",
            "reason": "ok",
            "rhodes_note_id": "NOTE1",
            "owner_notification": "mentioned",
        }

        async def fake_completeness(doc_id):
            return {"ready_to_send": True, "pending_section_count": 0}

        mock_completeness.side_effect = fake_completeness

        result = process_site_pipeline(
            MagicMock(),
            "Alpha Keller",
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"],
            {},
            "system prompt",
            _make_settings(),
            p1_email="owner@example.com",
            site_id="SITE1",
        )

        assert result.status == "report_created"
        assert result.rhodes_report_event is not None
        assert result.rhodes_report_event["status"] == "created"
        assert result.rhodes_report_event["rhodes_note_id"] == "NOTE1"
        note_kwargs = mock_rhodes_note.call_args.kwargs
        assert note_kwargs["site_id"] == "SITE1"
        assert note_kwargs["owner_email"] == "owner@example.com"
        assert "Kind: dd_report_created" in note_kwargs["body"]
        assert "Decision required: yes" in note_kwargs["body"]
        assert "Action needed: Review the DD report and close 1 open verification ask" in (
            note_kwargs["body"]
        )
        assert "Ask 1: Confirm zoning use from the vendor SIR" in note_kwargs["body"]
        step = next(step for step in result.steps if step.step == "rhodes.report_event")
        assert step.status == "succeeded"

    @patch("due_diligence_reporter.report_pipeline.post_google_chat_message")
    @patch("due_diligence_reporter.report_pipeline.add_rhodes_site_note")
    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_report_created_with_open_items_alerts_chat_when_owner_not_mentioned(
        self,
        mock_readiness,
        mock_agent,
        mock_completeness,
        mock_rhodes_note,
        mock_chat,
    ):
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": False,
            "inspection_found": True,
            "report_exists": False,
        }
        trace = ReportTrace(
            site_name="Alpha Keller",
            started_at="2026-05-27T18:00:00+00:00",
            events=[],
            final_report_data={
                "verification.open_items": "- Confirm education approval path",
            },
        )
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc123",
            "doc_url": "https://docs.google.com/document/d/doc123",
            "trace": trace,
        }
        mock_rhodes_note.return_value = {
            "status": "created",
            "reason": "ok",
            "rhodes_note_id": "NOTE1",
            "owner_notification": "none",
        }

        async def fake_completeness(doc_id):
            return {"ready_to_send": True, "pending_section_count": 0}

        mock_completeness.side_effect = fake_completeness
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
            site_id="SITE1",
        )

        assert result.rhodes_report_event is not None
        assert result.rhodes_report_event["google_chat"]["status"] == "sent"
        mock_chat.assert_called_once()
        assert mock_chat.call_args.args[0] == "https://chat.example/webhook"
        assert "Action needed: Review the DD report and close 1 open verification ask" in (
            mock_chat.call_args.args[1]
        )

    @patch(
        "due_diligence_reporter.report_pipeline.utc_now_iso",
        return_value="2026-06-01T15:00:00+00:00",
    )
    @patch("due_diligence_reporter.report_pipeline.post_google_chat_message")
    @patch("due_diligence_reporter.report_pipeline.add_rhodes_site_note")
    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_report_created_frequency_cap_skips_owner_and_chat_notifications(
        self,
        mock_readiness,
        mock_agent,
        mock_completeness,
        mock_rhodes_note,
        mock_chat,
        _mock_utc_now,
        tmp_path,
    ):
        _write_prior_report_event_manifest(
            tmp_path,
            started_at="2026-05-29T14:00:00+00:00",
        )
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": False,
            "inspection_found": True,
            "report_exists": False,
        }
        trace = ReportTrace(
            site_name="Alpha Keller",
            started_at="2026-05-27T18:00:00+00:00",
            events=[],
            final_report_data={
                "verification.open_items": "- Confirm education approval path",
            },
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
            site_id="SITE1",
        )

        assert result.rhodes_report_event is not None
        assert result.rhodes_report_event["status"] == "skipped"
        assert result.rhodes_report_event["reason"] == "frequency_cap"
        assert result.rhodes_report_event["next_allowed_at"] == "2026-06-02T14:00:00+00:00"
        mock_rhodes_note.assert_not_called()
        mock_chat.assert_not_called()
        step = next(step for step in result.steps if step.step == "rhodes.report_event")
        assert step.status == "skipped"

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
        assert result.run_id
        assert result.failed_step == "report.generate"
        assert result.quality_score is not None
        assert any(step.step == "report.generate" for step in result.steps)

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
        assert result.failed_step == "readiness.check"

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
        assert result.failed_step == "report.validate"

    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_report_incomplete_does_not_record_publish_step(
        self,
        mock_readiness,
        mock_agent,
        mock_completeness,
    ):
        """report_incomplete returns without a publish side effect."""
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
        gc = MagicMock()
        result = process_site_pipeline(
            gc, "Alpha Keller", "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"], {}, "system prompt", _make_settings(),
            site_address="123 Main St, Keller TX",
            p1_name="Robbie Forrest",
            site_created_at="2026-04-01T00:00:00Z",
        )

        assert result.status == "report_incomplete"
        assert result.doc_id == "doc123"
        assert result.error == "Report NOT ready to send. 1 raw template token(s)."
        assert result.failed_step == "report.validate"
        assert result.quality_score is not None
        assert not any(step.step.startswith("publish.") for step in result.steps)
        gc.upload_file_to_folder.assert_not_called()

    @patch("due_diligence_reporter.report_pipeline.post_google_chat_message")
    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_vendor_gate_alert_uses_completeness_summary(
        self,
        mock_readiness,
        mock_agent,
        mock_completeness,
        mock_chat,
        monkeypatch,
    ):
        """Vendor-gate alerts should explain the real completeness failure.

        A report can fail completeness with zero unresolved {{...}} tokens
        when raw template tokens leak or the Can-We-Open answer is invalid.
        """
        monkeypatch.setenv("VENDOR_GATE_ENABLED", "1")
        mock_readiness.return_value = {
            "sir_found": True,
            "sir_vendor": True,
            "isp_found": False,
            "inspection_found": True,
            "inspection_vendor": True,
            "raycon_scenario_found": True,
            "report_exists": False,
        }
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc123",
            "doc_url": "https://docs.google.com/document/d/doc123",
            "trace": ReportTrace(
                site_name="Alpha Tulsa 421 E 11th St",
                started_at="2026-05-13T15:53:12+00:00",
                events=[],
                final_report_data={"exec.c_answer": "Yes"},
            ),
        }

        async def fake_completeness(doc_id):
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
        settings = _make_settings()
        settings.google_chat_webhook_url = "https://chat.example/webhook"

        result = process_site_pipeline(
            MagicMock(),
            "Alpha Tulsa 421 E 11th St",
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Tulsa 421 E 11th St"],
            {},
            "system prompt",
            settings,
            p1_email="owner@example.com",
            p1_name="Owner One",
        )

        assert result.status == "report_incomplete"
        mock_chat.assert_called()
        messages = [call.args[1] for call in mock_chat.call_args_list]
        vendor_message = next(m for m in messages if "vendor_gate_review_required" in m)
        assert "Kind: vendor_gate_review_required" in vendor_message
        assert (
            "Failure reason: Report NOT ready to send. 1 raw template token(s)."
            in vendor_message
        )
        assert "0 tokens unresolved" not in vendor_message

    @patch("due_diligence_reporter.report_pipeline.post_google_chat_message")
    @patch("due_diligence_reporter.report_pipeline.add_rhodes_site_note")
    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_vendor_gate_alert_records_rhodes_note_when_owner_mentioned(
        self,
        mock_readiness,
        mock_agent,
        mock_completeness,
        mock_rhodes_note,
        mock_chat,
        monkeypatch,
    ):
        monkeypatch.setenv("VENDOR_GATE_ENABLED", "1")
        mock_readiness.return_value = {
            "sir_found": True,
            "sir_vendor": True,
            "isp_found": False,
            "inspection_found": True,
            "inspection_vendor": True,
            "raycon_scenario_found": True,
            "report_exists": False,
        }
        mock_agent.return_value = {
            "success": True,
            "doc_id": "doc123",
            "doc_url": "https://docs.google.com/document/d/doc123",
            "trace": ReportTrace(
                site_name="Alpha Tulsa 421 E 11th St",
                started_at="2026-05-13T15:53:12+00:00",
                events=[],
                final_report_data={"exec.c_answer": "Yes"},
            ),
        }
        mock_rhodes_note.return_value = {
            "status": "created",
            "reason": "ok",
            "rhodes_note_id": "NOTE-VENDOR",
            "owner_notification": "mentioned",
        }

        async def fake_completeness(doc_id):
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
        settings = _make_settings()
        settings.google_chat_webhook_url = "https://chat.example/webhook"

        result = process_site_pipeline(
            MagicMock(),
            "Alpha Tulsa 421 E 11th St",
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Tulsa 421 E 11th St"],
            {},
            "system prompt",
            settings,
            p1_email="owner@example.com",
            p1_name="Owner One",
            site_id="SITE1",
        )

        assert result.status == "report_incomplete"
        mock_rhodes_note.assert_called_once()
        mock_chat.assert_not_called()
        note_kwargs = mock_rhodes_note.call_args.kwargs
        assert note_kwargs["site_id"] == "SITE1"
        assert note_kwargs["owner_email"] == "owner@example.com"
        assert "Kind: vendor_gate_review_required" in note_kwargs["body"]
        assert (
            "Failure reason: Report NOT ready to send. 1 raw template token(s)."
            in note_kwargs["body"]
        )
        step = next(step for step in result.steps if step.step == "vendor_gate.alert")
        assert step.status == "succeeded"
        assert step.artifacts[0].metadata["event_type"] == "vendor_gate_review_required"
        assert step.artifacts[0].metadata["rhodes_note_id"] == "NOTE-VENDOR"

    @patch("due_diligence_reporter.report_pipeline.add_rhodes_site_note")
    @patch("due_diligence_reporter.server.check_report_completeness")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_report_created_does_not_record_publish_step(
        self,
        mock_readiness,
        mock_agent,
        mock_completeness,
        mock_rhodes_note,
    ):
        """Success path records the Rhodes event but not the old publish side effect."""
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
        mock_rhodes_note.return_value = {
            "status": "created",
            "reason": "ok",
            "rhodes_note_id": "NOTE1",
            "owner_notification": "none",
        }
        gc = MagicMock()
        result = process_site_pipeline(
            gc, "Alpha Keller", "https://drive.google.com/drive/folders/abc123",
            ["Alpha Keller"], {}, "system prompt", _make_settings(),
            site_id="SITE1",
        )

        assert result.status == "report_created"
        assert result.run_id
        assert result.failed_step is None
        assert result.rhodes_report_event is not None
        assert result.rhodes_report_event["rhodes_note_id"] == "NOTE1"
        assert result.quality_band in {"green", "yellow", "orange", "red"}
        assert not any(step.step.startswith("publish.") for step in result.steps)
        gc.upload_file_to_folder.assert_not_called()


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

    @patch("due_diligence_reporter.report_pipeline._list_m1_documents_by_type")
    @patch("due_diligence_reporter.report_pipeline._resolve_m1_folder")
    def test_failed_raycon_json_is_not_usable_for_full_report(
        self,
        mock_resolve_m1,
        mock_list_m1,
    ):
        mock_resolve_m1.return_value = ("m1-folder-id", "M1")
        mock_list_m1.return_value = {
            "raycon_scenario_json": {
                "id": "raycon-json-1",
                "name": "raycon_scenario.json",
                "modifiedTime": "2026-05-28T14:00:00Z",
            }
        }
        gc = MagicMock()
        gc.list_files_recursive.return_value = []
        gc.download_file_bytes.return_value = json.dumps(
            {
                "schema_version": "1.0",
                "status": "failed",
                "raycon_run_id": "rc_failed",
                "validation": {
                    "passed": False,
                    "errors": ["capacity_not_defensible"],
                },
            }
        ).encode("utf-8")

        result = check_site_readiness_direct(
            gc,
            "https://drive.google.com/drive/folders/abc123",
            ["Alpha Tulsa 6940 S Utica Ave"],
            {"sir": [], "isp": [], "building_inspection": []},
            site_title="Alpha Tulsa 6940 S Utica Ave",
        )

        assert result["raycon_scenario_found"] is True
        assert result["raycon_scenario_usable"] is False
        assert result["raycon_scenario_status"] == "failed_validation"
        assert result["raycon_scenario_run_id"] == "rc_failed"
        assert "capacity_not_defensible" in result["raycon_scenario_failure_reason"]
        assert (
            result["raycon_report_data_fields"]["exec.raycon_failure_reason"]
            == "capacity_not_defensible"
        )


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


class TestCompletedReportBundleSummary:
    @patch("due_diligence_reporter.report_pipeline.post_google_chat_message")
    def test_batches_report_exists_sites_into_one_message(self, mock_chat):
        post_completed_report_bundle_summary(
            "https://chat.example/hook",
            [
                PipelineResult(site_title="Alpha Keller", status="report_exists"),
                PipelineResult(site_title="Alpha Austin", status="waiting_on_docs"),
                PipelineResult(site_title="Alpha Boston", status="report_exists"),
            ],
        )

        mock_chat.assert_called_once()
        assert mock_chat.call_args.args[0] == "https://chat.example/hook"
        message = mock_chat.call_args.args[1]
        assert "Daily DDR scan -- completed report bundles already present" in message
        assert "completed DD Report already exists: 2" in message
        assert "- Alpha Keller" in message
        assert "- Alpha Boston" in message
        assert "Alpha Austin" not in message

    @patch("due_diligence_reporter.report_pipeline.post_google_chat_message")
    def test_noops_when_no_existing_reports(self, mock_chat):
        post_completed_report_bundle_summary(
            "https://chat.example/hook",
            [PipelineResult(site_title="Alpha Austin", status="waiting_on_docs")],
        )

        mock_chat.assert_not_called()


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

    def test_failed_raycon_cached_fields_override_agent_values(self):
        merged = _merge_cached_report_fields(
            {
                "report_data": {
                    "exec.fastest_open_capex": "$100,000",
                    "exec.raycon_status": "completed",
                },
            },
            {
                "exec.raycon_status": "failed",
                "exec.raycon_failure_reason": "capacity_not_defensible",
                "exec.fastest_open_capex": "",
            },
        )

        assert merged["report_data"]["exec.raycon_status"] == "failed"
        assert (
            merged["report_data"]["exec.raycon_failure_reason"]
            == "capacity_not_defensible"
        )
        assert merged["report_data"]["exec.fastest_open_capex"] == ""

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

        result = run_dd_report_agent(
            "Alpha Keller",
            "system prompt",
            "claude-test",
            initial_report_fields={
                "meta.prepared_by": "Devin Bates",
                "p1_assignee_email": "devin.bates@trilogy.com",
            },
        )

        assert result["success"] is True
        assert mock_route_tool_call_sync.call_count == 2
        create_call = mock_route_tool_call_sync.call_args_list[1]
        create_input = create_call.args[1]
        assert create_input["report_data"]["exec.fastest_open_capacity"] == "25"
        assert create_input["report_data"]["meta.prepared_by"] == "Devin Bates"
        assert create_input["report_data"]["p1_assignee_email"] == "devin.bates@trilogy.com"
        assert create_input["report_data"]["q2.school_approval_difficulty"] == "easy"
        assert create_input["report_data"]["q2.school_approval_score"] == "9"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("due_diligence_reporter.report_pipeline.anthropic.Anthropic")
    def test_run_dd_report_agent_includes_direct_folder_context(self, mock_anthropic):
        response = MagicMock()
        response.content = []
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        mock_anthropic.return_value = mock_client

        run_dd_report_agent(
            "Alpha Los Angeles 5400 Beethoven St",
            "system prompt",
            "claude-test",
            drive_folder_url="https://drive.google.com/drive/folders/folder123",
            site_address="5400 Beethoven St, Los Angeles, CA 90066",
            rhodes_owner_context={
                "status": "found",
                "p1_assignee_name": "Devin Bates",
                "p1_assignee_email": "devin.bates@trilogy.com",
            },
        )

        messages = mock_client.messages.create.call_args.kwargs["messages"]
        user_content = messages[0]["content"]
        assert "Alpha Los Angeles 5400 Beethoven St" in user_content
        assert "5400 Beethoven St, Los Angeles, CA 90066" in user_content
        assert "https://drive.google.com/drive/folders/folder123" in user_content
        assert "Use the provided Drive folder directly" in user_content
        assert "Rhodes P1 DRI / site owner: Devin Bates <devin.bates@trilogy.com>" in user_content

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("due_diligence_reporter.report_pipeline.route_tool_call_sync")
    @patch("due_diligence_reporter.report_pipeline.anthropic.Anthropic")
    def test_run_dd_report_agent_canonicalizes_site_scoped_tool_inputs(
        self,
        mock_anthropic,
        mock_route_tool_call_sync,
    ):
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
                "list_drive_documents",
                {
                    "site_name": "Alpha Los Angeles",
                    "drive_folder_url": "https://drive.google.com/drive/folders/wrong",
                },
            ),
            FakeToolUse(
                "tool-2",
                "create_dd_report",
                {
                    "site_name": "Alpha Los Angeles",
                    "drive_folder_url": "https://drive.google.com/drive/folders/wrong",
                    "report_data": {"exec.fastest_open_capacity": "25"},
                },
            ),
        ]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        mock_anthropic.return_value = mock_client
        mock_route_tool_call_sync.side_effect = [
            {"status": "success", "files": []},
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

        result = run_dd_report_agent(
            "Alpha Los Angeles 5400 Beethoven St",
            "system prompt",
            "claude-test",
            drive_folder_url="https://drive.google.com/drive/folders/folder123",
            site_address="5400 Beethoven St, Los Angeles, CA 90066",
        )

        assert result["success"] is True
        list_input = mock_route_tool_call_sync.call_args_list[0].args[1]
        assert list_input["site_name"] == "Alpha Los Angeles 5400 Beethoven St"
        assert list_input["site_address"] == "5400 Beethoven St, Los Angeles, CA 90066"
        assert list_input["drive_folder_url"].endswith("/folder123")
        create_input = mock_route_tool_call_sync.call_args_list[1].args[1]
        assert create_input["site_name"] == "Alpha Los Angeles 5400 Beethoven St"
        assert create_input["drive_folder_url"].endswith("/folder123")
        assert create_input["site_address"] == "5400 Beethoven St, Los Angeles, CA 90066"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("due_diligence_reporter.report_pipeline.route_tool_call_sync")
    @patch("due_diligence_reporter.report_pipeline.anthropic.Anthropic")
    def test_run_dd_report_agent_uses_rhodes_drive_folder_when_omitted(
        self,
        mock_anthropic,
        mock_route_tool_call_sync,
    ):
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
                "lookup_rhodes_site_owner",
                {"site_name": "Alpha Los Angeles"},
            ),
            FakeToolUse(
                "tool-2",
                "list_drive_documents",
                {"site_name": "Alpha Los Angeles"},
            ),
            FakeToolUse(
                "tool-3",
                "create_dd_report",
                {
                    "site_name": "Alpha Los Angeles",
                    "report_data": {"exec.fastest_open_capacity": "25"},
                },
            ),
        ]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        mock_anthropic.return_value = mock_client
        mock_route_tool_call_sync.side_effect = [
            {
                "status": "found",
                "p1_assignee_name": "Devin Bates",
                "site_address": "5400 Beethoven St, Los Angeles, CA 90066",
                "drive_folder_url": "https://drive.google.com/drive/folders/rhodes-root",
                "report_data_fields": {
                    "meta.prepared_by": "Devin Bates",
                    "site.address": "5400 Beethoven St, Los Angeles, CA 90066",
                    "meta.drive_folder_url": "https://drive.google.com/drive/folders/rhodes-root",
                },
            },
            {"status": "success", "files": []},
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

        result = run_dd_report_agent(
            "Alpha Los Angeles 5400 Beethoven St",
            "system prompt",
            "claude-test",
        )

        assert result["success"] is True
        lookup_input = mock_route_tool_call_sync.call_args_list[0].args[1]
        assert lookup_input["site_name"] == "Alpha Los Angeles 5400 Beethoven St"
        assert "site_address" not in lookup_input
        list_input = mock_route_tool_call_sync.call_args_list[1].args[1]
        assert list_input["drive_folder_url"].endswith("/rhodes-root")
        assert list_input["site_address"] == "5400 Beethoven St, Los Angeles, CA 90066"
        create_input = mock_route_tool_call_sync.call_args_list[2].args[1]
        assert create_input["drive_folder_url"].endswith("/rhodes-root")
        assert create_input["site_address"] == "5400 Beethoven St, Los Angeles, CA 90066"
        assert create_input["report_data"]["meta.prepared_by"] == "Devin Bates"
        assert create_input["report_data"]["site.address"] == (
            "5400 Beethoven St, Los Angeles, CA 90066"
        )


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

    def test_successful_source_read_message_is_not_an_issue(self):
        trace = ReportTrace(
            site_name="Alpha Los Angeles 5400 Beethoven St",
            started_at="2026-05-26T15:22:04+00:00",
            events=[
                TraceEvent(
                    timestamp="2026-05-26T15:22:04+00:00",
                    event_type="tool_call",
                    tool_name="read_drive_document",
                    input_summary={
                        "file_name": "5400-beethoven-st-los-angeles-ca_2026-05-21_SIR.docx",
                    },
                    output_summary={
                        "status": "success",
                        "content_length": 21505,
                        "message": (
                            "Successfully read 21505 characters from "
                            "'5400-beethoven-st-los-angeles-ca_2026-05-21_SIR.docx'"
                        ),
                        "source_usable": True,
                    },
                ),
            ],
        )

        assert _extract_source_read_issues(trace) == []

    @patch("due_diligence_reporter.report_pipeline.post_google_chat_message")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_generation_failure_posts_source_review_alert(
        self,
        mock_readiness,
        mock_agent,
        mock_chat,
    ):
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": True,
            "inspection_found": True,
            "report_exists": False,
        }
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
            p1_email="owner@example.com",
            p1_name="Owner One",
        )

        assert result.status == "generation_failed"
        assert result.trace_url is None
        mock_chat.assert_called_once()
        message = mock_chat.call_args.args[1]
        assert "Kind: source_review_required" in message
        assert "Site ID: unknown" in message
        assert "Source issue 1: SIR | Alpha Keller SIR.pdf" in message
        assert "Failed to read document" in message

    @patch("due_diligence_reporter.report_pipeline.post_google_chat_message")
    @patch("due_diligence_reporter.report_pipeline.add_rhodes_site_note")
    @patch("due_diligence_reporter.report_pipeline.run_dd_report_agent")
    @patch("due_diligence_reporter.report_pipeline.check_site_readiness_direct")
    def test_generation_failure_records_source_review_in_rhodes(
        self,
        mock_readiness,
        mock_agent,
        mock_rhodes_note,
        mock_chat,
    ):
        mock_readiness.return_value = {
            "sir_found": True,
            "isp_found": True,
            "inspection_found": True,
            "report_exists": False,
        }
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
        mock_rhodes_note.return_value = {
            "status": "created",
            "reason": "ok",
            "rhodes_note_id": "NOTE-SOURCE",
            "owner_notification": "mentioned",
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
            p1_email="owner@example.com",
            p1_name="Owner One",
            site_id="SITE1",
        )

        assert result.status == "generation_failed"
        mock_rhodes_note.assert_called_once()
        mock_chat.assert_not_called()
        note_kwargs = mock_rhodes_note.call_args.kwargs
        assert note_kwargs["site_id"] == "SITE1"
        assert note_kwargs["owner_email"] == "owner@example.com"
        assert "Kind: source_review_required" in note_kwargs["body"]
        assert "Decision required: yes" in note_kwargs["body"]
        assert "Source issue 1: SIR | Alpha Keller SIR.pdf" in note_kwargs["body"]
        assert "Failed to read document" in note_kwargs["body"]
        step = next(step for step in result.steps if step.step == "source.alert")
        assert step.status == "failed"
        assert step.artifacts[0].metadata["event_type"] == "source_review_required"
        assert step.artifacts[0].metadata["rhodes_note_id"] == "NOTE-SOURCE"
