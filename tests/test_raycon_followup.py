"""Tests for scripts/raycon_followup.py — the 5-minute cadence script that
publishes RayCon scenario reports and alerts on stuck sites.

Exercises the per-site processing logic in isolation using mocks for the
Google client, Wrike, and ``save_skill_report``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from scripts.raycon_followup import (
    _dispatch_raycon_job,
    _filter_dedup_alerts,
    _process_site,
    _republish_dd_report_if_present,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _site(title: str = "Alpha Keller") -> dict:
    return {
        "id": "site-123",
        "title": title,
        "drive_folder_url": "https://drive.google.com/drive/folders/abc123",
        "address": "123 Main St",
        "total_building_sf": 8500,
    }


def _block_plan(modified_minutes_ago: int = 5) -> dict:
    """Fake Drive file dict for a Block Plan PDF."""
    when = datetime.now(timezone.utc) - timedelta(minutes=modified_minutes_ago)
    return {
        "id": "bp_file_1",
        "name": "Block Plan v3.pdf",
        "modifiedTime": when.isoformat().replace("+00:00", "Z"),
        "mimeType": "application/pdf",
    }


def _scenario_payload(json_modified: str | None = None) -> dict:
    return {
        "site_id": "S1",
        "scenarios": [{"name": "fastest_open"}],
        "_drive_modified_time": json_modified
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


# ---------------------------------------------------------------------------
# Per-site processing
# ---------------------------------------------------------------------------


class TestProcessSite:
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_alert_when_block_plan_old_and_no_scenario(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
    ):
        """Block Plan landed >alert-after ago AND no scenario JSON → alert.

        With dispatch_state pre-populated to dedup the same block_plan_file_id,
        the dispatch path is suppressed so we hit the legacy stuck-site alert.
        """
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=120)
        mock_read_scenario.return_value = None  # No scenario yet.

        # Pre-populate dispatch state so we suppress this run's dispatch and
        # exercise the original alert path.
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        dispatch_state = {
            "bp_file_1": {"last_dispatch": recent, "count": 1, "site": "Alpha Keller"}
        }

        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dispatch_state=dispatch_state,
        )

        assert "alert" in row
        assert "no raycon_scenario.json" in row["alert"]
        assert row["site"] == "Alpha Keller"
        assert "block_plan_modified" in row

    @patch("scripts.raycon_followup._find_published_doc")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_skip_when_doc_up_to_date(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_find_doc,
    ):
        """Existing report Doc with modifiedTime >= scenario JSON's → skip."""
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        json_t = "2026-04-30T12:00:00Z"
        doc_t = "2026-04-30T12:30:00Z"  # Doc is newer than JSON.
        mock_read_scenario.return_value = _scenario_payload(json_modified=json_t)
        mock_find_doc.return_value = {
            "id": "doc1",
            "name": "RayCon Scenario Assessment - Alpha Keller",
            "modifiedTime": doc_t,
        }

        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
        )

        assert row.get("skipped") == "report doc up to date"
        assert "published" not in row
        assert "alert" not in row

    @patch("scripts.raycon_followup.save_skill_report")
    @patch("scripts.raycon_followup._find_published_doc")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_publish_failure_returns_error_row(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_find_doc,
        mock_save,
    ):
        """save_skill_report returning non-success → error row, not published."""
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = _scenario_payload()
        mock_find_doc.return_value = None  # No existing doc → must publish.

        async def _fake_save(**_kwargs):
            return {"status": "error", "message": "Drive 503"}

        mock_save.side_effect = _fake_save

        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
        )

        assert "error" in row
        assert "Drive 503" in row["error"]
        assert "published" not in row

    @patch("scripts.raycon_followup.save_skill_report")
    @patch("scripts.raycon_followup._find_published_doc")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_dry_run_does_not_call_save_skill_report(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_find_doc,
        mock_save,
    ):
        """dry_run=True returns would_publish row without invoking publisher."""
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = _scenario_payload()
        mock_find_doc.return_value = None

        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=True,
            alert_after=timedelta(minutes=60),
        )

        assert row.get("would_publish") is True
        assert row.get("doc_existed") is False
        mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# Failed RayCon runs surface as alerts, not published Docs
# ---------------------------------------------------------------------------


class TestFailedScenarioAlerts:
    """When ``raycon_scenario.json`` arrives with ``status: failed`` (or
    ``validation.passed: false``), the followup must NOT publish a Doc —
    that would render an empty/zero-cost scenario the dashboard treats as
    real. Instead surface the failure as an alert row that flows through
    the existing Chat alert path."""

    @patch("scripts.raycon_followup.save_skill_report")
    @patch("scripts.raycon_followup._find_published_doc")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_failed_status_returns_alert_not_published_doc(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_find_doc,
        mock_save,
    ):
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        # RayCon's real failed-run shape — envelope with status:failed and a
        # validation.errors list explaining why scenarios couldn't compute.
        mock_read_scenario.return_value = {
            "schema_version": "1.0",
            "status": "failed",
            "raycon_run_id": "rc_pbg_abc",
            "analysis": {
                "summary": "RayCon could not complete scenario pricing.",
                "fastest_open": None,
                "max_capacity": None,
            },
            "validation": {
                "passed": False,
                "errors": ["no_address_match: tier not resolved"],
            },
            "_drive_modified_time": "2026-05-05T19:54:30Z",
        }
        mock_find_doc.return_value = None

        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
        )

        assert "alert" in row
        assert "raycon run failed" in row["alert"]
        assert "no_address_match" in row["alert"]
        assert row["raycon_status"] == "failed"
        assert row["raycon_run_id"] == "rc_pbg_abc"
        # Critical: no Doc published for a failed run.
        mock_save.assert_not_called()
        assert "published" not in row

    @patch("scripts.raycon_followup.save_skill_report")
    @patch("scripts.raycon_followup._find_published_doc")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_validation_passed_false_alone_blocks_publish(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_find_doc,
        mock_save,
    ):
        """Even with optimistic ``status: completed``, validation.passed=false
        prevents publishing."""
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = {
            "schema_version": "1.0",
            "status": "completed",
            "analysis": {
                "fastest_open": {"grand_total": 100, "timeline_weeks": 4},
                "max_capacity": {"grand_total": 200, "timeline_weeks": 8},
            },
            "validation": {"passed": False, "errors": ["missing inputs"]},
            "_drive_modified_time": "2026-05-05T20:00:00Z",
        }
        mock_find_doc.return_value = None

        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
        )

        assert "alert" in row
        assert "missing inputs" in row["alert"]
        mock_save.assert_not_called()

    @patch("scripts.raycon_followup.save_skill_report")
    @patch("scripts.raycon_followup._find_published_doc")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_successful_envelope_payload_still_publishes(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_find_doc,
        mock_save,
    ):
        """Sanity: a v1.1 envelope with ``status: completed`` and
        ``validation.passed: true`` still flows to publish like before."""
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = {
            "schema_version": "1.0",
            "status": "completed",
            "raycon_run_id": "rc_happy_xyz",
            "analysis": {
                "fastest_open": {"grand_total": 412000, "timeline_weeks": 14},
                "max_capacity": {"grand_total": 587000, "timeline_weeks": 22},
            },
            "validation": {"passed": True, "errors": []},
            "_drive_modified_time": "2026-05-05T20:00:00Z",
        }
        mock_find_doc.return_value = None

        async def _fake_save(**_kwargs):
            return {"status": "success", "doc_url": "https://docs.google.com/d/abc"}

        mock_save.side_effect = _fake_save

        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
        )

        assert row.get("published") is True
        assert row.get("doc_url") == "https://docs.google.com/d/abc"
        mock_save.assert_called_once()
        # Verify report_data_fields populated with envelope traceability.
        call_kwargs = mock_save.call_args.kwargs
        rdf = call_kwargs["skill_data"]["report_data_fields"]
        assert rdf["exec.fastest_open_capex"] == "$412,000"
        assert rdf["exec.raycon_status"] == "completed"
        assert rdf["exec.raycon_run_id"] == "rc_happy_xyz"


# ---------------------------------------------------------------------------
# Safety-net dispatch (cron-driven post_raycon_job)
# ---------------------------------------------------------------------------


class TestSafetyNetDispatch:
    """Verifies _process_site and _dispatch_raycon_job behavior when a Block
    Plan is present but raycon_scenario.json has not yet appeared."""

    @patch("scripts.raycon_followup.post_raycon_job")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_dispatch_fires_when_block_plan_present_and_no_scenario(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_post,
    ):
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = None
        mock_post.return_value = {
            "raycon_run_id": "run-abc",
            "status": "accepted",
        }

        dispatch_state: dict = {}
        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dispatch_state=dispatch_state,
            redispatch_after=timedelta(minutes=30),
        )

        assert row.get("dispatched") is True
        assert row.get("raycon_run_id") == "run-abc"
        assert row.get("status") == "accepted"
        assert row.get("block_plan_file_id") == "bp_file_1"
        # State updated for future runs.
        assert "bp_file_1" in dispatch_state
        assert dispatch_state["bp_file_1"]["count"] == 1
        assert dispatch_state["bp_file_1"]["raycon_run_id"] == "run-abc"

        # post_raycon_job called with the right kwargs.
        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        assert kwargs["site_id"] == "site-123"
        assert kwargs["site_name"] == "Alpha Keller"
        assert kwargs["address"] == "123 Main St"
        assert kwargs["m1_folder_id"] == "m1_folder_id"
        assert kwargs["block_plan_file_id"] == "bp_file_1"
        assert kwargs["total_building_sf"] == 8500

    @patch("scripts.raycon_followup.post_raycon_job")
    @patch("scripts.raycon_followup._find_published_doc")
    @patch("scripts.raycon_followup.save_skill_report")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_no_dispatch_when_scenario_already_present(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_save,
        mock_find_doc,
        mock_post,
    ):
        """When raycon_scenario.json is already in M1, never call post_raycon_job."""
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = _scenario_payload()
        mock_find_doc.return_value = None  # Force publish path.

        async def _fake_save(**_kwargs):
            return {"status": "success", "doc_url": "https://docs/x"}

        mock_save.side_effect = _fake_save

        dispatch_state: dict = {}
        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dispatch_state=dispatch_state,
        )

        assert row.get("published") is True
        mock_post.assert_not_called()
        # Dispatch state untouched.
        assert dispatch_state == {}

    @patch("scripts.raycon_followup.post_raycon_job")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_dispatch_skipped_when_recently_dispatched(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_post,
    ):
        """Within redispatch window → no second post_raycon_job call."""
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = None

        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        dispatch_state = {
            "bp_file_1": {
                "last_dispatch": recent,
                "count": 1,
                "site": "Alpha Keller",
                "raycon_run_id": "run-prior",
            }
        }

        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dispatch_state=dispatch_state,
            redispatch_after=timedelta(minutes=30),
        )

        # No re-fire, no dispatched flag, falls through to skipped.
        mock_post.assert_not_called()
        assert "dispatched" not in row
        assert row.get("skipped") == "scenario JSON not yet present"
        assert row.get("dispatch_skipped") == "recently dispatched"
        # State unchanged.
        assert dispatch_state["bp_file_1"]["count"] == 1
        assert dispatch_state["bp_file_1"]["last_dispatch"] == recent

    @patch("scripts.raycon_followup.post_raycon_job")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_dispatch_re_fires_after_redispatch_window(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_post,
    ):
        """Outside the redispatch window → fire again, increment count."""
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=45)
        mock_read_scenario.return_value = None
        mock_post.return_value = {
            "raycon_run_id": "run-second",
            "status": "accepted",
        }

        old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
        dispatch_state = {
            "bp_file_1": {
                "last_dispatch": old,
                "count": 1,
                "site": "Alpha Keller",
                "raycon_run_id": "run-first",
            }
        }

        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dispatch_state=dispatch_state,
            redispatch_after=timedelta(minutes=30),
        )

        assert row.get("dispatched") is True
        assert row.get("raycon_run_id") == "run-second"
        mock_post.assert_called_once()
        # Count incremented, last_dispatch refreshed.
        assert dispatch_state["bp_file_1"]["count"] == 2
        assert dispatch_state["bp_file_1"]["last_dispatch"] != old

    @patch("scripts.raycon_followup.post_raycon_job")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_dispatch_error_captured_in_row(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_post,
    ):
        """post_raycon_job raising → error row, no state mutation."""
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = None
        mock_post.side_effect = RuntimeError("RayCon 503")

        dispatch_state: dict = {}
        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dispatch_state=dispatch_state,
        )

        assert "error" in row
        assert "raycon dispatch" in row["error"]
        assert "RayCon 503" in row["error"]
        # State NOT mutated on failure (avoids fake-success dedup).
        assert dispatch_state == {}

    @patch("scripts.raycon_followup.post_raycon_job")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_dispatch_dry_run_does_not_call_post(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_post,
    ):
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = None

        dispatch_state: dict = {}
        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=True,
            alert_after=timedelta(minutes=60),
            dispatch_state=dispatch_state,
        )

        mock_post.assert_not_called()
        assert row.get("dispatch_skipped") == "dry_run"
        assert dispatch_state == {}

    def test_dispatch_helper_missing_required_field_returns_error(self):
        """_dispatch_raycon_job fails closed when site_id is missing."""
        site = _site()
        site["id"] = ""  # Missing required field.
        result = _dispatch_raycon_job(
            site,
            _block_plan(),
            "m1_folder_id",
            {},
            dry_run=False,
            redispatch_after=timedelta(minutes=30),
        )
        assert "dispatch_error" in result
        assert "site_id" in result["dispatch_error"]

    def test_dispatch_helper_missing_block_plan_id_returns_error(self):
        bp = _block_plan()
        bp["id"] = ""
        result = _dispatch_raycon_job(
            _site(),
            bp,
            "m1_folder_id",
            {},
            dry_run=False,
            redispatch_after=timedelta(minutes=30),
        )
        assert "dispatch_error" in result
        assert "file id" in result["dispatch_error"]


# ---------------------------------------------------------------------------
# Alert dedup (added in C4)
# ---------------------------------------------------------------------------


class TestFilterDedupAlerts:
    def test_first_alert_passes_through_and_state_updated(self):
        now = datetime(2026, 4, 30, 15, 0, tzinfo=timezone.utc)
        alerts = [{"site": "Alpha Keller", "alert": "no scenario after 1:00:00"}]

        fresh, new_state = _filter_dedup_alerts(alerts, {}, now=now)

        assert len(fresh) == 1
        assert fresh[0]["site"] == "Alpha Keller"
        assert new_state["Alpha Keller"] == now.isoformat()

    def test_recent_alert_is_suppressed(self):
        now = datetime(2026, 4, 30, 15, 0, tzinfo=timezone.utc)
        recent = (now - timedelta(hours=2)).isoformat()
        alerts = [{"site": "Alpha Keller", "alert": "stuck"}]

        fresh, new_state = _filter_dedup_alerts(
            alerts, {"Alpha Keller": recent}, now=now
        )

        assert fresh == []
        # State unchanged for suppressed sites.
        assert new_state["Alpha Keller"] == recent

    def test_old_alert_outside_window_passes_through(self):
        now = datetime(2026, 4, 30, 15, 0, tzinfo=timezone.utc)
        old = (now - timedelta(hours=25)).isoformat()
        alerts = [{"site": "Alpha Keller", "alert": "stuck"}]

        fresh, new_state = _filter_dedup_alerts(
            alerts, {"Alpha Keller": old}, now=now
        )

        assert len(fresh) == 1
        assert new_state["Alpha Keller"] == now.isoformat()


# ---------------------------------------------------------------------------
# Event-driven DD Report republish (Rec. 1)
# ---------------------------------------------------------------------------


class TestDDReportRepublish:
    """Verifies the new ``_republish_dd_report_if_present`` helper and its
    wiring inside ``_process_site`` after a successful RayCon Scenario Doc
    publish.
    """

    def _settings(self) -> MagicMock:
        s = MagicMock()
        s.google_chat_webhook_url = ""
        return s

    @patch("scripts.raycon_followup.process_site_pipeline")
    @patch("scripts.raycon_followup._find_existing_dd_report")
    def test_existing_dd_report_triggers_force_regenerate(
        self, mock_find_dd, mock_pipeline
    ):
        """Existing DD Report → ``process_site_pipeline(force_regenerate=True)``."""
        mock_find_dd.return_value = {
            "id": "dd1",
            "name": "Alpha Keller DD Report - 2026-05-01",
        }
        result_obj = MagicMock()
        result_obj.status = "report_created"
        result_obj.doc_url = "https://docs.google.com/document/d/dd1"
        mock_pipeline.return_value = result_obj

        gc = MagicMock()
        republish_state: dict = {}
        out = _republish_dd_report_if_present(
            gc,
            _site(),
            "rc_run_abc",
            settings=self._settings(),
            system_prompt="prompt",
            shared_cache={},
            republish_state=republish_state,
            dry_run=False,
        )

        assert out["dd_report_republish"] == "republished"
        assert out["raycon_run_id"] == "rc_run_abc"
        # State updated for dedup on the next cron tick.
        assert republish_state.get("site-123:rc_run_abc")
        mock_pipeline.assert_called_once()
        kwargs = mock_pipeline.call_args.kwargs
        assert kwargs.get("force_regenerate") is True

    @patch("scripts.raycon_followup.process_site_pipeline")
    @patch("scripts.raycon_followup._find_existing_dd_report")
    def test_no_existing_dd_report_is_noop(self, mock_find_dd, mock_pipeline):
        """No existing DD Report → no pipeline call, marker set to skipped."""
        mock_find_dd.return_value = None

        gc = MagicMock()
        out = _republish_dd_report_if_present(
            gc,
            _site(),
            "rc_run_abc",
            settings=self._settings(),
            system_prompt="prompt",
            shared_cache={},
            republish_state={},
            dry_run=False,
        )

        assert out["dd_report_republish"] == "skipped_no_existing_report"
        mock_pipeline.assert_not_called()

    @patch("scripts.raycon_followup.process_site_pipeline")
    @patch("scripts.raycon_followup._find_existing_dd_report")
    def test_already_deduped_run_is_noop(self, mock_find_dd, mock_pipeline):
        """Same (site_id, raycon_run_id) within force_after window → no-op."""
        mock_find_dd.return_value = {
            "id": "dd1",
            "name": "Alpha Keller DD Report - 2026-05-01",
        }
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        republish_state = {"site-123:rc_run_abc": recent}

        gc = MagicMock()
        out = _republish_dd_report_if_present(
            gc,
            _site(),
            "rc_run_abc",
            settings=self._settings(),
            system_prompt="prompt",
            shared_cache={},
            republish_state=republish_state,
            dry_run=False,
        )

        assert out["dd_report_republish"] == "deduped"
        mock_pipeline.assert_not_called()
        # State unchanged.
        assert republish_state["site-123:rc_run_abc"] == recent

    @patch("scripts.raycon_followup.process_site_pipeline")
    @patch("scripts.raycon_followup._find_existing_dd_report")
    def test_pipeline_raise_is_caught(self, mock_find_dd, mock_pipeline):
        """``process_site_pipeline`` exception → 'failed' marker, no crash."""
        mock_find_dd.return_value = {"id": "dd1", "name": "Alpha Keller DD Report"}
        mock_pipeline.side_effect = RuntimeError("Anthropic 500")

        gc = MagicMock()
        out = _republish_dd_report_if_present(
            gc,
            _site(),
            "rc_run_abc",
            settings=self._settings(),
            system_prompt="prompt",
            shared_cache={},
            republish_state={},
            dry_run=False,
        )

        assert out["dd_report_republish"] == "failed"
        assert "Anthropic 500" in out["reason"]

    @patch("scripts.raycon_followup.extract_timeline_confidence_from_record")
    @patch("scripts.raycon_followup.extract_school_feasibility_from_record")
    @patch("scripts.raycon_followup.process_site_pipeline")
    @patch("scripts.raycon_followup._find_existing_dd_report")
    def test_republish_forwards_p1_and_feasibility_fields(
        self,
        mock_find_dd,
        mock_pipeline,
        mock_feasibility,
        mock_timeline,
    ):
        """Regression for #82: republish must thread p1/feasibility/timeline/
        wrike_created_at into ``process_site_pipeline`` so the regenerated DD
        Report email still CC's the P1 owner and the dashboard publish keeps
        the W74/W81 ratings + Wrike createdDate.

        Covers two paths:
          1. all fields present on site_summary → all forwarded.
          2. fields missing on site_summary → forwarded as None (no crash).
        """
        mock_find_dd.return_value = {"id": "dd1", "name": "Alpha Keller DD Report"}
        result_obj = MagicMock()
        result_obj.status = "report_created"
        result_obj.doc_url = "https://docs.google.com/document/d/dd1"
        mock_pipeline.return_value = result_obj
        mock_feasibility.return_value = "high"
        mock_timeline.return_value = "medium"

        gc = MagicMock()
        full_site = {
            **_site(),
            "p1_assignee_email": "owner@example.com",
            "p1_assignee_name": "Alex Owner",
            "created_date": "2026-01-15T12:00:00Z",
            "custom_fields": [{"id": "f1", "value": "high"}],
        }
        _republish_dd_report_if_present(
            gc,
            full_site,
            "rc_run_abc",
            settings=self._settings(),
            system_prompt="prompt",
            shared_cache={},
            republish_state={},
            dry_run=False,
        )

        kwargs = mock_pipeline.call_args.kwargs
        assert kwargs.get("force_regenerate") is True
        assert kwargs.get("p1_email") == "owner@example.com"
        assert kwargs.get("p1_name") == "Alex Owner"
        assert kwargs.get("school_feasibility") == "high"
        assert kwargs.get("timeline_confidence") == "medium"
        assert kwargs.get("wrike_created_at") == "2026-01-15T12:00:00Z"

        # Graceful-default path: site_summary missing the optional fields.
        mock_pipeline.reset_mock()
        mock_feasibility.return_value = None
        mock_timeline.return_value = None
        _republish_dd_report_if_present(
            gc,
            _site(),  # no p1_*, no created_date, no custom_fields
            "rc_run_def",
            settings=self._settings(),
            system_prompt="prompt",
            shared_cache={},
            republish_state={},
            dry_run=False,
        )
        kwargs = mock_pipeline.call_args.kwargs
        assert kwargs.get("p1_email") is None
        assert kwargs.get("p1_name") is None
        assert kwargs.get("school_feasibility") is None
        assert kwargs.get("timeline_confidence") is None
        assert kwargs.get("wrike_created_at") is None

    @patch("scripts.raycon_followup.save_skill_report")
    @patch("scripts.raycon_followup._find_published_doc")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_skip_dd_republish_flag_suppresses_callback(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_find_doc,
        mock_save,
    ):
        """``skip_dd_republish=True`` → callback is never invoked even after
        a successful RayCon Scenario Doc publish.
        """
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = {
            "schema_version": "1.0",
            "status": "completed",
            "raycon_run_id": "rc_happy",
            "analysis": {
                "fastest_open": {"grand_total": 10, "timeline_weeks": 4},
                "max_capacity": {"grand_total": 20, "timeline_weeks": 8},
            },
            "validation": {"passed": True, "errors": []},
            "_drive_modified_time": "2026-05-05T20:00:00Z",
        }
        mock_find_doc.return_value = None

        async def _fake_save(**_kwargs):
            return {"status": "success", "doc_url": "https://docs/x"}

        mock_save.side_effect = _fake_save

        callback = MagicMock()
        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            skip_dd_republish=True,
            dd_republish_callback=callback,
        )

        assert row.get("published") is True
        callback.assert_not_called()
        # No republish marker leaks into the row.
        assert "dd_report_republish" not in row

    @patch("scripts.raycon_followup.save_skill_report")
    @patch("scripts.raycon_followup._find_published_doc")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_callback_invoked_after_successful_publish(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_find_doc,
        mock_save,
    ):
        """Successful RayCon Scenario Doc publish → callback fires once,
        result merged into the per-site row, raycon_run_id forwarded.
        """
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = {
            "schema_version": "1.0",
            "status": "completed",
            "raycon_run_id": "rc_happy_xyz",
            "analysis": {
                "fastest_open": {"grand_total": 10, "timeline_weeks": 4},
                "max_capacity": {"grand_total": 20, "timeline_weeks": 8},
            },
            "validation": {"passed": True, "errors": []},
            "_drive_modified_time": "2026-05-05T20:00:00Z",
        }
        mock_find_doc.return_value = None

        async def _fake_save(**_kwargs):
            return {"status": "success", "doc_url": "https://docs/x"}

        mock_save.side_effect = _fake_save

        callback = MagicMock(
            return_value={"dd_report_republish": "republished", "raycon_run_id": "rc_happy_xyz"}
        )
        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dd_republish_callback=callback,
        )

        assert row.get("published") is True
        assert row.get("dd_report_republish") == "republished"
        callback.assert_called_once()
        assert callback.call_args.kwargs["raycon_run_id"] == "rc_happy_xyz"

    @patch("scripts.raycon_followup.save_skill_report")
    @patch("scripts.raycon_followup._find_published_doc")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_callback_exception_does_not_break_publish(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_find_doc,
        mock_save,
    ):
        """Callback raising → row still reports published=True; failure
        marker captured. The RayCon Scenario Doc publish has already
        succeeded by the time we get here, and a republish failure must
        not undo that.
        """
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = {
            "schema_version": "1.0",
            "status": "completed",
            "raycon_run_id": "rc_happy",
            "analysis": {
                "fastest_open": {"grand_total": 10, "timeline_weeks": 4},
                "max_capacity": {"grand_total": 20, "timeline_weeks": 8},
            },
            "validation": {"passed": True, "errors": []},
            "_drive_modified_time": "2026-05-05T20:00:00Z",
        }
        mock_find_doc.return_value = None

        async def _fake_save(**_kwargs):
            return {"status": "success", "doc_url": "https://docs/x"}

        mock_save.side_effect = _fake_save

        callback = MagicMock(side_effect=RuntimeError("pipeline blew up"))
        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dd_republish_callback=callback,
        )

        assert row.get("published") is True
        assert row.get("dd_report_republish") == "failed"
        assert "pipeline blew up" in row.get("reason", "")


# ---------------------------------------------------------------------------
# Block Plan filename detection (PFP / Preliminary Floor Plan aliases)
# ---------------------------------------------------------------------------


class TestFilenameMatchesBlockPlan:
    """Verify that ``_filename_matches_block_plan`` recognizes all three
    partner-side aliases: \"Block Plan\", \"Preliminary Floor Plan(s)\", and
    \"PFP\". These are interchangeable terms for the same artifact.
    """

    def test_block_plan_phrase(self):
        from scripts.raycon_followup import _filename_matches_block_plan
        assert _filename_matches_block_plan("alpha keller block plan.pdf")

    def test_blockplan_concatenated(self):
        from scripts.raycon_followup import _filename_matches_block_plan
        assert _filename_matches_block_plan("alphakeller_blockplan.pdf")

    def test_block_plan_underscore(self):
        from scripts.raycon_followup import _filename_matches_block_plan
        assert _filename_matches_block_plan("keller_block_plan_v3.pdf")

    def test_preliminary_floor_plan(self):
        from scripts.raycon_followup import _filename_matches_block_plan
        assert _filename_matches_block_plan("alpha keller preliminary floor plan.pdf")

    def test_preliminary_floor_plans_plural(self):
        from scripts.raycon_followup import _filename_matches_block_plan
        assert _filename_matches_block_plan("preliminary floor plans - tampa.pdf")

    def test_pfp_word_boundary_match(self):
        from scripts.raycon_followup import _filename_matches_block_plan
        assert _filename_matches_block_plan("alpha keller pfp.pdf")

    def test_pfp_hyphenated_match(self):
        from scripts.raycon_followup import _filename_matches_block_plan
        assert _filename_matches_block_plan("alpha-keller-pfp.pdf")

    def test_pfp_substring_does_not_match(self):
        """Word boundary required: 'pfp' inside 'epfpro' must NOT match."""
        from scripts.raycon_followup import _filename_matches_block_plan
        assert not _filename_matches_block_plan("epfpro_brochure.pdf")

    def test_unrelated_filename_does_not_match(self):
        from scripts.raycon_followup import _filename_matches_block_plan
        assert not _filename_matches_block_plan("alpha keller sir.pdf")
