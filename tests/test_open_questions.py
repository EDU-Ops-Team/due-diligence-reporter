from __future__ import annotations

import json

from due_diligence_reporter.open_questions import (
    close_open_questions,
    extract_open_questions_from_report_data,
    load_latest_open_questions,
    source_event_from_drive_file,
)


def test_extract_open_questions_from_report_data_normalizes_bullets() -> None:
    questions = extract_open_questions_from_report_data(
        {
            "verification.open_items": (
                "- Confirm zoning path with LA Planning.\n"
                "- Verify occupancy path after E-Occupancy report."
            )
        },
        created_run="run-1",
    )

    assert len(questions) == 2
    assert questions[0].affected_ddr_field == "Zoning"
    assert questions[0].expected_source_type == "vendor_sir"
    assert questions[0].created_run == "run-1"
    assert questions[1].affected_ddr_field == "Occupancy path"
    assert questions[1].expected_source_type == "e_occupancy_report"


def test_close_open_questions_requires_absence_after_rerun() -> None:
    previous = [
        {
            "open_question_id": "oq_1",
            "display_text": "Confirm zoning.",
            "affected_ddr_field": "Zoning",
            "expected_source_type": "vendor_sir",
        },
        {
            "open_question_id": "oq_2",
            "display_text": "Confirm construction cost.",
            "affected_ddr_field": "Construction Timeline",
            "expected_source_type": "raycon_scenario",
        },
    ]
    current = [previous[1]]

    closed = close_open_questions(
        previous,
        current,
        source_event={"source_type": "vendor_sir", "drive_url": "https://drive/file"},
        closed_run="run-2",
    )

    assert [item.open_question_id for item in closed] == ["oq_1"]
    assert closed[0].evidence_source == "https://drive/file"
    assert closed[0].closed_run == "run-2"


def test_source_event_uses_drive_id_modified_time_fingerprint() -> None:
    event = source_event_from_drive_file(
        "school_approval_report",
        {
            "id": "file-1",
            "modifiedTime": "2026-05-26T10:00:00Z",
            "name": "School Approval - Alpha.pdf",
            "webViewLink": "https://drive/file-1",
        },
        doc_type="school_approval_report",
    )

    assert event.fingerprint == "file-1:2026-05-26T10:00:00Z"
    assert event.source_type == "school_approval_report"


def test_load_latest_open_questions_reads_latest_matching_manifest(tmp_path) -> None:
    old = tmp_path / "20260101-alpha.json"
    new = tmp_path / "20260102-alpha.json"
    old.write_text(
        json.dumps(
            {
                "run_id": "20260101-alpha",
                "site_id": "site-1",
                "site_title": "Alpha Test",
                "open_questions": [{"open_question_id": "old"}],
            }
        ),
        encoding="utf-8",
    )
    new.write_text(
        json.dumps(
            {
                "run_id": "20260102-alpha",
                "site_id": "site-1",
                "site_title": "Alpha Test",
                "open_questions": [{"open_question_id": "new"}],
            }
        ),
        encoding="utf-8",
    )

    questions = load_latest_open_questions(tmp_path, site_id="site-1")

    assert questions == [{"open_question_id": "new"}]
