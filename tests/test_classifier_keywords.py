"""Tests for classifier.py — regex tier, three-tier cascade, and wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from due_diligence_reporter.classifier import (
    classify_by_keywords,
    classify_document,
    classify_document_type,
)

# ---------------------------------------------------------------------------
# Tier 1 — classify_by_keywords
# ---------------------------------------------------------------------------


class TestClassifyByKeywordsSIR:
    """SIR detection — all variations and word-boundary checks."""

    def test_sir_plain(self):
        assert classify_by_keywords("Alpha Keller SIR.pdf") == ("sir", 0.95)

    def test_sir_prefix(self):
        assert classify_by_keywords("SIR - Tampa.pdf") == ("sir", 0.95)

    def test_sir_hyphenated(self):
        assert classify_by_keywords("keller-sir.pdf") == ("sir", 0.95)

    def test_sir_underscore_suffix(self):
        assert classify_by_keywords("5400-beethoven_2026-05-21_SIR.docx") == (
            "sir",
            0.95,
        )

    def test_sir_underscore_no_boundary_match(self):
        """Underscore is a word char, so \\bsir\\b does not match 'sir_report'."""
        assert classify_by_keywords("sir_report_v2.pdf") == ("unknown", 0.0)

    def test_siren_not_matched(self):
        """Word boundary: 'sir' inside 'Siren' must NOT match."""
        assert classify_by_keywords("Siren Sound Report.pdf") == ("unknown", 0.0)


class TestClassifyByKeywordsISP:
    """ISP detection — all variations and word-boundary checks."""

    def test_isp_plain(self):
        assert classify_by_keywords("Alpha Keller ISP.pdf") == ("isp", 0.95)

    def test_isp_hyphenated(self):
        assert classify_by_keywords("keller-isp.pdf") == ("isp", 0.95)

    def test_isp_docx(self):
        doc_type, _ = classify_by_keywords("spectrum-isp.docx")
        assert doc_type == "isp"

    def test_crispy_not_matched(self):
        """'isp' inside 'crispy' must NOT match."""
        assert classify_by_keywords("crispy_noodles.pdf") == ("unknown", 0.0)


class TestClassifyByKeywordsBuildingInspection:
    def test_building_inspection(self):
        assert classify_by_keywords("Building Inspection - Tampa.pdf") == (
            "building_inspection",
            0.95,
        )

    def test_property_inspection(self):
        doc_type, _ = classify_by_keywords("property inspection report.pdf")
        assert doc_type == "building_inspection"


class TestClassifyByKeywordsEOccupancy:
    def test_hyphenated(self):
        assert classify_by_keywords("E-Occupancy Assessment.pdf") == (
            "e_occupancy_report",
            0.95,
        )

    def test_space(self):
        assert classify_by_keywords("E Occupancy Report.pdf") == (
            "e_occupancy_report",
            0.95,
        )


class TestClassifyByKeywordsSchoolApproval:
    def test_school_approval(self):
        assert classify_by_keywords("School Approval Assessment.pdf") == (
            "school_approval_report",
            0.95,
        )

    def test_school_approval_hyphenated(self):
        assert classify_by_keywords("5400-beethoven_school-approval.docx") == (
            "school_approval_report",
            0.95,
        )


class TestClassifyByKeywordsBlockPlan:
    def test_block_plan(self):
        assert classify_by_keywords("Alpha Keller Block Plan.pdf") == (
            "block_plan",
            0.95,
        )

    def test_blockplan_concatenated(self):
        assert classify_by_keywords("AlphaKeller_BlockPlan.pdf") == (
            "block_plan",
            0.95,
        )

    def test_block_plan_underscore(self):
        assert classify_by_keywords("alpha_keller_block_plan.pdf") == (
            "block_plan",
            0.95,
        )

    def test_preliminary_floor_plan(self):
        assert classify_by_keywords("Alpha Keller Preliminary Floor Plan.pdf") == (
            "block_plan",
            0.95,
        )

    def test_preliminary_floor_plans_plural(self):
        """\"Preliminary Floor Plans\" (plural) is the same artifact."""
        assert classify_by_keywords("Preliminary Floor Plans - Alpha Tampa.pdf") == (
            "block_plan",
            0.95,
        )

    def test_pfp_acronym_uppercase(self):
        assert classify_by_keywords("Alpha Keller PFP.pdf") == (
            "block_plan",
            0.95,
        )

    def test_pfp_acronym_lowercase_hyphenated(self):
        assert classify_by_keywords("alpha-keller-pfp.pdf") == (
            "block_plan",
            0.95,
        )

    def test_pfp_acronym_with_extension_only(self):
        assert classify_by_keywords("PFP.pdf") == (
            "block_plan",
            0.95,
        )

    def test_pfp_substring_does_not_false_positive(self):
        """PFP word boundary: must not match inside a longer word."""
        assert classify_by_keywords("epfpro_brochure.pdf") == ("unknown", 0.0)


class TestClassifyByKeywordsCapacityBrainlift:
    def test_capacity_brainlift(self):
        assert classify_by_keywords("Capacity Brainlift - Alpha Keller") == (
            "capacity_brainlift_report",
            0.95,
        )

    def test_alpha_capacity_analysis(self):
        assert classify_by_keywords("Alpha Capacity Analysis - Alpha Keller.json") == (
            "alpha_capacity_analysis",
            0.95,
        )


class TestClassifyByKeywordsM2SourcePacketDocs:
    def test_outdoor_play_space_report(self):
        assert classify_by_keywords("Outdoor Play Space Report - Alpha Keller.md") == (
            "outdoor_play_space_report",
            0.95,
        )

    def test_kh_traffic_analysis(self):
        assert classify_by_keywords("KH Traffic Analysis - Alpha Keller.pdf") == (
            "traffic_analysis",
            0.95,
        )


class TestClassifyByKeywordsRayconScenario:
    def test_raycon_scenario(self):
        assert classify_by_keywords("RayCon Scenario - Alpha Keller") == (
            "raycon_scenario_report",
            0.95,
        )


class TestClassifyByKeywordsAlphaPhasingPlan:
    def test_alpha_phasing_plan(self):
        assert classify_by_keywords("Alpha Phasing Plan - Alpha Keller.xlsx") == (
            "alpha_phasing_plan_report",
            0.95,
        )

    def test_phase_i_phase_ii_quality_plan(self):
        assert classify_by_keywords("Phase I Phase II Quality Bar Plan - Alpha Keller.xlsx") == (
            "alpha_phasing_plan_report",
            0.95,
        )


class TestClassifyByKeywordsDDReport:
    def test_dd_report(self):
        assert classify_by_keywords("DD Report - Alpha Tampa.pdf") == (
            "dd_report",
            0.95,
        )

    def test_opening_plan_report(self):
        assert classify_by_keywords("Opening Plan - Alpha Tampa") == (
            "opening_plan_report",
            0.95,
        )

    def test_report_trace_json_is_ignored(self):
        assert classify_by_keywords("Alpha Tampa DD Report Trace - 2026-04-20.json") == (
            "unknown",
            0.0,
        )


class TestClassifyByKeywordsMatterport:
    def test_matterport(self):
        assert classify_by_keywords("Matterport Scan - Tampa.pdf") == (
            "matterport",
            0.95,
        )


class TestClassifyByKeywordsUnknown:
    def test_random(self):
        assert classify_by_keywords("random_document.pdf") == ("unknown", 0.0)

    def test_empty(self):
        assert classify_by_keywords("") == ("unknown", 0.0)

    def test_lease(self):
        assert classify_by_keywords("lease_agreement.pdf") == ("unknown", 0.0)


class TestClassifyByKeywordsCaseInsensitivity:
    def test_upper_sir(self):
        doc_type, _ = classify_by_keywords("ALPHA KELLER SIR.PDF")
        assert doc_type == "sir"

    def test_upper_inspection(self):
        doc_type, _ = classify_by_keywords("building INSPECTION.pdf")
        assert doc_type == "building_inspection"


class TestClassifyByKeywordsPriority:
    def test_e_occupancy_before_sir(self):
        """E-Occupancy is checked before SIR in the chain, so it wins."""
        doc_type, _ = classify_by_keywords("E-Occupancy SIR.pdf")
        assert doc_type == "e_occupancy_report"


# ---------------------------------------------------------------------------
# classify_document — three-tier cascade
# ---------------------------------------------------------------------------


class TestClassifyDocumentCascade:
    @patch("due_diligence_reporter.classifier.classify_by_filename_llm")
    def test_tier1_match_short_circuits(self, mock_llm):
        """Tier 1 regex match should never invoke Tier 2 LLM."""
        doc_type, conf = classify_document("Alpha Keller SIR.pdf")
        assert doc_type == "sir"
        assert conf == 0.95
        mock_llm.assert_not_called()

    @patch("due_diligence_reporter.classifier.classify_by_filename_llm")
    def test_tier1_miss_falls_to_tier2(self, mock_llm):
        """When regex returns unknown, Tier 2 should be called."""
        mock_llm.return_value = ("sir", 0.85)
        doc_type, conf = classify_document("ambiguous_file.pdf")
        assert doc_type == "sir"
        assert conf == 0.85
        mock_llm.assert_called_once()

    @patch("due_diligence_reporter.classifier.classify_by_content_llm")
    @patch("due_diligence_reporter.classifier.classify_by_filename_llm")
    def test_tier2_low_conf_falls_to_tier3(self, mock_t2, mock_t3):
        """When Tier 2 returns confidence < 0.7, Tier 3 runs for PDFs."""
        mock_t2.return_value = ("sir", 0.5)
        mock_t3.return_value = ("building_inspection", 0.75)

        gc = MagicMock()
        gc.download_file_bytes.return_value = b"%PDF-fake"

        with patch(
            "due_diligence_reporter.utils.extract_text_from_pdf_bytes",
            return_value="Inspection report text...",
        ):
            doc_type, conf = classify_document(
                "ambiguous.pdf", file_id="fid_1", gc=gc
            )

        assert doc_type == "building_inspection"
        assert conf == 0.75
        mock_t3.assert_called_once()

    @patch("due_diligence_reporter.classifier.classify_by_content_llm")
    @patch("due_diligence_reporter.classifier.classify_by_filename_llm")
    def test_tier3_skipped_for_non_pdf(self, mock_t2, mock_t3):
        """Non-PDF files should never invoke Tier 3, even with gc + file_id."""
        mock_t2.return_value = ("unknown", 0.3)

        gc = MagicMock()
        doc_type, conf = classify_document(
            "ambiguous.docx", file_id="fid_2", gc=gc
        )

        assert doc_type == "unknown"
        mock_t3.assert_not_called()

    @patch("due_diligence_reporter.classifier.classify_by_filename_llm")
    def test_tier3_pdf_extraction_failure(self, mock_t2):
        """When gc.download_file_bytes raises, return unknown without crash."""
        mock_t2.return_value = ("unknown", 0.3)

        gc = MagicMock()
        gc.download_file_bytes.side_effect = RuntimeError("download boom")

        doc_type, conf = classify_document(
            "weird.pdf", file_id="fid_3", gc=gc
        )

        assert doc_type == "unknown"
        assert conf == 0.0

    @patch("due_diligence_reporter.classifier.classify_by_content_llm")
    @patch("due_diligence_reporter.classifier.classify_by_filename_llm")
    def test_all_tiers_fail(self, mock_t2, mock_t3):
        """When all tiers return unknown / low confidence, final result is unknown."""
        mock_t2.return_value = ("unknown", 0.2)
        mock_t3.return_value = ("unknown", 0.3)

        gc = MagicMock()
        gc.download_file_bytes.return_value = b"%PDF-fake"

        with patch(
            "due_diligence_reporter.utils.extract_text_from_pdf_bytes",
            return_value="some text",
        ):
            doc_type, conf = classify_document(
                "mystery.pdf", file_id="fid_4", gc=gc
            )

        assert doc_type == "unknown"
        assert conf == 0.0


# ---------------------------------------------------------------------------
# classify_document_type — thin wrapper
# ---------------------------------------------------------------------------


class TestClassifyDocumentType:
    def test_returns_doc_type_string(self):
        assert classify_document_type("Alpha SIR.pdf") == "sir"

    def test_unknown_returns_unknown(self):
        assert classify_document_type("random.pdf") == "unknown"
