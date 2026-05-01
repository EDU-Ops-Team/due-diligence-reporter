"""Tests for scripts/cleanup_phantom_recovery_slugs.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "cleanup_phantom_recovery_slugs.py"

_spec = importlib.util.spec_from_file_location(
    "cleanup_phantom_recovery_slugs", _SCRIPT_PATH
)
cleanup = importlib.util.module_from_spec(_spec)
sys.modules["cleanup_phantom_recovery_slugs"] = cleanup
_spec.loader.exec_module(cleanup)  # type: ignore[union-attr]


def _resp(status: int, body: dict | None = None, text: str | None = None):
    """Build a mock response. Pass body for JSON; text overrides."""
    r = MagicMock()
    r.status_code = status
    if text is not None:
        r.text = text
        # json() should raise if text isn't valid JSON
        try:
            r.json.return_value = json.loads(text)
        except (ValueError, TypeError):
            r.json.side_effect = ValueError("not json")
    elif body is not None:
        r.text = json.dumps(body)
        r.json.return_value = body
    else:
        r.text = ""
        r.json.return_value = {}
    return r


# ──────────────────────────────────────────────────────────────────────────────
# _is_wiped_stub
# ──────────────────────────────────────────────────────────────────────────────


def test_is_wiped_stub_none_record():
    """None (slug absent) is treated as a stub — already gone."""
    assert cleanup._is_wiped_stub(None) is True


def test_is_wiped_stub_empty_dict():
    """No hydrated fields -> stub."""
    assert cleanup._is_wiped_stub({}) is True


def test_is_wiped_stub_only_slug_and_metadata():
    """A wiped stub keeps slug/published_at but loses analytical fields."""
    record = {
        "slug": "alpha-school-foo",
        "published_at": "2026-04-01T00:00:00Z",
        "address": "1 Main St",
    }
    assert cleanup._is_wiped_stub(record) is True


def test_is_wiped_stub_with_can_we_open():
    record = {"slug": "x", "can_we_open": "Yes"}
    assert cleanup._is_wiped_stub(record) is False


def test_is_wiped_stub_with_scenarios():
    record = {"slug": "x", "scenarios": [{"id": "s1"}]}
    assert cleanup._is_wiped_stub(record) is False


def test_is_wiped_stub_with_sources():
    record = {"slug": "x", "sources": {"permitting": "https://..."}}
    assert cleanup._is_wiped_stub(record) is False


def test_is_wiped_stub_falsy_fields_are_stub():
    """Empty list / empty dict / empty string for hydrated fields == stub."""
    record = {
        "slug": "x",
        "can_we_open": "",
        "scenarios": [],
        "sources": {},
    }
    assert cleanup._is_wiped_stub(record) is True


# ──────────────────────────────────────────────────────────────────────────────
# _fetch_site_record
# ──────────────────────────────────────────────────────────────────────────────


def test_fetch_site_record_found():
    session = MagicMock()
    session.get.return_value = _resp(
        200,
        body={
            "sites": [
                {"slug": "a", "can_we_open": "Yes"},
                {"slug": "b"},
            ]
        },
    )
    ok, record, note = cleanup._fetch_site_record(session, "https://x", "b")
    assert ok is True
    assert record == {"slug": "b"}
    assert "found" in note


def test_fetch_site_record_not_found():
    session = MagicMock()
    session.get.return_value = _resp(200, body={"sites": [{"slug": "a"}]})
    ok, record, note = cleanup._fetch_site_record(session, "https://x", "missing")
    assert ok is True
    assert record is None
    assert "not in sites.json" in note


def test_fetch_site_record_http_error():
    session = MagicMock()
    session.get.return_value = _resp(503, text="upstream down")
    ok, record, note = cleanup._fetch_site_record(session, "https://x", "a")
    assert ok is False
    assert "HTTP 503" in note


def test_fetch_site_record_network_error():
    import requests as _req

    session = MagicMock()
    session.get.side_effect = _req.RequestException("connection refused")
    ok, record, note = cleanup._fetch_site_record(session, "https://x", "a")
    assert ok is False
    assert "network error" in note


def test_fetch_site_record_malformed_payload():
    session = MagicMock()
    session.get.return_value = _resp(200, body={"sites": "not a list"})
    ok, record, note = cleanup._fetch_site_record(session, "https://x", "a")
    assert ok is False
    assert "missing or not a list" in note


# ──────────────────────────────────────────────────────────────────────────────
# _delete_stub
# ──────────────────────────────────────────────────────────────────────────────


def test_delete_stub_200():
    session = MagicMock()
    session.delete.return_value = _resp(200)
    ok, note = cleanup._delete_stub(session, "https://x", "secret", "slug-a")
    assert ok is True
    assert "deleted" in note
    # Auth header carried
    call = session.delete.call_args
    assert call.kwargs["headers"]["Authorization"] == "Bearer secret"


def test_delete_stub_404_idempotent():
    session = MagicMock()
    session.delete.return_value = _resp(404)
    ok, note = cleanup._delete_stub(session, "https://x", "secret", "slug-a")
    assert ok is True
    assert "already absent" in note


def test_delete_stub_500_failure():
    session = MagicMock()
    session.delete.return_value = _resp(500, text="bad")
    ok, note = cleanup._delete_stub(session, "https://x", "secret", "slug-a")
    assert ok is False
    assert "HTTP 500" in note


def test_delete_stub_network_error():
    import requests as _req

    session = MagicMock()
    session.delete.side_effect = _req.RequestException("boom")
    ok, note = cleanup._delete_stub(session, "https://x", "secret", "slug-a")
    assert ok is False
    assert "network error" in note


# ──────────────────────────────────────────────────────────────────────────────
# _rename — honest checks
# ──────────────────────────────────────────────────────────────────────────────


def test_rename_200_full_success():
    session = MagicMock()
    session.post.return_value = _resp(
        200,
        body={
            "ok": True,
            "action": "rename",
            "overrides_moved": True,
            "reviews_moved": True,
        },
    )
    ok, note = cleanup._rename(
        session, "https://x", "secret", old_slug="old", new_slug="new"
    )
    assert ok is True
    assert "rename" in note
    assert "overrides_moved=True" in note
    assert "reviews_moved=True" in note


def test_rename_200_noop_no_data_to_move():
    """Common case: no per-slug data existed for the old slug. Both flags
    false. Still success."""
    session = MagicMock()
    session.post.return_value = _resp(
        200,
        body={
            "ok": True,
            "action": "rename",
            "overrides_moved": False,
            "reviews_moved": False,
        },
    )
    ok, note = cleanup._rename(
        session, "https://x", "secret", old_slug="old", new_slug="new"
    )
    assert ok is True


def test_rename_200_action_noop():
    """Idempotent retry: dashboard reports action=noop."""
    session = MagicMock()
    session.post.return_value = _resp(
        200,
        body={"ok": True, "action": "noop", "message": "already renamed"},
    )
    ok, note = cleanup._rename(
        session, "https://x", "secret", old_slug="old", new_slug="new"
    )
    assert ok is True
    assert "noop" in note


def test_rename_502_partial_failure():
    """Dashboard reports sites.json renamed but overrides re-key failed.
    Caller MUST retry. _rename returns False to surface this."""
    session = MagicMock()
    session.post.return_value = _resp(
        502,
        body={
            "ok": False,
            "action": "rename_partial",
            "sites_renamed": True,
            "overrides_had_data": True,
            "overrides_moved": False,
            "overrides_error": "GitHub 422",
            "reviews_had_data": False,
            "reviews_moved": False,
            "reviews_error": None,
        },
    )
    ok, note = cleanup._rename(
        session, "https://x", "secret", old_slug="old", new_slug="new"
    )
    assert ok is False
    assert "partial" in note
    assert "overrides_had_data=True" in note
    assert "overrides_moved=False" in note


def test_rename_200_with_explicit_ok_false_treated_as_failure():
    """If dashboard ever returns 200 ok:false, treat as failure rather
    than silently advance."""
    session = MagicMock()
    session.post.return_value = _resp(
        200, body={"ok": False, "message": "weird state"}
    )
    ok, note = cleanup._rename(
        session, "https://x", "secret", old_slug="old", new_slug="new"
    )
    assert ok is False
    assert "ok=false" in note


def test_rename_400_failure():
    session = MagicMock()
    session.post.return_value = _resp(400, body={"message": "new_slug invalid"})
    ok, note = cleanup._rename(
        session, "https://x", "secret", old_slug="old", new_slug="new"
    )
    assert ok is False
    assert "HTTP 400" in note


def test_rename_500_failure():
    session = MagicMock()
    session.post.return_value = _resp(500, text="boom")
    ok, note = cleanup._rename(
        session, "https://x", "secret", old_slug="old", new_slug="new"
    )
    assert ok is False
    assert "HTTP 500" in note


def test_rename_network_error():
    import requests as _req

    session = MagicMock()
    session.post.side_effect = _req.RequestException("timeout")
    ok, note = cleanup._rename(
        session, "https://x", "secret", old_slug="old", new_slug="new"
    )
    assert ok is False
    assert "network error" in note


def test_rename_malformed_json_body():
    """Non-JSON body on 200 — _rename should still parse and treat as ok
    if status is 200 and we can't read ok=False."""
    session = MagicMock()
    r = MagicMock()
    r.status_code = 200
    r.text = "not json"
    r.json.side_effect = ValueError("not json")
    session.post.return_value = r
    ok, note = cleanup._rename(
        session, "https://x", "secret", old_slug="old", new_slug="new"
    )
    # Body coerced to {}, ok != False, so treated as success.
    assert ok is True


# ──────────────────────────────────────────────────────────────────────────────
# _process_pair — pre-flight + full flow
# ──────────────────────────────────────────────────────────────────────────────


def _stub_canonical_record():
    """Return a record that _is_wiped_stub considers a stub."""
    return {"slug": "canonical", "published_at": "2026-04-01T00:00:00Z"}


def _populated_canonical_record():
    return {
        "slug": "canonical",
        "can_we_open": "Yes",
        "scenarios": [{"id": "s1"}],
    }


def test_process_pair_dry_run_skips_writes_but_runs_preflight():
    """Dry run still does the pre-flight fetch (so the user sees real
    state) but performs no DELETE or rename."""
    session = MagicMock()
    session.get.return_value = _resp(
        200, body={"sites": [_stub_canonical_record()]}
    )
    ok = cleanup._process_pair(
        session,
        "https://x",
        "secret",
        phantom="phantom",
        canonical="canonical",
        dry_run=True,
    )
    assert ok is True
    session.get.assert_called_once()
    session.delete.assert_not_called()
    session.post.assert_not_called()


def test_process_pair_refuses_to_delete_populated_canonical():
    """Footgun guard: canonical record is populated, NOT a wiped stub.
    Map entry must be wrong — refuse to delete and skip the pair."""
    session = MagicMock()
    session.get.return_value = _resp(
        200, body={"sites": [_populated_canonical_record()]}
    )
    ok = cleanup._process_pair(
        session,
        "https://x",
        "secret",
        phantom="phantom",
        canonical="canonical",
        dry_run=False,
    )
    assert ok is False
    session.delete.assert_not_called()
    session.post.assert_not_called()


def test_process_pair_refuses_to_delete_populated_canonical_dry_run():
    """Same guard applies in dry-run: surfaces the issue without writes."""
    session = MagicMock()
    session.get.return_value = _resp(
        200, body={"sites": [_populated_canonical_record()]}
    )
    ok = cleanup._process_pair(
        session,
        "https://x",
        "secret",
        phantom="phantom",
        canonical="canonical",
        dry_run=True,
    )
    assert ok is False


def test_process_pair_canonical_absent_proceeds():
    """Canonical not in sites.json = absent = stub-equivalent. Proceed."""
    session = MagicMock()
    session.get.return_value = _resp(200, body={"sites": []})
    session.delete.return_value = _resp(404)  # already gone
    session.post.return_value = _resp(
        200,
        body={
            "ok": True,
            "action": "rename",
            "overrides_moved": True,
            "reviews_moved": False,
        },
    )
    ok = cleanup._process_pair(
        session,
        "https://x",
        "secret",
        phantom="phantom",
        canonical="canonical",
        dry_run=False,
    )
    assert ok is True


def test_process_pair_happy_path():
    session = MagicMock()
    session.get.return_value = _resp(
        200, body={"sites": [_stub_canonical_record()]}
    )
    session.delete.return_value = _resp(200)
    session.post.return_value = _resp(
        200,
        body={
            "ok": True,
            "action": "rename",
            "overrides_moved": False,
            "reviews_moved": False,
        },
    )
    ok = cleanup._process_pair(
        session,
        "https://x",
        "secret",
        phantom="phantom",
        canonical="canonical",
        dry_run=False,
    )
    assert ok is True


def test_process_pair_delete_failure_aborts_rename():
    session = MagicMock()
    session.get.return_value = _resp(
        200, body={"sites": [_stub_canonical_record()]}
    )
    session.delete.return_value = _resp(500, text="bad")
    ok = cleanup._process_pair(
        session,
        "https://x",
        "secret",
        phantom="phantom",
        canonical="canonical",
        dry_run=False,
    )
    assert ok is False
    session.post.assert_not_called()


def test_process_pair_rename_partial_failure_returns_false():
    """Pre-flight passes, DELETE succeeds, but rename returns 502
    rename_partial. Caller must surface this so the run reports failure
    and is retried."""
    session = MagicMock()
    session.get.return_value = _resp(
        200, body={"sites": [_stub_canonical_record()]}
    )
    session.delete.return_value = _resp(200)
    session.post.return_value = _resp(
        502,
        body={
            "ok": False,
            "action": "rename_partial",
            "sites_renamed": True,
            "overrides_had_data": True,
            "overrides_moved": False,
            "overrides_error": "GitHub 422",
            "reviews_had_data": False,
            "reviews_moved": False,
            "reviews_error": None,
        },
    )
    ok = cleanup._process_pair(
        session,
        "https://x",
        "secret",
        phantom="phantom",
        canonical="canonical",
        dry_run=False,
    )
    assert ok is False


def test_process_pair_preflight_fetch_failure_aborts():
    """Pre-flight network failure: don't blindly proceed; abort the pair."""
    import requests as _req

    session = MagicMock()
    session.get.side_effect = _req.RequestException("dns")
    ok = cleanup._process_pair(
        session,
        "https://x",
        "secret",
        phantom="phantom",
        canonical="canonical",
        dry_run=False,
    )
    assert ok is False
    session.delete.assert_not_called()
    session.post.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# main() — argparse + filtering
# ──────────────────────────────────────────────────────────────────────────────


def test_main_apply_without_secret_returns_2(monkeypatch, caplog):
    monkeypatch.delenv("DASHBOARD_PUBLISH_SECRET", raising=False)
    rc = cleanup.main(["--apply"])
    assert rc == 2


def test_main_pair_filter_no_match_returns_2(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PUBLISH_SECRET", "x")
    rc = cleanup.main(["--apply", "--pair", "no-such-substring-anywhere"])
    assert rc == 2


def test_main_dry_run_no_secret_required(monkeypatch):
    """Dry run should work without DASHBOARD_PUBLISH_SECRET (no writes)."""
    monkeypatch.delenv("DASHBOARD_PUBLISH_SECRET", raising=False)
    with patch.object(cleanup, "_process_pair", return_value=True) as mp:
        rc = cleanup.main(["--dry-run"])
    assert rc == 0
    # All pairs were processed
    assert mp.call_count == len(cleanup.PHANTOM_TO_CANONICAL)


def test_main_pair_filter_matches_substring(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PUBLISH_SECRET", "x")
    with patch.object(cleanup, "_process_pair", return_value=True) as mp:
        rc = cleanup.main(["--apply", "--pair", "miami-beach"])
    assert rc == 0
    # Filter matches the Miami Beach phantom only
    assert mp.call_count == 1
    kwargs = mp.call_args.kwargs
    assert "miami-beach" in kwargs["phantom"].lower()


def test_main_failed_pair_returns_1(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PUBLISH_SECRET", "x")
    with patch.object(cleanup, "_process_pair", return_value=False):
        rc = cleanup.main(["--apply", "--pair", "miami-beach"])
    assert rc == 1
