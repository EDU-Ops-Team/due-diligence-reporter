from __future__ import annotations

import json
from pathlib import Path

from due_diligence_reporter.source_packet import (
    SCHEMA_GAP_CONFIRMATION_FIELDS,
    SourceDocumentRef,
    build_dd_field_updates,
    build_m2_source_packet,
    locationos_fields_allowed_by_source_packet,
    m2_field_matrix,
    source_packet_completion,
    source_packet_is_complete,
    source_packet_note_lines,
    translate_outdoor_play_score,
)

MATRIX_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "reference"
    / "m2-diligence-field-source-matrix.json"
)


def _registered(source_type: str, title: str | None = None) -> SourceDocumentRef:
    return SourceDocumentRef(
        source_type=source_type,
        title=title or source_type.replace("_", " ").title(),
        drive_url=f"https://drive.example/{source_type}",
        rhodes_doc_type="other",
        registration_status="registered",
    )


def test_m2_field_matrix_covers_sources_and_schema_gaps() -> None:
    fields = {spec.field: spec for spec in m2_field_matrix()}
    snapshot = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    snapshot_fields = {row["field"]: row for row in snapshot["fields"]}

    assert set(fields) == set(snapshot_fields)
    assert "ddr_attached" not in fields
    assert "max_plan_mode_confirmed" not in fields
    assert "permit_of_record_confirmed" not in fields
    assert fields["play_area_score"].writer == "outdoor_play_space"
    assert fields["play_area_score"].locationos_key == "playAreaScore"
    assert fields["play_area_score"].required_sources[0].any_of == (
        "outdoor_play_space_report",
    )
    assert fields["play_area_comment"].writer == "outdoor_play_space"

    schema_gap_fields = {
        spec.field
        for spec in fields.values()
        if spec.hold_reason == "locationos_schema_gap"
    }
    assert schema_gap_fields == SCHEMA_GAP_CONFIRMATION_FIELDS
    assert len(schema_gap_fields) == 6

    for field, spec in fields.items():
        expected = snapshot_fields[field]
        assert spec.writer
        assert spec.required_sources
        assert spec.locationos_key or spec.hold_reason == "locationos_schema_gap"
        assert spec.writer == expected["writer"]
        assert spec.locationos_key == expected["locationos_key"]
        assert spec.hold_reason == expected.get("hold_reason", "")
        actual_sources = {
            source_type
            for required in spec.required_sources
            for source_type in required.any_of
        }
        assert actual_sources == set(expected["required_sources"])


def test_unregistered_source_blocks_related_field_write() -> None:
    packet = build_m2_source_packet(
        values={
            "exec.fastest_open_capacity": "36",
            "exec.max_capacity_capacity": "54",
        },
        supporting_documents=[
            SourceDocumentRef(
                source_type="alpha_capacity_analysis",
                title="Alpha Capacity Analysis",
                drive_url="https://drive.example/capacity",
                rhodes_doc_type="capacityCalculation",
                registration_status="failed",
                fields_supported=("fast_open_capacity", "max_plan_capacity"),
            )
        ],
    )

    updates = {row["field"]: row for row in packet["dd_field_updates"]}

    assert updates["fast_open_capacity"]["write_status"] == "blocked"
    assert updates["fast_open_capacity"]["hold_reason"] == (
        "required_source_not_registered: Alpha Capacity Analysis"
    )
    assert updates["max_plan_capacity"]["write_status"] == "blocked"
    assert packet["status"] == "blocked"


def test_unmapped_source_blocks_related_field_write() -> None:
    packet = build_m2_source_packet(
        values={
            "exec.play_area_score": "1",
            "exec.play_area_comment": "Outdoor play passes.",
        },
        supporting_documents=[
            SourceDocumentRef(
                source_type="outdoor_play_space_report",
                title="Outdoor Play Space Report",
                drive_url="https://drive.example/play",
                registration_status="registered",
                fields_supported=("play_area_score", "play_area_comment"),
            )
        ],
    )

    updates = {row["field"]: row for row in packet["dd_field_updates"]}

    assert updates["play_area_score"]["write_status"] == "blocked"
    assert updates["play_area_comment"]["write_status"] == "blocked"
    assert "Map source document: Outdoor Play Space Report" in packet["open_items"]


def test_source_packet_completion_requires_registered_sources_and_verified_writes() -> None:
    docs = [
        _registered("alpha_capacity_analysis", "Alpha Capacity Analysis"),
        _registered("cost_timeline_estimate", "Cost/Timeline Estimate"),
        _registered("opening_plan_report", "Opening Plan"),
        _registered("alpha_phasing_plan_report", "Alpha Phasing Plan"),
        _registered("outdoor_play_space_report", "Outdoor Play Space Report"),
        _registered("sir", "SIR"),
        _registered("school_approval_report", "School Approval Report"),
        _registered("traffic_analysis", "KH Traffic Analysis"),
        _registered("certificate_of_occupancy", "CO"),
        _registered("measured_floor_plan", "Measured Floor Plan"),
    ]
    values = {
        "exec.fastest_open_capacity": "36",
        "exec.max_capacity_capacity": "54",
        "exec.fastest_open_open_date": "08/12/26",
        "exec.max_capacity_open_date": "10/01/26",
        "exec.fastest_open_capex": "$125,000",
        "exec.max_capacity_capex": "$250,000",
        "exec.building_score": "2",
        "exec.building_comment": "Phasing plan needs Phase II review.",
        "exec.play_area_score": "1",
        "exec.play_area_comment": "Off-site park passes within walk limit.",
        "exec.regulatory_score": "2",
        "exec.regulatory_comment": "Use permit timing needs AHJ confirmation.",
        "exec.school_ops_score": "2",
        "exec.school_ops_comment": "Traffic analysis needs arrival plan review.",
        "fast_open_mode_confirmed": "Fast open uses Phase I scope.",
        "fast_open_occupancy_type_confirmed": "Fast open targets B occupancy.",
        "max_plan_occupancy_type_confirmed": "Max plan targets E occupancy.",
        "current_occupancy_confirmed": "CO shows current occupancy.",
        "zoning_status_confirmed": "SIR and Opening Plan align.",
        "site_square_footage_confirmed": "Measured plan confirms SF.",
    }

    updates = build_dd_field_updates(values=values, supporting_documents=docs)
    verified_updates = []
    for update in updates:
        if update.locationos_key:
            update.write_status = "written"
            update.readback_status = "verified"
        verified_updates.append(update)

    completion = source_packet_completion(
        supporting_documents=docs,
        dd_field_updates=verified_updates,
    )

    assert completion["status"] == "complete"
    assert completion["m2_source_packet_complete"] is True
    assert completion["open_items"] == []


def test_source_packet_note_lines_are_concise_and_do_not_use_ddr_as_source() -> None:
    docs = [_registered("outdoor_play_space_report", "Outdoor Play Space Report")]
    updates = build_dd_field_updates(
        values={
            "exec.play_area_score": "1",
            "exec.play_area_comment": "On-site playscape passes.",
        },
        supporting_documents=docs,
    )
    lines = source_packet_note_lines(supporting_documents=docs, dd_field_updates=updates)

    assert lines == [
        "play_area_score -> 1 -> Outdoor Play Space Report",
        "play_area_comment -> On-site playscape passes. -> Outdoor Play Space Report",
    ]
    assert "DDR" not in "\n".join(lines)
    assert "drive.example" not in "\n".join(lines)


def test_cost_timeline_source_is_required_for_dates_and_capex() -> None:
    packet = build_m2_source_packet(
        values={
            "exec.fastest_open_open_date": "08/12/26",
            "exec.max_capacity_open_date": "10/01/26",
            "exec.fastest_open_capex": "$125,000",
            "exec.max_capacity_capex": "$250,000",
        },
        supporting_documents=[
            _registered("opening_plan_report", "Opening Plan"),
            _registered("alpha_phasing_plan_report", "Alpha Phasing Plan"),
        ],
    )

    updates = {row["field"]: row for row in packet["dd_field_updates"]}

    assert updates["fast_open_date"]["write_status"] == "blocked"
    assert updates["fast_open_date"]["hold_reason"] == (
        "required_source_not_registered: Cost/Timeline Estimate"
    )
    assert updates["max_plan_date"]["write_status"] == "blocked"
    assert updates["fast_open_capex"]["write_status"] == "blocked"
    assert updates["max_plan_capex"]["write_status"] == "blocked"


def test_locationos_filter_holds_completion_fields_until_packet_complete() -> None:
    filtered = locationos_fields_allowed_by_source_packet(
        {
            "status": "complete",
            "dateCompleted": "2026-06-17",
            "ddReportLink": "https://docs.google.com/document/d/doc123",
            "playAreaScore": 1,
        },
        {
            "status": "blocked",
            "m2_source_packet_complete": False,
            "open_items": ["play_area_score: readback not verified"],
            "dd_field_updates": [
                {
                    "field": "play_area_score",
                    "locationos_key": "playAreaScore",
                    "value": "1",
                    "writer": "outdoor_play_space",
                    "required_source_docs": ["Outdoor Play Space Report"],
                    "write_status": "pending",
                    "readback_status": "pending",
                    "source_titles": ["Outdoor Play Space Report"],
                }
            ],
        },
    )

    assert filtered == {
        "status": "data-gathering",
        "playAreaScore": 1,
    }


def test_source_packet_completion_requires_explicit_complete_flag() -> None:
    packet = {
        "status": "complete",
        "open_items": [],
        "dd_field_updates": [],
    }

    assert source_packet_is_complete(packet) is False

    filtered = locationos_fields_allowed_by_source_packet(
        {
            "status": "complete",
            "dateCompleted": "2026-06-17",
            "ddReportLink": "https://docs.google.com/document/d/doc123",
        },
        packet,
    )

    assert filtered == {"status": "data-gathering"}


def test_translate_outdoor_play_score_uses_confidence_and_review_rules() -> None:
    green = translate_outdoor_play_score(
        {
            "on_site_verdict": "pass",
            "off_site_verdict": "not_required",
            "confidence": "B",
            "required_outdoor_sf": 3600,
        }
    )
    yellow = translate_outdoor_play_score(
        {
            "on_site_verdict": "pass",
            "off_site_verdict": "not_required",
            "confidence": "C",
            "required_outdoor_sf": 3600,
        }
    )
    red = translate_outdoor_play_score(
        {
            "on_site_verdict": "fail",
            "off_site_verdict": "fail",
            "confidence": "D",
            "no_candidate_reason": "no_candidate_found",
        }
    )

    assert green["score"] == 1
    assert "passes" in green["comment"]
    assert yellow["score"] == 2
    assert "manual review" in yellow["comment"].lower()
    assert red["score"] == 3
    assert "No viable" in red["comment"]
