"""Tests for the programmatic Google Doc builder."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from due_diligence_reporter.google_doc_builder import (
    _COST_BREAKDOWN_ROWS,
    _DUE_DILIGENCE_DATA_ROWS,
    _HEADER_ROWS,
    _LINK_GAP_LABELS,
    _SOURCE_DOC_ROWS,
    CITATIONS_BLOCK_KEY,
    SOURCE_QUALITY_NOTES_KEY,
    VERIFICATION_OPEN_ITEMS_KEY,
    _cell_index,
    _doc_end_index,
    _DocBuilder,
    _find_table,
    _format_score_value,
    _normalize_bulleted_field,
    _normalize_replacements_for_rendering,
    _resolve_link_value,
    _resolve_value,
    _sanitize_ascii_punctuation,
    _split_bullets_and_footnotes,
    _summary_display_lines,
    _table_cell_range,
    _validate_batch_update_requests,
    build_dd_report_doc,
)
from due_diligence_reporter.report_schema import (
    LINK_DISPLAY_LABELS,
    LINK_TOKENS,
    TEMPLATE_TOKENS,
)

# ---------------------------------------------------------------------------
# Helper: build a fake doc body for API read-back
# ---------------------------------------------------------------------------


def _make_table_element(
    start_index: int,
    rows: int,
    cols: int,
    *,
    cell_texts: dict[tuple[int, int], str] | None = None,
) -> dict[str, Any]:
    """Create a minimal table element for tests.

    Each cell has a single paragraph with one textRun.
    Cell indices are assigned sequentially starting from start_index + 1.
    """
    idx = start_index + 1
    table_rows = []
    for r in range(rows):
        cells = []
        for c in range(cols):
            text = ""
            if cell_texts and (r, c) in cell_texts:
                text = cell_texts[(r, c)]
            cell_content = {
                "paragraph": {
                    "elements": [
                        {
                            "startIndex": idx,
                            "endIndex": idx + max(len(text), 1),
                            "textRun": {"content": text or "\n"},
                        }
                    ]
                },
                "startIndex": idx,
                "endIndex": idx + max(len(text), 1),
            }
            cells.append({
                "content": [cell_content],
                "startIndex": idx,
                "endIndex": idx + max(len(text), 1),
            })
            idx += max(len(text), 1) + 2  # gap for structural chars
        table_rows.append({"tableCells": cells})

    return {
        "startIndex": start_index,
        "endIndex": idx,
        "table": {
            "rows": rows,
            "columns": cols,
            "tableRows": table_rows,
        },
    }


def _make_paragraph_element(start_index: int, text: str) -> dict[str, Any]:
    return {
        "startIndex": start_index,
        "endIndex": start_index + len(text),
        "paragraph": {
            "elements": [
                {
                    "startIndex": start_index,
                    "endIndex": start_index + len(text),
                    "textRun": {"content": text},
                }
            ]
        },
    }


# ---------------------------------------------------------------------------
# _DocBuilder unit tests
# ---------------------------------------------------------------------------


class TestDocBuilder:
    def test_insert_text_advances_index(self) -> None:
        b = _DocBuilder(start_index=1)
        start, end = b.insert_text("hello")
        assert start == 1
        assert end == 6
        assert b.index == 6

    def test_insert_text_creates_request(self) -> None:
        b = _DocBuilder(start_index=1)
        b.insert_text("hello")
        assert len(b.requests) == 1
        req = b.requests[0]
        assert "insertText" in req
        assert req["insertText"]["location"]["index"] == 1
        assert req["insertText"]["text"] == "hello"

    def test_style_text_creates_request(self) -> None:
        b = _DocBuilder()
        b.style_text(1, 6, bold=True, font_size=12, font_family="Arial")
        assert len(b.requests) == 1
        req = b.requests[0]["updateTextStyle"]
        assert req["range"]["startIndex"] == 1
        assert req["range"]["endIndex"] == 6
        assert req["textStyle"]["bold"] is True
        assert req["textStyle"]["fontSize"]["magnitude"] == 12
        assert "bold" in req["fields"]
        assert "fontSize" in req["fields"]

    def test_style_text_with_link(self) -> None:
        b = _DocBuilder()
        b.style_text(1, 10, link_url="https://example.com")
        assert len(b.requests) == 1
        req = b.requests[0]["updateTextStyle"]
        assert req["textStyle"]["link"]["url"] == "https://example.com"
        assert "link" in req["fields"]

    def test_style_text_noop_when_no_fields(self) -> None:
        b = _DocBuilder()
        b.style_text(1, 5)
        assert len(b.requests) == 0

    def test_style_paragraph(self) -> None:
        b = _DocBuilder()
        b.style_paragraph(1, 10, named_style="HEADING_1", alignment="CENTER")
        assert len(b.requests) == 1
        req = b.requests[0]["updateParagraphStyle"]
        assert req["paragraphStyle"]["namedStyleType"] == "HEADING_1"
        assert req["paragraphStyle"]["alignment"] == "CENTER"

    def test_insert_heading(self) -> None:
        b = _DocBuilder(start_index=1)
        start, end = b.insert_heading("Test Heading", level=2)
        assert start == 1
        # "Test Heading\n" = 13 chars
        assert end == 14
        # Should have insertText + updateParagraphStyle
        assert len(b.requests) == 2
        assert "insertText" in b.requests[0]
        assert "updateParagraphStyle" in b.requests[1]
        assert b.requests[1]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "HEADING_2"

    def test_insert_table_sets_sentinel_index(self) -> None:
        b = _DocBuilder(start_index=10)
        table_start = b.insert_table(3, 2)
        assert table_start == 10
        assert b.index == -1  # sentinel — must re-read doc

    def test_insert_paragraph(self) -> None:
        b = _DocBuilder(start_index=1)
        start, end = b.insert_paragraph("Some text")
        assert start == 1
        assert end == 11  # "Some text\n" = 10 chars


class TestBatchUpdateValidation:
    def test_rejects_insert_text_at_zero(self) -> None:
        with pytest.raises(ValueError, match=r"requests\[0\].insertText.location.index"):
            _validate_batch_update_requests([
                {"insertText": {"location": {"index": 0}, "text": "bad"}},
            ])

    def test_rejects_insert_text_after_table_sentinel(self) -> None:
        builder = _DocBuilder(start_index=10)
        builder.insert_heading("First table", level=3)
        builder.insert_table(2, 2)
        builder.insert_heading("Second table", level=3)

        with pytest.raises(ValueError, match="insertText.location.index"):
            _validate_batch_update_requests(builder.requests)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestResolveValue:
    def test_returns_value_when_present(self) -> None:
        assert _resolve_value({"key": "val"}, "key") == "val"

    def test_returns_gap_when_missing(self) -> None:
        assert _resolve_value({}, "key", "[gap]") == "[gap]"

    def test_returns_gap_when_empty(self) -> None:
        assert _resolve_value({"key": ""}, "key", "[gap]") == "[gap]"

    def test_returns_gap_when_whitespace(self) -> None:
        assert _resolve_value({"key": "  "}, "key", "[gap]") == "[gap]"

    def test_returns_empty_string_default_gap(self) -> None:
        assert _resolve_value({}, "key") == ""


class TestFormatScoreValue:
    def test_numeric_scores_render_with_color_labels(self) -> None:
        assert _format_score_value("1") == "1 - Green"
        assert _format_score_value("2") == "2 - Yellow"
        assert _format_score_value("3") == "3 - Red"

    def test_color_scores_render_with_numeric_labels(self) -> None:
        assert _format_score_value("GREEN") == "1 - Green"
        assert _format_score_value("Yellow") == "2 - Yellow"
        assert _format_score_value("red") == "3 - Red"

    def test_existing_canonical_or_gap_values_are_preserved(self) -> None:
        assert _format_score_value("2 - Yellow") == "2 - Yellow"
        assert _format_score_value("[Not found -- score not stated]") == (
            "[Not found -- score not stated]"
        )


class TestResolveLinkValue:
    def test_url_returns_display_and_url(self) -> None:
        repl = {"sources.sir_link": "https://drive.google.com/file/abc"}
        display, url = _resolve_link_value(repl, "sources.sir_link")
        assert display == "View SIR"
        assert url == "https://drive.google.com/file/abc"

    def test_non_url_returns_value_and_none(self) -> None:
        repl = {"sources.sir_link": "[Not found - SIR]"}
        display, url = _resolve_link_value(repl, "sources.sir_link")
        assert display == "[Not found - SIR]"
        assert url is None

    def test_missing_returns_gap_and_none(self) -> None:
        display, url = _resolve_link_value({}, "sources.sir_link")
        assert display == _LINK_GAP_LABELS["sources.sir_link"]
        assert url is None

    def test_empty_returns_gap_and_none(self) -> None:
        display, url = _resolve_link_value({"sources.sir_link": ""}, "sources.sir_link")
        assert display == _LINK_GAP_LABELS["sources.sir_link"]
        assert url is None


class TestFindTable:
    def test_finds_first_table(self) -> None:
        table = _make_table_element(10, 2, 2)
        para = _make_paragraph_element(1, "text\n")
        content = [para, table]
        result = _find_table(content, 0)
        assert result is not None
        assert "table" in result

    def test_finds_second_table(self) -> None:
        t1 = _make_table_element(10, 2, 2)
        t2 = _make_table_element(100, 3, 3)
        content = [t1, t2]
        result = _find_table(content, 1)
        assert result is not None
        assert result["startIndex"] == 100

    def test_returns_none_when_no_tables(self) -> None:
        para = _make_paragraph_element(1, "text\n")
        assert _find_table([para], 0) is None

    def test_returns_none_when_index_too_high(self) -> None:
        table = _make_table_element(10, 2, 2)
        assert _find_table([table], 1) is None


class TestDocEndIndex:
    def test_returns_last_end_minus_one(self) -> None:
        content = [
            {"endIndex": 10},
            {"endIndex": 50},
        ]
        assert _doc_end_index(content) == 49

    def test_empty_content_returns_1(self) -> None:
        assert _doc_end_index([]) == 1

    def test_never_below_1(self) -> None:
        content = [{"endIndex": 1}]
        assert _doc_end_index(content) == 1


class TestCellIndex:
    def test_returns_start_index_of_cell_content(self) -> None:
        table = _make_table_element(10, 2, 2)
        idx = _cell_index(table, 0, 0)
        # First cell should start at start_index + 1 = 11
        assert idx == 11

    def test_empty_cell_content_uses_cell_start_fallback(self) -> None:
        table = _make_table_element(10, 2, 2)
        cell = table["table"]["tableRows"][0]["tableCells"][0]
        cell["content"] = []

        assert _cell_index(table, 0, 0) == 11

    def test_missing_paragraph_elements_use_paragraph_start_fallback(self) -> None:
        table = _make_table_element(10, 2, 2)
        para = table["table"]["tableRows"][0]["tableCells"][0]["content"][0]
        para["paragraph"]["elements"] = []

        assert _cell_index(table, 0, 0) == 11

    def test_empty_cell_range_uses_cell_metadata(self) -> None:
        table = _make_table_element(10, 2, 2)
        cell = table["table"]["tableRows"][0]["tableCells"][0]
        cell["content"] = []

        assert _table_cell_range(table, 0, 0) == (11, 12)

    def test_missing_cell_metadata_raises_clear_error(self) -> None:
        table = _make_table_element(10, 2, 2)
        cell = table["table"]["tableRows"][0]["tableCells"][0]
        cell["content"] = []
        cell.pop("startIndex")

        with pytest.raises(ValueError, match="missing a valid start index"):
            _cell_index(table, 0, 0)

    def test_missing_cell_range_metadata_raises_clear_error(self) -> None:
        table = _make_table_element(10, 2, 2)
        cell = table["table"]["tableRows"][0]["tableCells"][0]
        cell["content"] = []
        cell.pop("startIndex")

        with pytest.raises(ValueError, match="missing a valid start index"):
            _table_cell_range(table, 0, 0)


# ---------------------------------------------------------------------------
# Template token coverage
# ---------------------------------------------------------------------------


class TestTokenCoverage:
    """Verify the builder handles all current template tokens."""

    # Collect all tokens referenced by the builder module
    _BUILDER_TOKENS: set[str] = set()

    @classmethod
    def setup_class(cls) -> None:
        # Header tokens
        for _, token in _HEADER_ROWS:
            cls._BUILDER_TOKENS.add(token)

        # Executive summary tokens
        cls._BUILDER_TOKENS.update([
            "exec.c_answer", "exec.c_zoning", "exec.c_edreg", "exec.c_occupancy",
            "exec.c_permit_timeline", "exec.c_construction_timeline",
            "exec.direct_viable_buildout", "exec.alpha_fit",
        ])

        # Scenario summary tokens
        for scenario in ("fastest_open", "max_capacity"):
            for metric in ("capacity", "open_date", "capex"):
                cls._BUILDER_TOKENS.add(f"exec.{scenario}_{metric}")
        cls._BUILDER_TOKENS.update([
            "exec.fastest_open_summary",
            "exec.max_capacity_summary",
        ])

        cls._BUILDER_TOKENS.update([
            "exec.alpha_phasing_phase_i_scope",
            "exec.alpha_phasing_phase_ii_scope",
            "exec.alpha_phasing_phase_ii_allowance",
            "exec.alpha_phasing_recommended_timing",
            "exec.alpha_phasing_quality_bar_status",
        ])

        # Cost breakdown tokens
        for row_key, _ in _COST_BREAKDOWN_ROWS:
            for scenario in ("fastest_open", "max_capacity"):
                cls._BUILDER_TOKENS.add(f"exec.cost_{row_key}_{scenario}")

        cls._BUILDER_TOKENS.update([
            "exec.regulatory_score",
            "exec.regulatory_comment",
            "exec.building_score",
            "exec.building_comment",
            "exec.play_area_score",
            "exec.play_area_comment",
            "exec.school_ops_score",
            "exec.school_ops_comment",
        ])

        # Narrative tokens
        cls._BUILDER_TOKENS.update([
            "exec.acquisition_conditions", "exec.tradeoffs_and_deficiencies",
        ])

        # Source link tokens
        for _, token in _SOURCE_DOC_ROWS:
            cls._BUILDER_TOKENS.add(token)

    def test_all_current_template_tokens_covered(self) -> None:
        """Every token in the live two-scenario contract is handled."""
        current_tokens = set()

        # meta tokens
        current_tokens.update([
            "meta.site_name", "meta.marketing_name", "meta.city_state_zip",
            "meta.school_type", "meta.report_date", "meta.prepared_by", "meta.rebl_site_id",
            "meta.drive_folder_url",
        ])

        # exec can-we-open tokens
        current_tokens.update([
            "exec.c_answer", "exec.c_edreg", "exec.c_occupancy", "exec.c_zoning",
            "exec.c_permit_timeline", "exec.c_construction_timeline",
            "exec.direct_viable_buildout", "exec.alpha_fit",
        ])

        # exec scenario summary (2 scenarios × 3 metrics = 6)
        for scenario in ("fastest_open", "max_capacity"):
            for metric in ("capacity", "capex", "open_date"):
                current_tokens.add(f"exec.{scenario}_{metric}")
        current_tokens.update([
            "exec.fastest_open_summary",
            "exec.max_capacity_summary",
        ])

        current_tokens.update([
            "exec.alpha_phasing_phase_i_scope",
            "exec.alpha_phasing_phase_ii_scope",
            "exec.alpha_phasing_phase_ii_allowance",
            "exec.alpha_phasing_recommended_timing",
            "exec.alpha_phasing_quality_bar_status",
        ])

        # exec cost breakdown (12 rows × 2 scenarios = 24)
        cost_keys = [
            "cost_demolition", "cost_framing_doors", "cost_mep_fire_life_safety",
            "cost_plumbing_bathrooms", "cost_finish_work", "cost_furniture",
            "cost_tech_security_signage", "cost_other_hard_costs", "cost_soft_costs",
            "cost_gc_fee", "cost_contingency", "cost_grand_total",
        ]
        for key in cost_keys:
            for scenario in ("fastest_open", "max_capacity"):
                current_tokens.add(f"exec.{key}_{scenario}")

        current_tokens.update([
            "exec.regulatory_score",
            "exec.regulatory_comment",
            "exec.building_score",
            "exec.building_comment",
            "exec.play_area_score",
            "exec.play_area_comment",
            "exec.school_ops_score",
            "exec.school_ops_comment",
        ])

        # exec narrative
        current_tokens.update(["exec.acquisition_conditions", "exec.tradeoffs_and_deficiencies"])

        # sources
        current_tokens.update([
            "sources.sir_link", "sources.inspection_link",
            "sources.block_plan_link", "sources.rebl_link",
            "sources.e_occupancy_link", "sources.school_approval_link",
            "sources.opening_plan_link", "sources.alpha_phasing_plan_link",
        ])

        # 8 meta + 8 exec summary/direct + 8 scenario summary/detail + 5 phasing
        # + 24 cost + 8 score/comment + 2 narrative + 8 sources = 71
        assert len(current_tokens) == 71, f"Expected 71 tokens, got {len(current_tokens)}"

        # All template tokens should be covered by the builder
        missing = current_tokens - self._BUILDER_TOKENS
        assert not missing, f"Builder does not handle current tokens: {missing}"

    def test_builder_tokens_are_subset_of_template_tokens(self) -> None:
        """Every token the builder references exists in TEMPLATE_TOKENS."""
        template_set = set(TEMPLATE_TOKENS)
        unknown = self._BUILDER_TOKENS - template_set
        assert not unknown, f"Builder references unknown tokens: {unknown}"


# ---------------------------------------------------------------------------
# build_dd_report_doc integration test (mocked API)
# ---------------------------------------------------------------------------


def _make_mock_docs_service(
    *,
    num_tables: int = 5,
) -> MagicMock:
    """Create a mock docs_service that returns plausible doc structures.

    Each call to documents().get() returns a body with the expected
    number of tables plus trailing paragraph.
    """
    service = MagicMock()

    def _make_body() -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        idx = 1

        # Title paragraph
        content.append(_make_paragraph_element(idx, "Site Due Diligence Report\n"))
        idx += 30

        # Tables
        for t in range(num_tables):
            if t == 0:
                rows, cols = len(_HEADER_ROWS), 2
            elif t == 1:
                rows, cols = len(_DUE_DILIGENCE_DATA_ROWS), 4
            elif t in (2, 3):
                rows, cols = len(_COST_BREAKDOWN_ROWS) + 1, 2
            else:
                rows, cols = len(_SOURCE_DOC_ROWS) + 1, 3

            table = _make_table_element(idx, rows, cols)
            content.append(table)
            idx = table["endIndex"] + 5

        # Trailing paragraph
        content.append(_make_paragraph_element(idx, "\n"))
        idx += 1

        return {"body": {"content": content}, "documentId": "doc123"}

    service.documents.return_value.get.return_value.execute = _make_body
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {}

    return service


def _all_batch_requests(docs_svc: MagicMock) -> list[dict[str, Any]]:
    return [
        request
        for call_args in docs_svc.documents.return_value.batchUpdate.call_args_list
        for request in call_args.kwargs["body"]["requests"]
    ]


def _inserted_text(docs_svc: MagicMock) -> str:
    return "".join(
        request["insertText"]["text"]
        for request in _all_batch_requests(docs_svc)
        if "insertText" in request
    )


class TestBuildDdReportDoc:
    """Integration-level tests for build_dd_report_doc with mocked API."""

    def _full_replacements(self) -> dict[str, str]:
        """Build a complete set of replacements for all live template tokens."""
        repl: dict[str, str] = {
            "meta.site_name": "Alpha Boca Raton 2200",
            "meta.marketing_name": "Boca Academy",
            "meta.city_state_zip": "Boca Raton, FL 33431",
            "meta.school_type": "micro",
            "meta.report_date": "04/14/2026",
            "meta.prepared_by": "Jane Smith",
            "meta.rebl_site_id": "alpha-boca-raton-2200",
            "meta.drive_folder_url": "https://drive.google.com/drive/folders/abc123",
            "exec.c_answer": "Yes",
            "exec.c_edreg": "FL: Registration required. Timeline: 30 days.",
            "exec.c_occupancy": "78/100 YELLOW - Office general, 6-9 months",
            "exec.c_zoning": "Permitted",
            "exec.c_permit_timeline": "10 weeks — admin CUP, no public hearing (SIR p.3)",
            "exec.c_construction_timeline": "8 weeks — minimal TI, 4-classroom layout",
            "exec.direct_viable_buildout": "Fastest Open",
            "exec.alpha_fit": "No",
            "exec.fastest_open_capacity": "69 students",
            "exec.fastest_open_capex": "$487,000",
            "exec.fastest_open_open_date": "07/15/26",
            "exec.max_capacity_capacity": "125 students",
            "exec.max_capacity_capex": "$812,000",
            "exec.max_capacity_open_date": "11/26",
            "exec.fastest_open_summary": (
                "Fastest Open is viable for a small opening."
                "\nUses the lightest classroom and life-safety scope."
            ),
            "exec.max_capacity_summary": (
                "Max Capacity requires a longer buildout."
                "\nAdds the deferred classroom scope after opening."
            ),
            "exec.alpha_phasing_phase_i_scope": "Open with four classrooms and code-required life safety.",
            "exec.alpha_phasing_phase_ii_scope": "Lobby refresh; outdoor shade.",
            "exec.alpha_phasing_phase_ii_allowance": "$120k",
            "exec.alpha_phasing_recommended_timing": "Winter break after opening.",
            "exec.alpha_phasing_quality_bar_status": "Q1 target with 2 confirmed Phase II gaps.",
            "exec.regulatory_score": "YELLOW",
            "exec.regulatory_comment": "Private school registration is available after entity setup.",
            "exec.building_score": "YELLOW",
            "exec.building_comment": "MEP and life-safety scope needs confirmation.",
            "exec.play_area_score": "RED",
            "exec.play_area_comment": "No on-site play area has been documented.",
            "exec.school_ops_score": "YELLOW",
            "exec.school_ops_comment": "Opening can work with tight staffing and vendor sequencing.",
            "exec.acquisition_conditions": "- Landlord must provide 6-month TI window\n- ADA ramp required",
            "exec.tradeoffs_and_deficiencies": "- No dedicated outdoor playspace\n- Fire alarm > 15 years old",
            "sources.sir_link": "https://drive.google.com/file/d/sir123",
            "sources.inspection_link": "https://drive.google.com/file/d/insp123",
            "sources.block_plan_link": "https://drive.google.com/file/d/block123",
            "sources.rebl_link": "https://rebl3.vercel.app/site/alpha-boca-raton-2200",
            "sources.e_occupancy_link": "https://drive.google.com/file/d/eocc123",
            "sources.school_approval_link": "https://drive.google.com/file/d/sa123",
            "sources.opening_plan_link": "https://drive.google.com/file/d/op123",
            "sources.alpha_phasing_plan_link": "https://drive.google.com/file/d/phasing123",
        }
        # Cost breakdown tokens
        cost_keys = [
            "cost_demolition", "cost_framing_doors", "cost_mep_fire_life_safety",
            "cost_plumbing_bathrooms", "cost_finish_work", "cost_furniture",
            "cost_tech_security_signage", "cost_other_hard_costs", "cost_soft_costs",
            "cost_gc_fee", "cost_contingency", "cost_grand_total",
        ]
        for key in cost_keys:
            repl[f"exec.{key}_fastest_open"] = f"${hash(key) % 100},000"
            repl[f"exec.{key}_max_capacity"] = f"${hash(key) % 200},000"
        return repl

    def test_cost_tables_are_created_in_separate_valid_batches(self) -> None:
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()

        build_dd_report_doc(
            docs_svc,
            drive_svc,
            "doc123",
            self._full_replacements(),
            "Alpha Boca Raton 2200",
        )

        structural_batches = []
        for call_args in docs_svc.documents.return_value.batchUpdate.call_args_list:
            requests = call_args.kwargs["body"]["requests"]
            inserted = [
                request["insertText"]["text"]
                for request in requests
                if "insertText" in request
            ]
            if (
                "Fastest Open Cost Breakdown\n" in inserted
                or "Max Capacity Cost Breakdown\n" in inserted
            ):
                structural_batches.append(requests)

        assert len(structural_batches) == 2
        for requests in structural_batches:
            assert sum(1 for request in requests if "insertTable" in request) == 1
            for request in requests:
                if "insertText" in request:
                    assert request["insertText"]["location"]["index"] >= 1

    def test_returns_hyperlink_trace(self) -> None:
        """build_dd_report_doc returns a dict with applied/found/not_found."""
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = self._full_replacements()

        result = build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Alpha Boca")

        assert "applied" in result
        assert "found_tokens" in result
        assert "not_found_tokens" in result
        assert isinstance(result["applied"], int)
        assert result["applied"] >= 0

    def test_batch_update_called_multiple_times(self) -> None:
        """The builder issues multiple batchUpdate calls for the multi-pass approach."""
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = self._full_replacements()

        build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Alpha Boca")

        batch_calls = docs_svc.documents.return_value.batchUpdate.call_count
        assert batch_calls >= 4, f"Expected at least 4 batchUpdate calls, got {batch_calls}"

    def test_hyperlinks_applied_for_source_links(self) -> None:
        """Source document URLs get hyperlink requests."""
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = self._full_replacements()

        result = build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Alpha Boca")

        # We have source URLs plus the Drive folder URL.
        assert result["applied"] >= 6
        assert "sources.sir_link" in result["found_tokens"]

    def test_missing_values_get_gap_labels(self) -> None:
        """When tokens are missing, the builder still generates requests."""
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        # Minimal replacements — most tokens missing
        repl: dict[str, str] = {
            "meta.site_name": "Test Site",
            "meta.report_date": "04/14/2026",
        }

        result = build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Test Site")

        # Should still complete without error
        assert "applied" in result
        # No hyperlinks since no URLs provided
        assert result["applied"] == 0

class TestBuildDdReportDocRequestStructure:
    """Verify the structure of batchUpdate requests."""

    def test_first_batch_contains_insert_text_and_table(self) -> None:
        """The first batchUpdate should contain the title text insertion and table."""
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = {"meta.site_name": "Test", "meta.report_date": "04/14/2026"}

        build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Test")

        # Get the first batchUpdate call
        first_call = docs_svc.documents.return_value.batchUpdate.call_args_list[0]
        body = first_call[1]["body"] if "body" in first_call[1] else first_call.kwargs["body"]
        requests = body["requests"]

        # Should contain insertText for the title
        insert_texts = [r for r in requests if "insertText" in r]
        assert len(insert_texts) >= 1
        title_req = insert_texts[0]
        assert "Site Due Diligence Report" in title_req["insertText"]["text"]

        # Should contain insertTable for header table
        insert_tables = [r for r in requests if "insertTable" in r]
        assert len(insert_tables) == 1
        assert insert_tables[0]["insertTable"]["rows"] == len(_HEADER_ROWS)
        assert insert_tables[0]["insertTable"]["columns"] == 2

    def test_referenced_reports_table_uses_type_column(self) -> None:
        """The references table distinguishes source-folder docs from AI-generated reports."""
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = {"meta.site_name": "Test", "meta.report_date": "04/14/2026"}

        build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Test")

        insert_tables = []
        for call_args in docs_svc.documents.return_value.batchUpdate.call_args_list:
            body = call_args.kwargs["body"]
            insert_tables.extend(
                request["insertTable"]
                for request in body["requests"]
                if "insertTable" in request
            )

        assert any(
            table["rows"] == len(_SOURCE_DOC_ROWS) + 1 and table["columns"] == 3
            for table in insert_tables
        )

    def test_first_round_supporting_notes_only_render_open_items(self) -> None:
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = {
            "meta.site_name": "Test",
            "meta.report_date": "04/14/2026",
            VERIFICATION_OPEN_ITEMS_KEY: "- Confirm zoning path with Planning",
            SOURCE_QUALITY_NOTES_KEY: "- Building Inspection from another site was excluded",
            "exec.acquisition_conditions": "- Require landlord sprinkler records",
            "exec.tradeoffs_and_deficiencies": "- Parking count unknown",
        }

        build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Test")

        inserted_text = _inserted_text(docs_svc)

        assert "Supporting Notes\n" in inserted_text
        assert "Open Items to Verify\n" in inserted_text
        assert "Confirm zoning path with Planning" in inserted_text
        assert "Source Quality Notes\n" not in inserted_text
        assert "Lease Conditions\n" not in inserted_text
        assert "Trade-Offs and Deficiencies\n" not in inserted_text

    def test_greg_edits_section_order_is_canonical(self) -> None:
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = {
            "meta.site_name": "Alpha Los Angeles 5400 Beethoven St",
            "meta.report_date": "05/26/2026",
            "exec.c_answer": "No",
            "exec.c_zoning": "Use Permit Required (public)",
            "exec.c_edreg": (
                "California requires a Private School Affidavit (PSA)\n"
                "The CO is the only pre-open milestone on the education track."
            ),
            "exec.c_occupancy": (
                "Building is currently active as a school -- Group E occupancy is already established."
            ),
            "exec.c_permit_timeline": (
                "Best case: 16 weeks, worst case: 40 weeks\n"
                "9/8/2026 is 15 weeks from today"
            ),
            "exec.c_construction_timeline": (
                "SIR estimates 8 to 20 weeks for construction after permit issuance."
            ),
            "exec.direct_viable_buildout": "Fastest Open",
            "exec.alpha_fit": "Yes",
            "exec.fastest_open_summary": "Open the smallest viable school first.\nKeep Phase I tight.",
            "exec.max_capacity_summary": "Max Capacity is possible later.\nIt needs Phase II scope.",
            "exec.alpha_phasing_phase_i_scope": "Open with minimum code and classroom scope.",
            "exec.alpha_phasing_phase_ii_scope": "Finish quality-bar gaps after opening.",
            "exec.alpha_phasing_phase_ii_allowance": "$90k",
            "exec.alpha_phasing_recommended_timing": "Winter break.",
            "exec.regulatory_score": "YELLOW",
            "exec.regulatory_comment": "PSA path is available after setup.",
            "exec.building_score": "YELLOW",
            "exec.building_comment": "Building scope is manageable but not turnkey.",
            "exec.play_area_score": "RED",
            "exec.play_area_comment": "No play-area path has been validated.",
            "exec.school_ops_score": "YELLOW",
            "exec.school_ops_comment": "Ops can work with tight launch controls.",
            VERIFICATION_OPEN_ITEMS_KEY: (
                "- Pull ZIMAS case records and LADBS CO history\n"
                "- Confirm correct lease address with landlord"
            ),
            SOURCE_QUALITY_NOTES_KEY: "- Building Inspection from another site was excluded",
            "exec.acquisition_conditions": "- Require landlord sprinkler records",
            "exec.tradeoffs_and_deficiencies": "- Parking count unknown",
            CITATIONS_BLOCK_KEY: (
                "SIR -- zoning classification is M2-1 (Limited Industrial)\n"
                "School Approval Report -- California NOTIFICATION archetype confirmed"
            ),
        }

        build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Test")

        inserted_text = _inserted_text(docs_svc)

        assert (
            inserted_text.index("Due Diligence\n")
            < inserted_text.index("Executive Summary\n")
            < inserted_text.index("Fastest Open\n")
            < inserted_text.index("Max Capacity\n")
            < inserted_text.index("Direct Answer\n")
            < inserted_text.index("Phase 1 Phase 2 workbook\n")
            < inserted_text.index("Detailed Cost Breakdown\n")
            < inserted_text.index("Fastest Open Cost Breakdown\n")
            < inserted_text.index("Max Capacity Cost Breakdown\n")
            < inserted_text.index("Score Explanations\n")
            < inserted_text.index("Supporting Notes\n")
            < inserted_text.index("Open Items to Verify\n")
            < inserted_text.index("Referenced Reports\n")
            < inserted_text.index("Source Notes\n")
        )
        assert "Source Quality Notes\n" not in inserted_text
        assert "Lease Conditions\n" not in inserted_text
        assert "Trade-Offs and Deficiencies\n" not in inserted_text
        assert "Report Trace" not in inserted_text

    def test_partial_banner_emitted_when_completeness_partial(self) -> None:
        """build_dd_report_doc emits the PARTIAL REPORT banner when stage='partial'."""
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = {"meta.site_name": "Test", "meta.report_date": "04/14/2026"}
        completeness = {
            "stage": "partial",
            "filled_token_count": 0,
            "pending_token_count": 30,
            "pending_reasons": {"raycon_scenario_pending": ["exec.fastest_open_capacity"]},
            "auto_republish_on": ["raycon_scenario.json"],
            "block_plan_submitted_display": "2026-05-07 13:42 UTC",
        }

        build_dd_report_doc(
            docs_svc, drive_svc, "doc123", repl, "Test",
            completeness=completeness,
        )

        inserted_text = "\n".join(
            request["insertText"]["text"]
            for call_args in docs_svc.documents.return_value.batchUpdate.call_args_list
            for request in call_args.kwargs["body"]["requests"]
            if "insertText" in request
        )

        assert "PARTIAL REPORT" in inserted_text
        assert "RayCon cost & capacity" in inserted_text
        assert "Block Plan submitted 2026-05-07 13:42 UTC" in inserted_text
        # Banner sits above the title's header table
        assert inserted_text.index("PARTIAL REPORT") < inserted_text.index("Executive Summary")

    def test_partial_banner_omitted_when_completeness_complete(self) -> None:
        """No banner when stage='complete' — the republish path naturally
        removes the banner when raycon_scenario.json arrives."""
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = {"meta.site_name": "Test", "meta.report_date": "04/14/2026"}
        completeness = {
            "stage": "complete",
            "filled_token_count": 30,
            "pending_token_count": 0,
            "pending_reasons": {},
            "auto_republish_on": [],
        }

        build_dd_report_doc(
            docs_svc, drive_svc, "doc123", repl, "Test",
            completeness=completeness,
        )

        inserted_text = "\n".join(
            request["insertText"]["text"]
            for call_args in docs_svc.documents.return_value.batchUpdate.call_args_list
            for request in call_args.kwargs["body"]["requests"]
            if "insertText" in request
        )

        assert "PARTIAL REPORT" not in inserted_text

    def test_partial_banner_omitted_when_completeness_none(self) -> None:
        """Backwards compatibility: callers that don't pass completeness
        get the legacy (no banner) rendering."""
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = {"meta.site_name": "Test", "meta.report_date": "04/14/2026"}

        build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Test")

        inserted_text = "\n".join(
            request["insertText"]["text"]
            for call_args in docs_svc.documents.return_value.batchUpdate.call_args_list
            for request in call_args.kwargs["body"]["requests"]
            if "insertText" in request
        )

        assert "PARTIAL REPORT" not in inserted_text

    def test_direct_answer_renders_after_scenario_answers(self) -> None:
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = {
            "meta.site_name": "Test",
            "meta.report_date": "04/14/2026",
            "exec.direct_viable_buildout": "Fastest Open",
            "exec.alpha_fit": "No",
        }

        build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Test")

        inserted_text = "\n".join(
            request["insertText"]["text"]
            for call_args in docs_svc.documents.return_value.batchUpdate.call_args_list
            for request in call_args.kwargs["body"]["requests"]
            if "insertText" in request
        )

        assert (
            inserted_text.index("Fastest Open\n")
            < inserted_text.index("Max Capacity\n")
            < inserted_text.index("Direct Answer\n")
            < inserted_text.index("Detailed Cost Breakdown\n")
            < inserted_text.index("Fastest Open Cost Breakdown\n")
            < inserted_text.index("Max Capacity Cost Breakdown\n")
        )
        assert "Viable Buildout: " in inserted_text
        assert "Great Alpha School Site: " in inserted_text
        assert "2a. Viable Buildout: " not in inserted_text

    def test_exec_summary_support_lines_are_bulleted(self) -> None:
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = {
            "meta.site_name": "Test",
            "meta.report_date": "04/14/2026",
            "exec.c_zoning": (
                "Use Permit Required (public)\n"
                "Confirm CUP transfer before lease signing"
            ),
        }

        build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Test")

        requests = _all_batch_requests(docs_svc)
        zoning_insert = next(
            request["insertText"]
            for request in requests
            if request.get("insertText", {}).get("text")
            == "Zoning: Use Permit Required (public)\n"
        )
        zoning_start = zoning_insert["location"]["index"]
        support_insert = next(
            request["insertText"]
            for request in requests
            if request.get("insertText", {}).get("text")
            == "Confirm CUP transfer before lease signing\n"
        )
        support_start = support_insert["location"]["index"]

        assert any(
            "createParagraphBullets" in request
            and request["createParagraphBullets"]["range"]["startIndex"]
            <= support_start
            < request["createParagraphBullets"]["range"]["endIndex"]
            for request in requests
        )
        assert any(
            "createParagraphBullets" in request
            and request["createParagraphBullets"]["range"]["startIndex"]
            <= zoning_start
            < request["createParagraphBullets"]["range"]["endIndex"]
            for request in requests
        )

    def test_exec_summary_single_paragraph_support_is_bulleted(self) -> None:
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = {
            "meta.site_name": "Test",
            "meta.report_date": "04/14/2026",
            "exec.c_occupancy": (
                "An existing Conditional Use Permit covers school use at this site, "
                "capped at 180 students. Transferring the CUP to Alpha School "
                "requires a Planning Board modification hearing. The historic "
                "district review must also be cleared before permit issuance."
            ),
        }

        build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Test")

        requests = _all_batch_requests(docs_svc)
        support_insert = next(
            request["insertText"]
            for request in requests
            if request.get("insertText", {}).get("text")
            == (
                "Transferring the CUP to Alpha School requires a Planning Board "
                "modification hearing.\n"
            )
        )
        support_start = support_insert["location"]["index"]

        assert any(
            "createParagraphBullets" in request
            and request["createParagraphBullets"]["range"]["startIndex"]
            <= support_start
            < request["createParagraphBullets"]["range"]["endIndex"]
            for request in requests
        )

    def test_exec_summary_gap_prefix_stays_answer_line(self) -> None:
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = {
            "meta.site_name": "Test",
            "meta.report_date": "04/14/2026",
            "exec.c_construction_timeline": (
                "[Not found - RayCon scenario pending] Construction timeline cannot "
                "be confirmed without a RayCon Scenario. Existing school use reduces "
                "scope uncertainty, but historic review can still delay permits."
            ),
        }

        build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Test")

        inserted_text = _inserted_text(docs_svc)

        assert "Construction Timeline: [Not found - RayCon scenario pending]\n" in inserted_text
        assert "Construction timeline cannot be confirmed without a RayCon Scenario.\n" in inserted_text


class TestGapLabelsForLinks:
    """Test that link tokens with no value get appropriate gap labels."""

    def test_all_link_tokens_have_gap_labels(self) -> None:
        """Every link token in _LINK_GAP_LABELS maps to a source doc row or header."""
        all_link_tokens_in_builder = {t for _, t in _SOURCE_DOC_ROWS}
        all_link_tokens_in_builder.add("meta.drive_folder_url")
        for token in all_link_tokens_in_builder:
            assert token in _LINK_GAP_LABELS, f"No gap label for {token}"

    def test_source_doc_rows_match_link_tokens(self) -> None:
        """Every source doc row token is a known LINK_TOKEN."""
        for _, token in _SOURCE_DOC_ROWS:
            assert token in LINK_TOKENS, f"{token} not in LINK_TOKENS"

    def test_display_labels_exist_for_all_source_tokens(self) -> None:
        """Every source doc token has a display label in LINK_DISPLAY_LABELS."""
        for _, token in _SOURCE_DOC_ROWS:
            assert token in LINK_DISPLAY_LABELS, f"No display label for {token}"


# ---------------------------------------------------------------------------
# _split_bullets_and_footnotes
# ---------------------------------------------------------------------------


class TestSplitBulletsAndFootnotes:
    def test_standard_bullets_with_footnotes(self):
        text = (
            "- TI allowance ~$45,000 [1]\n"
            "- Landlord must repair roof [2]\n"
            "\n"
            "[1] Building Inspection p.3\n"
            "[2] Building Inspection p.7"
        )
        bullets, footnotes = _split_bullets_and_footnotes(text)
        assert bullets == ["TI allowance ~$45,000 [1]", "Landlord must repair roof [2]"]
        assert footnotes == ["[1] Building Inspection p.3", "[2] Building Inspection p.7"]

    def test_citation_markers_stay_in_bullet_text(self):
        text = "- Finding one [1]\n\n[1] Source doc p.1"
        bullets, footnotes = _split_bullets_and_footnotes(text)
        assert "[1]" in bullets[0]
        assert bullets[0] == "Finding one [1]"

    def test_no_footnotes(self):
        text = "- Item one\n- Item two"
        bullets, footnotes = _split_bullets_and_footnotes(text)
        assert bullets == ["Item one", "Item two"]
        assert footnotes == []

    def test_round_bullet_prefix_stripped(self):
        text = "\u2022 Item one\n\u2022 Item two"
        bullets, _ = _split_bullets_and_footnotes(text)
        assert bullets == ["Item one", "Item two"]

    def test_single_line_no_prefix_returns_as_bullet(self):
        text = "No acquisition conditions required"
        bullets, footnotes = _split_bullets_and_footnotes(text)
        assert bullets == ["No acquisition conditions required"]
        assert footnotes == []

    def test_placeholder_string(self):
        # "[No ...]" doesn't match ^\[\d+\] so it lands in bullets, not footnotes
        text = "[No acquisition conditions provided]"
        bullets, footnotes = _split_bullets_and_footnotes(text)
        assert bullets == ["[No acquisition conditions provided]"]
        assert footnotes == []

    def test_footnote_detected_by_pattern_without_blank_line(self):
        text = "- Risk item [1]\n[1] SIR: evidence here"
        bullets, footnotes = _split_bullets_and_footnotes(text)
        assert bullets == ["Risk item [1]"]
        assert footnotes == ["[1] SIR: evidence here"]

    def test_empty_string(self):
        bullets, footnotes = _split_bullets_and_footnotes("")
        assert bullets == []
        assert footnotes == []

    def test_apply_bullets_adds_correct_request(self):
        b = _DocBuilder(start_index=10)
        b.apply_bullets(10, 30)
        req = b.requests[0]
        assert "createParagraphBullets" in req
        assert req["createParagraphBullets"]["bulletPreset"] == "BULLET_DISC_CIRCLE_SQUARE"
        assert req["createParagraphBullets"]["range"]["startIndex"] == 10
        assert req["createParagraphBullets"]["range"]["endIndex"] == 30


class TestNarrativeNormalization:
    def test_summary_display_lines_splits_lexington_paragraph_cleanly(self) -> None:
        value = (
            "Change-of-use permit required. Building is currently office use (CRO-1). "
            "Building Commissioner confirmed change-of-use is required for any school "
            "or tutoring center use. IBC E-occupancy conversion path needed. Wet "
            "sprinkler system and addressable fire alarm are in place. Right-side "
            "exit door measured at 34 in. -- below 36 in. IBC criterion -- must be "
            "resolved. No E-Occupancy assessment in Drive folder."
        )

        assert _summary_display_lines(value) == [
            "Change-of-use permit required.",
            "Building is currently office use (CRO-1).",
            "Building Commissioner confirmed change-of-use is required for any school or tutoring center use.",
            "IBC E-occupancy conversion path needed.",
            "Wet sprinkler system and addressable fire alarm are in place.",
            "Right-side exit door measured at 34 in. -- below 36 in. IBC criterion -- must be resolved.",
            "No E-Occupancy assessment in Drive folder.",
        ]

    def test_summary_display_lines_preserves_gap_label_without_trailing_period(self) -> None:
        value = (
            "[Not found - RayCon scenario pending]. Building Inspection notes a "
            "plug-and-play Class A base. Full school-conversion TI schedule is "
            "not yet sourced."
        )

        assert _summary_display_lines(value) == [
            "[Not found - RayCon scenario pending]",
            "Building Inspection notes a plug-and-play Class A base.",
            "Full school-conversion TI schedule is not yet sourced.",
        ]

    def test_summary_display_lines_splits_before_numeric_support_sentence(self) -> None:
        value = (
            "No expedited review available from Lexington Building Department. "
            "9/8/26 is approximately 15 weeks from today."
        )

        assert _summary_display_lines(value) == [
            "No expedited review available from Lexington Building Department.",
            "9/8/26 is approximately 15 weeks from today.",
        ]

    def test_dedupes_identical_footnotes_and_renumbers_markers(self) -> None:
        value = (
            "- Landlord must repair roof [2]\n"
            "- Request TI allowance for roof repairs [1]\n"
            "\n"
            "[1] Building Inspection p.7\n"
            "[2] Building Inspection p.7"
        )

        normalized = _normalize_bulleted_field(value)
        bullets, footnotes = _split_bullets_and_footnotes(normalized)

        assert bullets == [
            "Landlord must repair roof [1]",
            "Request TI allowance for roof repairs [1]",
        ]
        assert footnotes == ["[1] Building Inspection p.7"]

    def test_moves_source_warnings_out_of_exec_summary(self) -> None:
        replacements = _normalize_replacements_for_rendering({
            "exec.c_edreg": (
                "[Document unreadable -- Document 'School Approval.docx' contained no "
                "readable DOCX text and was excluded from this run.]"
            ),
        })

        assert replacements["exec.c_edreg"] == (
            "[Not found -- School Approval source could not be validated/read]"
        )
        assert "School Approval.docx" in replacements[SOURCE_QUALITY_NOTES_KEY]

    def test_replaces_internal_template_key_mentions_in_narrative(self) -> None:
        replacements = _normalize_replacements_for_rendering({
            SOURCE_QUALITY_NOTES_KEY: (
                "- No P1 DRI was supplied; meta.prepared_by could not be populated."
            ),
            "exec.acquisition_conditions": (
                "- Confirm exec.cost_demolition_fastest_open once RayCon is available."
            ),
        })

        assert "meta.prepared_by" not in replacements[SOURCE_QUALITY_NOTES_KEY]
        assert "Prepared By could not be populated" in replacements[SOURCE_QUALITY_NOTES_KEY]
        assert "exec.cost_demolition_fastest_open" not in replacements[
            "exec.acquisition_conditions"
        ]
        assert "Cost Demolition Fastest Open" in replacements["exec.acquisition_conditions"]


class TestAsciiPunctuationSanitizer:
    def test_replaces_em_and_en_dashes(self) -> None:
        assert _sanitize_ascii_punctuation("10 weeks \u2014 admin CUP") == "10 weeks -- admin CUP"
        assert _sanitize_ascii_punctuation("$3\u20137/SF") == "$3-7/SF"

    def test_replaces_smart_quotes(self) -> None:
        assert _sanitize_ascii_punctuation("\u201chello\u201d") == '"hello"'
        assert _sanitize_ascii_punctuation("it\u2019s a test") == "it's a test"

    def test_replaces_ellipsis_minus_and_nbsp(self) -> None:
        assert _sanitize_ascii_punctuation("foo\u2026") == "foo..."
        assert _sanitize_ascii_punctuation("\u22125") == "-5"
        assert _sanitize_ascii_punctuation("a\u00a0b") == "a b"

    def test_passes_through_ascii_unchanged(self) -> None:
        assert _sanitize_ascii_punctuation("plain ascii -- text") == "plain ascii -- text"
        assert _sanitize_ascii_punctuation("") == ""

    def test_normalize_replacements_sanitizes_narrative_fields(self) -> None:
        repl = _normalize_replacements_for_rendering({
            "exec.c_permit_timeline": "10 weeks \u2014 admin CUP",
            "exec.acquisition_conditions": "- Landlord must provide TI \u2014 6 months\n- Range $3\u20137/SF",
            "exec.tradeoffs_and_deficiencies": "- It\u2019s old",
        })
        assert "\u2014" not in repl["exec.c_permit_timeline"]
        assert "--" in repl["exec.c_permit_timeline"]
        assert "\u2014" not in repl["exec.acquisition_conditions"]
        assert "\u2013" not in repl["exec.acquisition_conditions"]
        assert "$3-7/SF" in repl["exec.acquisition_conditions"]
        assert "\u2019" not in repl["exec.tradeoffs_and_deficiencies"]
        assert "It's old" in repl["exec.tradeoffs_and_deficiencies"]

    def test_normalize_replacements_does_not_mangle_link_urls(self) -> None:
        url = "https://example.com/path?a=1\u20132"  # contrived URL with en-dash
        repl = _normalize_replacements_for_rendering({
            "sources.sir_link": url,
        })
        # Link tokens are excluded from sanitization to avoid mangling URLs.
        assert repl["sources.sir_link"] == url


class TestSourceNotesBlockConsolidation:
    def test_strips_per_field_footnotes_when_block_present(self) -> None:
        repl = _normalize_replacements_for_rendering({
            "exec.acquisition_conditions": (
                "- TI allowance ~$45,000 [1]\n"
                "- ADA ramp required [2]\n"
                "\n"
                "[1] Building Inspection p.3\n"
                "[2] SIR p.7"
            ),
            "exec.tradeoffs_and_deficiencies": (
                "- Fire alarm > 15 years old [1]\n"
                "\n"
                "[1] Building Inspection p.10"
            ),
            CITATIONS_BLOCK_KEY: (
                "[1] Building Inspection p.3\n"
                "[2] SIR p.7\n"
                "[3] Building Inspection p.10"
            ),
        })

        assert "[1] Building Inspection p.3" not in repl["exec.acquisition_conditions"]
        assert "[2] SIR p.7" not in repl["exec.acquisition_conditions"]
        assert "[1] Building Inspection p.10" not in repl["exec.tradeoffs_and_deficiencies"]
        # Inline markers are removed from display text; the single source-note
        # block carries the source context.
        assert "[1]" not in repl["exec.acquisition_conditions"]
        assert "[2]" not in repl["exec.acquisition_conditions"]
        assert "TI allowance ~$45,000" in repl["exec.acquisition_conditions"]

    def test_normalizes_per_field_footnotes_when_block_absent(self) -> None:
        # Without a citations block, the existing per-field renumbering still runs.
        repl = _normalize_replacements_for_rendering({
            "exec.acquisition_conditions": (
                "- TI allowance ~$45,000 [1]\n"
                "\n"
                "[1] Building Inspection p.3"
            ),
        })
        assert "[1] Building Inspection p.3" in repl["exec.acquisition_conditions"]

    def test_citations_block_renders_once_after_referenced_reports(self) -> None:
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = {
            "meta.site_name": "Test",
            "meta.report_date": "04/14/2026",
            "exec.acquisition_conditions": (
                "- TI allowance [1]\n\n[1] Building Inspection p.3"
            ),
            "exec.tradeoffs_and_deficiencies": (
                "- Fire alarm old [1]\n\n[1] Building Inspection p.10"
            ),
            CITATIONS_BLOCK_KEY: (
                "[1] Building Inspection p.3\n[2] Building Inspection p.10"
            ),
        }

        build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Test")

        inserted_text = _inserted_text(docs_svc)

        # The Source Notes heading appears exactly once.
        assert inserted_text.count("Source Notes\n") == 1
        # And appears after Referenced Reports in the Greg Edits format.
        assert (
            inserted_text.index("Referenced Reports\n")
            < inserted_text.index("Source Notes\n")
        )
        assert "[1] Building Inspection p.3" not in inserted_text
        assert "Building Inspection p.3" in inserted_text

    def test_citations_block_omitted_when_not_provided(self) -> None:
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = {
            "meta.site_name": "Test",
            "meta.report_date": "04/14/2026",
        }

        build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Test")

        inserted_text = _inserted_text(docs_svc)
        assert "Source Notes\n" not in inserted_text


class TestReferencedReportsTableInsertOrder:
    """Phase 7 must populate cells in strict reverse so cached cell indices
    remain valid as inserts shift the document."""

    def test_phase7_inserts_are_in_descending_index_order(self) -> None:
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = {
            "meta.site_name": "Test",
            "meta.report_date": "04/14/2026",
            "sources.sir_link": "https://drive.google.com/file/d/sir1",
            "sources.inspection_link": "https://drive.google.com/file/d/insp1",
            "sources.block_plan_link": "https://drive.google.com/file/d/block1",
            "sources.rebl_link": "https://rebl3.vercel.app/site/test",
            "sources.e_occupancy_link": "https://drive.google.com/file/d/eocc1",
            "sources.school_approval_link": "https://drive.google.com/file/d/sa1",
            "sources.opening_plan_link": "https://drive.google.com/file/d/op1",
        }

        build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Test")

        # The Referenced Reports (source documents) table is the 4th table.
        # Phase 7 is the batchUpdate that inserts the source-table cell text.
        # Identify it by looking for the batch whose insertText payload contains
        # the literal header strings "Type", "Document", "Link".
        source_table_batch = None
        for call_args in docs_svc.documents.return_value.batchUpdate.call_args_list:
            requests = call_args.kwargs["body"]["requests"]
            inserted_strings = [
                r["insertText"]["text"] for r in requests if "insertText" in r
            ]
            if (
                "Type" in inserted_strings
                and "Document" in inserted_strings
                and "Link" in inserted_strings
                and "Site Investigation Report (SIR)" in inserted_strings
            ):
                source_table_batch = requests
                break

        assert source_table_batch is not None, "Could not find Phase 7 batchUpdate"

        # Every insertText location index must be strictly non-increasing.
        # If the loop ever inserts at a larger index after a smaller one,
        # cached indices for later cells get shifted and content corrupts.
        insert_indices = [
            r["insertText"]["location"]["index"]
            for r in source_table_batch
            if "insertText" in r
        ]
        for prev, cur in zip(insert_indices, insert_indices[1:], strict=False):
            assert cur <= prev, (
                f"Phase 7 insert order is not strictly reverse: "
                f"index {cur} follows {prev}"
            )


class TestCostBreakdownTableInsertOrder:
    """Cost table inserts must be globally descending across both tables."""

    def test_phase5_cost_table_inserts_are_in_descending_index_order(self) -> None:
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = TestBuildDdReportDoc()._full_replacements()

        build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Test")

        cost_table_batch = None
        for call_args in docs_svc.documents.return_value.batchUpdate.call_args_list:
            requests = call_args.kwargs["body"]["requests"]
            inserted_strings = [
                request["insertText"]["text"]
                for request in requests
                if "insertText" in request
            ]
            if (
                "Line Item" in inserted_strings
                and "Amount" in inserted_strings
                and "Demolition" in inserted_strings
                and "Grand Total" in inserted_strings
            ):
                cost_table_batch = requests
                break

        assert cost_table_batch is not None, "Could not find Phase 5 cost table batch"

        insert_indices = [
            request["insertText"]["location"]["index"]
            for request in cost_table_batch
            if "insertText" in request
        ]
        for prev, cur in zip(insert_indices, insert_indices[1:], strict=False):
            assert cur <= prev, (
                f"Phase 5 cost table insert order is not strictly reverse: "
                f"index {cur} follows {prev}"
            )
