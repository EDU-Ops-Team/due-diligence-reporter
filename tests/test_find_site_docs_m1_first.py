"""Tests for the M1-first read path in `_find_site_docs_in_shared_folders`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from due_diligence_reporter.server import _find_site_docs_in_shared_folders


class _FakeSettings:
    sir_folder_id = "shared-sir-folder"
    isp_folder_id = "shared-isp-folder"
    building_inspection_folder_id = "shared-bi-folder"


def _patch_settings():
    return patch(
        "due_diligence_reporter.server.get_settings",
        return_value=_FakeSettings(),
    )


def _patch_resolve(folder_id="m1-folder-id"):
    return patch(
        "due_diligence_reporter.server._resolve_m1_folder",
        return_value=(folder_id, f"https://drive.google.com/drive/folders/{folder_id}"),
    )


def test_m1_hits_short_circuit_shared_folder_scan():
    """When M1 has all three doc types, shared folders are not scanned."""
    gc = MagicMock()

    m1_files = {
        "sir": {"id": "m1-sir", "name": "Alpha Keller SIR.pdf"},
        "isp": {"id": "m1-isp", "name": "Alpha Keller ISP.pdf"},
        "building_inspection": {
            "id": "m1-bi",
            "name": "Alpha Keller Building Inspection Report.pdf",
        },
    }

    with (
        _patch_settings(),
        _patch_resolve(),
        patch(
            "due_diligence_reporter.server._list_m1_documents_by_type",
            return_value=m1_files,
        ),
    ):
        result = _find_site_docs_in_shared_folders(
            gc,
            ["Alpha Keller"],
            site_title="Alpha Keller",
            site_address="123 Main St, Keller, TX",
            drive_folder_url="https://drive.google.com/drive/folders/site-folder",
        )

    assert result["sir"]["id"] == "m1-sir"
    assert result["isp"]["id"] == "m1-isp"
    assert result["building_inspection"]["id"] == "m1-bi"
    assert all(doc["doc_type"] for doc in result.values())  # type stamped
    # Shared folders never listed because every doc type was satisfied by M1.
    gc.list_files_in_folder.assert_not_called()
    gc.list_files_recursive.assert_not_called()


def test_m1_partial_falls_back_to_shared_for_missing_types():
    """When M1 only has SIR, the shared folder scan still runs for ISP/BI."""
    gc = MagicMock()
    # Shared-folder listings (only ISP + BI will be queried).
    gc.list_files_in_folder.return_value = [
        {"id": "shared-isp", "name": "Alpha Keller ISP.pdf"},
    ]
    gc.list_files_recursive.return_value = [
        {"id": "shared-bi", "name": "Alpha Keller Building Inspection Report.pdf"},
    ]

    m1_files = {"sir": {"id": "m1-sir", "name": "Alpha Keller SIR.pdf"}}

    with (
        _patch_settings(),
        _patch_resolve(),
        patch(
            "due_diligence_reporter.server._list_m1_documents_by_type",
            return_value=m1_files,
        ),
    ):
        result = _find_site_docs_in_shared_folders(
            gc,
            ["Alpha Keller"],
            site_title="Alpha Keller",
            site_address="123 Main St, Keller, TX",
            drive_folder_url="https://drive.google.com/drive/folders/site-folder",
        )

    # M1 wins for SIR.
    assert result["sir"]["id"] == "m1-sir"
    # Shared folders fill the gaps.
    assert result["isp"]["id"] == "shared-isp"
    assert result["building_inspection"]["id"] == "shared-bi"
    # SIR shared folder was skipped (only ISP and BI listed).
    listed_folders = [c.args[0] for c in gc.list_files_in_folder.call_args_list]
    assert "shared-sir-folder" not in listed_folders
    assert "shared-isp-folder" in listed_folders


def test_no_drive_folder_url_skips_m1_check():
    """When caller doesn't pass `drive_folder_url`, M1 is not consulted."""
    gc = MagicMock()
    gc.list_files_in_folder.return_value = [
        {"id": "shared-sir", "name": "Alpha Keller SIR.pdf"},
    ]
    gc.list_files_recursive.return_value = []

    with (
        _patch_settings(),
        patch("due_diligence_reporter.server._resolve_m1_folder") as resolve_mock,
        patch(
            "due_diligence_reporter.server._list_m1_documents_by_type"
        ) as list_mock,
    ):
        result = _find_site_docs_in_shared_folders(
            gc,
            ["Alpha Keller"],
            site_title="Alpha Keller",
            site_address="123 Main St, Keller, TX",
        )

    resolve_mock.assert_not_called()
    list_mock.assert_not_called()
    assert result["sir"]["id"] == "shared-sir"


def test_m1_resolve_failure_falls_through_to_shared_folders():
    """A failure resolving M1 should not break the shared-folder fallback."""
    gc = MagicMock()
    gc.list_files_in_folder.return_value = [
        {"id": "shared-sir", "name": "Alpha Keller SIR.pdf"},
    ]
    gc.list_files_recursive.return_value = []

    with (
        _patch_settings(),
        patch(
            "due_diligence_reporter.server._resolve_m1_folder",
            side_effect=RuntimeError("drive blew up"),
        ),
        patch(
            "due_diligence_reporter.server._list_m1_documents_by_type"
        ) as list_mock,
    ):
        result = _find_site_docs_in_shared_folders(
            gc,
            ["Alpha Keller"],
            site_title="Alpha Keller",
            site_address="123 Main St, Keller, TX",
            drive_folder_url="https://drive.google.com/drive/folders/site-folder",
        )

    # Listing M1 was never attempted because resolution failed.
    list_mock.assert_not_called()
    # Shared-folder match still works.
    assert result["sir"]["id"] == "shared-sir"
