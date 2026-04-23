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
