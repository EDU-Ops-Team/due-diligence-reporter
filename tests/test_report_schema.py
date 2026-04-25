"""Tests for the DD report V3 schema, alias map, and normalization."""

from __future__ import annotations

from datetime import date

import pytest

from due_diligence_reporter.report_schema import (
    AGENT_KEY_ALIASES,
    ALLOWED_CAN_WE_ANSWERS,
    ALLOWED_VIABLE_BUILDOUTS,
    ALLOWED_ZONING_STATUSES,
    LINK_TOKENS,
    MISSING_P1_ASSIGNEE_LABEL,
    SCHOOL_YEAR_DEADLINE,
    TEMPLATE_TOKEN_SET,
    TEMPLATE_TOKENS,
    normalize_report_data,
    parse_open_date,
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
    assert len(TEMPLATE_TOKENS) == 56, f"Expected 56 tokens, got {len(TEMPLATE_TOKENS)}"
    assert all("delta_" not in t for t in TEMPLATE_TOKENS)


class TestNormalization:
    def test_normalize_direct_match(self) -> None:
        report_data = {
            "exec": {
                "c_answer": "Yes",
                "c_zoning": "Permitted by right",
                "direct_viable_buildout": "Fastest Open",
                "alpha_fit": "YES",
                "fastest_open_capacity": "36",
                "fastest_open_capex": "$185,000",
                "fastest_open_open_date": "08/01/26",
                "max_capacity_capacity": "54",
                "max_capacity_capex": "$290,000",
                "max_capacity_open_date": "04/27",
                "acquisition_conditions": "Lease conditions: Condition lease on traffic study completion [1]",
                "tradeoffs_and_deficiencies": "Trade-offs and Deficiencies: No nearby park access [1]",
                "cost_demolition_fastest_open": "$0",
                "cost_demolition_max_capacity": "$5,200",
                "cost_grand_total_fastest_open": "$86,000",
                "cost_grand_total_max_capacity": "$245,000",
            },
            "sources": {
                "sir_link": "https://example.com/sir",
                "block_plan_link": "https://example.com/block-plan",
                "e_occupancy_link": "https://example.com/eocc",
            },
            "meta": {"site_name": "Alpha Test"},
            "rebl": {
                "site_id": "alpha-test-site",
                "url": "https://rebl3.vercel.app/site/alpha-test-site",
            },
        }
        replacements, unmatched, _unfilled, sources = normalize_report_data(
            report_data,
            site_name="Alpha Test",
            report_date="03/19/2026",
        )
        assert replacements["exec.fastest_open_capacity"] == "36"
        assert replacements["exec.max_capacity_capex"] == "$290,000"
        assert replacements["exec.c_zoning"] == "Permitted"
        assert replacements["exec.direct_viable_buildout"] == "Fastest Open"
        assert replacements["exec.alpha_fit"] == "Yes"
        assert replacements["exec.tradeoffs_and_deficiencies"] == "No nearby park access [1]"
        assert replacements["sources.sir_link"] == "https://example.com/sir"
        assert replacements["sources.block_plan_link"] == "https://example.com/block-plan"
        assert replacements["meta.rebl_site_id"] == "alpha-test-site"
        assert replacements["sources.rebl_link"] == "https://rebl3.vercel.app/site/alpha-test-site"
        assert replacements["meta.site_name"] == "Alpha Test"
        assert unmatched == ["rebl.site_id", "rebl.url"]
        assert sources["exec.fastest_open_capacity"] == "Capacity Brainlift"
        assert sources["exec.max_capacity_capacity"] == "Capacity Brainlift"
        assert sources["exec.fastest_open_capex"] == "RayCon"
        assert sources["exec.fastest_open_open_date"] == "RayCon"
        assert sources["meta.rebl_site_id"] == "alias:rebl.site_id"

    def test_legacy_v2_aliases_map_to_v3(self) -> None:
        report_data = {
            "exec": {
                "e_mvp_capacity": "36",
                "e_mvp_cost": "$185,000",
                "f_mvp_ready": "01/27",
                "e_max_capacity_capacity": "54",
                "e_max_capacity_cost": "$290,000",
                "f_max_capacity_ready": "04/27",
                "cost_demolition_mvp": "$0",
                "cost_demolition_max_capacity": "$5,200",
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
        assert "exec.direct_viable_buildout" in unfilled
        assert "exec.alpha_fit" in unfilled
        assert "meta.rebl_site_id" in unfilled
        assert "exec.fastest_open_capacity" in unfilled
        assert "exec.max_capacity_capacity" in unfilled
        assert "exec.tradeoffs_and_deficiencies" in unfilled
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
            # Canonical Go / No Go (case/whitespace tolerant)
            ("Go", "Go"),
            ("GO", "Go"),
            ("No Go", "No Go"),
            ("NO GO", "No Go"),
            ("no-go", "No Go"),
            ("NoGo", "No Go"),
            # Legacy Yes/No (pre-rename) — must still alias forward
            ("Yes", "Go"),
            ("YES", "Go"),
            ("No", "No Go"),
            # Legacy three-state "Yes see notes" / "Conditional" collapse to Go
            ("CONDITIONAL", "Go"),
            ("Yes see notes", "Go"),
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

    @pytest.mark.parametrize(
        ("raw_value", "expected"),
        [
            ("Permitted", "Permitted"),
            ("Permitted by right", "Permitted"),
            ("Use Permit Required (admin)", "Use Permit Required (admin)"),
            ("Use Permit Required (public)", "Use Permit Required (public)"),
            ("Prohibited", "Prohibited"),
        ],
    )
    def test_zoning_status_normalizes_to_allowed_values(self, raw_value: str, expected: str) -> None:
        replacements, _, unfilled, _ = normalize_report_data(
            {"exec": {"c_zoning": raw_value}},
            site_name="Test",
            report_date="01/01/2026",
        )
        assert replacements["exec.c_zoning"] == expected
        assert replacements["exec.c_zoning"] in ALLOWED_ZONING_STATUSES
        assert "exec.c_zoning" not in unfilled

    def test_invalid_zoning_status_is_left_unfilled(self) -> None:
        replacements, _, unfilled, _ = normalize_report_data(
            {"exec": {"c_zoning": "Variance maybe required"}},
            site_name="Test",
            report_date="01/01/2026",
        )
        assert "exec.c_zoning" not in replacements
        assert "exec.c_zoning" in unfilled

    @pytest.mark.parametrize(
        ("raw_value", "expected"),
        [
            ("Fastest Open", "Fastest Open"),
            ("fastest", "Fastest Open"),
            ("Max Capacity", "Max Capacity"),
            ("None", "None"),
        ],
    )
    def test_viable_buildout_normalizes_to_allowed_values(self, raw_value: str, expected: str) -> None:
        replacements, _, unfilled, _ = normalize_report_data(
            {"exec": {"direct_viable_buildout": raw_value}},
            site_name="Test",
            report_date="01/01/2026",
        )
        assert replacements["exec.direct_viable_buildout"] == expected
        assert replacements["exec.direct_viable_buildout"] in ALLOWED_VIABLE_BUILDOUTS
        assert "exec.direct_viable_buildout" not in unfilled

    def test_invalid_viable_buildout_is_left_unfilled(self) -> None:
        replacements, _, unfilled, _ = normalize_report_data(
            {"exec": {"direct_viable_buildout": "Both"}},
            site_name="Test",
            report_date="01/01/2026",
        )
        assert "exec.direct_viable_buildout" not in replacements
        assert "exec.direct_viable_buildout" in unfilled

    def test_invalid_alpha_fit_is_left_unfilled(self) -> None:
        replacements, _, unfilled, _ = normalize_report_data(
            {"exec": {"alpha_fit": "Maybe"}},
            site_name="Test",
            report_date="01/01/2026",
        )
        assert "exec.alpha_fit" not in replacements
        assert "exec.alpha_fit" in unfilled


class TestLinkTokenSets:
    def test_link_tokens_are_valid(self) -> None:
        bad = LINK_TOKENS - TEMPLATE_TOKEN_SET
        assert bad == set(), f"Link tokens not in template: {bad}"

    def test_block_plan_link_present_in_v3_schema(self) -> None:
        assert "sources.block_plan_link" in TEMPLATE_TOKEN_SET
        assert "sources.block_plan_link" in LINK_TOKENS

    def test_rebl_tokens_present_in_v3_schema(self) -> None:
        assert "meta.rebl_site_id" in TEMPLATE_TOKEN_SET
        assert "sources.rebl_link" in TEMPLATE_TOKEN_SET
        assert "sources.rebl_link" in LINK_TOKENS

    def test_isp_link_removed_from_v3_schema(self) -> None:
        assert "sources.isp_link" not in TEMPLATE_TOKEN_SET
        assert "sources.isp_link" not in LINK_TOKENS


class TestParseOpenDate:
    def test_mm_dd_yy(self) -> None:
        assert parse_open_date("08/12/26") == date(2026, 8, 12)

    def test_mm_dd_yy_with_whitespace(self) -> None:
        assert parse_open_date("  08/12/26  ") == date(2026, 8, 12)

    def test_legacy_mm_yy_assumes_first_of_month(self) -> None:
        assert parse_open_date("08/26") == date(2026, 8, 1)

    def test_invalid_returns_none(self) -> None:
        assert parse_open_date("Fall 2027") is None

    def test_empty_returns_none(self) -> None:
        assert parse_open_date("") is None


class TestDeterministicCAnswer:
    """Verify the date-comparison override emits canonical Go / No Go.

    The deterministic computation in normalize_report_data compares
    fastest_open_open_date against SCHOOL_YEAR_DEADLINE and overrides
    the agent's answer with "Go" (date <= deadline) or "No Go" (after).
    """

    def _run(self, fastest_open_date: str, agent_answer: str = "No Go") -> dict:
        replacements, _, _, sources = normalize_report_data(
            {"exec": {"c_answer": agent_answer, "fastest_open_open_date": fastest_open_date}},
            site_name="Test",
            report_date="01/01/2026",
        )
        return {"replacements": replacements, "sources": sources}

    def test_date_before_deadline_yields_go(self) -> None:
        result = self._run("07/15/26")
        assert result["replacements"]["exec.c_answer"] == "Go"
        assert result["sources"]["exec.c_answer"] == "computed:date_comparison"

    def test_date_on_deadline_yields_go(self) -> None:
        result = self._run("09/08/26")
        assert result["replacements"]["exec.c_answer"] == "Go"

    def test_date_after_deadline_yields_no_go(self) -> None:
        result = self._run("09/09/26")
        assert result["replacements"]["exec.c_answer"] == "No Go"
        assert result["sources"]["exec.c_answer"] == "computed:date_comparison"

    def test_far_future_date_yields_no_go(self) -> None:
        result = self._run("06/01/27")
        assert result["replacements"]["exec.c_answer"] == "No Go"

    def test_legacy_mm_yy_date_before_deadline_yields_go(self) -> None:
        # 08/26 parsed as 08/01/26, which is before 09/08/26
        result = self._run("08/26")
        assert result["replacements"]["exec.c_answer"] == "Go"

    def test_overrides_agent_answer(self) -> None:
        # Agent says "Go" but date is after deadline — deterministic wins
        result = self._run("10/15/26", agent_answer="Go")
        assert result["replacements"]["exec.c_answer"] == "No Go"

    def test_unparseable_date_keeps_agent_answer(self) -> None:
        result = self._run("Fall 2027", agent_answer="Go")
        assert result["replacements"]["exec.c_answer"] == "Go"
        assert result["sources"]["exec.c_answer"] == "Agent"

    def test_legacy_yes_agent_answer_aliased_when_date_unparseable(self) -> None:
        # Old agent that still emits "Yes" — normalizer aliases to canonical Go
        result = self._run("Fall 2027", agent_answer="Yes")
        assert result["replacements"]["exec.c_answer"] == "Go"

    def test_school_year_deadline_constant(self) -> None:
        assert SCHOOL_YEAR_DEADLINE == date(2026, 9, 8)


class TestPipelineToolDefinitions:
    def test_save_skill_report_tool_exists(self) -> None:
        from due_diligence_reporter.report_pipeline import TOOL_DEFINITIONS

        tool_names = [tool["name"] for tool in TOOL_DEFINITIONS]
        assert "save_skill_report" in tool_names

    def test_capacity_brainlift_tool_exists(self) -> None:
        from due_diligence_reporter.report_pipeline import TOOL_DEFINITIONS

        tool_names = [tool["name"] for tool in TOOL_DEFINITIONS]
        assert "apply_capacity_brainlift_skill" in tool_names
