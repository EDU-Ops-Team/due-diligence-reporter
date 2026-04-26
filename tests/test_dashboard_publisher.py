from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from due_diligence_reporter.dashboard_publisher import (
    _derive_dd_dates,
    build_site_meta,
    publish_to_dashboard,
)


class TestBuildSiteMeta:
    def test_includes_rebl_block(self) -> None:
        meta = build_site_meta(
            "Austin",
            address="123 Main St, Austin, TX 78701",
            school_type="micro",
            drive_folder_url="https://drive.google.com/drive/folders/abc",
            dd_report_url="https://docs.google.com/document/d/xyz",
            rebl_site_id="123-main-st-austin-tx",
            rebl_url="https://rebl3.vercel.app/site/123-main-st-austin-tx",
            report_date=date(2026, 4, 23),
        )

        assert meta["slug"] == "austin"
        assert meta["rebl"]["site_id"] == "123-main-st-austin-tx"
        assert meta["rebl"]["url"] == "https://rebl3.vercel.app/site/123-main-st-austin-tx"
        assert meta["report_date"] == "2026-04-23"

    def test_dd_provenance_omitted_when_unset(self) -> None:
        """Phase 1 dd_* keys must NOT appear when callers don't pass them.

        Keeps the wire payload tidy and makes diffs in committed sites.json
        minimal during Phase 1 rollout.
        """
        meta = build_site_meta("Austin", address="123 Main St, Austin, TX")
        for key in (
            "dd_author",
            "dd_owner",
            "dd_version",
            "dd_report_length",
            "dd_commissioned_date",
            "dd_due_date",
        ):
            assert key not in meta, f"{key} should be omitted when not provided"

    def test_dd_provenance_included_when_set(self) -> None:
        meta = build_site_meta(
            "Austin",
            address="123 Main St, Austin, TX",
            dd_author="Jane Doe",
            dd_owner="Greg Foote",
            dd_version="v2",
            dd_report_length=14,
            dd_commissioned_date="2026-04-10",
            dd_due_date="2026-05-01",
        )
        assert meta["dd_author"] == "Jane Doe"
        assert meta["dd_owner"] == "Greg Foote"
        assert meta["dd_version"] == "v2"
        assert meta["dd_report_length"] == 14
        assert meta["dd_commissioned_date"] == "2026-04-10"
        assert meta["dd_due_date"] == "2026-05-01"

    def test_dd_provenance_strings_are_stripped(self) -> None:
        meta = build_site_meta(
            "Austin",
            address="123 Main St, Austin, TX",
            dd_author="  Jane Doe  ",
            dd_owner="\tGreg\n",
        )
        assert meta["dd_author"] == "Jane Doe"
        assert meta["dd_owner"] == "Greg"

    def test_dd_report_length_rejects_negative(self) -> None:
        """Negative page counts are nonsense — omit rather than persist."""
        meta = build_site_meta(
            "Austin",
            address="123 Main St, Austin, TX",
            dd_report_length=-1,
        )
        assert "dd_report_length" not in meta

    def test_dd_report_length_zero_is_accepted(self) -> None:
        """Zero is a valid (if odd) page count and shouldn't be coerced away."""
        meta = build_site_meta(
            "Austin",
            address="123 Main St, Austin, TX",
            dd_report_length=0,
        )
        assert meta["dd_report_length"] == 0


class TestDeriveDDDates:
    """_derive_dd_dates() default behavior:

      - dd_commissioned_date defaults to today
      - dd_due_date defaults to commissioned + 14 days
      - Caller values short-circuit both defaults independently

    Stickiness across reruns is enforced by the dashboard transform's
    `preserve()` helper, not here — we always send today's date and let
    the dashboard lock the first non-empty value. See
    dd-dashboard/api/_lib/transform.ts.
    """

    def test_defaults_to_today_plus_14(self) -> None:
        today = date(2026, 4, 25)
        commissioned, due = _derive_dd_dates(
            explicit_commissioned=None,
            explicit_due=None,
            today=today,
        )
        assert commissioned == "2026-04-25"
        assert due == "2026-05-09"

    def test_explicit_commissioned_recomputes_due(self) -> None:
        """When caller back-dates commissioned, due re-derives from it."""
        commissioned, due = _derive_dd_dates(
            explicit_commissioned="2026-04-10",
            explicit_due=None,
            today=date(2026, 4, 25),
        )
        assert commissioned == "2026-04-10"
        assert due == "2026-04-24"

    def test_explicit_due_overrides_default(self) -> None:
        commissioned, due = _derive_dd_dates(
            explicit_commissioned=None,
            explicit_due="2026-06-01",
            today=date(2026, 4, 25),
        )
        assert commissioned == "2026-04-25"  # today fallback
        assert due == "2026-06-01"  # explicit wins

    def test_both_explicit_passed_through(self) -> None:
        commissioned, due = _derive_dd_dates(
            explicit_commissioned="2026-03-15",
            explicit_due="2026-04-15",
            today=date(2026, 4, 25),
        )
        assert commissioned == "2026-03-15"
        assert due == "2026-04-15"

    def test_blank_strings_treated_as_unset(self) -> None:
        commissioned, due = _derive_dd_dates(
            explicit_commissioned="   ",
            explicit_due="",
            today=date(2026, 4, 25),
        )
        assert commissioned == "2026-04-25"
        assert due == "2026-05-09"

    def test_malformed_explicit_commissioned_yields_no_due(self) -> None:
        """If a caller passes garbage we don't fabricate a +14d due."""
        commissioned, due = _derive_dd_dates(
            explicit_commissioned="not-a-date",
            explicit_due=None,
            today=date(2026, 4, 25),
        )
        assert commissioned == "not-a-date"  # passed through verbatim
        assert due is None

    def test_malformed_does_not_break_when_due_explicit(self) -> None:
        commissioned, due = _derive_dd_dates(
            explicit_commissioned="garbage",
            explicit_due="2026-05-01",
            today=date(2026, 4, 25),
        )
        assert commissioned == "garbage"
        assert due == "2026-05-01"


class TestPhase2DDProvenance:
    """Phase 2 DD provenance fields on build_site_meta:

      - school_feasibility (Wrike W74) — free string
      - timeline_confidence (Wrike W81) — free string
      - dd_status — "in_progress" / "complete" / "follow_up"
      - dd_recommendation — "go" / "no_go" (derived from c_answer)

    The decision-driven override (approve / reject / info_req) is layered
    on top of dd_recommendation in the dashboard UI by
    `effectiveDdStatusForSite`.
    """

    def test_phase2_omitted_when_unset(self) -> None:
        meta = build_site_meta("Austin", address="123 Main St, Austin, TX")
        for key in ("school_feasibility", "timeline_confidence", "dd_status"):
            assert key not in meta, f"{key} should be omitted when not provided"

    def test_phase2_included_when_set(self) -> None:
        meta = build_site_meta(
            "Austin",
            address="123 Main St, Austin, TX",
            school_feasibility="high",
            timeline_confidence="medium",
            dd_status="complete",
        )
        assert meta["school_feasibility"] == "high"
        assert meta["timeline_confidence"] == "medium"
        assert meta["dd_status"] == "complete"

    def test_phase2_strings_are_stripped(self) -> None:
        meta = build_site_meta(
            "Austin",
            address="123 Main St, Austin, TX",
            school_feasibility="  high  ",
            timeline_confidence="\tlow\n",
            dd_status=" complete ",
        )
        assert meta["school_feasibility"] == "high"
        assert meta["timeline_confidence"] == "low"
        assert meta["dd_status"] == "complete"

    def test_phase2_blank_strings_omitted(self) -> None:
        """Blank/whitespace-only values should be treated as unset."""
        meta = build_site_meta(
            "Austin",
            address="123 Main St, Austin, TX",
            school_feasibility="",
            timeline_confidence="   ",
            dd_status=None,
        )
        for key in ("school_feasibility", "timeline_confidence", "dd_status"):
            assert key not in meta

    def test_dd_recommendation_omitted_when_unset(self) -> None:
        meta = build_site_meta(
            "Austin",
            address="123 Main St, Austin, TX",
            school_feasibility="high",
            timeline_confidence="high",
            dd_status="complete",
        )
        assert "dd_recommendation" not in meta

    def test_dd_recommendation_included_when_set(self) -> None:
        meta = build_site_meta(
            "Austin",
            address="123 Main St, Austin, TX",
            dd_recommendation="go",
        )
        assert meta["dd_recommendation"] == "go"

    def test_dd_recommendation_lowercased_and_stripped(self) -> None:
        meta = build_site_meta(
            "Austin",
            address="123 Main St, Austin, TX",
            dd_recommendation="  No_Go  ",
        )
        assert meta["dd_recommendation"] == "no_go"

    def test_dd_recommendation_blank_omitted(self) -> None:
        meta = build_site_meta(
            "Austin",
            address="123 Main St, Austin, TX",
            dd_recommendation="   ",
        )
        assert "dd_recommendation" not in meta


class TestDdRecommendationDerivation:
    """publish_to_dashboard derives dd_recommendation from report_data["exec.c_answer"].

    The report card stays plain-English Yes / No; the dashboard chip reads
    Go / No Go on the publisher field. The derivation maps Yes → "go",
    No → "no_go". Legacy values ("Go", "No Go", "Yes see notes",
    "Conditional") are aliased through LEGACY_CAN_WE_ANSWER_ALIASES so
    pre-rename payloads still derive correctly.
    """

    def _capture_meta(self, report_data: dict, **publish_kwargs) -> dict:
        """Run publish_to_dashboard with mocks; return the posted site_meta."""
        captured: dict = {}

        def fake_post(url, json, headers, timeout):
            captured.update(json)
            response = MagicMock()
            response.status_code = 200
            return response

        env = {
            "DASHBOARD_PUBLISH_SECRET": "test-secret",
            "DASHBOARD_PUBLISH_ENABLED": "1",
        }
        with patch.dict("os.environ", env, clear=False), \
                patch("due_diligence_reporter.dashboard_publisher.requests.post", side_effect=fake_post):
            publish_to_dashboard("Austin", report_data, **publish_kwargs)
        return captured.get("site_meta", {})

    @pytest.mark.parametrize(
        ("c_answer", "expected"),
        [
            # Canonical Yes / No
            ("Yes", "go"),
            ("No", "no_go"),
            ("yes", "go"),
            ("NO", "no_go"),
            # Legacy Go / No Go (from the brief publisher-vocab-on-c_answer
            # experiment) — must derive correctly
            ("Go", "go"),
            ("No Go", "no_go"),
            # Legacy three-state — collapses to go (was a yes-with-caveats)
            ("Yes see notes", "go"),
            ("Conditional", "go"),
        ],
    )
    def test_derives_from_c_answer(self, c_answer: str, expected: str) -> None:
        meta = self._capture_meta({"exec.c_answer": c_answer})
        assert meta["dd_recommendation"] == expected

    def test_missing_c_answer_omits_field(self) -> None:
        meta = self._capture_meta({})
        assert "dd_recommendation" not in meta

    def test_blank_c_answer_omits_field(self) -> None:
        meta = self._capture_meta({"exec.c_answer": "   "})
        assert "dd_recommendation" not in meta

    def test_unrecognized_c_answer_omits_field(self) -> None:
        meta = self._capture_meta({"exec.c_answer": "Maybe"})
        assert "dd_recommendation" not in meta

    def test_explicit_kwarg_overrides_derivation(self) -> None:
        # Caller passes Go but report says No — caller wins.
        meta = self._capture_meta(
            {"exec.c_answer": "No"},
            dd_recommendation="go",
        )
        assert meta["dd_recommendation"] == "go"


class TestDdSiteScoreDerivation:
    """publish_to_dashboard derives dd_site_score from q2.e_occupancy_score.

    The E-Occupancy tool (`apply_e_occupancy_skill`) emits a 0–100 score
    on the `q2.e_occupancy_score` token as part of its standard output.
    The publisher promotes this to a top-level `dd_site_score` field on
    the dashboard payload, plus a derived `dd_site_score_band`
    (green/yellow/orange/red).
    """

    def _capture_meta(self, report_data: dict, **publish_kwargs) -> dict:
        """Run publish_to_dashboard with mocks; return the posted site_meta."""
        captured: dict = {}

        def fake_post(url, json, headers, timeout):
            captured.update(json)
            response = MagicMock()
            response.status_code = 200
            return response

        env = {
            "DASHBOARD_PUBLISH_SECRET": "test-secret",
            "DASHBOARD_PUBLISH_ENABLED": "1",
        }
        with patch.dict("os.environ", env, clear=False), \
                patch("due_diligence_reporter.dashboard_publisher.requests.post", side_effect=fake_post):
            publish_to_dashboard("Austin", report_data, **publish_kwargs)
        return captured.get("site_meta", {})

    @pytest.mark.parametrize(
        ("raw_score", "expected_score", "expected_band"),
        [
            # GREEN 80–100
            ("100", 100, "green"),
            ("85", 85, "green"),
            ("80", 80, "green"),
            # YELLOW 60–79
            ("79", 79, "yellow"),
            ("70", 70, "yellow"),
            ("60", 60, "yellow"),
            # ORANGE 40–59
            ("55", 55, "orange"),
            ("40", 40, "orange"),
            # RED 0–39
            ("39", 39, "red"),
            ("15", 15, "red"),
            ("0", 0, "red"),
        ],
    )
    def test_derives_score_and_band_from_q2_token(
        self, raw_score: str, expected_score: int, expected_band: str
    ) -> None:
        meta = self._capture_meta({"q2.e_occupancy_score": raw_score})
        assert meta["dd_site_score"] == expected_score
        assert meta["dd_site_score_band"] == expected_band

    def test_int_token_value_is_accepted(self) -> None:
        # The MCP tool returns str(score), but defensive: an int should also work.
        meta = self._capture_meta({"q2.e_occupancy_score": 75})
        assert meta["dd_site_score"] == 75
        assert meta["dd_site_score_band"] == "yellow"

    def test_float_token_rounds_to_int(self) -> None:
        meta = self._capture_meta({"q2.e_occupancy_score": "79.6"})
        # 79.6 rounds to 80 — promotes the band to green.
        assert meta["dd_site_score"] == 80
        assert meta["dd_site_score_band"] == "green"

    def test_missing_token_omits_fields(self) -> None:
        meta = self._capture_meta({})
        assert "dd_site_score" not in meta
        assert "dd_site_score_band" not in meta

    def test_blank_token_omits_fields(self) -> None:
        meta = self._capture_meta({"q2.e_occupancy_score": "   "})
        assert "dd_site_score" not in meta
        assert "dd_site_score_band" not in meta

    def test_non_numeric_token_omits_fields(self) -> None:
        meta = self._capture_meta({"q2.e_occupancy_score": "high"})
        assert "dd_site_score" not in meta
        assert "dd_site_score_band" not in meta

    def test_negative_score_omits_fields(self) -> None:
        meta = self._capture_meta({"q2.e_occupancy_score": "-5"})
        assert "dd_site_score" not in meta
        assert "dd_site_score_band" not in meta

    def test_score_above_100_omits_fields(self) -> None:
        meta = self._capture_meta({"q2.e_occupancy_score": "150"})
        assert "dd_site_score" not in meta
        assert "dd_site_score_band" not in meta

    def test_explicit_score_kwarg_overrides_token(self) -> None:
        # Caller wins: report says 30 but caller passes 90.
        meta = self._capture_meta(
            {"q2.e_occupancy_score": "30"},
            dd_site_score=90,
        )
        assert meta["dd_site_score"] == 90
        assert meta["dd_site_score_band"] == "green"

    def test_explicit_band_kwarg_overrides_derived_band(self) -> None:
        # Caller-supplied band wins over the derived one.
        meta = self._capture_meta(
            {"q2.e_occupancy_score": "85"},
            dd_site_score_band="yellow",
        )
        assert meta["dd_site_score"] == 85
        assert meta["dd_site_score_band"] == "yellow"

    def test_explicit_invalid_band_kwarg_falls_back_to_derived(self) -> None:
        # Caller passes an invalid band — publisher silently drops it and
        # keeps the derived band so the payload stays self-consistent.
        meta = self._capture_meta(
            {"q2.e_occupancy_score": "85"},
            dd_site_score_band="purple",
        )
        assert meta["dd_site_score"] == 85
        assert meta["dd_site_score_band"] == "green"

    def test_band_only_no_score_passes_through(self) -> None:
        # Backfill scenario: no score available, but the human reviewer
        # already classified the band manually.
        meta = self._capture_meta(
            {},
            dd_site_score_band="orange",
        )
        assert "dd_site_score" not in meta
        assert meta["dd_site_score_band"] == "orange"


class TestDdRiskFlagsDerivation:
    """publish_to_dashboard derives dd_risk_flags from report_data.

    Mirrors the Phase 3 dd_site_score pattern: when the caller does not
    supply dd_risk_flags explicitly, the publisher canonicalizes the
    report's flag-like tokens (permit_history, e_occupancy, school_approval,
    sir.risk_watch) into a single deduped list. Caller-wins precedence:
    explicit kwargs override derivation.
    """

    def _capture_meta(self, report_data: dict, **publish_kwargs) -> dict:
        captured: dict = {}

        def fake_post(url, json, headers, timeout):
            captured.update(json)
            response = MagicMock()
            response.status_code = 200
            return response

        env = {
            "DASHBOARD_PUBLISH_SECRET": "test-secret",
            "DASHBOARD_PUBLISH_ENABLED": "1",
        }
        with patch.dict("os.environ", env, clear=False), \
                patch("due_diligence_reporter.dashboard_publisher.requests.post", side_effect=fake_post):
            publish_to_dashboard("Austin", report_data, **publish_kwargs)
        return captured.get("site_meta", {})

    def test_derives_from_permit_history_token(self) -> None:
        meta = self._capture_meta({
            "permit_history.risk_flags": [
                {
                    "flag_type": "OPEN_PERMIT",
                    "severity": "acquisition_condition",
                    "description": "2 open permits",
                    "evidence": "x",
                }
            ]
        })
        flags = meta["dd_risk_flags"]
        assert len(flags) == 1
        assert flags[0]["category"] == "ahj_history"
        assert flags[0]["source"] == "permit_history"
        assert flags[0]["severity"] == "high"

    def test_derives_from_e_occupancy_zone(self) -> None:
        meta = self._capture_meta({"q2.e_occupancy_zone": "Red"})
        flags = meta["dd_risk_flags"]
        assert len(flags) == 1
        assert flags[0]["category"] == "occupancy"
        assert flags[0]["source"] == "e_occupancy"

    def test_derives_from_multiple_sources_combined(self) -> None:
        meta = self._capture_meta({
            "permit_history.risk_flags": [{
                "flag_type": "DEFERRED_MAINTENANCE",
                "severity": "risk_note",
                "description": "No activity",
                "evidence": "x",
            }],
            "q2.e_occupancy_zone": "Red",
            "q1.school_approval_zone": "Yellow",
            "q1.school_approval_type": "CERTIFICATE_OR_APPROVAL_REQUIRED",
            "q1.school_approval_timeline_days": "90",
            "sir.risk_watch": ["FEMA flood zone AE intersects parcel"],
        })
        flags = meta["dd_risk_flags"]
        sources = {f["source"] for f in flags}
        assert sources == {
            "permit_history", "e_occupancy", "school_approval", "sir_risk_watch",
        }

    def test_caller_wins_overrides_derivation(self) -> None:
        # Even with derivable upstream tokens, an explicit caller list
        # wins (caller-wins precedence — same as Phase 3 dd_site_score).
        meta = self._capture_meta(
            {"q2.e_occupancy_zone": "Red"},  # would derive an occupancy:high flag
            dd_risk_flags=[{
                "category": "zoning",
                "severity": "medium",
                "source": "sir_risk_watch",
                "summary": "Manual override entry",
            }],
        )
        flags = meta["dd_risk_flags"]
        assert len(flags) == 1
        assert flags[0]["category"] == "zoning"
        assert flags[0]["source"] == "sir_risk_watch"

    def test_caller_invalid_falls_back_to_derivation(self) -> None:
        # Same pattern as Phase 3 invalid band: caller-supplied entries
        # that all fail validation are dropped, derivation takes over.
        meta = self._capture_meta(
            {"q2.e_occupancy_zone": "Red"},
            dd_risk_flags=[{
                "category": "made_up",
                "severity": "high",
                "source": "rumor",
                "summary": "Invalid",
            }],
        )
        flags = meta["dd_risk_flags"]
        assert len(flags) == 1
        assert flags[0]["category"] == "occupancy"

    def test_empty_when_no_signals(self) -> None:
        # No upstream signals → no key on payload (sticky-preserve safe).
        meta = self._capture_meta({"exec.c_answer": "Yes"})
        assert "dd_risk_flags" not in meta

    def test_explicit_empty_list_falls_back_to_derivation(self) -> None:
        # Truthy-empty caller list → publisher treats as "not supplied"
        # and runs derivation. (Avoids accidental clear-all from a
        # caller passing [] before they actually populate the field.)
        meta = self._capture_meta(
            {"q2.e_occupancy_zone": "Red"},
            dd_risk_flags=[],
        )
        flags = meta["dd_risk_flags"]
        assert len(flags) == 1
        assert flags[0]["category"] == "occupancy"


class TestDashboardPublishOwnerCutover:
    """Phase A5 cutover flag: DASHBOARD_PUBLISH_OWNER gates the POST.

    The reporter must keep publishing by default and yield to the pipeline
    only when an operator explicitly flips ownership. The flag is read on
    every call — not cached — so the flip is live, no redeploy required.
    """

    @staticmethod
    def _run(env: dict[str, str]) -> tuple[bool, MagicMock]:
        """Run publish_to_dashboard under ``env`` and return (returned, post_mock)."""
        post_mock = MagicMock()
        post_mock.return_value.status_code = 200
        with patch.dict("os.environ", env, clear=False), patch(
            "due_diligence_reporter.dashboard_publisher.requests.post",
            new=post_mock,
        ):
            returned = publish_to_dashboard(
                "Austin",
                {"exec.c_answer": "Yes"},
                address="123 Main St, Austin, TX 78701",
            )
        return returned, post_mock

    def test_owner_pipeline_short_circuits_without_post(self) -> None:
        """owner=pipeline → returns False, no HTTP call, no secret needed."""
        returned, post_mock = self._run({
            "DASHBOARD_PUBLISH_OWNER": "pipeline",
            # Secret + enabled set so we know the cutover gate is what's
            # short-circuiting, not one of the legacy gates.
            "DASHBOARD_PUBLISH_SECRET": "test-secret",
            "DASHBOARD_PUBLISH_ENABLED": "1",
        })
        assert returned is False
        post_mock.assert_not_called()

    def test_owner_pipeline_is_case_insensitive(self) -> None:
        """Operators frequently mis-case env values; tolerate it."""
        for value in ("PIPELINE", "Pipeline", "  pipeline  "):
            returned, post_mock = self._run({
                "DASHBOARD_PUBLISH_OWNER": value,
                "DASHBOARD_PUBLISH_SECRET": "test-secret",
                "DASHBOARD_PUBLISH_ENABLED": "1",
            })
            assert returned is False, f"value={value!r} should short-circuit"
            post_mock.assert_not_called()

    def test_owner_reporter_publishes_normally(self) -> None:
        """owner=reporter (the default) preserves legacy behavior."""
        returned, post_mock = self._run({
            "DASHBOARD_PUBLISH_OWNER": "reporter",
            "DASHBOARD_PUBLISH_SECRET": "test-secret",
            "DASHBOARD_PUBLISH_ENABLED": "1",
        })
        assert returned is True
        post_mock.assert_called_once()

    def test_owner_unset_publishes_normally(self) -> None:
        """Default (env unset) must behave exactly like ``reporter``.

        Critical for the rollout: deploying the cutover code without
        setting the env must not change current production behavior.
        """
        # Use clear=True to ensure the env var is unset for this test even
        # if it leaks from the host environment.
        post_mock = MagicMock()
        post_mock.return_value.status_code = 200
        with patch.dict(
            "os.environ",
            {
                "DASHBOARD_PUBLISH_SECRET": "test-secret",
                "DASHBOARD_PUBLISH_ENABLED": "1",
            },
            clear=True,
        ), patch(
            "due_diligence_reporter.dashboard_publisher.requests.post",
            new=post_mock,
        ):
            returned = publish_to_dashboard(
                "Austin",
                {"exec.c_answer": "Yes"},
                address="123 Main St, Austin, TX 78701",
            )
        assert returned is True
        post_mock.assert_called_once()

    def test_unknown_owner_value_publishes_normally(self) -> None:
        """Unrecognized values are treated as ``reporter`` (fail-safe to legacy).

        We never want a typo on the env var to silently kill publishing.
        Only the literal string ``pipeline`` (case-insensitive) yields.
        """
        returned, post_mock = self._run({
            "DASHBOARD_PUBLISH_OWNER": "pipline",  # typo
            "DASHBOARD_PUBLISH_SECRET": "test-secret",
            "DASHBOARD_PUBLISH_ENABLED": "1",
        })
        assert returned is True
        post_mock.assert_called_once()

    def test_owner_pipeline_takes_precedence_over_enabled(self) -> None:
        """owner=pipeline short-circuits before the ENABLED check, so the
        end state is identical regardless of which flag is set first."""
        returned, post_mock = self._run({
            "DASHBOARD_PUBLISH_OWNER": "pipeline",
            "DASHBOARD_PUBLISH_ENABLED": "0",
            "DASHBOARD_PUBLISH_SECRET": "test-secret",
        })
        assert returned is False
        post_mock.assert_not_called()

    def test_owner_pipeline_takes_precedence_over_missing_secret(self) -> None:
        """owner=pipeline must short-circuit even with no secret configured.

        Operators flipping to pipeline ownership often delete the secret
        from the reporter's env at the same time — we should not raise
        or warn about that.
        """
        # Use clear=True so a host-level DASHBOARD_PUBLISH_SECRET can't
        # leak in and turn this into a missing-secret-path test.
        post_mock = MagicMock()
        with patch.dict(
            "os.environ",
            {"DASHBOARD_PUBLISH_OWNER": "pipeline"},
            clear=True,
        ), patch(
            "due_diligence_reporter.dashboard_publisher.requests.post",
            new=post_mock,
        ):
            returned = publish_to_dashboard(
                "Austin",
                {"exec.c_answer": "Yes"},
                address="123 Main St, Austin, TX 78701",
            )
        assert returned is False
        post_mock.assert_not_called()
