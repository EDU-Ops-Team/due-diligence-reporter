"""Tests for scripts/raycon_followup.py — the 5-minute cadence script that
publishes RayCon scenario reports and alerts on stuck sites.

Exercises the per-site processing logic in isolation using mocks for the
Google client, Drive folder scanning, and ``save_skill_report``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts.raycon_followup import (
    _dispatch_raycon_job,
    _filter_dedup_alerts,
    _find_block_plan,
    _find_published_doc,
    _fresh_dedup_alerts,
    _load_site_summaries,
    _mark_notified_alerts,
    _notify_raycon_followup_rows,
    _process_site,
    _republish_dd_report_if_present,
    _site_id_matches,
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
    when = datetime.now(UTC) - timedelta(minutes=modified_minutes_ago)
    return {
        "id": "bp_file_1",
        "name": "Block Plan current.pdf",
        "modifiedTime": when.isoformat().replace("+00:00", "Z"),
        "mimeType": "application/pdf",
    }


def _scenario_payload(json_modified: str | None = None) -> dict:
    return {
        "site_id": "S1",
        "scenarios": [{"name": "fastest_open"}],
        "_drive_modified_time": json_modified
        or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


# ---------------------------------------------------------------------------
# Site inventory loading
# ---------------------------------------------------------------------------


@patch("scripts.raycon_followup.list_rhodes_site_records")
def test_load_site_summaries_prefers_rhodes_records_with_site_address(mock_records):
    mock_records.return_value = [
        {
            "id": "rhodes-site-1",
            "site_id": "rhodes-site-1",
            "title": "Alpha Keller",
            "address": "123 Main St, Keller, TX 76248",
            "drive_folder_id": "drive-root-1",
            "drive_folder_url": "https://drive.google.com/drive/folders/drive-root-1",
            "p1_assignee_name": "Devin Bates",
            "p1_assignee_email": "devin@example.com",
            "p1_assignee_user_id": "user-1",
        }
    ]

    gc = MagicMock()
    summaries = _load_site_summaries(gc, "all-locations-root")

    assert summaries == [
        {
            "id": "rhodes-site-1",
            "site_id": "rhodes-site-1",
            "title": "Alpha Keller",
            "name": "Alpha Keller",
            "slug": "",
            "address": "123 Main St, Keller, TX 76248",
            "drive_folder_id": "drive-root-1",
            "drive_folder_url": "https://drive.google.com/drive/folders/drive-root-1",
            "p1_assignee_name": "Devin Bates",
            "p1_assignee_email": "devin@example.com",
            "p1_assignee_user_id": "user-1",
            "created_date": "",
            "site_metadata_source": "rhodes",
        }
    ]
    gc.list_subfolders.assert_not_called()


@patch("scripts.raycon_followup.list_rhodes_site_records")
def test_load_site_summaries_uses_direct_rhodes_lookup_for_callback_site_id(
    mock_records,
):
    mock_records.return_value = [
        {
            "id": "rhodes-site-1",
            "site_id": "rhodes-site-1",
            "title": "Alpha Keller",
            "address": "123 Main St, Keller, TX 76248",
            "drive_folder_id": "drive-root-1",
            "drive_folder_url": "https://drive.google.com/drive/folders/drive-root-1",
        }
    ]

    gc = MagicMock()
    summaries = _load_site_summaries(
        gc,
        "all-locations-root",
        target_site_id="rhodes-site-1",
    )

    assert summaries[0]["id"] == "rhodes-site-1"
    mock_records.assert_called_once_with(site_ids=["rhodes-site-1"])
    gc.list_subfolders.assert_not_called()


@patch("scripts.raycon_followup.list_rhodes_site_records")
def test_load_site_summaries_falls_back_to_full_inventory_for_drive_folder_id(
    mock_records,
):
    mock_records.side_effect = [
        [],
        [
            {
                "id": "rhodes-site-1",
                "site_id": "rhodes-site-1",
                "title": "Alpha Keller",
                "address": "123 Main St, Keller, TX 76248",
                "drive_folder_id": "drive-root-1",
                "drive_folder_url": (
                    "https://drive.google.com/drive/folders/drive-root-1"
                ),
            }
        ],
    ]

    gc = MagicMock()
    summaries = _load_site_summaries(
        gc,
        "all-locations-root",
        target_site_id="drive-root-1",
    )

    assert summaries[0]["drive_folder_id"] == "drive-root-1"
    assert mock_records.call_args_list[0].kwargs == {"site_ids": ["drive-root-1"]}
    assert mock_records.call_args_list[1].kwargs == {}
    gc.list_subfolders.assert_not_called()


def test_site_id_matches_rhodes_site_id_or_drive_folder_id():
    summary = {
        "id": "rhodes-site-1",
        "site_id": "rhodes-site-1",
        "drive_folder_id": "drive-root-1",
        "drive_folder_url": "https://drive.google.com/drive/folders/drive-root-1",
    }

    assert _site_id_matches(summary, "rhodes-site-1") is True
    assert _site_id_matches(summary, "drive-root-1") is True
    assert _site_id_matches(summary, "other") is False


def test_m1_file_helpers_use_prelisted_files_without_drive_relist():
    gc = MagicMock()
    files = [
        {
            "id": "older-bp",
            "name": "Block Plan old.pdf",
            "modifiedTime": "2026-05-27T08:00:00Z",
        },
        {
            "id": "newer-bp",
            "name": "Block Plan current.pdf",
            "modifiedTime": "2026-05-27T09:00:00Z",
        },
        {
            "id": "published-doc",
            "name": "RayCon Scenario Assessment - Alpha Keller",
            "modifiedTime": "2026-05-27T10:00:00Z",
        },
    ]

    block_plan = _find_block_plan(gc, "m1-id", m1_files=files)
    published = _find_published_doc(
        gc,
        "m1-id",
        "Alpha Keller",
        m1_files=files,
    )

    assert block_plan is not None
    assert block_plan["id"] == "newer-bp"
    assert published is not None
    assert published["id"] == "published-doc"
    gc.list_files_in_folder.assert_not_called()


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
        recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
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
    that would render an empty/zero-cost scenario as
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
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_failed_status_alerts_even_without_direct_m1_block_plan(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_save,
    ):
        """A failed scenario JSON is actionable even when RayCon used a nested plan."""
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = None
        mock_read_scenario.return_value = {
            "schema_version": "1.0",
            "status": "failed",
            "raycon_run_id": "rc_nested_plan",
            "validation": {
                "passed": False,
                "errors": ["max capacity was not defensible"],
            },
            "_drive_modified_time": "2026-05-27T16:12:24Z",
        }

        row = _process_site(
            MagicMock(),
            _site("Alpha Tulsa 6940 S Utica Ave"),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dispatch_state={},
            redispatch_after=timedelta(minutes=30),
        )

        assert "raycon run failed" in row["alert"]
        assert "max capacity was not defensible" in row["alert"]
        assert row["raycon_run_id"] == "rc_nested_plan"
        assert row["alert_dedup_key"].endswith(":owner_note_v2")
        assert "dispatched" not in row
        mock_save.assert_not_called()

    @patch("scripts.raycon_followup.post_raycon_job")
    @patch("scripts.raycon_followup.save_skill_report")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_failed_status_dispatches_recovery_when_state_is_available(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_save,
        mock_post,
    ):
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = {
            "schema_version": "1.0",
            "status": "failed",
            "raycon_run_id": "rc_old",
            "validation": {
                "passed": False,
                "errors": [
                    "Real estate spreadsheet did not resolve a usable microschool tier"
                ],
            },
            "_drive_modified_time": "2026-05-05T21:18:26Z",
        }
        mock_post.return_value = {
            "status": "queued",
            "job_id": "job-retry",
            "raycon_run_id": None,
            "idempotency_key": "block_plan|site-123|bp_file_1",
            "retry_after_seconds": 30,
            "status_url": "https://raycon.test/v1/jobs/status/job-retry?token=opaque",
            "cached": False,
        }

        dispatch_state: dict = {}
        row = _process_site(
            MagicMock(),
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dispatch_state=dispatch_state,
            redispatch_after=timedelta(minutes=30),
        )

        assert row["dispatched"] is True
        assert row["dispatch_reason"] == "failed_scenario_retry"
        assert "raycon run failed" in row["alert"]
        assert "microschool tier" in row["previous_failure"]
        assert row["job_id"] == "job-retry"
        assert row["raycon_run_id"] == "rc_old"
        assert "failed_scenario:rc_old" in row["alert_dedup_key"]
        assert dispatch_state["bp_file_1"]["job_id"] == "job-retry"
        mock_post.assert_called_once()
        mock_save.assert_not_called()

    @patch("scripts.raycon_followup.post_raycon_job")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_failed_status_respects_recent_recovery_dispatch(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_post,
    ):
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = {
            "schema_version": "1.0",
            "status": "failed",
            "raycon_run_id": "rc_old",
            "validation": {"passed": False, "errors": ["no_address_match"]},
            "_drive_modified_time": "2026-05-05T21:18:26Z",
        }
        recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        dispatch_state = {
            "bp_file_1": {
                "last_dispatch": recent,
                "count": 1,
                "site": "Alpha Keller",
                "raycon_run_id": "rc_retry",
            }
        }

        row = _process_site(
            MagicMock(),
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dispatch_state=dispatch_state,
            redispatch_after=timedelta(minutes=30),
        )

        assert "raycon run failed" in row["alert"]
        assert row["dispatch_skipped"] == "recently dispatched"
        mock_post.assert_not_called()

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
            "status": "queued",
            "job_id": "job-abc",
            "raycon_run_id": None,
            "idempotency_key": "block_plan|site-123|bp_file_1",
            "retry_after_seconds": 30,
            "status_url": "https://raycon.test/v1/jobs/status/job-abc?token=opaque",
            "cached": False,
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
        assert row.get("job_id") == "job-abc"
        assert row.get("raycon_run_id") == ""
        assert row.get("status") == "queued"
        assert row.get("status_url_present") is True
        assert row.get("block_plan_file_id") == "bp_file_1"
        # State updated for future runs.
        assert "bp_file_1" in dispatch_state
        assert dispatch_state["bp_file_1"]["count"] == 1
        assert dispatch_state["bp_file_1"]["job_id"] == "job-abc"
        assert dispatch_state["bp_file_1"]["raycon_run_id"] is None
        assert dispatch_state["bp_file_1"]["status_url"].endswith("token=opaque")

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
    @patch("scripts.raycon_followup.get_raycon_job_status")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_existing_status_url_is_polled_before_redispatch(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_status,
        mock_post,
    ):
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = None
        mock_status.return_value = {"status": "running", "job_id": "job-abc"}
        dispatch_state = {
            "bp_file_1": {
                "status_url": "https://raycon.test/v1/jobs/status/job-abc?token=opaque"
            }
        }

        row = _process_site(
            MagicMock(),
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dispatch_state=dispatch_state,
            redispatch_after=timedelta(minutes=30),
        )

        assert row["skipped"] == "raycon job running"
        assert dispatch_state["bp_file_1"]["status"] == "running"
        assert dispatch_state["bp_file_1"]["job_id"] == "job-abc"
        mock_status.assert_called_once()
        mock_post.assert_not_called()

    @patch("scripts.raycon_followup.post_raycon_job")
    @patch("scripts.raycon_followup.get_raycon_job_status")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_terminal_validation_failed_status_alerts_without_redispatch(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_status,
        mock_post,
    ):
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = None
        mock_status.return_value = {
            "status": "validation_failed",
            "job_id": "job-abc",
            "validation": {"passed": False},
        }
        dispatch_state = {
            "bp_file_1": {
                "status_url": "https://raycon.test/v1/jobs/status/job-abc?token=opaque"
            }
        }

        row = _process_site(
            MagicMock(),
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dispatch_state=dispatch_state,
            redispatch_after=timedelta(minutes=30),
        )

        assert row["alert"] == "raycon job terminal status: validation_failed"
        assert dispatch_state["bp_file_1"]["status"] == "validation_failed"
        mock_post.assert_not_called()

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
    @patch("scripts.raycon_followup._find_published_doc")
    @patch("scripts.raycon_followup.save_skill_report")
    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_existing_scenario_does_not_publish_without_rhodes_address(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        mock_save,
        mock_find_doc,
        mock_post,
    ):
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.return_value = _scenario_payload()
        mock_find_doc.return_value = None

        row = _process_site(
            MagicMock(),
            {**_site(), "address": ""},
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dispatch_state={},
        )

        assert "missing Rhodes site identity/address" in row["error"]
        mock_save.assert_not_called()
        mock_post.assert_not_called()

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

        recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
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

        old = (datetime.now(UTC) - timedelta(minutes=45)).isoformat()
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
        now = datetime(2026, 4, 30, 15, 0, tzinfo=UTC)
        alerts = [{"site": "Alpha Keller", "alert": "no scenario after 1:00:00"}]

        fresh, new_state = _filter_dedup_alerts(alerts, {}, now=now)

        assert len(fresh) == 1
        assert fresh[0]["site"] == "Alpha Keller"
        assert new_state["Alpha Keller"] == now.isoformat()

    def test_recent_alert_is_suppressed(self):
        now = datetime(2026, 4, 30, 15, 0, tzinfo=UTC)
        recent = (now - timedelta(hours=2)).isoformat()
        alerts = [{"site": "Alpha Keller", "alert": "stuck"}]

        fresh, new_state = _filter_dedup_alerts(
            alerts, {"Alpha Keller": recent}, now=now
        )

        assert fresh == []
        # State unchanged for suppressed sites.
        assert new_state["Alpha Keller"] == recent

    def test_old_alert_outside_window_passes_through(self):
        now = datetime(2026, 4, 30, 15, 0, tzinfo=UTC)
        old = (now - timedelta(hours=25)).isoformat()
        alerts = [{"site": "Alpha Keller", "alert": "stuck"}]

        fresh, new_state = _filter_dedup_alerts(
            alerts, {"Alpha Keller": old}, now=now
        )

        assert len(fresh) == 1
        assert new_state["Alpha Keller"] == now.isoformat()


class TestNotificationDedupState:
    def test_fresh_dedup_alerts_does_not_advance_state_before_notification(self):
        now = datetime(2026, 5, 28, 13, 0, tzinfo=UTC)
        alerts = [{"site": "Alpha Keller", "alert": "raycon run failed"}]
        state: dict[str, str] = {}

        fresh = _fresh_dedup_alerts(alerts, state, now=now)

        assert fresh == alerts
        assert state == {}

    def test_mark_notified_alerts_only_records_successful_owner_or_chat_delivery(self):
        now = datetime(2026, 5, 28, 13, 0, tzinfo=UTC)
        rows = [
            {
                "site": "Owner Mentioned",
                "raycon_followup_event": {"owner_notification": "mentioned"},
            },
            {
                "site": "Chat Posted",
                "raycon_followup_event": {
                    "owner_notification": "none",
                    "google_chat": {"status": "posted"},
                },
            },
            {
                "site": "Not Delivered",
                "raycon_followup_event": {
                    "owner_notification": "none",
                    "google_chat": {"status": "skipped"},
                },
            },
        ]

        new_state = _mark_notified_alerts(rows, {}, now=now)

        assert new_state["Owner Mentioned"] == now.isoformat()
        assert new_state["Chat Posted"] == now.isoformat()
        assert "Not Delivered" not in new_state


class TestRayConFollowupEventNotification:
    @patch("scripts.raycon_followup._post_chat")
    @patch("scripts.raycon_followup.add_rhodes_site_note")
    def test_owner_mentioned_in_rhodes_skips_chat_fallback(
        self,
        mock_add_note,
        mock_post_chat,
    ):
        mock_add_note.return_value = {
            "status": "created",
            "reason": "ok",
            "rhodes_note_id": "note-1",
            "owner_user_id": "user-1",
            "owner_notification": "mentioned",
        }
        rows = [
            {
                "site": "Alpha Keller",
                "site_id": "SITE1",
                "alert": "no raycon_scenario.json after 1:00:00",
                "drive_folder_url": "https://drive.google.com/drive/folders/abc123",
                "block_plan_file_id": "block-plan-1",
                "p1_assignee_user_id": "user-1",
                "p1_assignee_email": "owner@example.com",
            }
        ]

        _notify_raycon_followup_rows(
            rows,
            SimpleNamespace(google_chat_webhook_url="https://chat.example/hook"),
            run_id="raycon-followup-20260527213000",
            alert_type="stuck_site",
            message_field="alert",
            heading="RayCon scenario follow-up: stuck sites",
        )

        mock_add_note.assert_called_once()
        assert mock_add_note.call_args.kwargs["site_id"] == "SITE1"
        assert mock_add_note.call_args.kwargs["owner_user_id"] == "user-1"
        assert "Kind: raycon_followup_alert" in mock_add_note.call_args.kwargs["body"]
        mock_post_chat.assert_not_called()
        assert rows[0]["raycon_followup_event"]["rhodes_note_id"] == "note-1"
        assert rows[0]["raycon_followup_event"]["owner_notification"] == "mentioned"

    @patch("scripts.raycon_followup._post_chat")
    @patch("scripts.raycon_followup.add_rhodes_site_note")
    def test_missing_owner_posts_same_event_to_chat(
        self,
        mock_add_note,
        mock_post_chat,
    ):
        mock_add_note.return_value = {
            "status": "created",
            "reason": "ok",
            "rhodes_note_id": "note-2",
            "owner_notification": "none",
        }
        rows = [
            {
                "site": "Alpha Keller",
                "site_id": "SITE1",
                "error": "raycon dispatch: RayCon 503",
                "drive_folder_url": "https://drive.google.com/drive/folders/abc123",
            }
        ]

        _notify_raycon_followup_rows(
            rows,
            SimpleNamespace(google_chat_webhook_url="https://chat.example/hook"),
            run_id="raycon-followup-20260527213000",
            alert_type="error",
            message_field="error",
            heading="RayCon scenario follow-up: errors",
        )

        mock_add_note.assert_called_once()
        mock_post_chat.assert_called_once()
        chat_body = mock_post_chat.call_args.args[1]
        assert "RayCon scenario follow-up: errors" in chat_body
        assert "Kind: raycon_followup_alert" in chat_body
        assert "Mutation status: error" in chat_body
        assert "Message: raycon dispatch: RayCon 503" in chat_body
        assert rows[0]["raycon_followup_event"]["google_chat"] == {
            "status": "posted"
        }


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
        assert republish_state.get("site-123:raycon_scenario:rc_run_abc")
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
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        republish_state = {"site-123:raycon_scenario:rc_run_abc": recent}

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
        assert republish_state["site-123:raycon_scenario:rc_run_abc"] == recent

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

    @patch("scripts.raycon_followup.process_site_pipeline")
    @patch("scripts.raycon_followup._find_existing_dd_report")
    def test_republish_forwards_p1_and_site_created_at(
        self,
        mock_find_dd,
        mock_pipeline,
    ):
        """Republish threads P1 and source-created metadata into the pipeline.

        Covers two paths:
          1. all fields present on site_summary → all forwarded.
          2. fields missing on site_summary → forwarded as None (no crash).
        """
        mock_find_dd.return_value = {"id": "dd1", "name": "Alpha Keller DD Report"}
        result_obj = MagicMock()
        result_obj.status = "report_created"
        result_obj.doc_url = "https://docs.google.com/document/d/dd1"
        mock_pipeline.return_value = result_obj

        gc = MagicMock()
        full_site = {
            **_site(),
            "p1_assignee_email": "owner@example.com",
            "p1_assignee_name": "Alex Owner",
            "created_date": "2026-01-15T12:00:00Z",
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
        assert "school_feasibility" not in kwargs
        assert "timeline_confidence" not in kwargs
        assert kwargs.get("site_created_at") == "2026-01-15T12:00:00Z"

        # Graceful-default path: site_summary missing the optional fields.
        mock_pipeline.reset_mock()
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
        assert "school_feasibility" not in kwargs
        assert "timeline_confidence" not in kwargs
        assert kwargs.get("site_created_at") is None

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


# ---------------------------------------------------------------------------
# Schema-fail handling (Rec. 6 — raise must not crash the cron)
# ---------------------------------------------------------------------------


class TestSchemaFailHandling:
    """Verify ``_process_site`` handles ``RayConSchemaError`` gracefully.

    ``read_raycon_scenario_from_m1`` raises ``RayConSchemaError`` when the
    JSON is present but malformed (vs returning ``None`` for the normal
    "still polling" case). The caller must surface this as an error row
    so the end-of-run errors batch posts a Google Chat alert — without
    crashing the 5-minute cron.
    """

    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_schema_error_yields_error_row_not_crash(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
    ):
        from due_diligence_reporter.raycon_client import RayConSchemaError

        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.side_effect = RayConSchemaError(
            "Unsupported RayCon schema_version '9.9' in folder m1_folder_id"
        )

        gc = MagicMock()
        # No try/except in the test — the cron itself must not crash.
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
            dispatch_state={},
        )

        assert row["site"] == "Alpha Keller"
        assert "error" in row
        assert "schema" in row["error"].lower()
        assert "9.9" in row["error"]
        # No crash, no alert row (errors are surfaced via the errors batch).
        assert "alert" not in row

    @patch("scripts.raycon_followup.read_raycon_scenario_from_m1")
    @patch("scripts.raycon_followup._find_block_plan")
    @patch("scripts.raycon_followup._resolve_m1_folder")
    def test_schema_error_logged_at_error_level(
        self,
        mock_resolve,
        mock_find_bp,
        mock_read_scenario,
        caplog,
    ):
        import logging

        from due_diligence_reporter.raycon_client import RayConSchemaError

        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=5)
        mock_read_scenario.side_effect = RayConSchemaError(
            "raycon_scenario.json in folder m1_folder_id is not valid JSON"
        )

        with caplog.at_level(logging.ERROR, logger="raycon_followup"):
            _process_site(
                MagicMock(),
                _site(),
                dry_run=False,
                alert_after=timedelta(minutes=60),
                dispatch_state={},
            )

        assert any(
            "not valid JSON" in record.getMessage() for record in caplog.records
        )


# ---------------------------------------------------------------------------
# RayCon callback receiver — `main()` entry point (Rec. 2)
# ---------------------------------------------------------------------------


class TestCallbackReceiverScoping:
    """Verify the workflow_dispatch callback path scopes to a single site
    and falls back cleanly when site_id is unknown or absent.

    Tests target ``main()`` with the integration boundaries (Drive,
    Google, _process_site) mocked. The point is to prove the wiring —
    parsing the new flags, narrowing the site list, and exiting cleanly
    on an unknown id — not to re-test what `_process_site` already does.
    """

    def _patch_integration_points(self, summaries):
        """Return patches that stand in for site inventory + Google.

        Callers use this with ``contextlib.ExitStack`` to keep test bodies
        short. ``summaries`` is the list ``main()`` will iterate.
        """
        from unittest.mock import patch as _patch

        fake_gc = MagicMock()

        return {
            "get_settings": _patch("scripts.raycon_followup.get_settings"),
            "google_client": _patch(
                "scripts.raycon_followup.GoogleClient.from_oauth_config",
                return_value=fake_gc,
            ),
            "load_site_summaries": _patch(
                "scripts.raycon_followup._load_site_summaries",
                return_value=summaries,
            ),
            "process_site": _patch(
                "scripts.raycon_followup._process_site",
                return_value={"site": "stub", "skipped": "test"},
            ),
            "save_dispatch": _patch("scripts.raycon_followup._save_dispatch_state"),
            "save_republish": _patch("scripts.raycon_followup._save_republish_state"),
            "load_dispatch": _patch(
                "scripts.raycon_followup._load_dispatch_state", return_value={}
            ),
            "load_republish": _patch(
                "scripts.raycon_followup._load_republish_state", return_value={}
            ),
        }

    def _run_main(self, argv, summaries):
        from contextlib import ExitStack

        from scripts.raycon_followup import main

        patches = self._patch_integration_points(summaries)
        with ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            rc = main(argv)
        return rc, mocks

    def test_site_id_scopes_run_to_one_site(self):
        """--site-id X causes _process_site to be called exactly once,
        with the site whose id == X."""
        summaries = [
            {"id": "site-A", "title": "Alpha", "drive_folder_url": "https://x/A"},
            {"id": "site-B", "title": "Bravo", "drive_folder_url": "https://x/B"},
            {"id": "site-C", "title": "Charlie", "drive_folder_url": "https://x/C"},
        ]
        rc, mocks = self._run_main(
            ["--site-id", "site-B", "--run-id", "r1", "--status", "succeeded"],
            summaries,
        )
        assert rc == 0
        assert mocks["process_site"].call_count == 1
        # _process_site is called positionally: (gc, site_summary, **kw)
        called_summary = mocks["process_site"].call_args.args[1]
        assert called_summary["id"] == "site-B"
        assert called_summary["title"] == "Bravo"

    def test_missing_site_id_falls_back_to_full_sweep(self):
        """Manual / cron path: no --site-id → every site is processed."""
        summaries = [
            {"id": "site-A", "title": "Alpha", "drive_folder_url": "https://x/A"},
            {"id": "site-B", "title": "Bravo", "drive_folder_url": "https://x/B"},
            {"id": "site-C", "title": "Charlie", "drive_folder_url": "https://x/C"},
        ]
        rc, mocks = self._run_main([], summaries)
        assert rc == 0
        assert mocks["process_site"].call_count == 3

    def test_unknown_site_id_exits_zero_with_warning(self, caplog):
        """Unknown site_id → log warning, return 0, do not call _process_site.

        Cron will catch the run on its next tick — same fallback shape
        the rest of the system uses.
        """
        import logging

        summaries = [
            {"id": "site-A", "title": "Alpha", "drive_folder_url": "https://x/A"},
        ]
        with caplog.at_level(logging.WARNING, logger="raycon_followup"):
            rc, mocks = self._run_main(
                ["--site-id", "site-DOES-NOT-EXIST"], summaries
            )
        assert rc == 0
        assert mocks["process_site"].call_count == 0
        assert any(
            "site-DOES-NOT-EXIST" in record.getMessage()
            for record in caplog.records
        )

    def test_run_id_and_status_logged_for_observability(self, caplog):
        """run_id and status are observability-only but must be logged."""
        import logging

        summaries = [
            {"id": "site-A", "title": "Alpha", "drive_folder_url": "https://x/A"},
        ]
        with caplog.at_level(logging.INFO, logger="raycon_followup"):
            rc, _ = self._run_main(
                [
                    "--site-id", "site-A",
                    "--run-id", "raycon-run-deadbeef",
                    "--status", "succeeded",
                ],
                summaries,
            )
        assert rc == 0
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "raycon-run-deadbeef" in joined
        assert "succeeded" in joined


class TestCallbackIdempotencyWiring:
    """The receiver path leans on the shared dd_republish helper for
    idempotency on (site_id, reason, content_fingerprint). This test is a
    thin wrapper that exercises the receiver-flavored
    ``_republish_dd_report_if_present`` twice with the same run_id +
    _drive_modified_time and asserts the underlying pipeline ran exactly
    once — proving PR #88/#89's dedup still holds when the trigger is a
    callback rather than the cron.
    """

    @patch("scripts.raycon_followup.process_site_pipeline")
    @patch("scripts.raycon_followup._find_existing_dd_report")
    def test_two_callback_invocations_same_run_id_dedup(
        self, mock_find_dd, mock_pipeline
    ):
        from scripts.raycon_followup import _republish_dd_report_if_present

        # An existing DD Report exists so the helper would otherwise
        # republish; we want to prove dedup blocks the second call.
        mock_find_dd.return_value = {"id": "doc-1", "name": "Alpha DD Report"}
        result_obj = MagicMock()
        result_obj.status = "report_created"
        result_obj.doc_url = "https://docs.google.com/document/d/doc-1"
        mock_pipeline.return_value = result_obj

        site_summary = {
            "id": "site-1",
            "title": "Alpha",
            "drive_folder_url": "https://drive.google.com/drive/folders/abc123",
            "address": "123 Main St",
        }
        republish_state: dict[str, str] = {}
        gc = MagicMock()

        # Settings stub — the helper just passes it through to the
        # pipeline runner, which we've mocked.
        settings = MagicMock()

        common = {
            "settings": settings,
            "system_prompt": "prompt",
            "shared_cache": {},
            "republish_state": republish_state,
            "dry_run": False,
            "drive_modified_time": "2026-05-07T14:42:00Z",
        }

        first = _republish_dd_report_if_present(
            gc, site_summary, "run-aaaa", **common
        )
        second = _republish_dd_report_if_present(
            gc, site_summary, "run-aaaa", **common
        )

        assert first["dd_report_republish"] == "republished"
        assert second["dd_report_republish"] == "deduped"
        assert mock_pipeline.call_count == 1
