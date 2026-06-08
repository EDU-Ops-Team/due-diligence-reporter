from __future__ import annotations

from unittest.mock import MagicMock, patch

from due_diligence_reporter.vendor_doc_sweep import (
    collect_core_source_events,
    run_vendor_doc_republish_sweep,
)


def _site() -> dict:
    return {
        "id": "site-1",
        "title": "Alpha Test",
        "address": "123 Main St",
        "drive_folder_url": "https://drive.google.com/drive/folders/root",
    }


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
                    "id": "raycon-1",
                    "name": "raycon_scenario.json",
                    "modifiedTime": "2026-05-26T11:00:00Z",
                    "webViewLink": "https://drive/raycon-1",
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
            }
        ]

    gc.list_files_in_folder.side_effect = list_files

    with patch("due_diligence_reporter.vendor_doc_sweep.is_vendor_sourced", return_value=True):
        events = collect_core_source_events(gc, _site())

    assert {event["source_type"] for event in events} == {
        "vendor_sir",
        "raycon_scenario",
        "alpha_phasing_plan_report",
        "school_approval_report",
    }
    assert any(event["fingerprint"] == "sir-1:2026-05-26T10:00:00Z" for event in events)


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
