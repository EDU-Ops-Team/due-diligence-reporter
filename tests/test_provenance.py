"""Tests for vendor-vs-AI provenance detection."""

from __future__ import annotations

from unittest.mock import patch

from due_diligence_reporter.provenance import (
    classify_provenance,
    is_gateable_doc_type,
    is_vendor_sourced,
    looks_ai_generated_by_filename,
)


class TestFilenameHeuristic:
    """Tier 1 — filename pattern matches conclude AI without I/O."""

    def test_tulsa_style_ai_sir(self):
        # The exact pattern that broke Tulsa.
        assert looks_ai_generated_by_filename(
            "6940-s-utica-ave-tulsa-ok_2026-04-29_SIR.docx"
        )

    def test_other_ai_artifacts(self):
        for name in [
            "alpha-school-santa-clara-2340_2026-04-15_cds-packet.docx",
            "foo-bar_2026-04-29_school-approval.docx",
            "site_2026-01-01_e-occupancy.pdf",
            "addr_2026-04-29_capacity-brainlift.docx",
            "addr_2026-04-29_dd-report.pdf",
        ]:
            assert looks_ai_generated_by_filename(name), name

    def test_vendor_sir_does_not_match(self):
        for name in [
            "Alpha School - Santa Barbara CA (27 E Cota St) - SIR UPDATE 6.18.25.pdf",
            "ACME Inspections - Building Report 2025.pdf",
            "Some Random SIR.pdf",
            "27 E Cota St SIR.pdf",  # has SIR but no date_artifact suffix
        ]:
            assert not looks_ai_generated_by_filename(name), name

    def test_empty_and_none(self):
        assert not looks_ai_generated_by_filename("")
        # None would crash; the function should guard.
        assert not looks_ai_generated_by_filename(None)  # type: ignore[arg-type]


class TestClassifyProvenance:
    def test_doc_type_short_circuit(self):
        # AI-only doc types skip all I/O.
        v = classify_provenance(
            {"name": "anything.docx", "id": "x"}, gc=None, doc_type="dd_report"
        )
        assert v.label == "ai_generated"
        assert v.tier == "trivial"

    def test_filename_tier_no_gc_needed(self):
        # Filename match wins without any gc/m1_folder lookups.
        v = classify_provenance(
            {"name": "addr_2026-04-29_SIR.docx", "id": "abc"}, gc=None
        )
        assert v.label == "ai_generated"
        assert v.tier == "filename"
        assert v.confidence >= 0.9

    def test_unknown_filename_no_gc_defaults_vendor(self):
        # Without gc we can't run Tier 2 \u2014 default-to-vendor policy applies.
        v = classify_provenance(
            {"name": "Vendor SIR Update.pdf", "id": "abc"}, gc=None
        )
        # Without gc, no content fetch; verdict path returns no-text \u2192 default vendor
        assert v.label == "vendor"

    def test_is_vendor_sourced_helper(self):
        assert is_vendor_sourced(
            {"name": "Vendor SIR.pdf", "id": "x"}, gc=None
        )
        assert not is_vendor_sourced(
            {"name": "site_2026-04-29_SIR.docx", "id": "x"}, gc=None
        )

    def test_bad_input(self):
        v = classify_provenance(None, gc=None)  # type: ignore[arg-type]
        assert v.label == "unknown"


class TestGateable:
    def test_sir_bi_isp_gateable(self):
        assert is_gateable_doc_type("sir")
        assert is_gateable_doc_type("building_inspection")
        assert is_gateable_doc_type("isp")

    def test_others_not_gateable(self):
        for dt in ["dd_report", "block_plan", "matterport", "unknown", None, ""]:
            assert not is_gateable_doc_type(dt)


class TestContentTier:
    """Tier 2 \u2014 content LLM. Mocks OpenAI."""

    def test_vendor_classification_via_content(self):
        # No filename signal, but content-LLM says vendor.
        class FakeGC:
            def list_files_in_folder(self, _id):
                return []

            def download_file_bytes(self, _id):
                return b"Pages of vendor content with letterhead and signed sections"

            def upload_file_to_folder(self, **_kw):
                return {"id": "cache"}

        with patch(
            "due_diligence_reporter.provenance._classify_by_content"
        ) as mock_content:
            from due_diligence_reporter.provenance import ProvenanceVerdict

            mock_content.return_value = ProvenanceVerdict(
                "vendor", 0.9, "content", "letterhead detected"
            )
            v = classify_provenance(
                {"name": "Vendor Report.docx", "id": "abc", "modifiedTime": "t1"},
                gc=FakeGC(),
                m1_folder_id="m1",
            )
            assert v.label == "vendor"
            assert v.tier == "content"
            mock_content.assert_called_once()

    def test_ai_classification_via_content(self):
        class FakeGC:
            def list_files_in_folder(self, _id):
                return []

            def download_file_bytes(self, _id):
                return b"Executive Summary token-driven content"

            def upload_file_to_folder(self, **_kw):
                return {"id": "cache"}

        with patch(
            "due_diligence_reporter.provenance._classify_by_content"
        ) as mock_content:
            from due_diligence_reporter.provenance import ProvenanceVerdict

            mock_content.return_value = ProvenanceVerdict(
                "ai_generated", 0.9, "content", "token-driven"
            )
            v = classify_provenance(
                {"name": "site report.docx", "id": "abc", "modifiedTime": "t1"},
                gc=FakeGC(),
                m1_folder_id="m1",
            )
            assert v.label == "ai_generated"
            # is_vendor should be False
            assert v.is_vendor is False

    def test_cache_hit(self):
        cache_blob = (
            '{"abc": {"modifiedTime": "t1", "label": "vendor", '
            '"confidence": 0.9, "tier": "content", "reason": "cached"}}'
        ).encode("utf-8")

        class FakeGC:
            def list_files_in_folder(self, _id):
                return [{"id": "cache_file", "name": "provenance.json"}]

            def download_file_bytes(self, _id):
                return cache_blob

        v = classify_provenance(
            {"name": "Vendor.pdf", "id": "abc", "modifiedTime": "t1"},
            gc=FakeGC(),
            m1_folder_id="m1",
        )
        assert v.label == "vendor"
        assert v.tier == "cached"
