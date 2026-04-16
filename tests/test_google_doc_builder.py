"""Tests for the programmatic Google Doc builder."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, call

import pytest

from due_diligence_reporter.google_doc_builder import (
    _COST_BREAKDOWN_ROWS,
    _DocBuilder,
    _HEADER_ROWS,
    _LINK_GAP_LABELS,
    _SOURCE_DOC_ROWS,
    _cell_index,
    _doc_end_index,
    _find_table,
    _resolve_link_value,
    _resolve_value,
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


# ---------------------------------------------------------------------------
# Template token coverage
# ---------------------------------------------------------------------------


class TestTokenCoverage:
    """Verify the builder handles all 40 template tokens from the V3 spec."""

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
        ])

        # Scenario summary tokens
        for scenario in ("fastest_open", "max_capacity"):
            for metric in ("capacity", "open_date", "capex"):
                cls._BUILDER_TOKENS.add(f"exec.{scenario}_{metric}")

        # Cost breakdown tokens
        for row_key, _ in _COST_BREAKDOWN_ROWS:
            for scenario in ("fastest_open", "max_capacity"):
                cls._BUILDER_TOKENS.add(f"exec.cost_{row_key}_{scenario}")

        # Narrative tokens
        cls._BUILDER_TOKENS.update([
            "exec.acquisition_conditions", "exec.risk_notes",
        ])

        # Source link tokens
        for _, token in _SOURCE_DOC_ROWS:
            cls._BUILDER_TOKENS.add(token)

    def test_all_v3_template_tokens_covered(self) -> None:
        """Every V3 token (40 tokens from the corrected template) is handled."""
        # The V3 corrected template only has fastest_open and max_capacity
        # scenarios — not recommended_path or max_value.
        v3_tokens = set()

        # meta tokens
        v3_tokens.update([
            "meta.site_name", "meta.marketing_name", "meta.city_state_zip",
            "meta.school_type", "meta.report_date", "meta.prepared_by",
            "meta.drive_folder_url",
        ])

        # exec can-we-open tokens
        v3_tokens.update([
            "exec.c_answer", "exec.c_edreg", "exec.c_occupancy", "exec.c_zoning",
            "exec.c_permit_timeline", "exec.c_construction_timeline",
        ])

        # exec scenario summary (2 scenarios × 3 metrics = 6)
        for scenario in ("fastest_open", "max_capacity"):
            for metric in ("capacity", "capex", "open_date"):
                v3_tokens.add(f"exec.{scenario}_{metric}")

        # exec cost breakdown (12 rows × 2 scenarios = 24)
        cost_keys = [
            "cost_demolition", "cost_framing_doors", "cost_mep_fire_life_safety",
            "cost_plumbing_bathrooms", "cost_finish_work", "cost_furniture",
            "cost_tech_security_signage", "cost_other_hard_costs", "cost_soft_costs",
            "cost_gc_fee", "cost_contingency", "cost_grand_total",
        ]
        for key in cost_keys:
            for scenario in ("fastest_open", "max_capacity"):
                v3_tokens.add(f"exec.{key}_{scenario}")

        # exec narrative
        v3_tokens.update(["exec.acquisition_conditions", "exec.risk_notes"])

        # sources
        v3_tokens.update([
            "sources.sir_link", "sources.inspection_link", "sources.isp_link",
            "sources.e_occupancy_link", "sources.school_approval_link",
            "sources.opening_plan_link",
            "sources.trace_link",
        ])

        # 7 meta + 6 can-we-open + 6 scenario summary + 24 cost + 2 narrative + 7 sources = 52
        assert len(v3_tokens) == 52, f"Expected 52 V3 tokens, got {len(v3_tokens)}"

        # All V3 tokens should be covered by the builder
        missing = v3_tokens - self._BUILDER_TOKENS
        assert not missing, f"Builder does not handle V3 tokens: {missing}"

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
    num_tables: int = 4,
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
                rows, cols = 4, 3
            elif t == 2:
                rows, cols = len(_COST_BREAKDOWN_ROWS) + 1, 3
            else:
                rows, cols = len(_SOURCE_DOC_ROWS) + 1, 2

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


class TestBuildDdReportDoc:
    """Integration-level tests for build_dd_report_doc with mocked API."""

    def _full_replacements(self) -> dict[str, str]:
        """Build a complete set of replacements for all 40 V3 tokens."""
        repl: dict[str, str] = {
            "meta.site_name": "Alpha Boca Raton 2200",
            "meta.marketing_name": "Boca Academy",
            "meta.city_state_zip": "Boca Raton, FL 33431",
            "meta.school_type": "micro",
            "meta.report_date": "04/14/2026",
            "meta.prepared_by": "Jane Smith",
            "meta.drive_folder_url": "https://drive.google.com/drive/folders/abc123",
            "exec.c_answer": "Yes",
            "exec.c_edreg": "FL: Registration required. Timeline: 30 days.",
            "exec.c_occupancy": "78/100 YELLOW - Office general, 6-9 months",
            "exec.c_zoning": "Permitted by right",
            "exec.c_permit_timeline": "10 weeks — admin CUP, no public hearing (SIR p.3)",
            "exec.c_construction_timeline": "8 weeks — minimal TI, 4-classroom layout",
            "exec.fastest_open_capacity": "69 students",
            "exec.fastest_open_capex": "$487,000",
            "exec.fastest_open_open_date": "07/15/26",
            "exec.max_capacity_capacity": "125 students",
            "exec.max_capacity_capex": "$812,000",
            "exec.max_capacity_open_date": "11/26",
            "exec.acquisition_conditions": "- Landlord must provide 6-month TI window\n- ADA ramp required",
            "exec.risk_notes": "- No sprinkler system\n- Fire alarm > 15 years old",
            "sources.sir_link": "https://drive.google.com/file/d/sir123",
            "sources.inspection_link": "https://drive.google.com/file/d/insp123",
            "sources.isp_link": "https://drive.google.com/file/d/isp123",
            "sources.e_occupancy_link": "https://drive.google.com/file/d/eocc123",
            "sources.school_approval_link": "https://drive.google.com/file/d/sa123",
            "sources.opening_plan_link": "https://drive.google.com/file/d/op123",
            "sources.trace_link": "",  # Not yet uploaded at build time
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

        # We have 5 source URLs + 1 drive folder URL = up to 6 hyperlinks
        # (trace_link is empty, so not hyperlinked)
        assert result["applied"] >= 5
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

    def test_trace_link_not_found_when_empty(self) -> None:
        """sources.trace_link is not in not_found_tokens when empty (expected)."""
        docs_svc = _make_mock_docs_service()
        drive_svc = MagicMock()
        repl = self._full_replacements()
        repl["sources.trace_link"] = ""

        result = build_dd_report_doc(docs_svc, drive_svc, "doc123", repl, "Alpha Boca")

        # trace_link should NOT be in not_found — it's expected to be empty
        assert "sources.trace_link" not in result.get("not_found_tokens", [])


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
