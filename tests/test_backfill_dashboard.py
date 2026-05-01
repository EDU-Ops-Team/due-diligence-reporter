"""Tests for scripts/backfill_dashboard.py trace-selection logic."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "backfill_dashboard.py"

# Load the script as a module without executing main().
_spec = importlib.util.spec_from_file_location("backfill_dashboard", _SCRIPT_PATH)
backfill_dashboard = importlib.util.module_from_spec(_spec)
sys.modules["backfill_dashboard"] = backfill_dashboard
_spec.loader.exec_module(backfill_dashboard)  # type: ignore[union-attr]


def _trace_file(file_id: str, name: str, modified: str) -> dict[str, str]:
    return {"id": file_id, "name": name, "modifiedTime": modified}


def _trace_payload(token_report: dict[str, dict] | None) -> bytes:
    return json.dumps({"token_report": token_report or {}, "date": "04/30/2026"}).encode("utf-8")


def test_candidate_trace_files_sorted_newest_first():
    gc = MagicMock()
    gc.list_files_in_folder.return_value = [
        _trace_file("a", "Site Report Trace - 04-30-2026.json", "2026-04-30T10:00:00Z"),
        _trace_file("b", "Site DD Report Trace - 2026-04-30.json", "2026-04-30T20:00:00Z"),
        _trace_file("c", "unrelated.json", "2026-04-30T22:00:00Z"),
    ]

    result = backfill_dashboard._candidate_trace_files(gc, "folder123")

    assert [f["id"] for f in result] == ["b", "a"]


def test_backfill_one_falls_back_to_richer_trace_when_newest_is_empty(monkeypatch):
    """The newer 'DD Report Trace' file is empty; older 'Report Trace' has data.

    backfill_one must skip the empty newest trace and publish from the older
    candidate instead. This protects against the morning Tulsa case where two
    traces exist for the same date but only the older one has token_report.
    """
    gc = MagicMock()
    gc.list_files_in_folder.return_value = [
        _trace_file("empty", "Tulsa DD Report Trace - 2026-04-30.json", "2026-04-30T20:00:00Z"),
        _trace_file("rich", "Tulsa Report Trace - 04-30-2026.json", "2026-04-30T10:00:00Z"),
    ]
    gc.download_file_bytes.side_effect = lambda fid: {
        "empty": _trace_payload({}),
        "rich": _trace_payload(
            {
                "address.full": {"value": "421 E 11th St, Tulsa, OK"},
                "report.summary": {"value": "Site is suitable for school use."},
            }
        ),
    }[fid]

    publish_calls: list[dict] = []

    def fake_publish(site_title, report_data, **kwargs):
        publish_calls.append(
            {
                "site_title": site_title,
                "report_data": report_data,
                "kwargs": kwargs,
            }
        )
        return True

    monkeypatch.setattr(backfill_dashboard, "publish_to_dashboard", fake_publish)
    monkeypatch.setattr(
        backfill_dashboard,
        "extract_folder_id_from_url",
        lambda url: "folder123",
    )

    ok = backfill_dashboard.backfill_one(
        gc,
        "Alpha School Tulsa 421",
        "https://drive.google.com/drive/folders/folder123",
        address="421 E 11th St, Tulsa, OK",
        school_type="K-8",
        site_owner="Greg",
    )

    assert ok is True
    assert len(publish_calls) == 1
    call = publish_calls[0]
    assert call["site_title"] == "Alpha School Tulsa 421"
    assert call["report_data"]["address.full"] == "421 E 11th St, Tulsa, OK"
    assert call["report_data"]["report.summary"] == "Site is suitable for school use."
    # Both candidates were downloaded -- empty first, then rich fallback.
    downloaded_ids = [c.args[0] for c in gc.download_file_bytes.call_args_list]
    assert downloaded_ids == ["empty", "rich"]


def test_backfill_one_returns_false_when_no_candidate_has_data(monkeypatch):
    gc = MagicMock()
    gc.list_files_in_folder.return_value = [
        _trace_file("e1", "Site DD Report Trace - 2026-04-30.json", "2026-04-30T20:00:00Z"),
        _trace_file("e2", "Site Report Trace - 04-30-2026.json", "2026-04-30T10:00:00Z"),
    ]
    gc.download_file_bytes.return_value = _trace_payload({})

    monkeypatch.setattr(
        backfill_dashboard,
        "extract_folder_id_from_url",
        lambda url: "folder123",
    )
    publish_called = []
    monkeypatch.setattr(
        backfill_dashboard,
        "publish_to_dashboard",
        lambda *a, **kw: publish_called.append((a, kw)) or True,
    )

    ok = backfill_dashboard.backfill_one(
        gc,
        "Empty Site",
        "https://drive.google.com/drive/folders/folder123",
        address=None,
        school_type=None,
    )

    assert ok is False
    assert publish_called == []


def test_backfill_one_skips_unparseable_trace_and_uses_next(monkeypatch):
    gc = MagicMock()
    gc.list_files_in_folder.return_value = [
        _trace_file("bad", "Site DD Report Trace - 2026-04-30.json", "2026-04-30T20:00:00Z"),
        _trace_file("ok", "Site Report Trace - 04-30-2026.json", "2026-04-30T10:00:00Z"),
    ]

    def _download(fid):
        if fid == "bad":
            return b"not-valid-json{"
        return _trace_payload({"address.full": {"value": "X"}})

    gc.download_file_bytes.side_effect = _download

    monkeypatch.setattr(
        backfill_dashboard,
        "extract_folder_id_from_url",
        lambda url: "folder123",
    )
    monkeypatch.setattr(
        backfill_dashboard,
        "publish_to_dashboard",
        lambda *a, **kw: True,
    )

    ok = backfill_dashboard.backfill_one(
        gc,
        "Site",
        "https://drive.google.com/drive/folders/folder123",
        address=None,
        school_type=None,
    )

    assert ok is True

def test_backfill_one_threads_force_slug_to_publish(monkeypatch):
    """backfill_one(force_slug=...) must forward the kwarg to publish_to_dashboard.

    This is the recovery-path safety: callers that already know the canonical
    dashboard slug (the migration-recovery script) pass it as force_slug so
    the publisher does not re-derive one from the trace's rebl_site_id token
    or slugify(title). Without this threading, recovery silently mints
    phantom legacy-slug records.
    """
    gc = MagicMock()
    gc.list_files_in_folder.return_value = [
        _trace_file("ok", "Site Report Trace - 04-30-2026.json", "2026-04-30T10:00:00Z"),
    ]
    gc.download_file_bytes.return_value = _trace_payload(
        {"address.full": {"value": "421 E 11th St, Tulsa, OK"}}
    )

    publish_calls: list[dict] = []

    def fake_publish(site_title, report_data, **kwargs):
        publish_calls.append({"site_title": site_title, "kwargs": kwargs})
        return True

    monkeypatch.setattr(backfill_dashboard, "publish_to_dashboard", fake_publish)
    monkeypatch.setattr(
        backfill_dashboard,
        "extract_folder_id_from_url",
        lambda url: "folder123",
    )

    canonical = "421-e-11th-st-tulsa-ok"
    ok = backfill_dashboard.backfill_one(
        gc,
        "Alpha School Tulsa 421",
        "https://drive.google.com/drive/folders/folder123",
        address="421 E 11th St, Tulsa, OK",
        school_type="K-8",
        site_owner="Greg",
        force_slug=canonical,
    )

    assert ok is True
    assert len(publish_calls) == 1
    assert publish_calls[0]["kwargs"].get("force_slug") == canonical


def test_backfill_one_omits_force_slug_when_caller_omits_it(monkeypatch):
    """When force_slug is not passed, the kwarg defaults to None.

    Pre-existing callers (the daily backfill loop) must keep working
    unchanged: they don't know the canonical slug and rely on the
    rebl_site_id token / slugify(title) chain inside publish_to_dashboard.
    """
    gc = MagicMock()
    gc.list_files_in_folder.return_value = [
        _trace_file("ok", "Site Report Trace - 04-30-2026.json", "2026-04-30T10:00:00Z"),
    ]
    gc.download_file_bytes.return_value = _trace_payload(
        {"address.full": {"value": "X"}}
    )

    publish_calls: list[dict] = []
    monkeypatch.setattr(
        backfill_dashboard,
        "publish_to_dashboard",
        lambda *a, **kw: publish_calls.append({"args": a, "kwargs": kw}) or True,
    )
    monkeypatch.setattr(
        backfill_dashboard,
        "extract_folder_id_from_url",
        lambda url: "folder123",
    )

    backfill_dashboard.backfill_one(
        gc,
        "Site",
        "https://drive.google.com/drive/folders/folder123",
        address=None,
        school_type=None,
    )

    assert publish_calls[0]["kwargs"].get("force_slug") is None
