"""Tests for scripts/validate_rebl_slugs.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "validate_rebl_slugs.py"

_spec = importlib.util.spec_from_file_location("validate_rebl_slugs", _SCRIPT_PATH)
validate_rebl_slugs = importlib.util.module_from_spec(_spec)
sys.modules["validate_rebl_slugs"] = validate_rebl_slugs
_spec.loader.exec_module(validate_rebl_slugs)  # type: ignore[union-attr]


# ---------- classify_rebl_response ----------


@pytest.mark.parametrize(
    "obj,expected_slug,expected_status",
    [
        # Direct slug match (the canonical path)
        (
            {
                "site_id": "6940-s-utica-ave-tulsa-ok",
                "matched_by": "slug",
                "scored": True,
                "lat": 36.0,
                "lng": -96.0,
            },
            "6940-s-utica-ave-tulsa-ok",
            "ok",
        ),
        # geocode_slug match -> still trustable
        (
            {
                "site_id": "4717-fletcher-ave-fort-worth-tx",
                "matched_by": "geocode_slug",
                "scored": True,
                "lat": 32.7,
                "lng": -97.3,
            },
            "4717-fletcher-ave-fort-worth-tx",
            "ok",
        ),
        # scored: false but valid match still returns the slug
        (
            {
                "site_id": "995-oak-creek-dr-lombard-il",
                "matched_by": "geocode_slug",
                "scored": False,
                "lat": 41.8,
                "lng": -88.0,
            },
            "995-oak-creek-dr-lombard-il",
            "ok",
        ),
        # matched_by=none with lat/lng -> trust the slug (Rebl knows it geographically)
        (
            {
                "site_id": "some-new-place",
                "matched_by": "none",
                "scored": False,
                "lat": 40.0,
                "lng": -100.0,
            },
            "some-new-place",
            "ok",
        ),
        # matched_by=none AND no lat/lng -> MISSING
        (
            {
                "site_id": "garbage",
                "matched_by": "none",
                "scored": False,
            },
            None,
            "missing",
        ),
        # Explicit error key -> MISSING
        (
            {"index": 3, "error": "Could not build address"},
            None,
            "missing",
        ),
        # Empty site_id -> MISSING
        (
            {"site_id": "", "matched_by": "slug", "lat": 1.0, "lng": 2.0},
            None,
            "missing",
        ),
        # None response -> api_error
        (None, None, "api_error"),
    ],
)
def test_classify_rebl_response(obj, expected_slug, expected_status):
    slug, status, _note = validate_rebl_slugs.classify_rebl_response(obj)
    assert slug == expected_slug
    assert status == expected_status


# ---------- resolve_addresses_in_batches ----------


def test_resolve_addresses_in_batches_chunks_and_preserves_order():
    addresses = [f"addr-{i}" for i in range(7)]

    captured_payloads: list[list[dict]] = []

    def _fake_post(url, json=None, timeout=None):  # noqa: A002
        captured_payloads.append(json)
        # Echo back a site_id derived from the address so we can verify order.
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = lambda: None
        resp.json = lambda: [
            {"site_id": item["address"], "matched_by": "slug", "lat": 1, "lng": 2}
            for item in json
        ]
        return resp

    with patch.object(validate_rebl_slugs.requests, "post", side_effect=_fake_post):
        results = validate_rebl_slugs.resolve_addresses_in_batches(
            addresses, batch_size=3, resolve_url="http://stub"
        )

    assert len(results) == 7
    assert [r["site_id"] for r in results] == addresses
    # 7 items in batches of 3 -> 3 calls (3+3+1)
    assert len(captured_payloads) == 3
    assert [len(p) for p in captured_payloads] == [3, 3, 1]


def test_resolve_addresses_in_batches_returns_none_on_network_failure():
    import requests as _requests

    def _boom(*_a, **_kw):
        raise _requests.RequestException("boom")

    with patch.object(validate_rebl_slugs.requests, "post", side_effect=_boom):
        results = validate_rebl_slugs.resolve_addresses_in_batches(
            ["a", "b"], batch_size=5, resolve_url="http://stub"
        )

    assert results == [None, None]


def test_resolve_addresses_in_batches_handles_malformed_response():
    def _fake_post(*_a, **_kw):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = lambda: None
        resp.json = lambda: {"not": "a list"}
        return resp

    with patch.object(validate_rebl_slugs.requests, "post", side_effect=_fake_post):
        results = validate_rebl_slugs.resolve_addresses_in_batches(
            ["a", "b"], batch_size=5, resolve_url="http://stub"
        )

    assert results == [None, None]


# ---------- build_rows ----------


def _summary(title: str, address: str, wid: str = "1") -> dict:
    return {"id": wid, "title": title, "address": address}


def _rebl_ok(site_id: str) -> dict:
    return {"site_id": site_id, "matched_by": "slug", "scored": True, "lat": 1, "lng": 2}


def test_build_rows_classifies_ok_when_slugs_match():
    summaries = [_summary("Tulsa", "6940 S Utica Ave, Tulsa, OK")]
    dashboard = {
        "6940-s-utica-ave-tulsa-ok": {
            "slug": "6940-s-utica-ave-tulsa-ok",
            "address": "6940 S Utica Ave, Tulsa, OK",
        }
    }
    rebl_results = [_rebl_ok("6940-s-utica-ave-tulsa-ok")]

    rows = validate_rebl_slugs.build_rows(summaries, dashboard, rebl_results)
    assert len(rows) == 1
    assert rows[0].classification == "ok"
    assert rows[0].dashboard_slug == "6940-s-utica-ave-tulsa-ok"
    assert rows[0].rebl_slug == "6940-s-utica-ave-tulsa-ok"


def test_build_rows_classifies_migrate_when_slugs_differ():
    summaries = [_summary("Tulsa", "6940 S Utica Ave, Tulsa, OK")]
    dashboard = {
        "alpha-school-tulsa-6940-s-utica-ave": {
            "slug": "alpha-school-tulsa-6940-s-utica-ave",
            "address": "6940 S Utica Ave, Tulsa, OK",
        }
    }
    rebl_results = [_rebl_ok("6940-s-utica-ave-tulsa-ok")]

    rows = validate_rebl_slugs.build_rows(summaries, dashboard, rebl_results)
    assert rows[0].classification == "migrate"
    assert rows[0].dashboard_slug == "alpha-school-tulsa-6940-s-utica-ave"
    assert rows[0].rebl_slug == "6940-s-utica-ave-tulsa-ok"


def test_build_rows_finds_match_via_stored_rebl_site_id():
    summaries = [_summary("Tulsa", "6940 S Utica Ave, Tulsa, OK")]
    # Address differs (perhaps the dashboard stored the marketing form), but
    # meta.rebl.site_id matches the canonical slug.
    dashboard = {
        "alpha-school-tulsa-6940-s-utica-ave": {
            "slug": "alpha-school-tulsa-6940-s-utica-ave",
            "address": "Different formatted address",
            "meta": {"rebl": {"site_id": "6940-s-utica-ave-tulsa-ok"}},
        }
    }
    rebl_results = [_rebl_ok("6940-s-utica-ave-tulsa-ok")]

    rows = validate_rebl_slugs.build_rows(summaries, dashboard, rebl_results)
    assert rows[0].classification == "migrate"
    assert rows[0].dashboard_slug == "alpha-school-tulsa-6940-s-utica-ave"


def test_build_rows_classifies_unknown_when_not_on_dashboard():
    summaries = [_summary("New Site", "999 Nowhere St, Austin, TX")]
    dashboard: dict = {}
    rebl_results = [_rebl_ok("999-nowhere-st-austin-tx")]

    rows = validate_rebl_slugs.build_rows(summaries, dashboard, rebl_results)
    assert rows[0].classification == "unknown"
    assert rows[0].rebl_slug == "999-nowhere-st-austin-tx"
    assert rows[0].dashboard_slug is None


def test_build_rows_classifies_missing_on_rebl_error():
    summaries = [_summary("Bad", "")]
    dashboard: dict = {}
    rebl_results = [{"index": 0, "error": "Could not build address"}]

    rows = validate_rebl_slugs.build_rows(summaries, dashboard, rebl_results)
    assert rows[0].classification == "missing"
    assert "Could not build address" in rows[0].note


def test_build_rows_classifies_api_error_on_none():
    summaries = [_summary("Net Down", "100 Main St")]
    dashboard: dict = {}
    rebl_results = [None]

    rows = validate_rebl_slugs.build_rows(summaries, dashboard, rebl_results)
    assert rows[0].classification == "api_error"
    assert rows[0].rebl_error


def test_build_rows_raises_on_length_mismatch():
    with pytest.raises(RuntimeError):
        validate_rebl_slugs.build_rows(
            [_summary("a", "x"), _summary("b", "y")],
            {},
            [_rebl_ok("a")],  # only one result for two summaries
        )


# ---------- migrate_slug ----------


def _ok_response(status: int = 200, text: str = "{}"):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


def _json_response(status: int, body: dict | None = None, raw_text: str | None = None):
    """Build a mock requests.Response.

    If ``raw_text`` is given, ``.json()`` raises ValueError (mimics Vercel's
    HTML 404 page, which our fallback path keys on).
    """
    resp = MagicMock()
    resp.status_code = status
    if raw_text is not None:
        resp.text = raw_text
        resp.json.side_effect = ValueError("not json")
    else:
        body = body or {}
        resp.text = repr(body)
        resp.json.return_value = body
    return resp


def test_migrate_slug_calls_rename_endpoint_on_happy_path():
    """Preferred path: POST /api/sites/{old}/rename {new_slug}. No
    fallback to publish/delete."""
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        return _json_response(200, {"ok": True, "action": "rename"})

    full_record = {
        "slug": "alpha-school-tulsa-6940-s-utica-ave",
        "site_name": "Tulsa",
    }

    with patch.object(validate_rebl_slugs.requests, "post", side_effect=fake_post), \
         patch.object(validate_rebl_slugs.requests, "delete") as mock_delete:
        ok, note = validate_rebl_slugs.migrate_slug(
            "https://dash.example.com",
            "secret-xyz",
            old_slug="alpha-school-tulsa-6940-s-utica-ave",
            new_slug="6940-s-utica-ave-tulsa-ok",
            full_record=full_record,
        )

    assert ok is True
    assert "renamed" in note
    assert captured["url"].endswith(
        "/api/sites/alpha-school-tulsa-6940-s-utica-ave/rename"
    )
    assert captured["payload"] == {"new_slug": "6940-s-utica-ave-tulsa-ok"}
    assert captured["headers"]["Authorization"] == "Bearer secret-xyz"
    assert "rebl-canonical-slug-migration" in captured["headers"]["X-Reconcile-Reason"]
    assert "alpha-school-tulsa-6940-s-utica-ave" in captured["headers"]["X-Reconcile-Reason"]
    # Critical: the legacy DELETE path must NOT run when rename succeeds,
    # otherwise we'd nuke the row we just renamed.
    mock_delete.assert_not_called()


def test_migrate_slug_fails_on_real_slug_not_found():
    """JSON 404 with message='slug not found' = real not-found, not
    a missing endpoint. Do NOT fall back to legacy."""
    with patch.object(
        validate_rebl_slugs.requests,
        "post",
        return_value=_json_response(404, {"message": "slug not found"}),
    ) as mock_post, \
         patch.object(validate_rebl_slugs.requests, "delete") as mock_delete:
        ok, note = validate_rebl_slugs.migrate_slug(
            "https://dash.example.com",
            "secret",
            old_slug="old",
            new_slug="new",
            full_record={"slug": "old"},
        )
    assert ok is False
    assert "not on dashboard" in note
    # Exactly one POST (rename), no fallback POST/DELETE.
    assert mock_post.call_count == 1
    mock_delete.assert_not_called()


def test_migrate_slug_falls_back_to_legacy_on_endpoint_missing():
    """HTML 404 (Vercel hasn't deployed rename.ts yet) triggers legacy
    POST(new)+DELETE(old) so the slug rename still happens."""
    posts: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posts.append({"url": url, "json": json})
        if url.endswith("/rename"):
            return _json_response(404, raw_text="<html>404</html>")
        # Legacy publish call.
        return _json_response(200, {"ok": True})

    with patch.object(validate_rebl_slugs.requests, "post", side_effect=fake_post), \
         patch.object(
             validate_rebl_slugs.requests,
             "delete",
             return_value=_json_response(200, {"ok": True}),
         ) as mock_delete:
        ok, note = validate_rebl_slugs.migrate_slug(
            "https://dash.example.com",
            "secret",
            old_slug="old",
            new_slug="new",
            full_record={"slug": "old", "site_name": "X"},
        )
    assert ok is True
    assert "legacy" in note
    # rename + legacy publish.
    assert len(posts) == 2
    assert posts[0]["url"].endswith("/api/sites/old/rename")
    assert posts[1]["url"].endswith("/api/sites/new/publish")
    # Slug was overwritten on the legacy site_meta.
    assert posts[1]["json"]["site_meta"]["slug"] == "new"
    # And legacy DELETE fired against the old slug.
    assert mock_delete.call_count == 1
    assert mock_delete.call_args.args[0].endswith("/api/sites/old/publish")


def test_migrate_slug_legacy_treats_delete_404_as_success():
    """Half-applied prior legacy run: rename returns HTML 404 (endpoint
    missing), publish succeeds, DELETE returns 404 (already gone). The
    overall migration should still report success."""

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("/rename"):
            return _json_response(404, raw_text="missing")
        return _json_response(200, {"ok": True})

    with patch.object(validate_rebl_slugs.requests, "post", side_effect=fake_post), \
         patch.object(
             validate_rebl_slugs.requests,
             "delete",
             return_value=_json_response(404, {"message": "slug not found"}),
         ):
        ok, note = validate_rebl_slugs.migrate_slug(
            "https://dash.example.com",
            "secret",
            old_slug="old",
            new_slug="new",
            full_record={"slug": "old"},
        )
    assert ok is True
    assert "legacy" in note


def test_migrate_slug_fails_on_collision():
    """409 from rename = both slugs exist as distinct records. Caller
    must resolve manually; do NOT fall back to legacy (which would
    silently merge)."""
    with patch.object(
        validate_rebl_slugs.requests,
        "post",
        return_value=_json_response(409, {"message": "new_slug collides"}),
    ) as mock_post, \
         patch.object(validate_rebl_slugs.requests, "delete") as mock_delete:
        ok, note = validate_rebl_slugs.migrate_slug(
            "https://dash.example.com",
            "secret",
            old_slug="old",
            new_slug="new",
            full_record={"slug": "old"},
        )
    assert ok is False
    assert "409" in note
    assert "collides" in note
    assert mock_post.call_count == 1
    mock_delete.assert_not_called()


def test_migrate_slug_fails_on_unexpected_status():
    """500 from rename = real server error. Surface verbatim, no
    fallback (the rename endpoint exists, it just blew up)."""
    with patch.object(
        validate_rebl_slugs.requests,
        "post",
        return_value=_json_response(500, {"message": "boom"}),
    ) as mock_post, \
         patch.object(validate_rebl_slugs.requests, "delete") as mock_delete:
        ok, note = validate_rebl_slugs.migrate_slug(
            "https://dash.example.com",
            "secret",
            old_slug="old",
            new_slug="new",
            full_record={"slug": "old"},
        )
    assert ok is False
    assert "500" in note
    assert mock_post.call_count == 1
    mock_delete.assert_not_called()


def test_migrate_slug_legacy_fails_on_publish_error():
    """Fallback path: if legacy publish fails after rename returns HTML
    404, surface the error and do NOT issue DELETE."""

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("/rename"):
            return _json_response(404, raw_text="nope")
        return _json_response(500, {"message": "boom"})

    with patch.object(validate_rebl_slugs.requests, "post", side_effect=fake_post), \
         patch.object(validate_rebl_slugs.requests, "delete") as mock_delete:
        ok, note = validate_rebl_slugs.migrate_slug(
            "https://dash.example.com",
            "secret",
            old_slug="old",
            new_slug="new",
            full_record={"slug": "old"},
        )
    assert ok is False
    assert "POST new" in note
    mock_delete.assert_not_called()


# ---------- render_report ----------


def test_render_report_includes_all_sections():
    rows = [
        validate_rebl_slugs.SiteRow(
            title="Migrate Site",
            address="addr1",
            dashboard_slug="old-slug",
            rebl_slug="new-slug",
            rebl_matched_by="slug",
            classification="migrate",
        ),
        validate_rebl_slugs.SiteRow(
            title="Missing Site",
            address="addr2",
            classification="missing",
            note="Rebl matched_by=none and no lat/lng",
        ),
        validate_rebl_slugs.SiteRow(
            title="Net Site",
            address="addr3",
            classification="api_error",
            rebl_error="boom",
        ),
        validate_rebl_slugs.SiteRow(
            title="OK Site",
            address="addr4",
            dashboard_slug="canonical",
            rebl_slug="canonical",
            classification="ok",
        ),
    ]
    out = validate_rebl_slugs.render_report(rows)
    assert "ok=1" in out
    assert "migrate=1" in out
    assert "missing=1" in out
    assert "api_error=1" in out
    assert "Migrate Site" in out
    assert "Missing Site" in out
    assert "Net Site" in out
