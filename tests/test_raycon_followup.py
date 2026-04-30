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
    _filter_dedup_alerts,
    _process_site,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _site(title: str = "Alpha Keller") -> dict:
    return {
        "title": title,
        "drive_folder_url": "https://drive.google.com/drive/folders/abc123",
        "address": "123 Main St",
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
        """Block Plan landed >alert-after ago AND no scenario JSON → alert."""
        mock_resolve.return_value = ("m1_folder_id", "M1")
        mock_find_bp.return_value = _block_plan(modified_minutes_ago=120)
        mock_read_scenario.return_value = None  # No scenario yet.

        gc = MagicMock()
        row = _process_site(
            gc,
            _site(),
            dry_run=False,
            alert_after=timedelta(minutes=60),
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
