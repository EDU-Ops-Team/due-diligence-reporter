"""Tests for the ``diagnose_site_readiness`` MCP tool.

Recommendation 4B from ``docs/event-driven-ddr-recommendations.md``: a
read-only "should I run now or wait?" diagnostic that surfaces the
cron-path readiness view, RayCon dispatch state, and full token
projection in one structured payload.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from due_diligence_reporter import server

SITE_TITLE = "Alpha Keller"
SITE_ADDRESS = "123 Main St"
DRIVE_URL = "https://drive.google.com/drive/folders/abc123"


def _record() -> dict:
    return {"id": "wrike-1", "title": SITE_TITLE}


def _summary() -> dict:
    return {
        "title": SITE_TITLE,
        "address": SITE_ADDRESS,
        "drive_folder_url": DRIVE_URL,
    }


def _readiness(
    *,
    sir_found: bool = True,
    sir_vendor: bool = True,
    inspection_found: bool = True,
    inspection_vendor: bool = True,
    raycon_scenario_found: bool = True,
    report_exists: bool = False,
) -> dict:
    return {
        "sir_found": sir_found,
        "sir_vendor": sir_vendor,
        "inspection_found": inspection_found,
        "inspection_vendor": inspection_vendor,
        "isp_found": False,
        "raycon_scenario_found": raycon_scenario_found,
        "report_exists": report_exists,
        "e_occupancy_report_found": False,
        "school_approval_report_found": False,
        "all_files": [],
    }


def _run(site: str = SITE_TITLE) -> dict:
    return asyncio.run(server.diagnose_site_readiness(site))


def _vendor_gate_on(monkeypatch):
    monkeypatch.setenv("VENDOR_GATE_ENABLED", "1")


def _vendor_gate_off(monkeypatch):
    monkeypatch.setenv("VENDOR_GATE_ENABLED", "0")


def _no_dispatch_state(tmp_path: Path, monkeypatch):
    """Point the dispatch state path at an empty location."""
    monkeypatch.setattr(
        server, "_RAYCON_DISPATCH_STATE_PATH", tmp_path / ".raycon_dispatch_state.json"
    )


def _patch_common(
    *,
    readiness: dict,
    m1_docs: dict | None = None,
):
    """Bundle the patches every test in this file needs."""
    if m1_docs is None:
        m1_docs = {}
    return [
        patch("due_diligence_reporter.server.find_site_record", return_value=_record()),
        patch("due_diligence_reporter.server.build_site_summary", return_value=_summary()),
        patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=MagicMock(),
        ),
        patch(
            "due_diligence_reporter.server._build_site_match_terms",
            return_value=[SITE_TITLE],
        ),
        patch(
            "due_diligence_reporter.server._resolve_m1_folder",
            return_value=("m1-folder-id", "https://drive.google.com/drive/folders/m1"),
        ),
        patch(
            "due_diligence_reporter.server._list_m1_documents_by_type",
            return_value=m1_docs,
        ),
        patch(
            "due_diligence_reporter.report_pipeline.list_shared_folders_once",
            return_value={},
        ),
        patch(
            "due_diligence_reporter.report_pipeline.check_site_readiness_direct",
            return_value=readiness,
        ),
    ]


def _enter_all(patchers):
    started = [p.start() for p in patchers]
    return started


def _stop_all(patchers):
    for p in patchers:
        p.stop()


# ---------------------------------------------------------------------------
# 1. All docs present → ready_for_full_report=True, no pending tokens.
# ---------------------------------------------------------------------------


def test_all_docs_present_ready_for_full_report(tmp_path, monkeypatch):
    _vendor_gate_on(monkeypatch)
    _no_dispatch_state(tmp_path, monkeypatch)

    patchers = _patch_common(
        readiness=_readiness(),
        m1_docs={
            "block_plan": {"id": "bp-1", "modifiedTime": "2026-05-07T13:00:00Z"},
            "raycon_scenario_json": {
                "id": "rs-1",
                "modifiedTime": "2026-05-07T13:30:00Z",
            },
        },
    )
    _enter_all(patchers)
    try:
        result = _run()
    finally:
        _stop_all(patchers)

    assert result["status"] == "success"
    assert result["site"] == SITE_TITLE
    assert result["ready_for_full_report"] is True
    assert result["partial_report_possible"] is True
    assert result["vendor_gate_enabled"] is True

    statuses = {b["doc"]: b["status"] for b in result["blocking"]}
    assert statuses == {
        "vendor_sir": "present",
        "building_inspection": "present",
        "raycon_scenario": "present",
    }
    # When RayCon scenario is present, no block-plan / dispatch metadata
    # should leak into the entry.
    raycon_entry = next(b for b in result["blocking"] if b["doc"] == "raycon_scenario")
    assert "block_plan_present" not in raycon_entry
    assert "last_dispatch" not in raycon_entry
    assert "minutes_since" not in raycon_entry

    assert result["would_be_pending"] == []
    assert len(result["would_be_filled_now"]) > 0


# ---------------------------------------------------------------------------
# 2. Vendor SIR missing → not ready, partial NOT possible.
# ---------------------------------------------------------------------------


def test_vendor_sir_missing_blocks_partial(tmp_path, monkeypatch):
    _vendor_gate_on(monkeypatch)
    _no_dispatch_state(tmp_path, monkeypatch)

    patchers = _patch_common(
        readiness=_readiness(
            sir_found=False,
            sir_vendor=False,
            raycon_scenario_found=False,
        ),
    )
    _enter_all(patchers)
    try:
        result = _run()
    finally:
        _stop_all(patchers)

    assert result["status"] == "success"
    assert result["ready_for_full_report"] is False
    assert result["partial_report_possible"] is False

    statuses = {b["doc"]: b["status"] for b in result["blocking"]}
    assert statuses == {
        "vendor_sir": "missing",
        "building_inspection": "present",
        "raycon_scenario": "pending",
    }
    raycon_entry = next(b for b in result["blocking"] if b["doc"] == "raycon_scenario")
    assert raycon_entry["block_plan_present"] is False
    assert raycon_entry["last_dispatch"] is None
    assert raycon_entry["minutes_since"] is None


# ---------------------------------------------------------------------------
# 3. SIR present, BI missing, RayCon pending with Block Plan dispatched 14
#    minutes ago → matches the example response shape.
# ---------------------------------------------------------------------------


def test_partial_with_pending_raycon_dispatched_14_min_ago(tmp_path, monkeypatch):
    _vendor_gate_on(monkeypatch)

    # Seed a dispatch state file 14 minutes in the past, keyed by
    # block_plan_file_id.
    state_path = tmp_path / ".raycon_dispatch_state.json"
    now = datetime.now(tz=UTC)
    last_dispatch_dt = now - timedelta(minutes=14)
    state_path.write_text(
        json.dumps(
            {
                "bp-1": {
                    "last_dispatch": last_dispatch_dt.isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "count": 1,
                    "site": SITE_TITLE,
                    "raycon_run_id": "run-xyz",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "_RAYCON_DISPATCH_STATE_PATH", state_path)

    patchers = _patch_common(
        readiness=_readiness(
            inspection_found=False,
            inspection_vendor=False,
            raycon_scenario_found=False,
        ),
        m1_docs={
            "block_plan": {"id": "bp-1", "modifiedTime": "2026-05-07T13:00:00Z"},
        },
    )
    _enter_all(patchers)
    try:
        result = _run()
    finally:
        _stop_all(patchers)

    assert result["status"] == "success"
    assert result["site"] == SITE_TITLE
    assert result["ready_for_full_report"] is False
    assert result["partial_report_possible"] is True

    statuses = {b["doc"]: b["status"] for b in result["blocking"]}
    assert statuses == {
        "vendor_sir": "present",
        "building_inspection": "missing",
        "raycon_scenario": "pending",
    }

    raycon_entry = next(b for b in result["blocking"] if b["doc"] == "raycon_scenario")
    assert raycon_entry["block_plan_present"] is True
    assert raycon_entry["last_dispatch"] is not None
    # Allow ±1 min jitter for the test's own walltime.
    assert raycon_entry["minutes_since"] in (13, 14, 15)

    # Pending tokens should include all RayCon paths.
    assert any(
        path.startswith("exec.fastest_open_") for path in result["would_be_pending"]
    )
    assert any(path.startswith("exec.cost_") for path in result["would_be_pending"])
    assert result["would_be_filled_now"] == []


# ---------------------------------------------------------------------------
# 4. RayCon scenario present → status: present, no pending block.
# ---------------------------------------------------------------------------


def test_raycon_scenario_present_uses_run_timestamp(tmp_path, monkeypatch):
    _vendor_gate_on(monkeypatch)
    _no_dispatch_state(tmp_path, monkeypatch)

    patchers = _patch_common(
        readiness=_readiness(),
        m1_docs={
            "block_plan": {"id": "bp-1", "modifiedTime": "2026-05-07T13:00:00Z"},
            "raycon_scenario_json": {
                "id": "rs-1",
                "modifiedTime": "2026-05-07T13:30:00Z",
            },
        },
    )
    _enter_all(patchers)
    try:
        result = _run()
    finally:
        _stop_all(patchers)

    raycon_entry = next(b for b in result["blocking"] if b["doc"] == "raycon_scenario")
    assert raycon_entry["status"] == "present"
    # When present, we don't surface dispatch metadata.
    assert "last_dispatch" not in raycon_entry
    assert "block_plan_present" not in raycon_entry
    assert "minutes_since" not in raycon_entry


# ---------------------------------------------------------------------------
# 5. Unknown site → graceful error response, not a crash.
# ---------------------------------------------------------------------------


def test_unknown_site_returns_graceful_error(tmp_path, monkeypatch):
    _vendor_gate_on(monkeypatch)
    _no_dispatch_state(tmp_path, monkeypatch)

    with patch("due_diligence_reporter.server.find_site_record", return_value=None):
        result = asyncio.run(server.diagnose_site_readiness("Nonexistent Site"))

    # New disambiguation payload: status is "not_found" with did_you_mean hints.
    assert result["status"] == "not_found"
    assert "could not find" in result["message"].lower()
    assert result["site"] == "Nonexistent Site"


def test_empty_site_name_returns_graceful_error():
    result = asyncio.run(server.diagnose_site_readiness(""))
    assert result["status"] == "error"
    assert "non-empty" in result["message"]


# ---------------------------------------------------------------------------
# 6. Vendor gate disabled → response reflects legacy view.
# ---------------------------------------------------------------------------


def test_vendor_gate_disabled_reports_legacy_view(tmp_path, monkeypatch):
    _vendor_gate_off(monkeypatch)
    _no_dispatch_state(tmp_path, monkeypatch)

    # SIR + BI present (legacy gate satisfied) but NOT vendor-classified
    # AND RayCon scenario MISSING. Under the legacy gate
    # ``_missing_required_docs`` only requires SIR + BI presence, so
    # ``ready_for_full_report`` must be True even though the diagnostic
    # ``blocking[]`` view still surfaces RayCon as pending and the
    # vendor entries as ``present`` (legacy semantics: bare presence).
    patchers = _patch_common(
        readiness=_readiness(
            sir_found=True,
            sir_vendor=False,
            inspection_found=True,
            inspection_vendor=False,
            raycon_scenario_found=False,
        ),
    )
    _enter_all(patchers)
    try:
        result = _run()
    finally:
        _stop_all(patchers)

    assert result["vendor_gate_enabled"] is False
    statuses = {b["doc"]: b["status"] for b in result["blocking"]}
    # Under the legacy gate, ``vendor_sir`` / ``building_inspection`` slots
    # report bare presence, and RayCon is reported as pending — but
    # ``ready_for_full_report`` still flips True (Fix 3: mirror
    # ``_missing_required_docs`` exactly).
    assert statuses == {
        "vendor_sir": "present",
        "building_inspection": "present",
        "raycon_scenario": "pending",
    }
    assert result["ready_for_full_report"] is True
    assert result["partial_report_possible"] is True


# ---------------------------------------------------------------------------
# 7. Fix 1 — diagnose path must not create the M1 folder when absent.
# ---------------------------------------------------------------------------


def test_diagnose_does_not_create_m1_folder(tmp_path, monkeypatch):
    """Diagnose against a site with no M1 folder must NOT call create_folder.

    Verifies (a) ``gc.create_folder`` is never invoked, (b) the response
    surfaces ``m1_folder_missing: True`` and ``drive_folder_url: None``,
    (c) other fields populate gracefully (vendor_gate_enabled, blocking
    showing all three docs missing/pending, partial_report_possible False).
    """
    _vendor_gate_on(monkeypatch)
    _no_dispatch_state(tmp_path, monkeypatch)

    fake_gc = MagicMock()
    fake_gc.list_subfolders.return_value = []  # no M1 subfolder anywhere

    # Use the *real* ``_resolve_m1_folder`` to confirm Fix 1's
    # ``create_if_missing=False`` propagates from the diagnose path.
    patchers = [
        patch(
            "due_diligence_reporter.server.find_site_record",
            return_value=_record(),
        ),
        patch(
            "due_diligence_reporter.server.build_site_summary",
            return_value=_summary(),
        ),
        patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=fake_gc,
        ),
        patch(
            "due_diligence_reporter.server._build_site_match_terms",
            return_value=[SITE_TITLE],
        ),
        patch(
            "due_diligence_reporter.report_pipeline.list_shared_folders_once",
            return_value={},
        ),
        patch(
            "due_diligence_reporter.report_pipeline.check_site_readiness_direct",
            return_value=_readiness(
                sir_found=False,
                sir_vendor=False,
                inspection_found=False,
                inspection_vendor=False,
                raycon_scenario_found=False,
            ),
        ),
    ]
    _enter_all(patchers)
    try:
        result = _run()
    finally:
        _stop_all(patchers)

    # Fix 1: no folder creation.
    fake_gc.create_folder.assert_not_called()

    assert result["status"] == "success"
    assert result["m1_folder_missing"] is True
    assert result["drive_folder_url"] is None
    assert result["vendor_gate_enabled"] is True
    assert result["partial_report_possible"] is False
    assert result["ready_for_full_report"] is False

    statuses = {b["doc"]: b["status"] for b in result["blocking"]}
    assert statuses == {
        "vendor_sir": "missing",
        "building_inspection": "missing",
        "raycon_scenario": "pending",
    }


# ---------------------------------------------------------------------------
# 8. Fix 2 — diagnose path must not write the provenance cache.
# ---------------------------------------------------------------------------


def test_diagnose_does_not_write_provenance_cache(tmp_path, monkeypatch):
    """Tier 2 provenance miss in the diagnose path must not call ``_save_cache``.

    We exercise the *real* ``check_site_readiness_direct`` (not mocked)
    so the ``read_only=True`` flag actually flows through to
    ``classify_provenance``. The fake GoogleClient pretends to find an
    SIR file whose filename does NOT match the AI heuristic and whose
    bytes don't disambiguate, forcing the default-to-vendor Tier 2 path
    that would normally write the cache.
    """
    _vendor_gate_on(monkeypatch)
    _no_dispatch_state(tmp_path, monkeypatch)

    sir_file = {
        "id": "sir-1",
        "name": "Vendor SIR Update.pdf",  # does NOT match AI filename heuristic
        "modifiedTime": "2026-05-07T10:00:00Z",
    }

    fake_gc = MagicMock()
    fake_gc.list_subfolders.return_value = [
        {
            "id": "m1-folder-id",
            "name": "M1",
            "webViewLink": "https://drive.google.com/drive/folders/m1",
        }
    ]
    # Recursive listing returns no docs at the site root (so shared cache /
    # M1 are the only sources). Provenance check still runs on the SIR.
    fake_gc.list_files_recursive.return_value = []
    fake_gc.list_files_in_folder.return_value = []
    # Tier 2 would download bytes — return a vague PDF blob that lands
    # on default-to-vendor.
    fake_gc.download_file_bytes.return_value = b"%PDF-1.4 some vendor doc"

    # Force the SIR into the readiness pipeline via the shared-folder cache
    # fallback path. The simplest way to drive ``classify_provenance``
    # through Tier 2 is to patch it directly and assert ``read_only=True``
    # is forwarded — the cache is only written when ``read_only`` is False.
    save_cache_calls: list[tuple] = []
    classify_call_kwargs: list[dict] = []

    def spy_classify_provenance(*args, **kwargs):
        classify_call_kwargs.append(dict(kwargs))
        # Return a vendor verdict so readiness fields populate normally.
        from due_diligence_reporter.provenance import ProvenanceVerdict
        return ProvenanceVerdict(
            label="vendor", confidence=0.9, tier="content", reason="spy"
        )

    def spy_save_cache(*args, **kwargs):
        save_cache_calls.append((args, kwargs))

    patchers = [
        patch(
            "due_diligence_reporter.server.find_site_record",
            return_value=_record(),
        ),
        patch(
            "due_diligence_reporter.server.build_site_summary",
            return_value=_summary(),
        ),
        patch(
            "due_diligence_reporter.server._make_google_client",
            return_value=fake_gc,
        ),
        patch(
            "due_diligence_reporter.server._build_site_match_terms",
            return_value=[SITE_TITLE],
        ),
        patch(
            "due_diligence_reporter.server._list_m1_documents_by_type",
            return_value={},
        ),
        patch(
            "due_diligence_reporter.report_pipeline.list_shared_folders_once",
            return_value={"sir": [sir_file]},
        ),
        # Patch the classifier so we can capture kwargs (specifically
        # ``read_only``). This is the symbol referenced by
        # ``check_site_readiness_direct``.
        patch(
            "due_diligence_reporter.report_pipeline.classify_provenance",
            side_effect=spy_classify_provenance,
        ),
        # Patch ``_save_cache`` to verify it's NEVER invoked from the
        # diagnose path. This belt-and-braces check guards against any
        # future code path that reintroduces a cache write.
        patch(
            "due_diligence_reporter.provenance._save_cache",
            side_effect=spy_save_cache,
        ),
        # Make ``match_site_in_shared_cache`` actually return our SIR
        # so the readiness pipeline runs the provenance check on it.
        patch(
            "due_diligence_reporter.report_pipeline.match_site_in_shared_cache",
            return_value={"sir": sir_file},
        ),
    ]
    _enter_all(patchers)
    try:
        result = _run()
    finally:
        _stop_all(patchers)

    # Fix 2 core assertion: cache write never happened.
    assert save_cache_calls == [], (
        f"_save_cache must not be called from the diagnose path; "
        f"got {len(save_cache_calls)} call(s)"
    )
    # And the classifier was invoked with read_only=True.
    assert any(
        kw.get("read_only") is True for kw in classify_call_kwargs
    ), (
        "classify_provenance must be called with read_only=True from the "
        f"diagnose path; saw kwargs: {classify_call_kwargs}"
    )
    # Sanity: response is still a success envelope.
    assert result["status"] == "success"


# ---------------------------------------------------------------------------
# 9. Fix 3 — gate-off + RayCon missing + vendor docs missing →
#    ready_for_full_report mirrors _missing_required_docs (i.e. True if
#    SIR + BI present), and blocking[] still surfaces all three docs.
# ---------------------------------------------------------------------------


def test_gate_off_raycon_missing_vendor_missing_ready_true(tmp_path, monkeypatch):
    """Gate-off, RayCon missing, vendor docs missing → ready=True.

    Under VENDOR_GATE_ENABLED=0, ``_missing_required_docs`` only
    requires SIR + BI presence. The diagnose tool's
    ``ready_for_full_report`` must mirror that exactly even though
    ``blocking[]`` reports vendor SIR/BI/RayCon with their actual
    statuses (so the agent sees the truth).
    """
    _vendor_gate_off(monkeypatch)
    _no_dispatch_state(tmp_path, monkeypatch)

    patchers = _patch_common(
        readiness=_readiness(
            sir_found=True,
            sir_vendor=False,         # vendor missing
            inspection_found=True,
            inspection_vendor=False,  # vendor missing
            raycon_scenario_found=False,  # RayCon missing
        ),
    )
    _enter_all(patchers)
    try:
        result = _run()
    finally:
        _stop_all(patchers)

    # ``_missing_required_docs`` under gate-off only checks SIR + BI
    # bare presence — both are present here, so ready_for_full_report
    # must be True even with RayCon missing and vendor classification
    # absent.
    assert result["vendor_gate_enabled"] is False
    assert result["ready_for_full_report"] is True
    assert result["partial_report_possible"] is True

    # ``blocking[]`` must still surface all three docs honestly so the
    # caller can see the real picture.
    statuses = {b["doc"]: b["status"] for b in result["blocking"]}
    assert statuses == {
        "vendor_sir": "present",       # legacy view: bare presence
        "building_inspection": "present",
        "raycon_scenario": "pending",  # actual status, even though
                                       # not blocking under gate-off
    }
    raycon_entry = next(
        b for b in result["blocking"] if b["doc"] == "raycon_scenario"
    )
    assert raycon_entry["block_plan_present"] is False


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_minutes_since_iso_handles_z_suffix():
    iso = "2026-05-07T13:00:00Z"
    fixed_now = datetime(2026, 5, 7, 13, 14, tzinfo=UTC)
    assert server._minutes_since_iso(iso, now=fixed_now) == 14


def test_minutes_since_iso_returns_none_for_garbage():
    assert server._minutes_since_iso("not-a-date") is None
    assert server._minutes_since_iso(None) is None


def test_latest_dispatch_for_site_prefers_block_plan_match():
    state = {
        "bp-old": {"last_dispatch": "2026-05-01T00:00:00Z", "site": "Alpha Keller"},
        "bp-new": {"last_dispatch": "2026-05-07T12:00:00Z", "site": "Alpha Keller"},
    }
    iso, fid = server._latest_dispatch_for_site(
        state, "Alpha Keller", block_plan_file_id="bp-new"
    )
    assert iso == "2026-05-07T12:00:00Z"
    assert fid == "bp-new"


def test_latest_dispatch_for_site_falls_back_to_most_recent_for_site():
    state = {
        "bp-rotated": {"last_dispatch": "2026-05-01T00:00:00Z", "site": "Alpha Keller"},
        "bp-newest": {"last_dispatch": "2026-05-07T12:00:00Z", "site": "Alpha Keller"},
        "bp-other": {"last_dispatch": "2026-05-09T12:00:00Z", "site": "Other Site"},
    }
    # block_plan_file_id is now stale (rotated upload), but we still want
    # the most recent dispatch for *this* site.
    iso, fid = server._latest_dispatch_for_site(
        state, "Alpha Keller", block_plan_file_id="bp-current-not-in-state"
    )
    assert iso == "2026-05-07T12:00:00Z"
    assert fid == "bp-newest"


def test_load_raycon_dispatch_state_returns_empty_on_missing_file(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    assert server._load_raycon_dispatch_state(missing) == {}


def test_load_raycon_dispatch_state_returns_empty_on_corrupt_file(tmp_path):
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert server._load_raycon_dispatch_state(corrupt) == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
