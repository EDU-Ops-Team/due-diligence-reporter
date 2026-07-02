from __future__ import annotations

from unittest.mock import MagicMock, patch

from due_diligence_reporter.vendor_doc_sweep import (
    SWEEP_CURSOR_STATE_KEY,
    advance_sweep_cursor,
    collect_core_source_events,
    run_vendor_doc_republish_sweep,
    select_sweep_site_records,
)


def _site() -> dict:
    return {
        "id": "site-1",
        "title": "Alpha Test",
        "address": "123 Main St",
        "drive_folder_url": "https://drive.google.com/drive/folders/root",
    }


def _cursor_site(site_id: str) -> dict:
    site = _site()
    site["id"] = site_id
    site["title"] = f"Alpha {site_id}"
    return site


def test_bounded_sweep_rotates_site_records_with_cursor() -> None:
    records = [_cursor_site("site-c"), _cursor_site("site-a"), _cursor_site("site-b")]
    state: dict[str, str] = {}

    selected = select_sweep_site_records(records, state, max_sites=2)
    assert [site["id"] for site in selected] == ["site-a", "site-b"]

    next_cursor = advance_sweep_cursor(
        state,
        records,
        selected,
        max_sites=2,
    )
    assert next_cursor == "site-c"
    assert state[SWEEP_CURSOR_STATE_KEY] == "site-c"

    selected = select_sweep_site_records(records, state, max_sites=2)
    assert [site["id"] for site in selected] == ["site-c", "site-a"]


def test_unbounded_sweep_clears_cursor() -> None:
    records = [_cursor_site("site-a"), _cursor_site("site-b")]
    state = {SWEEP_CURSOR_STATE_KEY: "site-b"}

    selected = select_sweep_site_records(records, state, max_sites=0)
    next_cursor = advance_sweep_cursor(
        state,
        records,
        selected,
        max_sites=0,
    )

    assert [site["id"] for site in selected] == ["site-a", "site-b"]
    assert next_cursor == ""
    assert SWEEP_CURSOR_STATE_KEY not in state


def test_collect_core_source_events_reads_m1_and_root_core_docs() -> None:
    gc = MagicMock()
    gc.list_subfolders.return_value = [
        {
            "id": "m1",
            "name": "M1 - Acquire Property",
            "webViewLink": "https://drive/m1",
        }
    ]

    def list_files(folder_id: str):
        if folder_id == "m1":
            return [
                {
                    "id": "sir-1",
                    "name": "Alpha Test SIR.pdf",
                    "modifiedTime": "2026-05-26T10:00:00Z",
                    "webViewLink": "https://drive/sir-1",
                },
                {
                    "id": "capacity-1",
                    "name": "Alpha Capacity Analysis - Alpha Test.json",
                    "modifiedTime": "2026-05-26T11:10:00Z",
                    "webViewLink": "https://drive/capacity-1",
                },
                {
                    "id": "cost-1",
                    "name": "Cost Timeline Estimate - Alpha Test.json",
                    "modifiedTime": "2026-05-26T11:12:00Z",
                    "webViewLink": "https://drive/cost-1",
                },
                {
                    "id": "block-1",
                    "name": "Block Plan - Alpha Test.pdf",
                    "modifiedTime": "2026-05-26T11:15:00Z",
                    "webViewLink": "https://drive/block-1",
                },
                {
                    "id": "outdoor-1",
                    "name": "Outdoor Play Space Report - Alpha Test.md",
                    "modifiedTime": "2026-05-26T11:20:00Z",
                    "webViewLink": "https://drive/outdoor-1",
                },
                {
                    "id": "security-1",
                    "name": "Security Due Diligence - Alpha Test.md",
                    "modifiedTime": "2026-05-26T11:25:00Z",
                    "webViewLink": "https://drive/security-1",
                },
                {
                    "id": "phasing-1",
                    "name": "Alpha Phasing Plan - Alpha Test.xlsx",
                    "modifiedTime": "2026-05-26T11:30:00Z",
                    "webViewLink": "https://drive/phasing-1",
                },
            ]
        return [
            {
                "id": "school-1",
                "name": "Alpha Test School Approval Report.pdf",
                "modifiedTime": "2026-05-26T12:00:00Z",
                "webViewLink": "https://drive/school-1",
            },
            {
                "id": "opening-1",
                "name": "Opening Plan - Alpha Test.pdf",
                "modifiedTime": "2026-05-26T12:10:00Z",
                "webViewLink": "https://drive/opening-1",
            },
            {
                "id": "traffic-1",
                "name": "KH Traffic Analysis - Alpha Test.pdf",
                "modifiedTime": "2026-05-26T12:20:00Z",
                "webViewLink": "https://drive/traffic-1",
            },
            {
                "id": "co-1",
                "name": "Certificate of Occupancy - Alpha Test.pdf",
                "modifiedTime": "2026-05-26T12:30:00Z",
                "webViewLink": "https://drive/co-1",
            },
            {
                "id": "floor-1",
                "name": "Measured Floor Plan - Alpha Test.pdf",
                "modifiedTime": "2026-05-26T12:40:00Z",
                "webViewLink": "https://drive/floor-1",
            },
            {
                "id": "lidar-1",
                "name": "LiDAR - Alpha Test.zip",
                "modifiedTime": "2026-05-26T12:50:00Z",
                "webViewLink": "https://drive/lidar-1",
            },
            {
                "id": "permit-1",
                "name": "Permit of Record - Alpha Test.pdf",
                "modifiedTime": "2026-05-26T13:00:00Z",
                "webViewLink": "https://drive/permit-1",
            },
        ]

    gc.list_files_in_folder.side_effect = list_files

    with patch("due_diligence_reporter.vendor_doc_sweep.is_vendor_sourced", return_value=True):
        events = collect_core_source_events(gc, _site())

    assert {event["source_type"] for event in events} == {
        "vendor_sir",
        "alpha_capacity_analysis",
        "cost_timeline_estimate",
        "block_plan",
        "outdoor_play_space_report",
        "security_due_diligence_report",
        "alpha_phasing_plan_report",
        "school_approval_report",
        "opening_plan_report",
        "traffic_analysis",
        "certificate_of_occupancy",
        "measured_floor_plan",
        "lidar",
        "permit_of_record",
    }
    assert any(event["fingerprint"] == "sir-1:2026-05-26T10:00:00Z" for event in events)
    assert any(event["fingerprint"] == "cost-1:2026-05-26T11:12:00Z" for event in events)
    assert any(event["fingerprint"] == "block-1:2026-05-26T11:15:00Z" for event in events)


def test_collect_core_source_events_can_skip_provenance_cache_writes() -> None:
    gc = MagicMock()
    gc.list_subfolders.return_value = [
        {
            "id": "m1",
            "name": "M1 - Acquire Property",
            "webViewLink": "https://drive/m1",
        }
    ]
    gc.list_files_in_folder.return_value = [
        {
            "id": "sir-1",
            "name": "Alpha Test SIR.pdf",
            "modifiedTime": "2026-05-26T10:00:00Z",
            "webViewLink": "https://drive/sir-1",
        }
    ]

    with patch(
        "due_diligence_reporter.vendor_doc_sweep.is_vendor_sourced",
        return_value=True,
    ) as is_vendor:
        events = collect_core_source_events(gc, _site(), read_only=True)

    assert events
    assert is_vendor.call_args.kwargs["read_only"] is True


def test_sweep_triggers_republish_for_core_source_doc() -> None:
    gc = MagicMock()
    callback = MagicMock(
        return_value={
            "dd_report_republish": "republish",
            "republish_reason": "vendor_sir",
            "content_fingerprint": "sir-1:2026-05-26T10:00:00Z",
        }
    )
    with patch(
        "due_diligence_reporter.vendor_doc_sweep.collect_core_source_events",
        return_value=[
            {
                "source_type": "vendor_sir",
                "fingerprint": "sir-1:2026-05-26T10:00:00Z",
                "doc_type": "sir",
            }
        ],
    ):
        result = run_vendor_doc_republish_sweep(
            gc,
            settings=MagicMock(),
            system_prompt="prompt",
            shared_cache={},
            republish_state={},
            site_records=[_site()],
            republish_callback=callback,
        )

    assert result["republished"] == 1
    callback.assert_called_once()
    assert callback.call_args.kwargs["source_event"]["source_type"] == "vendor_sir"


def test_sweep_can_emit_canonical_source_event_receipt() -> None:
    gc = MagicMock()
    callback = MagicMock(
        return_value={
            "dd_report_republish": "skip_no_prior_report",
            "republish_reason": "vendor_sir",
            "content_fingerprint": "sir-1:2026-05-26T10:00:00Z",
        }
    )
    source_event_emitter = MagicMock()
    with patch(
        "due_diligence_reporter.vendor_doc_sweep.collect_core_source_events",
        return_value=[
            {
                "source_type": "vendor_sir",
                "fingerprint": "sir-1:2026-05-26T10:00:00Z",
                "doc_type": "sir",
            }
        ],
    ):
        result = run_vendor_doc_republish_sweep(
            gc,
            settings=MagicMock(),
            system_prompt="prompt",
            shared_cache={},
            republish_state={},
            site_records=[_site()],
            republish_callback=callback,
            source_event_emitter=source_event_emitter,
        )

    assert result["canonical_source_events"] == 1
    assert result["source_event_errors"] == 0
    assert result["rows"][0]["source_event_status"] == "emitted"
    source_event_emitter.assert_called_once()
    assert source_event_emitter.call_args.args[0]["id"] == "site-1"
    assert source_event_emitter.call_args.args[1]["source_type"] == "vendor_sir"


def test_sweep_skips_site_with_no_prior_report_without_error() -> None:
    gc = MagicMock()
    callback = MagicMock(
        return_value={
            "dd_report_republish": "skip_no_prior_report",
            "republish_reason": "vendor_sir",
            "content_fingerprint": "sir-1:2026-05-26T10:00:00Z",
        }
    )
    with patch(
        "due_diligence_reporter.vendor_doc_sweep.collect_core_source_events",
        return_value=[
            {
                "source_type": "vendor_sir",
                "fingerprint": "sir-1:2026-05-26T10:00:00Z",
                "doc_type": "sir",
            }
        ],
    ):
        result = run_vendor_doc_republish_sweep(
            gc,
            settings=MagicMock(),
            system_prompt="prompt",
            shared_cache={},
            republish_state={},
            site_records=[_site()],
            republish_callback=callback,
        )

    assert result["republished"] == 0
    assert result["errors"] == 0
    assert result["rows"][0]["dd_report_republish"] == "skip_no_prior_report"


def test_sweep_reports_no_runnable_skill_inputs() -> None:
    gc = MagicMock()
    with patch(
        "due_diligence_reporter.vendor_doc_sweep.collect_core_source_events",
        return_value=[],
    ):
        result = run_vendor_doc_republish_sweep(
            gc,
            settings=MagicMock(),
            system_prompt="prompt",
            shared_cache={},
            republish_state={},
            site_records=[_site()],
        )

    assert result["republished"] == 0
    assert result["errors"] == 0
    assert result["rows"][0]["reason"] == "no_runnable_skill_inputs"
    assert "no DDR source skills" in result["rows"][0]["message"]


def test_provenance_error_surfaces_as_site_error() -> None:
    gc = MagicMock()
    gc.list_subfolders.return_value = [
        {
            "id": "m1",
            "name": "M1 - Acquire Property",
            "webViewLink": "https://drive/m1",
        }
    ]
    gc.list_files_in_folder.return_value = [
        {
            "id": "sir-1",
            "name": "Alpha Test SIR.pdf",
            "modifiedTime": "2026-05-26T10:00:00Z",
            "webViewLink": "https://drive/sir-1",
        }
    ]

    with patch(
        "due_diligence_reporter.vendor_doc_sweep.is_vendor_sourced",
        side_effect=RuntimeError("provenance timeout"),
    ):
        result = run_vendor_doc_republish_sweep(
            gc,
            settings=MagicMock(),
            system_prompt="prompt",
            shared_cache={},
            republish_state={},
            site_records=[_site()],
        )

    assert result["errors"] == 1
    assert result["rows"][0]["status"] == "error"
    assert "provenance timeout" in result["rows"][0]["reason"]
