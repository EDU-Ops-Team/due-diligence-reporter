"""Tests for RayCon cost parsing and breakdown normalization."""

from __future__ import annotations

from due_diligence_reporter.server import (
    _build_breakdown_fields,
    _build_raycon_request_payload,
    _extract_raycon_scenario,
    _extract_raycon_timeline_weeks,
    _read_raycon_done_event,
)


class _FakeResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def iter_lines(self, decode_unicode: bool = False):  # type: ignore[no-untyped-def]
        return iter(self._lines)


def test_read_raycon_done_event_returns_final_payload() -> None:
    response = _FakeResponse([
        "event: round_start",
        'data: {"round":1}',
        "",
        "event: done",
        'data: {"structured":{"costs_mvp":{"grandTotal":86000}}}',
        "",
    ])
    payload = _read_raycon_done_event(response)  # type: ignore[arg-type]
    assert payload["structured"]["costs_mvp"]["grandTotal"] == 86000


def test_build_breakdown_fields_normalizes_categories() -> None:
    fields = _build_breakdown_fields(
        "max_capacity",
        {
            "categories": [
                {"category": "Selective Demolition", "subtotal": 5200},
                {"category": "Framing & Drywall", "subtotal": 18500},
                {"category": "Interior Doors", "subtotal": 8000},
                {"category": "MEP Rough-In", "subtotal": 45000},
                {"category": "Plumbing (additional restroom)", "subtotal": 20000},
                {"category": "Finish Work", "subtotal": 55000},
                {"category": "Furniture", "subtotal": 34000},
                {"category": "Internet/Low Voltage", "subtotal": 4200},
                {"category": "Signage & Wayfinding", "subtotal": 2000},
            ],
            "softCosts": 22000,
            "gcFee": 15000,
            "contingency": 33000,
            "grandTotal": 245000,
        },
    )
    assert fields["exec.cost_demolition_max_capacity"] == "$5,200"
    assert fields["exec.cost_framing_doors_max_capacity"] == "$26,500"
    assert fields["exec.cost_mep_fire_life_safety_max_capacity"] == "$45,000"
    assert fields["exec.cost_plumbing_bathrooms_max_capacity"] == "$20,000"
    assert fields["exec.cost_finish_work_max_capacity"] == "$55,000"
    assert fields["exec.cost_furniture_max_capacity"] == "$34,000"
    assert fields["exec.cost_tech_security_signage_max_capacity"] == "$6,200"
    assert fields["exec.cost_soft_costs_max_capacity"] == "$22,000"
    assert fields["exec.cost_gc_fee_max_capacity"] == "$15,000"
    assert fields["exec.cost_contingency_max_capacity"] == "$33,000"
    assert fields["exec.cost_grand_total_max_capacity"] == "$245,000"


def test_build_raycon_request_payload_includes_source_documents() -> None:
    payload = _build_raycon_request_payload(
        site_name="Alpha Keller",
        total_building_sf=12000,
        region="default",
        room_list=[{"type": "learningroom", "sqft": 650, "name": "Classroom 1"}],
        address="123 Main St, Keller, TX 76248",
        inspection_content="BUILDING INSPECTION FULL TEXT",
        sir_content="SIR FULL TEXT",
        block_plan_content="BLOCK PLAN FULL TEXT",
    )

    assert payload["space"]["source"] == "block_plan"
    assert payload["drive_doc_summaries"]["inspection"] == "BUILDING INSPECTION FULL TEXT"
    assert payload["drive_doc_summaries"]["sir"] == "SIR FULL TEXT"
    assert payload["drive_doc_summaries"]["block_plan"] == "BLOCK PLAN FULL TEXT"


def test_extract_raycon_scenario_merges_matching_estimate_card_timeline() -> None:
    scenario = _extract_raycon_scenario(
        {
            "structured": {
                "costs_mvp": {
                    "grandTotal": 123000,
                    "timelineWeeks": 4,
                },
            },
            "estimate_cards": {
                "cards": [{
                    "label": "Furniture Only (MVP - As-Is + Code)",
                    "timeline": {
                        "weeks": 24,
                        "note": "16 weeks permitting + 8 weeks construction",
                    },
                }],
            },
        },
        "costs_mvp",
        "mvp",
    )

    assert scenario["grandTotal"] == 123000
    assert scenario["timelineWeeks"] == 4
    assert scenario["estimateCardTimelineWeeks"] == 24
    assert scenario["estimateCardTimelineNote"] == "16 weeks permitting + 8 weeks construction"


def test_extract_raycon_timeline_weeks_prefers_total_card_timeline() -> None:
    weeks = _extract_raycon_timeline_weeks({
        "timelineWeeks": 4,
        "estimateCardTimelineWeeks": 24,
    })

    assert weeks == 24

