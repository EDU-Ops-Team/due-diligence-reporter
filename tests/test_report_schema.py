"""Tests for the DD report V3 schema, alias map, and normalization."""

from __future__ import annotations

import pytest

from due_diligence_reporter.report_schema import (
    ALLOWED_CAN_WE_ANSWERS,
    AGENT_KEY_ALIASES,
    LINK_TOKENS,
    MISSING_P1_ASSIGNEE_LABEL,
    TEMPLATE_TOKEN_SET,
    TEMPLATE_TOKENS,
    normalize_report_data,
)


def test_no_duplicate_tokens() -> None:
    assert len(TEMPLATE_TOKENS) == len(set(TEMPLATE_TOKENS))


def test_set_matches_list() -> None:
    assert TEMPLATE_TOKEN_SET == frozenset(TEMPLATE_TOKENS)


def test_all_aliases_point_to_valid_tokens() -> None:
    bad = {
        alias: target
        for alias, target in AGENT_KEY_ALIASES.items()
        if target not in TEMPLATE_TOKEN_SET
    }
    assert bad == {}, f"Aliases pointing to invalid tokens: {bad}"


def test_no_alias_is_also_a_template_token() -> None:
    overlap = {key for key in AGENT_KEY_ALIASES if key in TEMPLATE_TOKEN_SET}
    assert overlap == set(), f"Alias keys that are also template tokens: {overlap}"


def test_token_count_v3() -> None:
    assert len(TEMPLATE_TOKENS) == 79, f"Expected 79 tokens, got {len(TEMPLATE_TOKENS)}"
    assert all("delta_" not in t for t in TEMPLATE_TOKENS)


class TestNormalization:
    def test_normalize_direct_match(self) -> None:
        report_data = {
            "exec": {
                "c_answer": "Yes",
                "recommended_path_capacity": "42",
                "recommended_path_capex": "$220,000",
                "recommended_path_open_date": "03/27",
                "fastest_open_capacity": "36",
                "fastest_open_capex": "$185,000",
                "fastest_open_open_date": "01/27",
                "max_capacity_capacity": "54",
                "max_capacity_capex": "$290,000",
                "max_capacity_open_date": "04/27",
                "max_value_capacity": "40",
                "max_value_capex": "$240,000",
                "max_value_open_date": "03/27",
                "cost_demolition_fastest_open": "$0",
                "cost_demolition_max_capacity": "$5,200",
                "cost_demolition_recommended_path": "$1,200",
                "cost_grand_total_fastest_open": "$86,000",
                "cost_grand_total_max_capacity": "$245,000",
            },
            "sources": {
                "sir_link": "https://example.com/sir",
                "e_occupancy_link": "https://example.com/eocc",
            },
            "meta": {"site_name": "Alpha Test"},
        }
        replacements, unmatched, _unfilled, sources = normalize_report_data(
            report_data,
            site_name="Alpha Test",
            report_date="03/19/2026",
        )
        assert replacements["exec.fastest_open_capacity"] == "36"
        assert replacements["exec.max_capacity_capex"] == "$290,000"
        assert replacements["exec.max_value_open_date"] == "03/27"
        assert replacements["exec.cost_demolition_recommended_path"] == "$1,200"
        assert replacements["sources.sir_link"] == "https://example.com/sir"
        assert replacements["meta.site_name"] == "Alpha Test"
        assert unmatched == []
        assert sources["exec.max_capacity_capacity"] == "agent"

    def test_legacy_v2_aliases_map_to_v3(self) -> None:
        report_data = {
            "exec": {
                "e_mvp_capacity": "36",
                "e_mvp_cost": "$185,000",
                "f_mvp_ready": "01/27",
                "e_max_capacity_capacity": "54",
                "e_max_capacity_cost": "$290,000",
                "f_max_capacity_ready": "04/27",
                "e_max_value_capacity": "42",
                "e_max_value_cost": "$240,000",
                "f_max_value_ready": "03/27",
                "cost_demolition_mvp": "$0",
                "cost_demolition_max_capacity": "$5,200",
                "cost_demolition_max_value": "$4,200",
            },
        }
        replacements, _, _, sources = normalize_report_data(
            report_data,
            site_name="Test",
            report_date="01/01/2026",
        )
        assert replacements["exec.fastest_open_capacity"] == "36"
        assert replacements["exec.fastest_open_capex"] == "$185,000"
        assert replacements["exec.fastest_open_open_date"] == "01/27"
        assert replacements["exec.max_capacity_capacity"] == "54"
        assert replacements["exec.max_capacity_capex"] == "$290,000"
        assert replacements["exec.max_capacity_open_date"] == "04/27"
        assert replacements["exec.max_value_capacity"] == "42"
        assert replacements["exec.max_value_capex"] == "$240,000"
        assert replacements["exec.max_value_open_date"] == "03/27"
        assert replacements["exec.cost_demolition_fastest_open"] == "$0"
        assert sources["exec.fastest_open_capacity"] == "alias:exec.e_mvp_capacity"

    def test_unmatched_keys_reported(self) -> None:
        _, unmatched, _, _ = normalize_report_data(
            {"q1": {"zoning_designation": "C-2"}},
            site_name="Test",
            report_date="01/01/2026",
        )
        assert "q1.zoning_designation" in unmatched

    def test_unfilled_tokens_include_v3_fields(self) -> None:
        _, _, unfilled, _ = normalize_report_data(
            {},
            site_name="Test",
            report_date="01/01/2026",
        )
        assert "meta.site_name" not in unfilled
        assert "meta.report_date" not in unfilled
        assert "exec.recommended_path_capacity" in unfilled
        assert "exec.fastest_open_capacity" in unfilled
        assert "exec.max_capacity_capacity" in unfilled
        assert "exec.max_value_capacity" in unfilled
        assert "exec.cost_grand_total_recommended_path" in unfilled
        assert "exec.cost_grand_total_fastest_open" in unfilled

    def test_meta_defaults(self) -> None:
        replacements, _, _, _ = normalize_report_data(
            {},
            site_name="Alpha Metro",
            report_date="03/19/2026",
        )
        assert replacements["meta.site_name"] == "Alpha Metro"
        assert replacements["meta.report_date"] == "03/19/2026"
        assert replacements["meta.prepared_by"] == MISSING_P1_ASSIGNEE_LABEL

    @pytest.mark.parametrize(
        ("raw_value", "expected"),
        [
            ("Yes", "Yes"),
            ("YES", "Yes"),
            ("No", "No"),
            ("CONDITIONAL", "Yes see notes"),
            ("Yes see notes", "Yes see notes"),
        ],
    )
    def test_can_we_answer_normalizes_to_allowed_values(self, raw_value: str, expected: str) -> None:
        replacements, _, unfilled, _ = normalize_report_data(
            {"exec": {"c_answer": raw_value}},
            site_name="Test",
            report_date="01/01/2026",
        )
        assert replacements["exec.c_answer"] == expected
        assert replacements["exec.c_answer"] in ALLOWED_CAN_WE_ANSWERS
        assert "exec.c_answer" not in unfilled

    def test_invalid_can_we_answer_is_left_unfilled(self) -> None:
        replacements, _, unfilled, _ = normalize_report_data(
            {"exec": {"c_answer": "Maybe"}},
            site_name="Test",
            report_date="01/01/2026",
        )
        assert "exec.c_answer" not in replacements
        assert "exec.c_answer" in unfilled


class TestLinkTokenSets:
    def test_link_tokens_are_valid(self) -> None:
        bad = LINK_TOKENS - TEMPLATE_TOKEN_SET
        assert bad == set(), f"Link tokens not in template: {bad}"


class TestPipelineToolDefinitions:
    def test_save_skill_report_tool_exists(self) -> None:
        from due_diligence_reporter.report_pipeline import TOOL_DEFINITIONS

        tool_names = [tool["name"] for tool in TOOL_DEFINITIONS]
        assert "save_skill_report" in tool_names
