from __future__ import annotations

import json
from datetime import UTC, datetime

from scripts.run_aadp_portfolio_gap_remediation import (
    mark_aadp_remediation_unavailable,
    run_aadp_remediation,
)


def test_unavailable_aadp_runner_marks_p1_and_drive_actions() -> None:
    snapshot = {
        "sites": [
            {
                "site_id": "SITE1",
                "site_name": "Alpha Austin",
                "gap_reasons": ["missing_p1_dri", "missing_current_milestone_documents"],
            },
            {
                "site_id": "SITE2",
                "site_name": "Alpha Tulsa",
                "gap_reasons": ["missing_drive_folder"],
            },
        ]
    }

    result = mark_aadp_remediation_unavailable(
        snapshot,
        as_of="2026-06-05T15:00:00+00:00",
        status="blocked",
        summary="AADP trigger unavailable.",
    )

    assert result["remediation"]["status"] == "needs_review"
    assert result["remediation"]["attempted_count"] == 2
    assert result["remediation"]["needs_review_count"] == 2
    assert [action["gap_type"] for action in result["sites"][0]["remediation_actions"]] == [
        "missing_p1_dri"
    ]
    assert result["sites"][0]["remediation_actions"][0]["schema_version"] == "action_record.v1"
    assert result["sites"][0]["remediation_actions"][0]["owning_workflow"] == "aadp"
    assert result["sites"][0]["remediation_actions"][0]["status"] == "blocked"
    assert result["sites"][0]["remediation_actions"][0]["review_required"] is True
    assert result["sites"][1]["remediation_actions"][0]["gap_type"] == "missing_drive_folder"


def test_run_aadp_remediation_imports_checked_out_runner(tmp_path) -> None:
    aadp_src = tmp_path / "aadp" / "src" / "alpha_analysis_downstream_processing_mcp"
    aadp_src.mkdir(parents=True)
    (aadp_src / "__init__.py").write_text("", encoding="utf-8")
    (aadp_src / "portfolio_gap_remediation.py").write_text(
        "\n".join(
            [
                "def remediate_portfolio_gap_snapshot(snapshot, **kwargs):",
                "    snapshot = dict(snapshot)",
                "    snapshot['remediation'] = {",
                "        'status': 'success',",
                "        'attempted_count': 1,",
                "        'success_count': 1,",
                "        'drive_parent_folder_id': kwargs.get('drive_parent_folder_id'),",
                "    }",
                "    return snapshot",
            ]
        ),
        encoding="utf-8",
    )

    result = run_aadp_remediation(
        {"sites": []},
        aadp_repo=tmp_path / "aadp",
        drive_parent_folder_id="ALL_LOCATIONS",
        now=_fixed_now,
    )

    assert result["remediation"] == {
        "status": "success",
        "attempted_count": 1,
        "success_count": 1,
        "drive_parent_folder_id": "ALL_LOCATIONS",
    }


def test_main_overwrites_snapshot_with_unavailable_action(tmp_path) -> None:
    from scripts import run_aadp_portfolio_gap_remediation as trigger

    snapshot_path = tmp_path / "portfolio-automation-gaps.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "sites": [
                    {
                        "site_id": "SITE1",
                        "site_name": "Alpha Austin",
                        "gap_reasons": ["missing_p1_dri"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    exit_code = trigger.main(
        [
            "--snapshot",
            str(snapshot_path),
            "--aadp-repo",
            str(tmp_path / "missing-aadp"),
        ]
    )

    assert exit_code == 0
    written = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert written["remediation"]["attempted_count"] == 1
    assert written["sites"][0]["remediation_actions"][0]["status"] == "blocked"


def _fixed_now() -> datetime:
    return datetime(2026, 6, 5, 15, 0, tzinfo=UTC)
