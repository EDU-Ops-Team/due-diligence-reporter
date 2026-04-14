"""Unit tests for Shovels.ai permit history helpers and trace integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from due_diligence_reporter.server import (
    _analyze_permit_flags,
    _build_report_trace_data,
    _call_shovels_metrics,
    _call_shovels_permits,
    _call_shovels_search,
    _format_permit_report_fields,
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
    assert fields["exec.risk_notes"] == ""


def test_format_permit_report_fields_risks_only():
    flags = [
        {"flag_type": "DEFERRED_MAINTENANCE", "severity": "risk_note", "description": "No permits in 10 years", "evidence": ""},
    ]
    fields = _format_permit_report_fields(flags)
    assert fields["exec.acquisition_conditions"] == ""
    assert fields["exec.risk_notes"] != ""


def test_format_permit_report_fields_info_flags_excluded():
    flags = [
        {"flag_type": "HVAC_PERMIT", "severity": "info", "description": "HVAC permit on file", "evidence": ""},
        {"flag_type": "ROOF_PERMIT", "severity": "info", "description": "Roof permit on file", "evidence": ""},
    ]
    fields = _format_permit_report_fields(flags)
    assert fields["exec.acquisition_conditions"] == ""
    assert fields["exec.risk_notes"] == ""


def test_format_permit_report_fields_empty_flags():
    fields = _format_permit_report_fields([])
    assert fields == {"exec.acquisition_conditions": "", "exec.risk_notes": ""}


# ---------------------------------------------------------------------------
# Trace report â€” supplemental_evidence section
# ---------------------------------------------------------------------------


def _minimal_trace_args(**overrides) -> dict:
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

