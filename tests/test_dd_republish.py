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

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from due_diligence_reporter.dd_republish import (
    DD_REPUBLISH_FORCE_AFTER,
    REASON_BUILDING_INSPECTION,
    REASON_E_OCCUPANCY,
    REASON_RAYCON,
    REASON_SCHOOL_APPROVAL,
    REASON_VENDOR_SIR,
    RepublishOutcome,
    load_state,
    maybe_republish_dd_report,
    record_dd_republish_failure_event,
    save_state,
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


def _pipeline_runner_factory(
    status: str = "report_created",
    doc_url: str = "https://docs/x",
    *,
    open_questions: list[dict] | None = None,
):
    """Return a MagicMock substitute for ``process_site_pipeline``."""
    result = MagicMock()
    result.status = status
    result.doc_url = doc_url
    result.run_id = "run-123"
    result.manifest_path = ".ddr-runs/run-123.json"
    result.open_questions = open_questions or []
    result.closed_open_questions = []
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
    open_questions_before=None,
    failure_event_recorder=None,
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
        open_questions_before=open_questions_before,
        failure_event_recorder=failure_event_recorder,
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

    def test_failed_pipeline_does_not_update_success_dedup_state(self):
        """Failed reruns must retry on the next scan instead of being suppressed."""
        runner = _pipeline_runner_factory(status="generation_failed")
        state: dict = {}
        outcome = _call_helper(
            reason=REASON_VENDOR_SIR,
            fingerprint="sir-file-1:2026-05-05T10:00:00Z",
            state=state,
            runner=runner,
        )
        assert outcome.decision == "republish"
        assert outcome.pipeline_status == "generation_failed"
        assert state == {}

    def test_failed_pipeline_status_records_failure_event_when_requested(self):
        recorder = MagicMock(return_value={"status": "created", "rhodes_note_id": "NOTE1"})
        runner = _pipeline_runner_factory(status="generation_failed")

        outcome = _call_helper(
            reason=REASON_VENDOR_SIR,
            fingerprint="sir-file-1:2026-05-05T10:00:00Z",
            runner=runner,
            failure_event_recorder=recorder,
        )

        assert outcome.decision == "republish"
        assert outcome.pipeline_status == "generation_failed"
        assert outcome.failure_event == {"status": "created", "rhodes_note_id": "NOTE1"}
        recorder.assert_called_once()
        assert recorder.call_args.args[0] is outcome

    def test_raised_pipeline_records_failure_event_when_requested(self):
        recorder = MagicMock(return_value={"status": "created", "rhodes_note_id": "NOTE1"})
        runner = MagicMock(side_effect=RuntimeError("Anthropic 500"))

        outcome = _call_helper(
            reason=REASON_VENDOR_SIR,
            fingerprint="sir-file-1:2026-05-05T10:00:00Z",
            runner=runner,
            failure_event_recorder=recorder,
        )

        assert outcome.decision == "failed"
        assert "Anthropic 500" in outcome.error
        assert outcome.failure_event == {"status": "created", "rhodes_note_id": "NOTE1"}
        recorder.assert_called_once()

    def test_record_dd_republish_failure_event_posts_chat_when_owner_note_not_verified(self):
        settings = MagicMock()
        settings.google_chat_webhook_url = "https://chat.example/webhook"
        outcome = RepublishOutcome(
            decision="failed",
            reason=REASON_VENDOR_SIR,
            site_id="SITE1",
            fingerprint="sir-file-1:2026-05-05T10:00:00Z",
            error="Anthropic 500",
            source_event={
                "source_type": "vendor_sir",
                "drive_file_id": "sir-file-1",
                "file_name": "Alpha Keller SIR.pdf",
            },
        )
        site_summary = {
            "id": "SITE1",
            "title": "Alpha Keller",
            "drive_folder_url": "https://drive.google.com/drive/folders/root",
            "p1_assignee_email": "owner@example.com",
        }

        with (
            patch(
                "due_diligence_reporter.dd_republish.record_rhodes_automation_event",
                return_value=(
                    {
                        "status": "failed",
                        "reason": "missing_note_id",
                        "owner_notification": "none",
                    },
                    "AutomationEvent v1\nKind: dd_report_republish_failed",
                ),
            ) as record_event,
            patch(
                "due_diligence_reporter.dd_republish.post_google_chat_to_configured_webhooks",
                return_value={"status": "sent"},
            ) as post_chat,
        ):
            status = record_dd_republish_failure_event(outcome, site_summary, settings)

        assert status["status"] == "failed"
        assert status["google_chat"] == {"status": "sent"}
        event_arg = record_event.call_args.args[0]
        assert event_arg.event_type == "dd_report_republish_failed"
        assert event_arg.site_id == "SITE1"
        assert "Anthropic 500" in event_arg.details["Failure reason"]
        assert record_event.call_args.kwargs["owner_email"] == "owner@example.com"
        post_chat.assert_called_once_with(
            "https://chat.example/webhook",
            "AutomationEvent v1\nKind: dd_report_republish_failed",
        )

    def test_same_fingerprint_inside_force_after_skips(self):
        """Same SIR fingerprint repeated within 12h → no diff, no republish."""
        runner = _pipeline_runner_factory()
        recent = (
            datetime.now(UTC) - timedelta(hours=1)
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
        old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
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
            datetime.now(UTC) - timedelta(minutes=15)
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
            datetime.now(UTC) - timedelta(hours=1)
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
            datetime.now(UTC) - timedelta(hours=DD_REPUBLISH_FORCE_AFTER.total_seconds() / 3600 + 1)
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


class TestExpandedSourceReasons:
    def test_e_occupancy_and_school_approval_reasons_are_supported(self):
        runner = _pipeline_runner_factory()
        state: dict = {}
        finder = MagicMock(return_value=_existing_dd_report())

        for reason in (REASON_E_OCCUPANCY, REASON_SCHOOL_APPROVAL):
            outcome = _call_helper(
                reason=reason,
                fingerprint=f"{reason}-file:2026-05-26T10:00:00Z",
                state=state,
                runner=runner,
                finder=finder,
            )
            assert outcome.decision == "republish"
            assert outcome.reason == reason

        assert runner.call_count == 2
        assert (
            "site-123:e_occupancy_report:e_occupancy_report-file:2026-05-26T10:00:00Z"
            in state
        )
        assert (
            "site-123:school_approval_report:school_approval_report-file:2026-05-26T10:00:00Z"
            in state
        )


class TestOpenQuestionClosure:
    def test_validated_rerun_closes_absent_prior_open_question(self):
        previous = [
            {
                "open_question_id": "oq_1",
                "display_text": "Confirm fire alarm path with Building Inspection.",
                "affected_ddr_field": "Occupancy path",
                "expected_source_type": "building_inspection",
                "status": "open",
            }
        ]
        runner = _pipeline_runner_factory(status="report_created", open_questions=[])

        outcome = _call_helper(
            reason=REASON_BUILDING_INSPECTION,
            fingerprint="bi-file:2026-05-26T10:00:00Z",
            runner=runner,
            state={},
            open_questions_before=previous,
        )

        assert outcome.closed_items == [
            {
                "open_question_id": "oq_1",
                "display_text": "Confirm fire alarm path with Building Inspection.",
                "affected_ddr_field": "Occupancy path",
                "expected_source_type": "building_inspection",
                "evidence_source": "bi-file",
                "closed_run": "run-123",
            }
        ]
        assert outcome.still_open_items == []

    def test_partial_or_failed_rerun_never_records_closure(self):
        previous = [
            {
                "open_question_id": "oq_1",
                "display_text": "Confirm zoning.",
                "affected_ddr_field": "Zoning",
                "expected_source_type": "vendor_sir",
                "status": "open",
            }
        ]
        runner = _pipeline_runner_factory(status="report_incomplete", open_questions=[])

        outcome = _call_helper(
            reason=REASON_VENDOR_SIR,
            fingerprint="sir-file:2026-05-26T10:00:00Z",
            runner=runner,
            state={},
            open_questions_before=previous,
        )

        assert outcome.decision == "republish"
        assert outcome.pipeline_status == "report_incomplete"
        assert outcome.closed_items == []


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
            datetime.now(UTC) - timedelta(minutes=5)
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


# ---------------------------------------------------------------------------
# Legacy state migration (PR #88 → follow-up)
# ---------------------------------------------------------------------------


class TestLoadRepublishStateMigration:
    """One-shot migration from the pre-Rec.3 ``.raycon_dd_republish_state.json``
    (keyed ``site_id:run_id``) into the shared ``.dd_republish_state.json``
    (keyed ``site_id:reason:fingerprint``).

    Lives in ``dd_republish.load_state`` so both raycon_followup and
    scan_inbox pick it up — otherwise scan_inbox would silently skip
    the migration and lose dedup on the cutover run.
    """

    def test_legacy_present_new_absent_keys_rewritten(self, tmp_path):
        legacy = tmp_path / ".raycon_dd_republish_state.json"
        new = tmp_path / ".dd_republish_state.json"
        legacy.write_text(
            json.dumps(
                {
                    "site-123:rc_run_abc": "2026-05-05T10:00:00+00:00",
                    "site-456:rc_run_def": "2026-05-06T10:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        state = load_state(new, legacy_path=legacy)

        assert state == {
            "site-123:raycon_scenario:rc_run_abc": "2026-05-05T10:00:00+00:00",
            "site-456:raycon_scenario:rc_run_def": "2026-05-06T10:00:00+00:00",
        }

    def test_both_present_new_wins_on_conflict(self, tmp_path):
        """If the new file already has the migrated key, don't clobber it
        with the legacy timestamp — the new file is post-migration truth.
        """
        legacy = tmp_path / ".raycon_dd_republish_state.json"
        new = tmp_path / ".dd_republish_state.json"
        legacy.write_text(
            json.dumps({"site-123:rc_run_abc": "2026-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )
        new.write_text(
            json.dumps(
                {
                    "site-123:raycon_scenario:rc_run_abc": "2026-05-05T10:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        state = load_state(new, legacy_path=legacy)

        assert (
            state["site-123:raycon_scenario:rc_run_abc"]
            == "2026-05-05T10:00:00+00:00"
        )

    def test_legacy_malformed_json_fails_closed(self, tmp_path):
        """Malformed legacy file → treated as no prior state, no crash."""
        legacy = tmp_path / ".raycon_dd_republish_state.json"
        new = tmp_path / ".dd_republish_state.json"
        legacy.write_text("{not valid json", encoding="utf-8")

        state = load_state(new, legacy_path=legacy)

        assert state == {}

    def test_legacy_left_in_place_after_migration(self, tmp_path):
        """Migration overlays the legacy file; we never delete it.

        Subsequent runs no-op once the new file has been written with
        the migrated keys (asserted by the new-wins test above). This
        test pins the actual behavior so future refactors that add a
        delete are caught here.
        """
        legacy = tmp_path / ".raycon_dd_republish_state.json"
        new = tmp_path / ".dd_republish_state.json"
        legacy.write_text(
            json.dumps({"site-123:rc_run_abc": "2026-05-05T10:00:00+00:00"}),
            encoding="utf-8",
        )

        load_state(new, legacy_path=legacy)

        assert legacy.exists()
        assert json.loads(legacy.read_text(encoding="utf-8")) == {
            "site-123:rc_run_abc": "2026-05-05T10:00:00+00:00"
        }

    def test_legacy_absent_returns_new_state_only(self, tmp_path):
        legacy = tmp_path / ".raycon_dd_republish_state.json"
        new = tmp_path / ".dd_republish_state.json"
        new.write_text(
            json.dumps(
                {"site-1:vendor_sir:f1:2026-05-05T10:00:00Z": "2026-05-05T11:00:00Z"}
            ),
            encoding="utf-8",
        )

        state = load_state(new, legacy_path=legacy)

        assert state == {
            "site-1:vendor_sir:f1:2026-05-05T10:00:00Z": "2026-05-05T11:00:00Z"
        }

    def test_legacy_skips_malformed_keys(self, tmp_path):
        """Legacy entries without a ':' separator are skipped, not crashed."""
        legacy = tmp_path / ".raycon_dd_republish_state.json"
        new = tmp_path / ".dd_republish_state.json"
        legacy.write_text(
            json.dumps(
                {
                    "no_colon_key": "2026-05-05T10:00:00+00:00",
                    "site-1:rc_ok": "2026-05-05T11:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        state = load_state(new, legacy_path=legacy)

        assert "site-1:raycon_scenario:rc_ok" in state
        assert all(":" in k for k in state)


# ---------------------------------------------------------------------------
# save_state — atomic write
# ---------------------------------------------------------------------------


class TestSaveStateAtomic:
    def test_save_then_load_roundtrip(self, tmp_path):
        path = tmp_path / ".dd_republish_state.json"
        payload = {
            "site-1:vendor_sir:f1:2026-05-05T10:00:00Z": "2026-05-05T11:00:00Z",
            "site-2:raycon_scenario:rc_xyz": "2026-05-05T12:00:00Z",
        }
        save_state(payload, path)
        # Read back via load_state with no legacy file present.
        loaded = load_state(path, legacy_path=tmp_path / ".no_such_legacy.json")
        assert loaded == payload

    def test_save_does_not_leave_tmp_files(self, tmp_path):
        path = tmp_path / ".dd_republish_state.json"
        save_state({"k": "v"}, path)
        # Atomic write → temp file should be renamed away. Only the
        # final state file should remain in the directory.
        leftovers = [
            p.name for p in tmp_path.iterdir() if p.name != path.name
        ]
        assert leftovers == [], f"unexpected leftovers: {leftovers}"

    def test_save_overwrites_existing(self, tmp_path):
        path = tmp_path / ".dd_republish_state.json"
        path.write_text(json.dumps({"old": "v"}), encoding="utf-8")
        save_state({"new": "v2"}, path)
        assert json.loads(path.read_text(encoding="utf-8")) == {"new": "v2"}

    def test_save_to_nested_directory_creates_parents(self, tmp_path):
        path = tmp_path / "nested" / "subdir" / ".dd_republish_state.json"
        save_state({"k": "v"}, path)
        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8")) == {"k": "v"}


# ---------------------------------------------------------------------------
# Fix 1 regression — RayCon fingerprint includes _drive_modified_time
# ---------------------------------------------------------------------------


class TestRayConCompositeFingerprint:
    """When RayCon recomputes the same ``run_id`` but writes fresh content
    (different ``_drive_modified_time``), the helper must republish — not
    silently skip for up to 12h. The fingerprint plumbed by
    ``_republish_dd_report_if_present`` is composite
    (``run_id:drive_modified_time``) so a content change always changes
    the dedup key.
    """

    def test_same_run_id_different_modified_time_republishes(self):
        from scripts.raycon_followup import _republish_dd_report_if_present

        runner = MagicMock()
        runner.return_value = MagicMock(
            status="report_created", doc_url="https://docs/x"
        )

        # Patch the pipeline + finder via the dd_republish helper so we
        # don't touch real Drive.
        from unittest.mock import patch

        existing = {"id": "dd1", "name": "Alpha Keller DD Report"}
        # Pre-seed state as if a prior run with the same run_id but an
        # older modifiedTime fingerprint had completed 1 hour ago — well
        # inside the 12h force-after window. Without the modifiedTime
        # suffix this would be a "deduped" no-op.
        recent = (
            datetime.now(UTC) - timedelta(hours=1)
        ).isoformat()
        state = {
            "site-123:raycon_scenario:rc_run_abc:2026-05-05T10:00:00Z": recent,
        }

        with patch(
            "scripts.raycon_followup._find_existing_dd_report",
            return_value=existing,
        ), patch(
            "scripts.raycon_followup.process_site_pipeline", runner
        ):
            out = _republish_dd_report_if_present(
                MagicMock(),
                _site(),
                "rc_run_abc",
                settings=MagicMock(),
                system_prompt="prompt",
                shared_cache={},
                republish_state=state,
                dry_run=False,
                drive_modified_time="2026-05-05T20:00:00Z",  # newer content
            )

        assert out["dd_report_republish"] == "republished", out
        runner.assert_called_once()
        # New composite key recorded; old one still present (not relevant).
        assert (
            "site-123:raycon_scenario:rc_run_abc:2026-05-05T20:00:00Z" in state
        )

    def test_unknown_run_id_fallback_includes_modified_time(self):
        """The ``unknown@<date>`` fallback (when run_id is empty) must
        also include the modifiedTime so we don't dedup all empty-run-id
        events on a given day. Review finding #10.
        """
        from unittest.mock import patch

        from scripts.raycon_followup import _republish_dd_report_if_present

        runner = MagicMock(
            return_value=MagicMock(
                status="report_created", doc_url="https://docs/x"
            )
        )
        existing = {"id": "dd1", "name": "Alpha Keller DD Report"}
        state: dict = {}

        with patch(
            "scripts.raycon_followup._find_existing_dd_report",
            return_value=existing,
        ), patch(
            "scripts.raycon_followup.process_site_pipeline", runner
        ):
            # First arrival — empty run_id, modifiedTime A.
            _republish_dd_report_if_present(
                MagicMock(),
                _site(),
                "",
                settings=MagicMock(),
                system_prompt="prompt",
                shared_cache={},
                republish_state=state,
                dry_run=False,
                drive_modified_time="2026-05-05T10:00:00Z",
            )
            # Second arrival — still empty run_id, but newer modifiedTime.
            # Must republish (different fingerprint), not dedup.
            _republish_dd_report_if_present(
                MagicMock(),
                _site(),
                "",
                settings=MagicMock(),
                system_prompt="prompt",
                shared_cache={},
                republish_state=state,
                dry_run=False,
                drive_modified_time="2026-05-05T20:00:00Z",
            )

        assert runner.call_count == 2
        # Two distinct keys recorded — one per arrival.
        assert len(state) == 2


# ---------------------------------------------------------------------------
# Legacy fingerprint migration dedup (Fix 9)
# ---------------------------------------------------------------------------


class TestLegacyFingerprintMigrationDedup:
    """A migrated legacy entry (`{site}:raycon_scenario:{run_id}`) must
    dedup against incoming live keys (`{site}:raycon_scenario:{run_id}:{drive_modified_time}`).

    Pre-Rec.3, RayCon dedup keyed on `{site}:{run_id}`. ``load_state``
    rewrites those into `{site}:raycon_scenario:{run_id}` (no drive
    modified time suffix). Live callers now build a longer fingerprint;
    without the prefix-match in ``maybe_republish_dd_report``, the
    one-shot republish would re-fire after the cutover. This test
    locks in the prefix-match behavior.
    """

    def test_legacy_entry_dedups_against_live_composite_key(self):
        runner = _pipeline_runner_factory()
        # Recent enough to be inside the force_after window.
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        # Migrated legacy key — no `:drive_modified_time` suffix.
        legacy_state = {"site-123:raycon_scenario:rc_run_abc": recent}

        # Live caller's fingerprint includes drive_modified_time.
        outcome = _call_helper(
            reason=REASON_RAYCON,
            fingerprint="rc_run_abc:2026-05-08T10:00:00Z",
            state=legacy_state,
            runner=runner,
        )

        assert outcome.decision == "skip_no_diff"
        runner.assert_not_called()
