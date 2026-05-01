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

    expected, inactive, all_titles = reconcile_dashboard._expected_slugs_from_wrike()

    assert expected == {
        "alpha-school-tulsa-421-e-11th-st",
        "alpha-school-dallas-4152",
    }
    assert inactive == {"alpha-school-minneapolis-1128": "CANCELLED_ID"}
    # all_titles preserves order from records, including the inactive one,
    # so downstream near-match search can find renamed/retitled candidates.
    assert all_titles == [
        ("Alpha School Tulsa 421 E 11th St", True),
        ("Alpha School Minneapolis 1128", False),
        ("Alpha School Dallas 4152", True),
    ]


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
            [
                ("Alpha School Tulsa 421 E 11th St", True),
                ("Alpha School Minneapolis 1128", False),
            ],
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
            [("Active Site", True), ("Alpha School Minneapolis 1128", False)],
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
        lambda: ({"active-site"}, {}, [("Active Site", True)]),
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
        lambda: (set(), {}, []),
    )

    def flaky_delete(base_url, slug, secret, *, reason, timeout=20):
        return slug == "ghost-1"

    monkeypatch.setattr(reconcile_dashboard, "_delete_site", flaky_delete)

    rc = reconcile_dashboard.main()
    assert rc == 5  # one failure


# ---------- _slug_tokens / _find_near_matches ----------

def test_slug_tokens_drops_stopwords_and_short_pieces():
    assert reconcile_dashboard._slug_tokens("alpha-school-minneapolis-1128") == [
        "minneapolis",
        "1128",
    ]
    assert reconcile_dashboard._slug_tokens("alpha-school-tulsa-6940-s-utica-ave") == [
        "tulsa",
        "6940",
        "utica",
    ]
    assert reconcile_dashboard._slug_tokens("") == []


def test_find_near_matches_surfaces_renamed_active_record():
    """An orphan slug like '6940-s-utica-ave-tulsa-ok' should surface its
    likely renamed active counterpart 'Alpha School Tulsa 6940 S Utica Ave'.
    """
    all_titles = [
        ("Alpha School Tulsa 6940 S Utica Ave", True),
        ("Alpha School Tulsa 421 E 11th St", True),
        ("Alpha School Minneapolis 1128", False),
    ]

    matches = reconcile_dashboard._find_near_matches(
        "6940-s-utica-ave-tulsa-ok", all_titles
    )
    assert matches == [("Alpha School Tulsa 6940 S Utica Ave", True)]

    matches = reconcile_dashboard._find_near_matches(
        "alpha-school-minneapolis-1128", all_titles
    )
    assert matches == [("Alpha School Minneapolis 1128", False)]


def test_find_near_matches_returns_empty_when_no_overlap():
    all_titles = [("Alpha School Dallas 4152", True)]
    assert (
        reconcile_dashboard._find_near_matches(
            "alpha-school-chicago-350-microschool", all_titles
        )
        == []
    )


def test_find_near_matches_numeric_anchor_catches_qualifier_drop_rename():
    """Numeric-anchor fallback: dashboard slug carries an extra qualifier the
    active Wrike title dropped. Same numeric address + same city must surface
    the active title as a rename suspect, not a genuine orphan.
    """
    all_titles = [
        ("Alpha School Chicago 350 (GEMS)", True),
        ("Alpha School Dallas 4152", True),
    ]

    # 'gems-full-school' qualifier: tier-3 picks up 350 + chicago.
    matches = reconcile_dashboard._find_near_matches(
        "alpha-school-chicago-350-gems-full-school", all_titles
    )
    assert matches == [("Alpha School Chicago 350 (GEMS)", True)]

    # 'microschool' qualifier dropped from active title: same anchor logic.
    matches = reconcile_dashboard._find_near_matches(
        "alpha-school-chicago-350-microschool", all_titles
    )
    assert matches == [("Alpha School Chicago 350 (GEMS)", True)]


def test_find_near_matches_numeric_anchor_requires_all_numeric_tokens_in_title():
    """Tier-3 (numeric anchor) must require EVERY numeric token in the slug
    to be present in the candidate title. A different-# same-city record
    must not be returned by tier 3 — only by tier 2 (city-only fallback),
    which is intentionally lax for safety.
    """
    all_titles = [
        ("Alpha School Minneapolis 4500", True),  # different street #
    ]

    # Slug 'alpha-school-minneapolis-1128' has tokens [minneapolis, 1128].
    # Tier 1 fails (no '1128' in title). Tier 2 succeeds because
    # 'minneapolis' is the only non-numeric token — by design, this is the
    # safe-by-default path: when in doubt, surface a rename suspect rather
    # than silently delete real data. The reconciler's skip behavior is the
    # correct outcome here.
    matches = reconcile_dashboard._find_near_matches(
        "alpha-school-minneapolis-1128", all_titles
    )
    assert matches == [("Alpha School Minneapolis 4500", True)]

    # But tier 3 alone (numeric anchor) must not fire when the slug's
    # numeric token is absent from the title. Verify by exercising a slug
    # whose tier-2 path is blocked (multiple non-numeric tokens, only one
    # of which appears in title) and whose only numeric token is missing.
    all_titles_2 = [
        ("Alpha School Chicago 4500 (GEMS)", True),
    ]
    matches = reconcile_dashboard._find_near_matches(
        "alpha-school-detroit-350-microschool", all_titles_2
    )
    # detroit not in title (tier 1 fails on 350+detroit+microschool), tier 2
    # fails on detroit+microschool, tier 3 fails because '350' not in title.
    assert matches == []


def test_main_logs_near_match_hint_for_orphans_with_no_exact_match(monkeypatch, caplog):
    monkeypatch.setenv("RECONCILE_DRY_RUN", "1")
    monkeypatch.setattr(
        reconcile_dashboard,
        "_fetch_dashboard_slugs",
        lambda base_url: [
            {"slug": "6940-s-utica-ave-tulsa-ok"},
            {"slug": "alpha-school-tulsa-6940-s-utica-ave"},
        ],
    )
    monkeypatch.setattr(
        reconcile_dashboard,
        "_expected_slugs_from_wrike",
        lambda: (
            {"alpha-school-tulsa-6940-s-utica-ave"},
            {},
            [("Alpha School Tulsa 6940 S Utica Ave", True)],
        ),
    )

    caplog.set_level("INFO", logger="reconcile_dashboard")
    rc = reconcile_dashboard.main()

    assert rc == 0
    text = caplog.text
    assert "6940-s-utica-ave-tulsa-ok" in text
    # The orphan log line should call out the near-match active record so a
    # human can see this is a rename/retitle, not a stale row to delete blindly.
    assert "near matches" in text
    assert "Alpha School Tulsa 6940 S Utica Ave" in text
    assert "[ACTIVE]" in text


# ---------- rename-suspect skip behavior ----------

def test_apply_mode_skips_rename_suspects_with_active_near_match(monkeypatch, caplog):
    """Apply mode must NOT delete an orphan whose active Wrike near-match
    suggests the slug is just stale (record was renamed).

    Scenario mirrors the live findings: \\
    - 'alpha-school-lombard-835' on the dashboard \\
    - 'Alpha School Lombard 995' is the current active Wrike title \\
    - 'alpha-school-minneapolis-1128' has no Wrike record — truly cancelled
    """
    monkeypatch.setenv("RECONCILE_DRY_RUN", "0")
    monkeypatch.setenv("DASHBOARD_PUBLISH_SECRET", "shh")
    monkeypatch.setenv("DASHBOARD_PUBLISH_URL", "https://dash.example")

    monkeypatch.setattr(
        reconcile_dashboard,
        "_fetch_dashboard_slugs",
        lambda base_url: [
            {"slug": "alpha-school-lombard-835"},          # rename suspect
            {"slug": "alpha-school-minneapolis-1128"},     # truly orphaned
            {"slug": "alpha-school-lombard-995"},          # the current active slug
        ],
    )
    monkeypatch.setattr(
        reconcile_dashboard,
        "_expected_slugs_from_wrike",
        lambda: (
            {"alpha-school-lombard-995"},
            {},
            [("Alpha School Lombard 995", True)],
        ),
    )

    delete_calls: list[dict] = []
    monkeypatch.setattr(
        reconcile_dashboard,
        "_delete_site",
        lambda base_url, slug, secret, *, reason, timeout=20: delete_calls.append(
            {"slug": slug, "reason": reason}
        )
        or True,
    )

    caplog.set_level("INFO", logger="reconcile_dashboard")
    rc = reconcile_dashboard.main()

    assert rc == 0
    deleted_slugs = {c["slug"] for c in delete_calls}
    # Truly orphaned slug is deleted…
    assert "alpha-school-minneapolis-1128" in deleted_slugs
    # …but rename-suspect is NOT deleted
    assert "alpha-school-lombard-835" not in deleted_slugs
    text = caplog.text
    assert "RENAME-SUSPECT" in text
    assert "DELETABLE" in text
    assert "Alpha School Lombard 995" in text  # active near-match surfaced


def test_dry_run_partitions_rename_suspects_separately_from_deletable(monkeypatch, caplog):
    monkeypatch.setenv("RECONCILE_DRY_RUN", "1")
    monkeypatch.setattr(
        reconcile_dashboard,
        "_fetch_dashboard_slugs",
        lambda base_url: [
            {"slug": "alpha-school-lombard-835"},
            {"slug": "alpha-school-minneapolis-1128"},
            {"slug": "alpha-school-lombard-995"},
        ],
    )
    monkeypatch.setattr(
        reconcile_dashboard,
        "_expected_slugs_from_wrike",
        lambda: (
            {"alpha-school-lombard-995"},
            {},
            [("Alpha School Lombard 995", True)],
        ),
    )

    caplog.set_level("INFO", logger="reconcile_dashboard")
    rc = reconcile_dashboard.main()

    assert rc == 0
    text = caplog.text
    # Dry-run "would delete" count must reflect ONLY truly deletable orphans,
    # not the rename-suspect ones.
    assert "would delete 1" in text
    assert "Skipping 1 rename-suspect" in text


def test_apply_mode_treats_inactive_near_match_as_deletable(monkeypatch):
    """If the only near-matches are INACTIVE Wrike records, the slug is
    deletable — the underlying site has been cancelled, just under a
    slightly different title than what the dashboard slug encodes.
    """
    monkeypatch.setenv("RECONCILE_DRY_RUN", "0")
    monkeypatch.setenv("DASHBOARD_PUBLISH_SECRET", "shh")

    monkeypatch.setattr(
        reconcile_dashboard,
        "_fetch_dashboard_slugs",
        lambda base_url: [{"slug": "old-cancelled-site-tx"}],
    )
    monkeypatch.setattr(
        reconcile_dashboard,
        "_expected_slugs_from_wrike",
        lambda: (set(), {}, [("Old Cancelled Site", False)]),
    )

    delete_calls: list[dict] = []
    monkeypatch.setattr(
        reconcile_dashboard,
        "_delete_site",
        lambda base_url, slug, secret, *, reason, timeout=20: delete_calls.append(
            {"slug": slug, "reason": reason}
        )
        or True,
    )

    rc = reconcile_dashboard.main()
    assert rc == 0
    assert delete_calls == [
        {"slug": "old-cancelled-site-tx", "reason": "wrike-near-match-inactive"}
    ]


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
