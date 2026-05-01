"""Tests for scripts/reconcile_dashboard.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "reconcile_dashboard.py"

_spec = importlib.util.spec_from_file_location("reconcile_dashboard", _SCRIPT_PATH)
reconcile_dashboard = importlib.util.module_from_spec(_spec)
sys.modules["reconcile_dashboard"] = reconcile_dashboard
_spec.loader.exec_module(reconcile_dashboard)  # type: ignore[union-attr]


# ---------- helpers ----------

def _wrike_record(title: str, status_id: str | None) -> dict:
    rec: dict = {"title": title}
    if status_id is not None:
        rec["customStatusId"] = status_id
    return rec


# ---------- _is_dry_run ----------

@pytest.mark.parametrize(
    "value,expected",
    [
        (None, True),  # default ON
        ("1", True),
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("no", False),
        ("yes", True),
        ("", True),
        ("anything-else", True),
    ],
)
def test_is_dry_run(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("RECONCILE_DRY_RUN", raising=False)
    else:
        monkeypatch.setenv("RECONCILE_DRY_RUN", value)
    assert reconcile_dashboard._is_dry_run() is expected


# ---------- _expected_slugs_from_wrike ----------

def test_expected_slugs_from_wrike_partitions_active_and_inactive(monkeypatch):
    records = [
        _wrike_record("Alpha School Tulsa 421 E 11th St", "ACTIVE_ID"),
        _wrike_record("Alpha School Minneapolis 1128", "CANCELLED_ID"),
        _wrike_record("Alpha School Dallas 4152", "ACTIVE_ID"),
        _wrike_record("", "ACTIVE_ID"),  # blank title — skipped
    ]

    monkeypatch.setattr(reconcile_dashboard, "load_wrike_config", lambda: MagicMock(access_token="t"))
    monkeypatch.setattr(reconcile_dashboard, "_get_all_site_records", lambda cfg: records)
    monkeypatch.setattr(
        reconcile_dashboard,
        "_get_active_status_ids",
        lambda access_token: {"ACTIVE_ID"},
    )

    expected, inactive = reconcile_dashboard._expected_slugs_from_wrike()

    assert expected == {
        "alpha-school-tulsa-421-e-11th-st",
        "alpha-school-dallas-4152",
    }
    assert inactive == {"alpha-school-minneapolis-1128": "CANCELLED_ID"}


# ---------- main(): dry-run finds orphans without deleting ----------

def test_main_dry_run_logs_orphans_and_does_not_delete(monkeypatch, caplog):
    monkeypatch.setenv("RECONCILE_DRY_RUN", "1")
    monkeypatch.setenv("DASHBOARD_PUBLISH_URL", "https://dash.example")
    monkeypatch.setenv("DASHBOARD_PUBLISH_SECRET", "shh")

    monkeypatch.setattr(
        reconcile_dashboard,
        "_fetch_dashboard_slugs",
        lambda base_url: [
            {"slug": "alpha-school-tulsa-421-e-11th-st"},
            {"slug": "alpha-school-minneapolis-1128"},  # inactive in Wrike
            {"slug": "ghost-site-no-wrike-record"},     # not in Wrike at all
        ],
    )
    monkeypatch.setattr(
        reconcile_dashboard,
        "_expected_slugs_from_wrike",
        lambda: (
            {"alpha-school-tulsa-421-e-11th-st"},
            {"alpha-school-minneapolis-1128": "CANCELLED_ID"},
        ),
    )
    delete_calls: list[tuple] = []
    monkeypatch.setattr(
        reconcile_dashboard,
        "_delete_site",
        lambda *a, **kw: delete_calls.append((a, kw)) or True,
    )

    caplog.set_level("INFO", logger="reconcile_dashboard")
    rc = reconcile_dashboard.main()

    assert rc == 0
    assert delete_calls == []  # dry-run never deletes
    text = caplog.text
    assert "DRY_RUN" in text
    assert "alpha-school-minneapolis-1128" in text
    assert "ghost-site-no-wrike-record" in text
    assert "no DELETE calls issued" in text


# ---------- main(): apply mode deletes only orphans ----------

def test_main_apply_mode_deletes_each_orphan_with_reason(monkeypatch):
    monkeypatch.setenv("RECONCILE_DRY_RUN", "0")
    monkeypatch.setenv("DASHBOARD_PUBLISH_URL", "https://dash.example")
    monkeypatch.setenv("DASHBOARD_PUBLISH_SECRET", "shh")

    monkeypatch.setattr(
        reconcile_dashboard,
        "_fetch_dashboard_slugs",
        lambda base_url: [
            {"slug": "active-site"},
            {"slug": "alpha-school-minneapolis-1128"},
            {"slug": "ghost-site"},
        ],
    )
    monkeypatch.setattr(
        reconcile_dashboard,
        "_expected_slugs_from_wrike",
        lambda: (
            {"active-site"},
            {"alpha-school-minneapolis-1128": "CANCELLED_ID"},
        ),
    )

    delete_calls: list[dict] = []

    def fake_delete(base_url, slug, secret, *, reason, timeout=20):
        delete_calls.append(
            {"base_url": base_url, "slug": slug, "secret": secret, "reason": reason}
        )
        return True

    monkeypatch.setattr(reconcile_dashboard, "_delete_site", fake_delete)

    rc = reconcile_dashboard.main()

    assert rc == 0
    by_slug = {c["slug"]: c for c in delete_calls}
    # Active site is NOT touched
    assert "active-site" not in by_slug
    # Orphans get correct reasons
    assert by_slug["alpha-school-minneapolis-1128"]["reason"] == "wrike-status:CANCELLED_ID"
    assert by_slug["ghost-site"]["reason"] == "wrike-record-missing"
    assert by_slug["alpha-school-minneapolis-1128"]["base_url"] == "https://dash.example"
    assert by_slug["alpha-school-minneapolis-1128"]["secret"] == "shh"


# ---------- main(): apply mode without secret refuses to run ----------

def test_main_apply_mode_without_secret_refuses(monkeypatch):
    monkeypatch.setenv("RECONCILE_DRY_RUN", "0")
    monkeypatch.delenv("DASHBOARD_PUBLISH_SECRET", raising=False)

    rc = reconcile_dashboard.main()
    assert rc == 2


# ---------- main(): no orphans → exits cleanly ----------

def test_main_no_orphans_returns_zero(monkeypatch):
    monkeypatch.setenv("RECONCILE_DRY_RUN", "1")
    monkeypatch.setattr(
        reconcile_dashboard,
        "_fetch_dashboard_slugs",
        lambda base_url: [{"slug": "active-site"}],
    )
    monkeypatch.setattr(
        reconcile_dashboard,
        "_expected_slugs_from_wrike",
        lambda: ({"active-site"}, {}),
    )
    delete_calls: list = []
    monkeypatch.setattr(
        reconcile_dashboard,
        "_delete_site",
        lambda *a, **kw: delete_calls.append(1) or True,
    )

    rc = reconcile_dashboard.main()
    assert rc == 0
    assert delete_calls == []


# ---------- main(): apply mode counts failures and surfaces non-zero rc ----------

def test_main_apply_mode_returns_nonzero_when_delete_fails(monkeypatch):
    monkeypatch.setenv("RECONCILE_DRY_RUN", "0")
    monkeypatch.setenv("DASHBOARD_PUBLISH_SECRET", "shh")
    monkeypatch.setattr(
        reconcile_dashboard,
        "_fetch_dashboard_slugs",
        lambda base_url: [{"slug": "ghost-1"}, {"slug": "ghost-2"}],
    )
    monkeypatch.setattr(
        reconcile_dashboard,
        "_expected_slugs_from_wrike",
        lambda: (set(), {}),
    )

    def flaky_delete(base_url, slug, secret, *, reason, timeout=20):
        return slug == "ghost-1"

    monkeypatch.setattr(reconcile_dashboard, "_delete_site", flaky_delete)

    rc = reconcile_dashboard.main()
    assert rc == 5  # one failure


# ---------- _delete_site: 200/404 = success, others = fail ----------

def test_delete_site_treats_200_and_404_as_success(monkeypatch):
    calls: list[dict] = []

    def fake_delete(url, headers=None, timeout=20):
        calls.append({"url": url, "headers": headers})
        resp = MagicMock()
        resp.status_code = 200 if "ok-slug" in url else 404
        resp.text = ""
        return resp

    monkeypatch.setattr(reconcile_dashboard.requests, "delete", fake_delete)

    assert reconcile_dashboard._delete_site(
        "https://dash.example", "ok-slug", "shh", reason="wrike-status:X"
    )
    assert reconcile_dashboard._delete_site(
        "https://dash.example", "missing-slug", "shh", reason="wrike-record-missing"
    )

    assert calls[0]["url"] == "https://dash.example/api/sites/ok-slug/publish"
    assert calls[0]["headers"]["Authorization"] == "Bearer shh"
    assert calls[0]["headers"]["X-Reconcile-Reason"] == "wrike-status:X"


def test_delete_site_returns_false_on_http_error(monkeypatch):
    def fake_delete(url, headers=None, timeout=20):
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "boom"
        return resp

    monkeypatch.setattr(reconcile_dashboard.requests, "delete", fake_delete)

    assert (
        reconcile_dashboard._delete_site(
            "https://dash.example", "slug", "shh", reason="r"
        )
        is False
    )
