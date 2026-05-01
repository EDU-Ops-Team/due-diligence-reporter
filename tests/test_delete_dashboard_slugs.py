"""Tests for scripts/delete_dashboard_slugs.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "delete_dashboard_slugs.py"

_spec = importlib.util.spec_from_file_location("delete_dashboard_slugs", _SCRIPT_PATH)
delete_dashboard_slugs = importlib.util.module_from_spec(_spec)
sys.modules["delete_dashboard_slugs"] = delete_dashboard_slugs
_spec.loader.exec_module(delete_dashboard_slugs)  # type: ignore[union-attr]


def _resp(status: int, text: str = "{}"):
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


# ---------- _parse_slugs ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a,b,c", ["a", "b", "c"]),
        ("a\nb\nc", ["a", "b", "c"]),
        ("a, b ,c\n  d  ", ["a", "b", "c", "d"]),
        ("", []),
        (",,,", []),
    ],
)
def test_parse_slugs(raw, expected):
    assert delete_dashboard_slugs._parse_slugs(raw) == expected


# ---------- _delete ----------


def test_delete_returns_success_on_200():
    captured: dict = {}

    def fake_delete(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _resp(200)

    with patch.object(delete_dashboard_slugs.requests, "delete", side_effect=fake_delete):
        ok, note = delete_dashboard_slugs._delete(
            "https://dash.example.com", "my-slug", "secret-xyz", "test-reason"
        )

    assert ok is True
    assert note == "deleted"
    assert captured["url"].endswith("/api/sites/my-slug/publish")
    assert captured["headers"]["Authorization"] == "Bearer secret-xyz"
    assert captured["headers"]["X-Reconcile-Reason"] == "test-reason"


def test_delete_treats_404_as_success():
    """If the slug is already gone (idempotent re-run), 404 is OK."""
    with patch.object(delete_dashboard_slugs.requests, "delete", return_value=_resp(404)):
        ok, note = delete_dashboard_slugs._delete(
            "https://dash.example.com", "absent-slug", "secret", "reason"
        )
    assert ok is True
    assert "absent" in note


def test_delete_fails_on_500():
    with patch.object(delete_dashboard_slugs.requests, "delete", return_value=_resp(500, "boom")):
        ok, note = delete_dashboard_slugs._delete(
            "https://dash.example.com", "slug", "secret", "reason"
        )
    assert ok is False
    assert "500" in note


def test_delete_fails_on_network_error():
    import requests as _requests

    def boom(*_a, **_kw):
        raise _requests.RequestException("boom")

    with patch.object(delete_dashboard_slugs.requests, "delete", side_effect=boom):
        ok, note = delete_dashboard_slugs._delete(
            "https://dash.example.com", "slug", "secret", "reason"
        )
    assert ok is False
    assert "network error" in note


# ---------- main ----------


def test_main_dry_run_prints_and_does_not_call(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PUBLISH_SECRET", "secret")
    with patch.object(delete_dashboard_slugs.requests, "delete") as mock_delete:
        rc = delete_dashboard_slugs.main(["--slugs", "a,b,c"])
    assert rc == 0
    mock_delete.assert_not_called()


def test_main_apply_calls_delete_for_each_slug(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PUBLISH_SECRET", "secret")
    # Patch sleep so the test stays fast.
    with patch.object(delete_dashboard_slugs.requests, "delete", return_value=_resp(200)) as mock_delete, \
         patch.object(delete_dashboard_slugs.time, "sleep"):
        rc = delete_dashboard_slugs.main(["--slugs", "a,b,c", "--apply", "--reason", "cleanup"])
    assert rc == 0
    assert mock_delete.call_count == 3
    # All three calls carry the reason header.
    for call in mock_delete.call_args_list:
        assert call.kwargs["headers"]["X-Reconcile-Reason"] == "cleanup"


def test_main_apply_returns_nonzero_on_partial_failure(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PUBLISH_SECRET", "secret")

    responses = [_resp(200), _resp(500, "boom"), _resp(200)]

    def fake_delete(*_a, **_kw):
        return responses.pop(0)

    with patch.object(delete_dashboard_slugs.requests, "delete", side_effect=fake_delete), \
         patch.object(delete_dashboard_slugs.time, "sleep"):
        rc = delete_dashboard_slugs.main(["--slugs", "a,b,c", "--apply"])

    assert rc == 4  # one failure


def test_main_refuses_apply_without_secret(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PUBLISH_SECRET", raising=False)
    rc = delete_dashboard_slugs.main(["--slugs", "a", "--apply"])
    assert rc == 3


def test_main_errors_on_no_slugs(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PUBLISH_SECRET", "secret")
    # Force stdin to look like a tty so it doesn't try to read.
    with patch.object(delete_dashboard_slugs.sys.stdin, "isatty", return_value=True):
        rc = delete_dashboard_slugs.main(["--slugs", ""])
    assert rc == 2
