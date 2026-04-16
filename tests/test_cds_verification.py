"""Tests for the CDS Verification Report generator (SCRIPT-04).

Covers:
- B/C row detection with various confidence tag formats
- Claim ID sequential generation
- Authority inference from section names
- Table rewriting with verification columns
- Cover sheet generation
- Full end-to-end report assembly
- Edge cases: no B/C items, mixed tables, nested headers
"""

from __future__ import annotations

import pytest

from due_diligence_reporter.cds_verification import (
    VerificationItem,
    VerificationReport,
    _add_verification_columns_to_table,
    _build_cover_sheet,
    _build_task_summary_table,
    _extract_confidence_from_cell,
    _find_bc_items,
    _infer_authority,
    generate_cds_verification_report,
)


# ---------------------------------------------------------------------------
# Fixtures: sample SIR content
# ---------------------------------------------------------------------------

SAMPLE_SIR = """\
# Site Investigation Report

## Zoning & Land Use

| Field | Finding | Confidence |
|---|---|---|
| Current Zoning | C-2 Commercial — [A] | Verified |
| Permitted Use | Education allowed with SUP — [B] | Inferred |
| Setback Requirements | 25 ft front, 10 ft side — [C] | Estimated |
| Lot Size | 1.2 acres — [A] | Verified |

## Fire & Life Safety

| Field | Finding | Confidence |
|---|---|---|
| Sprinkler Status | Full wet system — [A] | Verified |
| Max Occupancy | 350 persons — [B] | Inferred |
| ADA Compliance | Ramps present — [A] | Verified |

## Infrastructure

| Field | Finding | Confidence |
|---|---|---|
| Water Service | Municipal — [A] | Verified |
| Sewer Capacity | Adequate for E-occupancy — [C] | Estimated |
| Internet Provider | Spectrum 1Gbps — [B] | Inferred |

## General Notes

This section has no tables, just narrative text.
The building was constructed in 1998.
"""

SAMPLE_SIR_NO_BC = """\
# Site Investigation Report

## Zoning & Land Use

| Field | Finding |
|---|---|
| Current Zoning | C-2 Commercial — [A] |
| Lot Size | 1.2 acres — [A] |
"""

SAMPLE_SIR_MIXED_FORMATS = """\
# Site Investigation Report

## Building Code Framework

| Item | Value |
|---|---|
| Building Type | Type II-B [B] |
| Stories | 2 [A] |
| Year Built | 1985 [C] |
| Square Footage | 15,000 SF [A] |
"""


# ---------------------------------------------------------------------------
# Unit: _extract_confidence_from_cell
# ---------------------------------------------------------------------------


class TestExtractConfidenceFromCell:
    """Test confidence tag extraction from individual cells."""

    def test_standard_tag(self) -> None:
        assert _extract_confidence_from_cell("C-2 Commercial — [A]") == "A"

    def test_b_tag(self) -> None:
        assert _extract_confidence_from_cell("Education allowed with SUP — [B]") == "B"

    def test_c_tag(self) -> None:
        assert _extract_confidence_from_cell("25 ft front — [C]") == "C"

    def test_d_tag(self) -> None:
        assert _extract_confidence_from_cell("Field task — [D]") == "D"

    def test_no_separator(self) -> None:
        """Tags without em-dash separator should still be found."""
        assert _extract_confidence_from_cell("Type II-B [B]") == "B"

    def test_no_tag(self) -> None:
        assert _extract_confidence_from_cell("Some plain text") is None

    def test_empty_string(self) -> None:
        assert _extract_confidence_from_cell("") is None

    def test_brackets_but_not_confidence(self) -> None:
        assert _extract_confidence_from_cell("[E] rating") is None

    def test_en_dash_separator(self) -> None:
        assert _extract_confidence_from_cell("Value \u2013 [B]") == "B"

    def test_hyphen_separator(self) -> None:
        assert _extract_confidence_from_cell("Value - [C]") == "C"


# ---------------------------------------------------------------------------
# Unit: _infer_authority
# ---------------------------------------------------------------------------


class TestInferAuthority:
    """Test authority inference from section names."""

    def test_zoning_section(self) -> None:
        assert _infer_authority("Zoning & Land Use") == "Planning & Zoning"

    def test_fire_section(self) -> None:
        assert _infer_authority("Fire & Life Safety") == "Fire Department / Fire Marshal"

    def test_infrastructure_section(self) -> None:
        assert _infer_authority("Infrastructure") == "Public Works / Utilities"

    def test_building_code(self) -> None:
        assert _infer_authority("Building Code Framework") == "Building Department"

    def test_unknown_section(self) -> None:
        assert _infer_authority("Miscellaneous Notes") == "General"

    def test_case_insensitive(self) -> None:
        assert _infer_authority("ZONING ANALYSIS") == "Planning & Zoning"


# ---------------------------------------------------------------------------
# Unit: _find_bc_items
# ---------------------------------------------------------------------------


class TestFindBCItems:
    """Test scanning SIR text for B/C confidence rows."""

    def test_finds_correct_count(self) -> None:
        items = _find_bc_items(SAMPLE_SIR)
        assert len(items) == 5  # 2 from Zoning, 1 from Fire, 2 from Infra

    def test_sequential_claim_ids(self) -> None:
        items = _find_bc_items(SAMPLE_SIR)
        ids = [item.claim_id for item in items]
        assert ids == ["R-001", "R-002", "R-003", "R-004", "R-005"]

    def test_confidence_values(self) -> None:
        items = _find_bc_items(SAMPLE_SIR)
        confidences = [item.confidence for item in items]
        assert confidences == ["B", "C", "B", "C", "B"]

    def test_section_tracking(self) -> None:
        items = _find_bc_items(SAMPLE_SIR)
        sections = [item.section for item in items]
        assert sections == [
            "Zoning & Land Use",
            "Zoning & Land Use",
            "Fire & Life Safety",
            "Infrastructure",
            "Infrastructure",
        ]

    def test_item_names(self) -> None:
        items = _find_bc_items(SAMPLE_SIR)
        names = [item.item for item in items]
        assert names == [
            "Permitted Use",
            "Setback Requirements",
            "Max Occupancy",
            "Sewer Capacity",
            "Internet Provider",
        ]

    def test_authority_hints(self) -> None:
        items = _find_bc_items(SAMPLE_SIR)
        authorities = [item.authority_hint for item in items]
        assert authorities == [
            "Planning & Zoning",
            "Planning & Zoning",
            "Fire Department / Fire Marshal",
            "Public Works / Utilities",
            "Public Works / Utilities",
        ]

    def test_no_bc_items(self) -> None:
        items = _find_bc_items(SAMPLE_SIR_NO_BC)
        assert items == []

    def test_mixed_formats(self) -> None:
        """Tags without em-dash separators should still be detected."""
        items = _find_bc_items(SAMPLE_SIR_MIXED_FORMATS)
        assert len(items) == 2
        assert items[0].item == "Building Type"
        assert items[0].confidence == "B"
        assert items[1].item == "Year Built"
        assert items[1].confidence == "C"

    def test_empty_input(self) -> None:
        assert _find_bc_items("") == []

    def test_no_tables(self) -> None:
        assert _find_bc_items("# Header\n\nJust text, no tables.\n") == []


# ---------------------------------------------------------------------------
# Unit: _build_task_summary_table
# ---------------------------------------------------------------------------


class TestBuildTaskSummaryTable:
    """Test Verification Task Summary table generation."""

    def test_empty_list(self) -> None:
        result = _build_task_summary_table([])
        assert "No B/C confidence items found" in result

    def test_header_present(self) -> None:
        items = _find_bc_items(SAMPLE_SIR)
        result = _build_task_summary_table(items)
        assert "## Verification Task Summary" in result
        assert "5 items" in result

    def test_table_structure(self) -> None:
        items = _find_bc_items(SAMPLE_SIR)
        result = _build_task_summary_table(items)
        assert "| # | Claim ID | Section | Item | AI Finding | Confidence | Authority |" in result
        assert "R-001" in result
        assert "R-005" in result

    def test_long_finding_truncated(self) -> None:
        items = [
            VerificationItem(
                claim_id="R-001",
                section="Test",
                item="Long Item",
                ai_finding="A" * 100 + " — [B]",
                confidence="B",
                authority_hint="General",
            )
        ]
        result = _build_task_summary_table(items)
        assert "..." in result


# ---------------------------------------------------------------------------
# Unit: _build_cover_sheet
# ---------------------------------------------------------------------------


class TestBuildCoverSheet:
    """Test cover sheet generation."""

    def test_site_name_included(self) -> None:
        result = _build_cover_sheet("123 Main St, Tampa FL", 5)
        assert "123 Main St, Tampa FL" in result

    def test_item_count_included(self) -> None:
        result = _build_cover_sheet("Test Site", 12)
        assert "12" in result

    def test_instructions_present(self) -> None:
        result = _build_cover_sheet("Test", 1)
        assert "CDS Verified Finding" in result
        assert "CDS Source" in result
        assert "CDS Confidence" in result
        assert "[B]" in result
        assert "[C]" in result


# ---------------------------------------------------------------------------
# Unit: _add_verification_columns_to_table
# ---------------------------------------------------------------------------


class TestAddVerificationColumns:
    """Test table rewriting with verification columns."""

    def test_no_items_returns_unchanged(self) -> None:
        result = _add_verification_columns_to_table(SAMPLE_SIR, [])
        assert result == SAMPLE_SIR

    def test_bc_tables_get_extra_columns(self) -> None:
        items = _find_bc_items(SAMPLE_SIR)
        result = _add_verification_columns_to_table(SAMPLE_SIR, items)
        # Separator rows in tables with B/C items should have extra columns
        assert "--- | --- | --- |" in result

    def test_claim_id_comments_embedded(self) -> None:
        items = _find_bc_items(SAMPLE_SIR)
        result = _add_verification_columns_to_table(SAMPLE_SIR, items)
        assert "<!-- claim-id: R-001 -->" in result
        assert "<!-- claim-id: R-002 -->" in result

    def test_header_row_gets_column_names(self) -> None:
        items = _find_bc_items(SAMPLE_SIR)
        result = _add_verification_columns_to_table(SAMPLE_SIR, items)
        assert "CDS Verified Finding" in result
        assert "CDS Source" in result
        assert "CDS Confidence" in result

    def test_non_bc_section_unchanged(self) -> None:
        """The General Notes section has no tables and should be untouched."""
        items = _find_bc_items(SAMPLE_SIR)
        result = _add_verification_columns_to_table(SAMPLE_SIR, items)
        assert "This section has no tables, just narrative text." in result
        assert "The building was constructed in 1998." in result


# ---------------------------------------------------------------------------
# Integration: generate_cds_verification_report
# ---------------------------------------------------------------------------


class TestGenerateCDSVerificationReport:
    """End-to-end test of the full report generation."""

    def test_returns_verification_report(self) -> None:
        result = generate_cds_verification_report(SAMPLE_SIR, site_name="Test Site")
        assert isinstance(result, VerificationReport)

    def test_bc_count(self) -> None:
        result = generate_cds_verification_report(SAMPLE_SIR, site_name="Test Site")
        assert result.bc_item_count == 5

    def test_sections_with_items(self) -> None:
        result = generate_cds_verification_report(SAMPLE_SIR, site_name="Test Site")
        assert "Fire & Life Safety" in result.sections_with_items
        assert "Infrastructure" in result.sections_with_items
        assert "Zoning & Land Use" in result.sections_with_items

    def test_full_report_structure(self) -> None:
        result = generate_cds_verification_report(SAMPLE_SIR, site_name="123 Main St")
        md = result.markdown

        # Cover sheet
        assert "# CDS Verification Report" in md
        assert "123 Main St" in md

        # Task summary
        assert "## Verification Task Summary" in md
        assert "R-001" in md

        # Separator between summary and SIR body
        assert "---" in md

        # The original SIR content is present
        assert "## Zoning & Land Use" in md
        assert "## Fire & Life Safety" in md
        assert "## Infrastructure" in md

    def test_no_bc_items_report(self) -> None:
        result = generate_cds_verification_report(SAMPLE_SIR_NO_BC, site_name="Clean Site")
        assert result.bc_item_count == 0
        assert result.sections_with_items == []
        assert "No B/C confidence items found" in result.markdown

    def test_default_site_name(self) -> None:
        result = generate_cds_verification_report(SAMPLE_SIR)
        assert "Unknown Site" in result.markdown

    def test_report_contains_original_sir_text(self) -> None:
        """The report should contain the full SIR, not just a summary."""
        result = generate_cds_verification_report(SAMPLE_SIR, site_name="Full Report Test")
        md = result.markdown
        # Spot-check A-confidence rows are still present
        assert "C-2 Commercial" in md
        assert "Municipal" in md
        assert "Full wet system" in md

    def test_claim_ids_in_report(self) -> None:
        result = generate_cds_verification_report(SAMPLE_SIR, site_name="Claims Test")
        assert "<!-- claim-id: R-001 -->" in result.markdown
        assert "<!-- claim-id: R-005 -->" in result.markdown
