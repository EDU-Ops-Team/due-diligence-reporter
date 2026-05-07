"""Tests for the partial-on-purpose completeness metadata."""

from __future__ import annotations

from due_diligence_reporter.completeness import (
    RAYCON_PENDING_REASON,
    compute_completeness_block,
    is_raycon_pending_placeholder,
    project_completeness_from_readiness,
    raycon_token_paths,
)
from due_diligence_reporter.google_doc_builder import format_partial_banner_text

# ---------------------------------------------------------------------------
# Token classification
# ---------------------------------------------------------------------------


class TestIsRayconPendingPlaceholder:
    def test_fastest_open_placeholder(self) -> None:
        assert is_raycon_pending_placeholder(
            "[Not found - Fastest Open scenario not extracted]"
        )

    def test_max_capacity_placeholder(self) -> None:
        assert is_raycon_pending_placeholder(
            "[Not found - Max Capacity scenario not extracted]"
        )

    def test_real_value_is_not_placeholder(self) -> None:
        assert not is_raycon_pending_placeholder("$1,234,567")

    def test_unrelated_not_found_is_not_placeholder(self) -> None:
        assert not is_raycon_pending_placeholder("[Not found - SIR]")

    def test_empty_is_not_placeholder(self) -> None:
        assert not is_raycon_pending_placeholder("")


class TestRayconTokenPaths:
    def test_includes_summary_tokens(self) -> None:
        paths = set(raycon_token_paths())
        assert "exec.fastest_open_capacity" in paths
        assert "exec.fastest_open_capex" in paths
        assert "exec.fastest_open_open_date" in paths
        assert "exec.max_capacity_capacity" in paths

    def test_includes_cost_breakdown_tokens(self) -> None:
        paths = set(raycon_token_paths())
        assert "exec.cost_grand_total_fastest_open" in paths
        assert "exec.cost_demolition_max_capacity" in paths

    def test_total_count(self) -> None:
        # 12 cost rows * 2 scenarios + 3 summary fields * 2 scenarios = 30
        assert len(raycon_token_paths()) == 30


# ---------------------------------------------------------------------------
# compute_completeness_block — the unit-test contract called out in the
# Rec. 5 plan.
# ---------------------------------------------------------------------------


def _all_raycon_pending() -> dict[str, str]:
    """Every RayCon token holds the pending placeholder."""
    return {
        token: "[Not found - Fastest Open scenario not extracted]"
        if "fastest_open" in token
        else "[Not found - Max Capacity scenario not extracted]"
        for token in raycon_token_paths()
    }


def _all_raycon_filled() -> dict[str, str]:
    """Every RayCon token holds a real value."""
    return dict.fromkeys(raycon_token_paths(), "$0")


class TestComputeCompletenessBlock:
    def test_all_pending_is_partial(self) -> None:
        replacements = _all_raycon_pending()
        block = compute_completeness_block(replacements)
        assert block["stage"] == "partial"
        assert block["pending_token_count"] == 30
        assert block["filled_token_count"] == 0
        assert block["auto_republish_on"] == ["raycon_scenario.json"]
        assert RAYCON_PENDING_REASON in block["pending_reasons"]
        assert len(block["pending_reasons"][RAYCON_PENDING_REASON]) == 30

    def test_all_filled_is_complete(self) -> None:
        replacements = _all_raycon_filled()
        replacements.update({
            "meta.site_name": "Tulsa-North",
            "exec.c_answer": "Yes",
        })
        block = compute_completeness_block(replacements)
        assert block["stage"] == "complete"
        assert block["pending_token_count"] == 0
        assert block["pending_reasons"] == {}
        assert block["auto_republish_on"] == []
        # filled_token_count counts every non-empty, non-placeholder
        # entry in the map — the 30 RayCon tokens plus the 2 we added.
        assert block["filled_token_count"] == 32

    def test_partial_only_partially_filled_raycon(self) -> None:
        replacements = _all_raycon_pending()
        replacements["exec.fastest_open_capacity"] = "120 students"
        block = compute_completeness_block(replacements)
        assert block["stage"] == "partial"
        assert block["pending_token_count"] == 29
        # The one filled RayCon token contributes to filled_token_count.
        assert block["filled_token_count"] == 1
        pending_paths = block["pending_reasons"][RAYCON_PENDING_REASON]
        assert "exec.fastest_open_capacity" not in pending_paths

    def test_pending_reasons_open_ended_dict(self) -> None:
        # The dict shape is {reason_key: [token_paths]} so future
        # reasons can be added without a contract change.
        block = compute_completeness_block(_all_raycon_pending())
        assert isinstance(block["pending_reasons"], dict)
        for paths in block["pending_reasons"].values():
            assert isinstance(paths, list)


class TestProjectCompletenessFromReadiness:
    def test_raycon_missing_projects_partial(self) -> None:
        block = project_completeness_from_readiness(raycon_scenario_found=False)
        assert block["stage"] == "partial"
        assert block["pending_token_count"] == 30
        assert block["auto_republish_on"] == ["raycon_scenario.json"]
        # Filled count is unknown pre-generation.
        assert block["filled_token_count"] is None

    def test_raycon_present_projects_complete(self) -> None:
        block = project_completeness_from_readiness(raycon_scenario_found=True)
        assert block["stage"] == "complete"
        assert block["pending_token_count"] == 0
        assert block["pending_reasons"] == {}
        assert block["auto_republish_on"] == []


# ---------------------------------------------------------------------------
# Banner rendering — must render when partial, must be empty when complete.
# ---------------------------------------------------------------------------


class TestFormatPartialBannerText:
    def test_complete_renders_empty(self) -> None:
        block = {"stage": "complete", "pending_reasons": {}}
        assert format_partial_banner_text(block) == ""

    def test_none_renders_empty(self) -> None:
        assert format_partial_banner_text(None) == ""

    def test_partial_with_known_reason(self) -> None:
        block = {
            "stage": "partial",
            "pending_reasons": {RAYCON_PENDING_REASON: ["exec.fastest_open_capacity"]},
        }
        text = format_partial_banner_text(
            block,
            block_plan_submitted_display="2026-05-07 13:42 UTC",
        )
        assert "PARTIAL REPORT" in text
        assert "RayCon cost & capacity" in text
        assert "Block Plan submitted 2026-05-07 13:42 UTC" in text
        assert "republish automatically" in text

    def test_partial_falls_back_when_timestamp_missing(self) -> None:
        block = {
            "stage": "partial",
            "pending_reasons": {RAYCON_PENDING_REASON: []},
        }
        text = format_partial_banner_text(block, block_plan_submitted_display=None)
        assert "Block Plan submitted at unknown time" in text

    def test_partial_unknown_reason_uses_raw_key(self) -> None:
        block = {
            "stage": "partial",
            "pending_reasons": {"vendor_sir_pending": []},
        }
        text = format_partial_banner_text(block)
        # New reason keys must surface in the banner even before a
        # display label has been registered, so the fallback uses
        # the raw key rather than dropping the line.
        assert "vendor_sir_pending" in text
