"""Tests for the shared event-driven DD Report republish helper.

Covers Recommendation 3 from
``docs/event-driven-ddr-recommendations.md``: every authoritative-doc
arrival fires a "republish if needed" check. Tests assert that:

* Vendor SIR / Building Inspection arrival on a site WITH an existing
  report → republish fires.
* Vendor SIR / Building Inspection arrival on a site WITHOUT an
  existing report → no-op (the daily/inbox first-gen path handles it).
* Vendor SIR / Building Inspection arrival where the fingerprint
  matches the prior trace → skip (no diff means no republish; cost
  guard).
* RayCon arrival (the original Rec. 1 hook) continues to behave
  identically after the refactor.
* Idempotence: two rapid calls with the same fingerprint → exactly
  one republish.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from due_diligence_reporter.dd_republish import (
    DD_REPUBLISH_FORCE_AFTER,
    REASON_BUILDING_INSPECTION,
    REASON_RAYCON,
    REASON_VENDOR_SIR,
    RepublishOutcome,
    maybe_republish_dd_report,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _site(**overrides) -> dict:
    base = {
        "id": "site-123",
        "title": "Alpha Keller",
        "drive_folder_url": "https://drive.google.com/drive/folders/abc123",
        "address": "123 Main St",
    }
    base.update(overrides)
    return base


def _existing_dd_report() -> dict:
    return {
        "id": "dd1",
        "name": "Alpha Keller DD Report - 2026-05-01",
        "modifiedTime": "2026-05-01T00:00:00Z",
    }


def _pipeline_runner_factory(status: str = "report_created", doc_url: str = "https://docs/x"):
    """Return a MagicMock substitute for ``process_site_pipeline``."""
    result = MagicMock()
    result.status = status
    result.doc_url = doc_url
    return MagicMock(return_value=result)


def _call_helper(
    *,
    site_summary=None,
    reason=REASON_VENDOR_SIR,
    fingerprint="file-1:2026-05-05T10:00:00Z",
    state=None,
    finder=None,
    runner=None,
    dry_run=False,
    force=False,
    now=None,
):
    """Driver helper to keep the test signatures terse."""
    return maybe_republish_dd_report(
        MagicMock(),
        site_summary=site_summary or _site(),
        reason=reason,
        content_fingerprint=fingerprint,
        settings=MagicMock(),
        system_prompt="prompt",
        shared_cache={},
        republish_state=state if state is not None else {},
        dry_run=dry_run,
        force=force,
        now=now,
        existing_report_finder=finder
        or MagicMock(return_value=_existing_dd_report()),
        pipeline_runner=runner or _pipeline_runner_factory(),
    )


# ---------------------------------------------------------------------------
# Vendor SIR arrival
# ---------------------------------------------------------------------------


class TestVendorSIRArrival:
    def test_existing_report_fires_republish(self):
        """Vendor SIR arrival on a site WITH an existing report → republish."""
        runner = _pipeline_runner_factory()
        state: dict = {}
        outcome = _call_helper(
            reason=REASON_VENDOR_SIR,
            fingerprint="sir-file-1:2026-05-05T10:00:00Z",
            state=state,
            runner=runner,
        )
        assert outcome.decision == "republish"
        assert outcome.reason == REASON_VENDOR_SIR
        # State updated for dedup on subsequent calls.
        assert (
            "site-123:vendor_sir:sir-file-1:2026-05-05T10:00:00Z" in state
        )
        runner.assert_called_once()
        assert runner.call_args.kwargs["force_regenerate"] is True

    def test_no_existing_report_is_noop(self):
        """No prior DD Report → first-gen path handles it; we no-op."""
        runner = _pipeline_runner_factory()
        outcome = _call_helper(
            reason=REASON_VENDOR_SIR,
            finder=MagicMock(return_value=None),
            runner=runner,
        )
        assert outcome.decision == "skip_no_prior_report"
        runner.assert_not_called()

    def test_same_fingerprint_inside_force_after_skips(self):
        """Same SIR fingerprint repeated within 12h → no diff, no republish."""
        runner = _pipeline_runner_factory()
        recent = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        state = {
            "site-123:vendor_sir:sir-file-1:2026-05-05T10:00:00Z": recent,
        }
        outcome = _call_helper(
            reason=REASON_VENDOR_SIR,
            fingerprint="sir-file-1:2026-05-05T10:00:00Z",
            state=state,
            runner=runner,
        )
        assert outcome.decision == "skip_no_diff"
        runner.assert_not_called()
        # State unchanged.
        assert state[
            "site-123:vendor_sir:sir-file-1:2026-05-05T10:00:00Z"
        ] == recent

    def test_new_fingerprint_after_old_one_fires(self):
        """A re-uploaded SIR (new modifiedTime) → fresh fingerprint → republish."""
        runner = _pipeline_runner_factory()
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        state = {
            "site-123:vendor_sir:sir-file-1:2026-05-05T10:00:00Z": old,
        }
        outcome = _call_helper(
            reason=REASON_VENDOR_SIR,
            fingerprint="sir-file-1:2026-05-06T10:00:00Z",  # new modifiedTime
            state=state,
            runner=runner,
        )
        assert outcome.decision == "republish"
        runner.assert_called_once()


# ---------------------------------------------------------------------------
# Building Inspection arrival
# ---------------------------------------------------------------------------


class TestBuildingInspectionArrival:
    def test_existing_report_fires_republish(self):
        runner = _pipeline_runner_factory()
        outcome = _call_helper(
            reason=REASON_BUILDING_INSPECTION,
            fingerprint="bi-file-1:2026-05-05T10:00:00Z",
            runner=runner,
        )
        assert outcome.decision == "republish"
        assert outcome.reason == REASON_BUILDING_INSPECTION
        runner.assert_called_once()
        assert runner.call_args.kwargs["force_regenerate"] is True

    def test_no_existing_report_is_noop(self):
        runner = _pipeline_runner_factory()
        outcome = _call_helper(
            reason=REASON_BUILDING_INSPECTION,
            finder=MagicMock(return_value=None),
            runner=runner,
        )
        assert outcome.decision == "skip_no_prior_report"
        runner.assert_not_called()

    def test_same_fingerprint_skips(self):
        runner = _pipeline_runner_factory()
        recent = (
            datetime.now(timezone.utc) - timedelta(minutes=15)
        ).isoformat()
        state = {
            "site-123:building_inspection:bi-file-1:2026-05-05T10:00:00Z": recent,
        }
        outcome = _call_helper(
            reason=REASON_BUILDING_INSPECTION,
            fingerprint="bi-file-1:2026-05-05T10:00:00Z",
            state=state,
            runner=runner,
        )
        assert outcome.decision == "skip_no_diff"
        runner.assert_not_called()


# ---------------------------------------------------------------------------
# RayCon regression — Rec. 1 behavior preserved
# ---------------------------------------------------------------------------


class TestRayConRegression:
    """The RayCon path was the original Rec. 1 hook. After the refactor
    its trigger conditions must still call ``process_site_pipeline``
    with ``force_regenerate=True`` for a fresh raycon_run_id, and skip
    on a repeat."""

    def test_raycon_arrival_calls_force_regenerate(self):
        runner = _pipeline_runner_factory()
        outcome = _call_helper(
            reason=REASON_RAYCON,
            fingerprint="rc_run_abc",
            runner=runner,
        )
        assert outcome.decision == "republish"
        assert outcome.reason == REASON_RAYCON
        runner.assert_called_once()
        assert runner.call_args.kwargs["force_regenerate"] is True

    def test_raycon_repeat_within_window_skips(self):
        runner = _pipeline_runner_factory()
        recent = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        state = {"site-123:raycon_scenario:rc_run_abc": recent}
        outcome = _call_helper(
            reason=REASON_RAYCON,
            fingerprint="rc_run_abc",
            state=state,
            runner=runner,
        )
        assert outcome.decision == "skip_no_diff"
        runner.assert_not_called()

    def test_raycon_after_force_after_window_re_fires(self):
        """Past the 12h force-after, the same run_id republishes again."""
        runner = _pipeline_runner_factory()
        old = (
            datetime.now(timezone.utc) - timedelta(hours=DD_REPUBLISH_FORCE_AFTER.total_seconds() / 3600 + 1)
        ).isoformat()
        state = {"site-123:raycon_scenario:rc_run_abc": old}
        outcome = _call_helper(
            reason=REASON_RAYCON,
            fingerprint="rc_run_abc",
            state=state,
            runner=runner,
        )
        assert outcome.decision == "republish"
        runner.assert_called_once()


# ---------------------------------------------------------------------------
# Idempotence — two rapid calls with same fingerprint → exactly one republish
# ---------------------------------------------------------------------------


class TestIdempotence:
    def test_two_rapid_calls_with_same_fingerprint_run_pipeline_once(self):
        runner = _pipeline_runner_factory()
        state: dict = {}
        finder = MagicMock(return_value=_existing_dd_report())

        for _ in range(2):
            maybe_republish_dd_report(
                MagicMock(),
                site_summary=_site(),
                reason=REASON_VENDOR_SIR,
                content_fingerprint="sir-1:2026-05-05T10:00:00Z",
                settings=MagicMock(),
                system_prompt="prompt",
                shared_cache={},
                republish_state=state,
                dry_run=False,
                pipeline_runner=runner,
                existing_report_finder=finder,
            )
        assert runner.call_count == 1
        # State has exactly one entry for this (site, reason, fingerprint).
        assert (
            list(state.keys())
            == ["site-123:vendor_sir:sir-1:2026-05-05T10:00:00Z"]
        )

    def test_idempotence_across_reasons_does_not_collide(self):
        """SIR + BI fingerprints with the same Drive id must not dedup against
        each other — they're orthogonal authoritative inputs.
        """
        runner = _pipeline_runner_factory()
        state: dict = {}
        finder = MagicMock(return_value=_existing_dd_report())
        for reason in (REASON_VENDOR_SIR, REASON_BUILDING_INSPECTION):
            maybe_republish_dd_report(
                MagicMock(),
                site_summary=_site(),
                reason=reason,
                content_fingerprint="file-1:2026-05-05T10:00:00Z",
                settings=MagicMock(),
                system_prompt="prompt",
                shared_cache={},
                republish_state=state,
                dry_run=False,
                pipeline_runner=runner,
                existing_report_finder=finder,
            )
        assert runner.call_count == 2
        assert (
            "site-123:vendor_sir:file-1:2026-05-05T10:00:00Z" in state
        )
        assert (
            "site-123:building_inspection:file-1:2026-05-05T10:00:00Z" in state
        )


# ---------------------------------------------------------------------------
# Failure handling + edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_unknown_reason_returns_skip_bad_input(self):
        outcome = _call_helper(reason="garbage_reason")
        assert outcome.decision == "skip_bad_input"
        assert "unknown reason" in outcome.error

    def test_empty_fingerprint_returns_skip_bad_input(self):
        outcome = _call_helper(fingerprint="")
        assert outcome.decision == "skip_bad_input"
        assert "fingerprint" in outcome.error

    def test_missing_drive_folder_returns_skip_bad_input(self):
        runner = _pipeline_runner_factory()
        site = _site()
        site["drive_folder_url"] = ""
        outcome = _call_helper(site_summary=site, runner=runner)
        assert outcome.decision == "skip_bad_input"
        runner.assert_not_called()

    def test_pipeline_exception_is_caught(self):
        """A crashing pipeline must surface as ``failed``, not raise."""
        runner = MagicMock(side_effect=RuntimeError("Anthropic 500"))
        outcome = _call_helper(runner=runner)
        assert outcome.decision == "failed"
        assert "Anthropic 500" in outcome.error

    def test_dry_run_returns_skip_dry_run(self):
        runner = _pipeline_runner_factory()
        outcome = _call_helper(runner=runner, dry_run=True)
        assert outcome.decision == "skip_dry_run"
        runner.assert_not_called()

    def test_force_bypasses_dedup(self):
        """``force=True`` bypasses the same-fingerprint dedup."""
        runner = _pipeline_runner_factory()
        recent = (
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).isoformat()
        state = {"site-123:vendor_sir:sir-1:2026-05-05T10:00:00Z": recent}
        outcome = _call_helper(
            fingerprint="sir-1:2026-05-05T10:00:00Z",
            state=state,
            runner=runner,
            force=True,
        )
        assert outcome.decision == "republish"
        runner.assert_called_once()

    def test_outcome_as_dict_has_expected_shape(self):
        runner = _pipeline_runner_factory(
            status="report_created", doc_url="https://docs/abc"
        )
        outcome = _call_helper(runner=runner)
        d = outcome.as_dict()
        assert d["dd_report_republish"] == "republish"
        assert d["pipeline_status"] == "report_created"
        assert d["doc_url"] == "https://docs/abc"
        assert d["site_id"] == "site-123"
