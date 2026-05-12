"""Unit tests for the (DEPRECATED) Shovels.ai permit history helpers and
trace integration.

The Shovels integration has been moved upstream to the AI SIR /
source-evidence build; DDR no longer initiates live Shovels API calls
during report generation. The helper functions remain in the source
tree for legacy callers and are still unit-tested here so future
refactors don't silently break the code path. The MCP-tool exposure is
asserted to be disabled by default in
``test_get_permit_history_not_registered_by_default``.

The supplemental_evidence trace tests at the bottom of this file
verify that upstream-supplied ``shovels.permit_history`` evidence still
flows into the trace report when the SIR builder writes it into the
token bag.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
import requests

from due_diligence_reporter.rebl import ReblResolution
from due_diligence_reporter.server import (
    _analyze_permit_flags,
    _build_report_trace_data,
    _call_shovels_metrics,
    _call_shovels_permits,
    _call_shovels_search,
    _format_permit_report_fields,
    mcp,
)

API_KEY = "test-key"
BASE_URL = "https://api.shovels.ai/v2"


def _make_response(json_data: dict | list, status_code: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    if status_code >= 400:
        mock.raise_for_status.side_effect = requests.HTTPError(response=mock)
    else:
        mock.raise_for_status.return_value = None
    return mock


def _empty_metrics(**overrides) -> dict:
    base = {
        "permit_count": 0,
        "permit_active_count": 0,
        "permit_in_review_count": 0,
        "permit_final_count": 0,
        "permit_inactive_count": 0,
        "total_job_value": 0,
        "avg_inspection_pass_rate": None,
    }
    return {**base, **overrides}


# ---------------------------------------------------------------------------
# _call_shovels_search
# ---------------------------------------------------------------------------


@patch("due_diligence_reporter.server.requests.get")
def test_call_shovels_search_returns_geo_id(mock_get):
    mock_get.return_value = _make_response(
        {"items": [{"geo_id": "abc123", "name": "345 Peachtree St NE, Atlanta, GA 30308"}]}
    )
    result = _call_shovels_search(API_KEY, BASE_URL, "345 Peachtree St")
    assert result is not None
    assert result["geo_id"] == "abc123"


@patch("due_diligence_reporter.server.requests.get")
def test_call_shovels_search_empty_results_returns_none(mock_get):
    mock_get.return_value = _make_response({"items": []})
    result = _call_shovels_search(API_KEY, BASE_URL, "Unknown Address")
    assert result is None


@patch("due_diligence_reporter.server.requests.get")
def test_call_shovels_search_404_returns_none(mock_get):
    mock_get.return_value = _make_response({}, status_code=404)
    with pytest.raises(requests.HTTPError):
        _call_shovels_search(API_KEY, BASE_URL, "345 Peachtree St")


@patch("due_diligence_reporter.server.requests.get")
def test_call_shovels_metrics_returns_dict(mock_get):
    metrics = _empty_metrics(permit_count=5, permit_active_count=1)
    mock_get.return_value = _make_response(metrics)
    result = _call_shovels_metrics(API_KEY, BASE_URL, "geo123")
    assert result["permit_count"] == 5
    assert result["permit_active_count"] == 1


@patch("due_diligence_reporter.server.requests.get")
def test_call_shovels_permits_returns_list(mock_get):
    permits = [{"id": "p1", "type": "electrical", "tags": ["electrical"], "status": "final"}]
    mock_get.return_value = _make_response({"items": permits})
    result = _call_shovels_permits(API_KEY, BASE_URL, "geo123", "2016-01-01", "2026-01-01")
    assert len(result) == 1
    assert result[0]["id"] == "p1"


# ---------------------------------------------------------------------------
# _analyze_permit_flags
# ---------------------------------------------------------------------------


def test_analyze_permit_flags_open_permit():
    metrics = _empty_metrics(permit_active_count=1, permit_count=3)
    flags = _analyze_permit_flags(metrics, [])
    assert any(f["flag_type"] == "OPEN_PERMIT" for f in flags)
    open_flag = next(f for f in flags if f["flag_type"] == "OPEN_PERMIT")
    assert open_flag["severity"] == "acquisition_condition"


def test_analyze_permit_flags_in_review_triggers_open():
    metrics = _empty_metrics(permit_in_review_count=2, permit_count=5)
    flags = _analyze_permit_flags(metrics, [])
    assert any(f["flag_type"] == "OPEN_PERMIT" for f in flags)


def test_analyze_permit_flags_demo_permit_via_type():
    metrics = _empty_metrics(permit_count=1)
    permits = [{"type": "demolition", "tags": [], "description": "", "file_date": "2024-01-15", "status": "final"}]
    flags = _analyze_permit_flags(metrics, permits)
    assert any(f["flag_type"] == "DEMO_PERMIT" for f in flags)
    demo = next(f for f in flags if f["flag_type"] == "DEMO_PERMIT")
    assert demo["severity"] == "acquisition_condition"


def test_analyze_permit_flags_demo_permit_via_tags():
    metrics = _empty_metrics(permit_count=1)
    permits = [{"type": "interior", "tags": ["demolition", "interior"], "description": "", "file_date": "2024-03-01", "status": "active"}]
    flags = _analyze_permit_flags(metrics, permits)
    assert any(f["flag_type"] == "DEMO_PERMIT" for f in flags)


def test_analyze_permit_flags_demo_only_flagged_once():
    metrics = _empty_metrics(permit_count=2)
    permits = [
        {"type": "demolition", "tags": [], "description": "", "file_date": "2023-01-01", "status": "final"},
        {"type": "demolition", "tags": [], "description": "", "file_date": "2024-01-01", "status": "final"},
    ]
    flags = _analyze_permit_flags(metrics, permits)
    demo_flags = [f for f in flags if f["flag_type"] == "DEMO_PERMIT"]
    assert len(demo_flags) == 1


def test_analyze_permit_flags_deferred_maintenance():
    metrics = _empty_metrics()
    flags = _analyze_permit_flags(metrics, [])
    assert any(f["flag_type"] == "DEFERRED_MAINTENANCE" for f in flags)
    dm = next(f for f in flags if f["flag_type"] == "DEFERRED_MAINTENANCE")
    assert dm["severity"] == "risk_note"


def test_analyze_permit_flags_no_deferred_maintenance_when_permits_exist():
    metrics = _empty_metrics(permit_count=5)
    permits = [{"type": "electrical", "tags": [], "description": "", "file_date": "2020-01-01", "status": "final"}]
    flags = _analyze_permit_flags(metrics, permits)
    assert not any(f["flag_type"] == "DEFERRED_MAINTENANCE" for f in flags)


def test_analyze_permit_flags_low_inspection_quality():
    metrics = _empty_metrics(permit_count=5, avg_inspection_pass_rate=0.65)
    flags = _analyze_permit_flags(metrics, [])
    assert any(f["flag_type"] == "LOW_INSPECTION_QUALITY" for f in flags)
    liq = next(f for f in flags if f["flag_type"] == "LOW_INSPECTION_QUALITY")
    assert liq["severity"] == "risk_note"


def test_analyze_permit_flags_pass_rate_at_threshold_not_flagged():
    # boundary is exclusive: < 0.70 triggers, 0.70 does not
    metrics = _empty_metrics(permit_count=5, avg_inspection_pass_rate=0.70)
    flags = _analyze_permit_flags(metrics, [])
    assert not any(f["flag_type"] == "LOW_INSPECTION_QUALITY" for f in flags)


def test_analyze_permit_flags_pass_rate_none_not_flagged():
    metrics = _empty_metrics(permit_count=5, avg_inspection_pass_rate=None)
    flags = _analyze_permit_flags(metrics, [])
    assert not any(f["flag_type"] == "LOW_INSPECTION_QUALITY" for f in flags)


def test_analyze_permit_flags_hvac_info():
    metrics = _empty_metrics(permit_count=1)
    permits = [{"type": "mechanical", "tags": ["hvac", "mechanical"], "description": "", "file_date": "2021-06-01", "status": "final", "job_value": 1500000}]
    flags = _analyze_permit_flags(metrics, permits)
    assert any(f["flag_type"] == "HVAC_PERMIT" for f in flags)
    hvac = next(f for f in flags if f["flag_type"] == "HVAC_PERMIT")
    assert hvac["severity"] == "info"


def test_analyze_permit_flags_multiple():
    metrics = _empty_metrics(permit_count=3, permit_active_count=1, avg_inspection_pass_rate=0.60)
    permits = [
        {"type": "demolition", "tags": [], "description": "", "file_date": "2022-01-01", "status": "final"},
    ]
    flags = _analyze_permit_flags(metrics, permits)
    flag_types = {f["flag_type"] for f in flags}
    assert "OPEN_PERMIT" in flag_types
    assert "DEMO_PERMIT" in flag_types
    assert "LOW_INSPECTION_QUALITY" in flag_types


# ---------------------------------------------------------------------------
# _format_permit_report_fields
# ---------------------------------------------------------------------------


def test_format_permit_report_fields_conditions_only():
    flags = [
        {"flag_type": "OPEN_PERMIT", "severity": "acquisition_condition", "description": "1 open permit", "evidence": ""},
    ]
    fields = _format_permit_report_fields(flags)
    assert fields["exec.acquisition_conditions"] != ""
    assert fields["exec.tradeoffs_and_deficiencies"] == ""


def test_format_permit_report_fields_risks_only():
    flags = [
        {"flag_type": "DEFERRED_MAINTENANCE", "severity": "risk_note", "description": "No permits in 10 years", "evidence": ""},
    ]
    fields = _format_permit_report_fields(flags)
    assert fields["exec.acquisition_conditions"] == ""
    assert fields["exec.tradeoffs_and_deficiencies"] != ""


def test_format_permit_report_fields_info_flags_excluded():
    flags = [
        {"flag_type": "HVAC_PERMIT", "severity": "info", "description": "HVAC permit on file", "evidence": ""},
        {"flag_type": "ROOF_PERMIT", "severity": "info", "description": "Roof permit on file", "evidence": ""},
    ]
    fields = _format_permit_report_fields(flags)
    assert fields["exec.acquisition_conditions"] == ""
    assert fields["exec.tradeoffs_and_deficiencies"] == ""


def test_format_permit_report_fields_empty_flags():
    fields = _format_permit_report_fields([])
    assert fields == {"exec.acquisition_conditions": "", "exec.tradeoffs_and_deficiencies": ""}


# ---------------------------------------------------------------------------
# Trace report â€” supplemental_evidence section
# ---------------------------------------------------------------------------


def _minimal_trace_args(**overrides) -> dict:
    # `rebl_resolution` is required by `_build_report_trace_data`. These
    # supplemental_evidence tests don't care about Rebl behaviour, so we
    # pass a default (empty) ReblResolution. The Rebl-specific assertions
    # live in tests/test_rebl.py.
    base = {
        "site_name": "Test Site",
        "report_date": "2026-04-03",
        "doc_id": "doc123",
        "doc_url": "https://docs.google.com/doc123",
        "replacements": {},
        "unfilled": [],
        "unmatched": [],
        "hyperlink_trace": {},
        "token_evidence": None,
        "rebl_resolution": ReblResolution(),
    }
    return {**base, **overrides}


def test_trace_supplemental_evidence_included_for_non_template_keys():
    evidence = {"shovels.permit_history": '{"permit_count": 5, "risk_flags": []}'}
    trace = _build_report_trace_data(**_minimal_trace_args(token_evidence=evidence))
    assert "supplemental_evidence" in trace
    assert "shovels.permit_history" in trace["supplemental_evidence"]
    assert '"permit_count": 5' in trace["supplemental_evidence"]["shovels.permit_history"]


def test_trace_supplemental_evidence_omitted_when_empty():
    trace = _build_report_trace_data(**_minimal_trace_args(token_evidence=None))
    assert "supplemental_evidence" not in trace


def test_trace_supplemental_evidence_template_keys_not_duplicated():
    # A key that IS a template token should appear in token_report, not supplemental_evidence
    from due_diligence_reporter.report_schema import TEMPLATE_TOKENS
    first_token = next(iter(TEMPLATE_TOKENS))
    evidence = {
        first_token: "some value",
        "shovels.permit_history": "raw data",
    }
    trace = _build_report_trace_data(**_minimal_trace_args(token_evidence=evidence))
    assert first_token not in trace.get("supplemental_evidence", {})
    assert "shovels.permit_history" in trace["supplemental_evidence"]


# ---------------------------------------------------------------------------
# Shovels is out of normal DDR scope — these are the load-bearing assertions
# for the upstream-only direction. The Shovels integration now runs in the
# AI SIR / source-evidence build; DDR must not initiate live Shovels API
# calls during report generation, and the legacy MCP tool must not be
# advertised by default. Upstream-supplied ``permit_history.risk_flags``
# must still flow into ``dd_risk_flags[]`` so SIR evidence keeps surfacing.
# ---------------------------------------------------------------------------


def _list_mcp_tool_names() -> set[str]:
    """Return the set of tool names currently registered on the DDR MCP server."""
    return {t.name for t in asyncio.run(mcp.list_tools())}


class TestShovelsOutOfDdrScope:
    """Shovels.ai is upstream-only; assert it is not part of normal DDR scope."""

    def test_get_permit_history_not_registered_by_default(self) -> None:
        # DDR_ENABLE_SHOVELS defaults to False, so the legacy tool must
        # not appear in the MCP tool list the agent sees.
        names = _list_mcp_tool_names()
        assert "get_permit_history" not in names, (
            "get_permit_history must not be advertised as an MCP tool by default; "
            "the Shovels integration now runs upstream in the AI SIR build."
        )

    def test_other_ddr_tools_still_registered(self) -> None:
        # Sanity-check that disabling Shovels didn't accidentally drop
        # other DDR tools — the agent still needs the core toolset.
        names = _list_mcp_tool_names()
        assert "create_dd_report" in names
        assert "list_drive_documents" in names

    def test_upstream_permit_history_risk_flags_still_ingested(self) -> None:
        # When the upstream SIR / source-evidence build supplies
        # ``permit_history.risk_flags`` in the report's token bag, the
        # canonical risk-flag derivation must still pick them up and
        # surface them on ``dd_risk_flags[]``. This is the contract that
        # lets DDR keep consuming upstream permit evidence even though
        # it no longer calls Shovels itself.
        from due_diligence_reporter.risk_flags import derive_risk_flags

        report_data = {
            "permit_history.risk_flags": [
                {
                    "flag_type": "OPEN_PERMIT",
                    "severity": "acquisition_condition",
                    "description": "1 open permit — resolve before lease execution",
                    "evidence": "Shovels metrics: permit_active_count=1",
                },
                {
                    "flag_type": "DEFERRED_MAINTENANCE",
                    "severity": "risk_note",
                    "description": "No permit activity in last 10 years",
                    "evidence": "Shovels metrics: permit_count=0",
                },
                {
                    "flag_type": "HVAC_PERMIT",
                    "severity": "info",
                    "description": "HVAC permit on file",
                    "evidence": "",
                },
            ],
        }
        flags = derive_risk_flags(report_data)
        sources = {f["source"] for f in flags}
        severities = {f["severity"] for f in flags}
        assert "permit_history" in sources
        # acquisition_condition -> high, risk_note -> medium, info -> omitted.
        # Both upstream entries dedup onto (ahj_history, permit_history) so
        # only the higher-severity one survives, but the merged summary
        # carries both descriptions.
        assert "high" in severities
        assert all(f["severity"] != "info" for f in flags)
        assert all(f["category"] == "ahj_history" for f in flags if f["source"] == "permit_history")
        merged_summary = next(f["summary"] for f in flags if f["source"] == "permit_history")
        assert "open permit" in merged_summary.lower()
        assert "no permit activity" in merged_summary.lower()

    def test_upstream_shovels_supplemental_evidence_still_flows_to_trace(self) -> None:
        # If the upstream SIR builder stores ``shovels.permit_history``
        # in ``token_evidence``, DDR's trace report must continue to
        # surface it in ``supplemental_evidence`` so reviewers can see
        # the raw upstream payload that drove the flags.
        evidence = {"shovels.permit_history": '{"permit_count": 5, "risk_flags": []}'}
        trace = _build_report_trace_data(**_minimal_trace_args(token_evidence=evidence))
        assert "shovels.permit_history" in trace["supplemental_evidence"]


class TestLegacyShovelsToolOptIn:
    """The legacy ``get_permit_history`` MCP tool remains opt-in for callers
    that have not yet migrated. Toggling ``DDR_ENABLE_SHOVELS=true`` should
    restore the tool registration."""

    def test_function_still_importable_for_legacy_callers(self) -> None:
        # The Python function itself stays in the module so direct
        # callers (scripts, legacy tests) keep working.
        from due_diligence_reporter import server

        assert callable(server.get_permit_history)

    def test_missing_api_key_returns_configuration_error(self) -> None:
        # When invoked directly without an API key, the legacy function
        # short-circuits with a configuration error rather than
        # attempting a live call. This guards against accidental live
        # traffic if a legacy caller invokes it without DDR_ENABLE_SHOVELS.
        from due_diligence_reporter import server

        with patch.object(server, "get_settings") as mock_settings:
            mock_settings.return_value.shovels_api_key = ""
            mock_settings.return_value.shovels_api_base_url = "https://api.shovels.ai/v2"
            result = asyncio.run(server.get_permit_history("123 Main St"))
        assert result["status"] == "error"
        assert "SHOVELS_API_KEY" in result["message"]

