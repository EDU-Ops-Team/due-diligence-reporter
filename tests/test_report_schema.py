"""Tests for the DD report template schema, alias map, and normalization."""

from __future__ import annotations

import pytest

from due_diligence_reporter.report_schema import (
    ALLOWED_CAN_WE_ANSWERS,
    AGENT_KEY_ALIASES,
    LINK_TOKENS,
    TEMPLATE_TOKEN_SET,
    TEMPLATE_TOKENS,
    compute_deltas,
    normalize_report_data,
)


# ---------------------------------------------------------------------------
# Schema integrity
# ---------------------------------------------------------------------------

def test_no_duplicate_tokens():
    """Every token in TEMPLATE_TOKENS must appear exactly once."""
    seen: set[str] = set()
    dupes: list[str] = []
    for token in TEMPLATE_TOKENS:
        if token in seen:
            dupes.append(token)
        seen.add(token)
    assert dupes == [], f"Duplicate template tokens: {dupes}"


def test_set_matches_list():
    """TEMPLATE_TOKEN_SET must contain exactly the same items as TEMPLATE_TOKENS."""
    assert TEMPLATE_TOKEN_SET == frozenset(TEMPLATE_TOKENS)


def test_all_aliases_point_to_valid_tokens():
    """Every alias target must exist in TEMPLATE_TOKEN_SET."""
    bad = {
        alias: target
        for alias, target in AGENT_KEY_ALIASES.items()
        if target not in TEMPLATE_TOKEN_SET
    }
    assert bad == {}, f"Aliases pointing to invalid tokens: {bad}"


def test_no_alias_is_also_a_template_token():
    """An alias key should not itself be a canonical template token."""
    overlap = {k for k in AGENT_KEY_ALIASES if k in TEMPLATE_TOKEN_SET}
    assert overlap == set(), f"Alias keys that are also template tokens (pointless): {overlap}"


def test_token_count():
    """Template has exactly 28 tokens — guards accidental additions/removals."""
    assert len(TEMPLATE_TOKENS) == 28, (
        f"Expected 28 tokens, got {len(TEMPLATE_TOKENS)}"
    )


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

class TestNormalization:
    """Normalization tests for report data."""

    def test_normalize_direct_match(self):
        """exec.*, sources.*, meta.* keys pass through unchanged."""
        report_data = {
            "exec": {
                "c_answer": "Yes",
                "e_mvp_capacity": "36",
                "e_ideal_capacity": "54",
                "e_mvp_cost": "$185,000",
                "e_ideal_cost": "$290,000",
                "f_mvp_ready": "01/27",
                "f_ideal_ready": "04/27",
            },
            "sources": {
                "sir_link": "https://example.com/sir",
                "e_occupancy_link": "https://example.com/eocc",
            },
            "meta": {"site_name": "Alpha Test"},
        }
        replacements, unmatched, unfilled, sources = normalize_report_data(
            report_data, site_name="Alpha Test", report_date="03/19/2026",
        )
        assert replacements["exec.c_answer"] == "Yes"
        assert replacements["exec.e_mvp_capacity"] == "36"
        assert replacements["exec.e_ideal_capacity"] == "54"
        assert replacements["exec.e_mvp_cost"] == "$185,000"
        assert replacements["exec.e_ideal_cost"] == "$290,000"
        assert replacements["exec.f_mvp_ready"] == "01/27"
        assert replacements["exec.f_ideal_ready"] == "04/27"
        assert replacements["sources.sir_link"] == "https://example.com/sir"
        assert replacements["sources.e_occupancy_link"] == "https://example.com/eocc"
        assert replacements["meta.site_name"] == "Alpha Test"

    def test_normalize_alias(self):
        """Aliases resolve correctly (appendix.* → sources.*, etc.)."""
        report_data = {
            "appendix": {
                "sir_link": "https://example.com/sir",
                "inspection_link": "https://example.com/insp",
                "isp_link": "https://example.com/isp",
            },
        }
        replacements, _, _, _ = normalize_report_data(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert replacements.get("sources.sir_link") == "https://example.com/sir"
        assert replacements.get("sources.inspection_link") == "https://example.com/insp"
        assert replacements.get("sources.isp_link") == "https://example.com/isp"

    def test_alias_no_overwrite(self):
        """Canonical token wins when both alias and canonical are present."""
        report_data = {
            "sources": {"sir_link": "CANONICAL"},
            "appendix": {"sir_link": "ALIAS"},
        }
        replacements, _, _, _ = normalize_report_data(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert replacements["sources.sir_link"] == "CANONICAL"

    def test_unmatched_keys_reported(self):
        """Keys that match no template token appear in unmatched."""
        report_data = {
            "q1": {"zoning_designation": "C-2"},
            "q3": {"structural_low": "24,000"},
        }
        _, unmatched, _, _ = normalize_report_data(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert "q1.zoning_designation" in unmatched
        assert "q3.structural_low" in unmatched

    def test_unfilled_tokens(self):
        """Empty data → all tokens minus defaults are unfilled."""
        _, _, unfilled, _ = normalize_report_data(
            {}, site_name="Test", report_date="01/01/2026",
        )
        assert "meta.site_name" not in unfilled
        assert "meta.report_date" not in unfilled
        assert "exec.c_answer" in unfilled
        assert "exec.e_mvp_capacity" in unfilled
        assert "exec.f_mvp_ready" in unfilled
        assert "exec.delta_capacity" in unfilled
        assert "sources.sir_link" in unfilled

    def test_meta_defaults(self):
        """site_name, report_date, and prepared_by are auto-injected."""
        replacements, _, _, _ = normalize_report_data(
            {}, site_name="Alpha Metro", report_date="03/19/2026",
        )
        assert replacements["meta.site_name"] == "Alpha Metro"
        assert replacements["meta.report_date"] == "03/19/2026"
        assert replacements["meta.prepared_by"] == "EDU Ops Team"

    def test_pick_menu_tokens_pass_through(self):
        """Pick-menu dimension tokens pass through."""
        report_data = {
            "exec": {
                "c_zoning": "Permitted by right",
                "c_edreg": "Not required",
                "c_occupancy": "Has E-Occupancy",
            },
        }
        replacements, _, _, _ = normalize_report_data(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert replacements["exec.c_zoning"] == "Permitted by right"
        assert replacements["exec.c_edreg"] == "Not required"
        assert replacements["exec.c_occupancy"] == "Has E-Occupancy"

    def test_p1_assignee_name_aliases_to_prepared_by(self):
        report_data = {"p1_assignee_name": "Jane Owner"}
        replacements, _, _, sources = normalize_report_data(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert replacements["meta.prepared_by"] == "Jane Owner"
        assert sources["meta.prepared_by"] == "alias:p1_assignee_name"

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
    def test_can_we_answer_normalizes_to_allowed_values(self, raw_value, expected):
        report_data = {"exec": {"c_answer": raw_value}}
        replacements, _, unfilled, _ = normalize_report_data(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert replacements["exec.c_answer"] == expected
        assert replacements["exec.c_answer"] in ALLOWED_CAN_WE_ANSWERS
        assert "exec.c_answer" not in unfilled

    def test_invalid_can_we_answer_is_left_unfilled(self):
        report_data = {"exec": {"c_answer": "Maybe"}}
        replacements, _, unfilled, _ = normalize_report_data(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert "exec.c_answer" not in replacements
        assert "exec.c_answer" in unfilled

    def test_backward_compat_timeline_alias(self):
        """Old exec.f_ready_mm_yy aliases to exec.f_mvp_ready."""
        report_data = {
            "exec": {"f_ready_mm_yy": "09/27"},
        }
        replacements, _, _, _ = normalize_report_data(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert replacements.get("exec.f_mvp_ready") == "09/27"

    def test_typo_alias_ideal_capacity(self):
        """Typo alias exec.e_ideal_capcity → exec.e_ideal_capacity."""
        report_data = {
            "exec": {"e_ideal_capcity": "54"},
        }
        replacements, _, _, _ = normalize_report_data(
            report_data, site_name="Test", report_date="01/01/2026",
        )
        assert replacements.get("exec.e_ideal_capacity") == "54"

    def test_token_sources(self):
        """token_sources tracks where each token value came from."""
        report_data = {
            "exec": {
                "c_answer": "Yes",
                "c_zoning": "Permitted",
                "f_ready_mm_yy": "09/27",
            },
            "appendix": {
                "sir_link": "https://example.com/sir",
            },
        }
        _, _, _, sources = normalize_report_data(
            report_data, site_name="Test Site", report_date="03/20/2026",
        )
        assert sources["exec.c_answer"] == "agent"
        assert sources["exec.c_zoning"] == "agent"
        assert sources["meta.site_name"] == "default"
        assert sources["meta.report_date"] == "default"
        assert sources["exec.f_mvp_ready"] == "alias:exec.f_ready_mm_yy"
        assert sources["sources.sir_link"] == "alias:appendix.sir_link"
        assert sources["exec.e_mvp_capacity"] == "unfilled"
        assert sources["exec.delta_capacity"] == "unfilled"


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------

class TestDeltaComputation:
    """Tests for server-computed delta column values."""

    def test_all_deltas_computed(self):
        replacements = {
            "exec.e_mvp_capacity": "36",
            "exec.e_ideal_capacity": "54",
            "exec.e_mvp_cost": "$185,000",
            "exec.e_ideal_cost": "$290,000",
            "exec.f_mvp_ready": "01/27",
            "exec.f_ideal_ready": "04/27",
        }
        compute_deltas(replacements)
        assert replacements["exec.delta_capacity"] == "+18"
        assert replacements["exec.delta_cost"] == "+$105,000"
        assert replacements["exec.delta_ready"] == "+3 mo"

    def test_zero_delta(self):
        replacements = {
            "exec.e_mvp_capacity": "36",
            "exec.e_ideal_capacity": "36",
            "exec.e_mvp_cost": "$185,000",
            "exec.e_ideal_cost": "$185,000",
            "exec.f_mvp_ready": "01/27",
            "exec.f_ideal_ready": "01/27",
        }
        compute_deltas(replacements)
        assert replacements["exec.delta_capacity"] == "0"
        assert replacements["exec.delta_cost"] == "$0"
        assert replacements["exec.delta_ready"] == "0 mo"

    def test_negative_cost_delta(self):
        replacements = {
            "exec.e_mvp_cost": "$290,000",
            "exec.e_ideal_cost": "$185,000",
        }
        compute_deltas(replacements)
        assert replacements["exec.delta_cost"] == "-$105,000"

    def test_missing_values_no_delta(self):
        replacements = {
            "exec.e_mvp_capacity": "36",
            "exec.e_mvp_cost": "$185,000",
        }
        compute_deltas(replacements)
        assert "exec.delta_capacity" not in replacements
        assert "exec.delta_cost" not in replacements
        assert "exec.delta_ready" not in replacements

    def test_unparseable_values_no_delta(self):
        replacements = {
            "exec.e_mvp_capacity": "thirty-six",
            "exec.e_ideal_capacity": "54",
            "exec.e_mvp_cost": "unknown",
            "exec.e_ideal_cost": "$290,000",
            "exec.f_mvp_ready": "Jan 2027",
            "exec.f_ideal_ready": "04/27",
        }
        compute_deltas(replacements)
        assert "exec.delta_capacity" not in replacements
        assert "exec.delta_cost" not in replacements
        assert "exec.delta_ready" not in replacements

    def test_existing_delta_not_overwritten(self):
        replacements = {
            "exec.e_mvp_capacity": "36",
            "exec.e_ideal_capacity": "54",
            "exec.delta_capacity": "MANUAL",
        }
        compute_deltas(replacements)
        assert replacements["exec.delta_capacity"] == "MANUAL"

    def test_cross_year_timeline_delta(self):
        replacements = {
            "exec.f_mvp_ready": "11/26",
            "exec.f_ideal_ready": "02/27",
        }
        compute_deltas(replacements)
        assert replacements["exec.delta_ready"] == "+3 mo"


# ---------------------------------------------------------------------------
# Link token validation
# ---------------------------------------------------------------------------

class TestLinkTokenSets:
    """Verify link token sets are valid subsets of template tokens."""

    def test_link_tokens_are_valid(self):
        """Every link token must be in TEMPLATE_TOKEN_SET."""
        bad = LINK_TOKENS - TEMPLATE_TOKEN_SET
        assert bad == set(), f"Link tokens not in template: {bad}"


# ---------------------------------------------------------------------------
# Pipeline tool definitions
# ---------------------------------------------------------------------------

class TestPipelineToolDefinitions:
    """Verify save_skill_report is registered in the pipeline."""

    def test_save_skill_report_tool_exists(self):
        from due_diligence_reporter.report_pipeline import TOOL_DEFINITIONS
        tool_names = [t["name"] for t in TOOL_DEFINITIONS]
        assert "save_skill_report" in tool_names
