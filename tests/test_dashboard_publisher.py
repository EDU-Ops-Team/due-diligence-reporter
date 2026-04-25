from __future__ import annotations

from datetime import date

from due_diligence_reporter.dashboard_publisher import (
    _derive_dd_dates,
    build_site_meta,
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
