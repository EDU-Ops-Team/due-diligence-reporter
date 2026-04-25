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
