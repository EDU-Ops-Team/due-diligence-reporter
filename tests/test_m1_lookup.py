from unittest.mock import MagicMock

from due_diligence_reporter.m1_lookup import (
    M1_FOLDER_NAME,
    _list_m1_documents_by_type,
    _resolve_m1_folder,
)


def test_resolve_m1_prefers_acquire_property_folder_over_plain_m1() -> None:
    gc = MagicMock()
    gc.list_subfolders.return_value = [
        {
            "id": "plain-m1",
            "name": "M1",
            "webViewLink": "https://drive.google.com/drive/folders/plain-m1",
        },
        {
            "id": "acquire-m1",
            "name": "M1 - Acquire Property",
            "webViewLink": "https://drive.google.com/drive/folders/acquire-m1",
        },
    ]

    folder_id, folder_url = _resolve_m1_folder(
        gc,
        "https://drive.google.com/drive/folders/site-root",
    )

    assert folder_id == "acquire-m1"
    assert folder_url == "https://drive.google.com/drive/folders/acquire-m1"
    gc.create_folder.assert_not_called()


def test_resolve_m1_accepts_acquiring_property_folder_variant() -> None:
    gc = MagicMock()
    gc.list_subfolders.return_value = [
        {
            "id": "typed-m1",
            "name": "M1-Aquiring Property",
            "webViewLink": "https://drive.google.com/drive/folders/typed-m1",
        },
    ]

    folder_id, folder_url = _resolve_m1_folder(
        gc,
        "https://drive.google.com/drive/folders/site-root",
    )

    assert folder_id == "typed-m1"
    assert folder_url == "https://drive.google.com/drive/folders/typed-m1"
    gc.create_folder.assert_not_called()


def test_resolve_m1_can_create_canonical_folder_instead_of_using_plain_m1() -> None:
    gc = MagicMock()
    gc.list_subfolders.return_value = [
        {
            "id": "plain-m1",
            "name": "M1",
            "webViewLink": "https://drive.google.com/drive/folders/plain-m1",
        },
    ]
    gc.create_folder.return_value = {
        "id": "new-m1",
        "webViewLink": "https://drive.google.com/drive/folders/new-m1",
    }

    folder_id, folder_url = _resolve_m1_folder(
        gc,
        "https://drive.google.com/drive/folders/site-root",
        allow_legacy_fallback=False,
    )

    assert folder_id == "new-m1"
    assert folder_url == "https://drive.google.com/drive/folders/new-m1"
    gc.create_folder.assert_called_once_with("site-root", M1_FOLDER_NAME)


def test_resolve_m1_creates_canonical_acquire_property_folder_when_missing() -> None:
    gc = MagicMock()
    gc.list_subfolders.return_value = [
        {
            "id": "m10-folder",
            "name": "M10 - Archive",
            "webViewLink": "https://drive.google.com/drive/folders/m10-folder",
        },
    ]
    gc.create_folder.return_value = {
        "id": "new-m1",
        "webViewLink": "https://drive.google.com/drive/folders/new-m1",
    }

    folder_id, folder_url = _resolve_m1_folder(
        gc,
        "https://drive.google.com/drive/folders/site-root",
    )

    assert folder_id == "new-m1"
    assert folder_url == "https://drive.google.com/drive/folders/new-m1"
    gc.create_folder.assert_called_once_with("site-root", M1_FOLDER_NAME)


def test_resolve_m1_read_only_does_not_create_when_missing() -> None:
    gc = MagicMock()
    gc.list_subfolders.return_value = []

    folder_id, folder_url = _resolve_m1_folder(
        gc,
        "https://drive.google.com/drive/folders/site-root",
        create_if_missing=False,
    )

    assert folder_id is None
    assert folder_url is None
    gc.create_folder.assert_not_called()


def test_list_m1_documents_ignores_provenance_cache_files() -> None:
    gc = MagicMock()
    gc.list_files_in_folder.return_value = [
        {"id": "prov-1", "name": "provenance.json"},
        {"id": "prov-2", "name": "provenance (1).json"},
        {"id": "sir-1", "name": "Alpha Keller SIR.pdf"},
    ]

    docs = _list_m1_documents_by_type(gc, "m1")

    assert docs == {"sir": {"id": "sir-1", "name": "Alpha Keller SIR.pdf"}}
