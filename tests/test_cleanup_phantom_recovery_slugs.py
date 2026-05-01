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
# _rename — additional 502 partial-failure scenarios (iter2)
# ──────────────────────────────────────────────────────────────────────────────


def test_rename_502_partial_failure_overrides():
    """502 with overrides_had_data=True, overrides_moved=False — surfaces
    the GitHub commit error in the note for ops to triage."""
    session = MagicMock()
    session.post.return_value = _resp(
        502,
        body={
            "ok": False,
            "action": "rename_partial",
            "sites_renamed": True,
            "note": "rename",
            "overrides_had_data": True,
            "overrides_moved": False,
            "overrides_fetch_failed": False,
            "overrides_error": "GitHub 422",
            "reviews_had_data": False,
            "reviews_moved": False,
            "reviews_fetch_failed": False,
            "reviews_error": None,
        },
    )
    ok, note = cleanup._rename(
        session, "https://x", "secret", old_slug="old", new_slug="new"
    )
    assert ok is False
    assert "overrides_had_data=True" in note
    assert "overrides_moved=False" in note
    assert "overrides_fetch_failed=False" in note
    assert "GitHub 422" in note


def test_rename_502_partial_failure_reviews():
    """IMP-4: reviews-side per-slug data failed — caller MUST retry."""
    session = MagicMock()
    session.post.return_value = _resp(
        502,
        body={
            "ok": False,
            "action": "rename_partial",
            "sites_renamed": True,
            "note": "rename",
            "overrides_had_data": False,
            "overrides_moved": False,
            "overrides_fetch_failed": False,
            "overrides_error": None,
            "reviews_had_data": True,
            "reviews_moved": False,
            "reviews_fetch_failed": False,
            "reviews_error": "GitHub 409",
        },
    )
    ok, note = cleanup._rename(
        session, "https://x", "secret", old_slug="old", new_slug="new"
    )
    assert ok is False
    assert "reviews_had_data=True" in note
    assert "reviews_moved=False" in note
    assert "GitHub 409" in note


def test_rename_502_fetch_failure_surfaced():
    """C-1 regression test: fetch-side throw on the dashboard must surface
    in the 502 body, not silently turn into a 200 success. Caller logs
    the fetch_failed flag and retries."""
    session = MagicMock()
    session.post.return_value = _resp(
        502,
        body={
            "ok": False,
            "action": "rename_partial",
            "sites_renamed": True,
            "note": "rename",
            "overrides_had_data": False,
            "overrides_moved": False,
            "overrides_fetch_failed": True,
            "overrides_error": "GitHub fetch network error: ETIMEDOUT",
            "reviews_had_data": False,
            "reviews_moved": False,
            "reviews_fetch_failed": False,
            "reviews_error": None,
        },
    )
    ok, note = cleanup._rename(
        session, "https://x", "secret", old_slug="old", new_slug="new"
    )
    assert ok is False
    assert "overrides_fetch_failed=True" in note
    assert "ETIMEDOUT" in note


def test_rename_502_already_renamed_note():
    """Convergence retry hits the dashboard noop branch, which can still
    return 502 if the per-slug re-key fails again. The 'already-renamed'
    note distinguishes this from the fresh-rename 502 in logs."""
    session = MagicMock()
    session.post.return_value = _resp(
        502,
        body={
            "ok": False,
            "action": "rename_partial",
            "sites_renamed": False,
            "note": "already-renamed",
            "overrides_had_data": True,
            "overrides_moved": False,
            "overrides_fetch_failed": False,
            "overrides_error": "GitHub 422",
            "reviews_had_data": False,
            "reviews_moved": False,
            "reviews_fetch_failed": False,
            "reviews_error": None,
        },
    )
    ok, note = cleanup._rename(
        session, "https://x", "secret", old_slug="old", new_slug="new"
    )
    assert ok is False
    assert "note='already-renamed'" in note


# ──────────────────────────────────────────────────────────────────────────────
# _process_pair — pre-flight + Cases A/B/C/D/E
# ──────────────────────────────────────────────────────────────────────────────


def _stub_canonical_record():
    """Wiped canonical stub: present in sites.json but no real fields."""
    return {"slug": "canonical", "published_at": "2026-04-01T00:00:00Z"}


def _populated_canonical_record():
    return {
        "slug": "canonical",
        "can_we_open": "Yes",
        "scenarios": [{"id": "s1"}],
    }


def _phantom_record():
    return {
        "slug": "phantom",
        "can_we_open": "Yes",
        "scenarios": [{"id": "s1"}],
        "sources": {"x": "https://example.com"},
    }


def _make_get_returning(*records):
    """Build a session.get that returns sites.json with the given records on
    BOTH calls (canonical fetch and phantom fetch). Each fetch scans the
    same sites list for its own slug — so include both records here when
    both must be present."""
    payload = {"sites": list(records)}

    def fake_get(url, timeout=None):
        return _resp(200, body=payload)

    return fake_get


def _make_get_per_slug(canonical_rec=None, phantom_rec=None):
    """Build a session.get that returns DIFFERENT sites.json snapshots per
    fetch. _process_pair calls _fetch_site_record(canonical) first, then
    _fetch_site_record(phantom). Each looks up its own slug in the payload.

    For most tests the same sites list works for both calls. Use this only
    when you need the canonical and phantom fetches to see different
    payloads (e.g. simulating a race)."""
    canonical_payload = {
        "sites": [canonical_rec] if canonical_rec is not None else []
    }
    phantom_payload = {
        "sites": [phantom_rec] if phantom_rec is not None else []
    }
    calls = {"n": 0}

    def fake_get(url, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp(200, body=canonical_payload)
        return _resp(200, body=phantom_payload)

    return fake_get


# ── Case A: fresh state — canonical is wiped stub (or absent), phantom present


def test_process_pair_dry_run_fresh_state_skips_writes():
    """Dry run on Case A: pre-flight runs, no DELETE/rename issued."""
    session = MagicMock()
    session.get.side_effect = _make_get_returning(
        _stub_canonical_record(), _phantom_record()
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
    assert session.get.call_count == 2
    session.delete.assert_not_called()
    session.post.assert_not_called()


def test_process_pair_canonical_absent_proceeds():
    """Canonical not in sites.json (treated as stub-equivalent), phantom
    present — Case A. DELETE is idempotent (404), rename succeeds."""
    session = MagicMock()
    session.get.side_effect = _make_get_returning(_phantom_record())
    session.delete.return_value = _resp(404)
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
    session.delete.assert_called_once()
    session.post.assert_called_once()


def test_process_pair_happy_path():
    """Case A happy path: canonical is wiped stub, phantom present.
    DELETE 200, rename 200."""
    session = MagicMock()
    session.get.side_effect = _make_get_returning(
        _stub_canonical_record(), _phantom_record()
    )
    session.delete.return_value = _resp(200)
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
    session.delete.assert_called_once()
    session.post.assert_called_once()


def test_process_pair_delete_failure_aborts_rename():
    """Case A: DELETE 500 — abort, do not call rename."""
    session = MagicMock()
    session.get.side_effect = _make_get_returning(
        _stub_canonical_record(), _phantom_record()
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
    """Case A: pre-flight ok, DELETE 200, rename 502 rename_partial.
    Surface as failure for retry on next run."""
    session = MagicMock()
    session.get.side_effect = _make_get_returning(
        _stub_canonical_record(), _phantom_record()
    )
    session.delete.return_value = _resp(200)
    session.post.return_value = _resp(
        502,
        body={
            "ok": False,
            "action": "rename_partial",
            "sites_renamed": True,
            "note": "rename",
            "overrides_had_data": True,
            "overrides_moved": False,
            "overrides_fetch_failed": False,
            "overrides_error": "GitHub 422",
            "reviews_had_data": False,
            "reviews_moved": False,
            "reviews_fetch_failed": False,
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


# ── Case D: wrong map entry — both records populated. REFUSE.


def test_process_pair_refuses_when_both_records_populated():
    """Case D: phantom AND canonical both carry hydrated data.
    The map row is wrong — refuse to act."""
    session = MagicMock()
    session.get.side_effect = _make_get_returning(
        _populated_canonical_record(), _phantom_record()
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


def test_process_pair_refuses_when_both_records_populated_dry_run():
    """Same Case D guard fires under dry-run — surfaces the issue early."""
    session = MagicMock()
    session.get.side_effect = _make_get_returning(
        _populated_canonical_record(), _phantom_record()
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


# ── Case B: post-502 convergence retry — phantom absent, canonical populated.
#    DELETE must be skipped (canonical now holds the real data); rename is
#    re-issued so the dashboard noop path retries the per-slug re-key.


def test_process_pair_convergence_retry_skips_delete_calls_rename():
    """C-2 regression test. Phantom gone, canonical populated. Skipping
    DELETE is critical — the canonical slug now holds phantom's hydrated
    data; deleting it would destroy what we just moved. Rename hits the
    dashboard noop branch which re-attempts the per-slug re-key."""
    session = MagicMock()
    session.get.side_effect = _make_get_returning(_populated_canonical_record())
    session.post.return_value = _resp(
        200,
        body={
            "ok": True,
            "action": "noop",
            "note": "already-renamed",
            "overrides_had_data": True,
            "overrides_moved": True,
            "reviews_had_data": False,
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
    session.delete.assert_not_called()
    session.post.assert_called_once()


def test_process_pair_convergence_retry_dry_run():
    """Dry-run on Case B: log the retry plan, no DELETE, no rename."""
    session = MagicMock()
    session.get.side_effect = _make_get_returning(_populated_canonical_record())
    ok = cleanup._process_pair(
        session,
        "https://x",
        "secret",
        phantom="phantom",
        canonical="canonical",
        dry_run=True,
    )
    assert ok is True
    session.delete.assert_not_called()
    session.post.assert_not_called()


def test_process_pair_convergence_retry_rename_still_502():
    """Convergence retry: rename hits noop path but re-key still fails.
    Surface as failure — next run will retry again."""
    session = MagicMock()
    session.get.side_effect = _make_get_returning(_populated_canonical_record())
    session.post.return_value = _resp(
        502,
        body={
            "ok": False,
            "action": "rename_partial",
            "sites_renamed": False,
            "note": "already-renamed",
            "overrides_had_data": True,
            "overrides_moved": False,
            "overrides_fetch_failed": False,
            "overrides_error": "GitHub 422",
            "reviews_had_data": False,
            "reviews_moved": False,
            "reviews_fetch_failed": False,
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
    session.delete.assert_not_called()
    session.post.assert_called_once()


# ── Case C: already converged — phantom absent, canonical absent OR stub.


def test_process_pair_already_converged_canonical_absent():
    """Case C: nothing in sites.json for either slug. Idempotent skip."""
    session = MagicMock()
    session.get.side_effect = _make_get_returning()  # empty sites
    ok = cleanup._process_pair(
        session,
        "https://x",
        "secret",
        phantom="phantom",
        canonical="canonical",
        dry_run=False,
    )
    assert ok is True
    session.delete.assert_not_called()
    session.post.assert_not_called()


def test_process_pair_already_converged_canonical_stub():
    """Case C: canonical is a wiped stub, phantom absent. Idempotent skip."""
    session = MagicMock()
    session.get.side_effect = _make_get_returning(_stub_canonical_record())
    ok = cleanup._process_pair(
        session,
        "https://x",
        "secret",
        phantom="phantom",
        canonical="canonical",
        dry_run=False,
    )
    assert ok is True
    session.delete.assert_not_called()
    session.post.assert_not_called()


# ── Case E: pre-flight fetch failure on either slug.


def test_process_pair_preflight_fetch_failure_aborts():
    """Pre-flight network failure on canonical fetch: don't blindly
    proceed; abort the pair without calling DELETE or rename."""
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


def test_process_pair_preflight_fetch_failure_on_phantom_aborts():
    """Pre-flight network failure on phantom fetch (second GET): abort."""
    import requests as _req

    session = MagicMock()
    # First GET succeeds (canonical), second GET (phantom) raises.
    canonical_payload = {"sites": [_stub_canonical_record()]}
    calls = {"n": 0}

    def fake_get(url, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp(200, body=canonical_payload)
        raise _req.RequestException("dns")

    session.get.side_effect = fake_get
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
