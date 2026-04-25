from __future__ import annotations

from datetime import date

from due_diligence_reporter.dashboard_publisher import build_site_meta


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
