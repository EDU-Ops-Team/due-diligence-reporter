"""Unit tests for site_record — classification, slug, and projection."""

from __future__ import annotations

import pytest

from due_diligence_reporter.site_record import (
    CLASSIFICATIONS,
    ScenarioRecord,
    SiteRecord,
    classify_site,
    site_slug,
)


# ---------------------------------------------------------------------------
# Slug
# ---------------------------------------------------------------------------


class TestSiteSlug:
    def test_basic(self):
        assert site_slug("Palm Beach Gardens") == "palm-beach-gardens"

    def test_with_suffix(self):
        assert site_slug("Palm Beach Gardens", suffix="main") == "palm-beach-gardens-main"

    def test_strips_punctuation(self):
        assert site_slug("St. Paul's — Building 2") == "st-paul-s-building-2"

    def test_empty_fallback(self):
        assert site_slug("") == "unknown-site"
        assert site_slug("   ") == "unknown-site"

    def test_case_insensitive(self):
        assert site_slug("MIAMI BEACH") == site_slug("miami beach") == "miami-beach"

    def test_suffix_normalized(self):
        assert site_slug("Austin", suffix="Site A") == "austin-site-a"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class TestClassifySite:
    def test_clear_yes(self):
        label, conf, _ = classify_site({
            "exec.c_answer": "Yes",
            "exec.acquisition_conditions": "Standard closing.",
            "exec.risk_notes": "No material risks.",
        })
        assert label == "yes"
        assert conf >= 0.85

    def test_clear_no(self):
        label, conf, _ = classify_site({
            "exec.c_answer": "No",
            "exec.risk_notes": "Site does not meet occupancy requirements.",
        })
        assert label == "no"
        assert conf >= 0.90

    def test_yes_see_notes_is_yes_if(self):
        label, conf, signals = classify_site({
            "exec.c_answer": "Yes see notes",
            "exec.acquisition_conditions": "Yes, if we secure variance by June.",
            "exec.risk_notes": "Tradeoff on capacity.",
        })
        assert label == "yes_if"
        assert conf >= 0.90
        assert any("yes_if_phrase" in s for s in signals)

    def test_yes_with_tradeoff_downgrades_to_yes_if(self):
        # c_answer="Yes" but exec summary mentions tradeoffs → Yes-if
        label, conf, _ = classify_site({
            "exec.c_answer": "Yes",
            "exec.acquisition_conditions": "Close as-is.",
            "exec.risk_notes": "Tradeoff: smaller outdoor space than spec.",
        })
        assert label == "yes_if"
        assert 0.6 <= conf <= 0.85

    def test_yes_with_no_phrase_goes_to_review(self):
        # Conflicting signal: c_answer=Yes but phrase says hard blocker
        label, conf, signals = classify_site({
            "exec.c_answer": "Yes",
            "exec.risk_notes": "Fatal: zoning does not allow schools.",
        })
        assert label == "review"
        assert conf < 0.5
        assert any("conflict_no_phrase" in s for s in signals)

    def test_missing_c_answer_with_tradeoff(self):
        label, conf, _ = classify_site({
            "exec.acquisition_conditions": "Needs to go right: permit in 90 days.",
        })
        assert label == "yes_if"
        # Phrase-only evidence → moderate confidence
        assert conf < 0.70

    def test_missing_c_answer_with_no_phrase(self):
        label, _conf, _ = classify_site({
            "exec.risk_notes": "Not feasible without full rebuild.",
        })
        assert label == "no"

    def test_nothing_to_go_on(self):
        label, conf, _ = classify_site({})
        assert label == "review"
        assert conf == 0.0

    def test_case_insensitive(self):
        label, _, _ = classify_site({
            "exec.c_answer": "yes",
            "exec.risk_notes": "NEEDS TO GO RIGHT: permit on time.",
        })
        assert label == "yes_if"

    @pytest.mark.parametrize("label", CLASSIFICATIONS)
    def test_all_labels_are_strings(self, label):
        assert isinstance(label, str)


# ---------------------------------------------------------------------------
# SiteRecord.from_replacements
# ---------------------------------------------------------------------------


def _full_replacements() -> dict[str, str]:
    """A realistic, fully-populated V3 replacements dict (condensed)."""
    data: dict[str, str] = {
        "meta.site_name": "Palm Beach Gardens",
        "meta.marketing_name": "Alpha PBG",
        "meta.city_state_zip": "Palm Beach Gardens, FL 33410",
        "meta.school_type": "K-8",
        "meta.report_date": "04/22/26",
        "meta.prepared_by": "Greg Foote",
        "meta.drive_folder_url": "https://drive.google.com/drive/folders/ABC",
        "exec.c_answer": "Yes see notes",
        "exec.c_edreg": "Approved — FL nonpublic registration",
        "exec.c_occupancy": "E via minor TI",
        "exec.c_zoning": "Permitted by right",
        "exec.c_permit_timeline": "8-10 weeks",
        "exec.c_construction_timeline": "12 weeks",
        "exec.acquisition_conditions": "Close subject to landlord LOI execution.\nVariance on parking required.",
        "exec.risk_notes": "Tradeoff: Sharing egress with neighbor.\nNeeds to go right: permit approval by June 15.",
        "sources.sir_link": "https://drive.google.com/sir",
        "sources.inspection_link": "https://drive.google.com/inspection",
        "sources.isp_link": "https://drive.google.com/isp",
        "sources.e_occupancy_link": "https://drive.google.com/eo",
        "sources.school_approval_link": "https://drive.google.com/sa",
        "sources.opening_plan_link": "https://drive.google.com/op",
        "sources.trace_link": "https://drive.google.com/trace.json",
    }
    # Scenario metrics
    for scenario in ("recommended_path", "fastest_open", "max_capacity", "max_value"):
        data[f"exec.{scenario}_capacity"] = "180"
        data[f"exec.{scenario}_open_date"] = "08/12/26"
        data[f"exec.{scenario}_capex"] = "$1.2M"
    # Cost cells — populate one to prove plumbing
    data["exec.cost_demolition_recommended_path"] = "$50k"
    data["exec.cost_grand_total_recommended_path"] = "$1,200,000"
    return data


class TestFromReplacements:
    def test_happy_path(self):
        rec = SiteRecord.from_replacements(
            _full_replacements(),
            site_name="Palm Beach Gardens",
            report_date="04/22/26",
            drive_folder_url="https://drive.google.com/drive/folders/ABC",
            dd_report_url="https://docs.google.com/document/d/XYZ",
        )
        assert rec.slug == "palm-beach-gardens"
        assert rec.site_name == "Palm Beach Gardens"
        assert rec.marketing_name == "Alpha PBG"
        assert rec.city_state_zip == "Palm Beach Gardens, FL 33410"
        assert rec.school_type == "K-8"
        assert rec.prepared_by == "Greg Foote"
        assert rec.report_date == "04/22/26"
        assert rec.can_we_open == "Yes see notes"
        assert rec.classification.label == "yes_if"
        assert rec.classification.confidence >= 0.80
        assert "Close subject to landlord LOI execution." in rec.classification.tradeoffs
        assert any("permit approval by June 15" in b for b in rec.classification.needs_to_go_right)

    def test_scenarios_fully_populated(self):
        rec = SiteRecord.from_replacements(
            _full_replacements(),
            site_name="Palm Beach Gardens",
            report_date="04/22/26",
            drive_folder_url="",
            dd_report_url="",
        )
        for scenario in ("recommended_path", "fastest_open", "max_capacity", "max_value"):
            assert scenario in rec.scenarios
            s = rec.scenarios[scenario]
            assert s.capacity == "180"
            assert s.open_date == "08/12/26"
            assert s.capex == "$1.2M"
            # Every cost key present (empty strings for unfilled cells is fine)
            assert "cost_demolition" in s.costs
            assert "cost_grand_total" in s.costs
        assert rec.scenarios["recommended_path"].costs["cost_grand_total"] == "$1,200,000"

    def test_sources_wired(self):
        rec = SiteRecord.from_replacements(
            _full_replacements(),
            site_name="Palm Beach Gardens",
            report_date="04/22/26",
            drive_folder_url="https://drive.google.com/drive/folders/ABC",
            dd_report_url="https://docs.google.com/document/d/XYZ",
        )
        assert rec.sources.sir == "https://drive.google.com/sir"
        assert rec.sources.opening_plan == "https://drive.google.com/op"
        assert rec.sources.dd_report == "https://docs.google.com/document/d/XYZ"
        assert rec.sources.drive_folder == "https://drive.google.com/drive/folders/ABC"

    def test_to_dict_is_json_serializable(self):
        import json
        rec = SiteRecord.from_replacements(
            _full_replacements(),
            site_name="Palm Beach Gardens",
            report_date="04/22/26",
            drive_folder_url="",
            dd_report_url="",
        )
        blob = json.dumps(rec.to_dict())
        reloaded = json.loads(blob)
        assert reloaded["slug"] == "palm-beach-gardens"
        assert reloaded["classification"]["label"] == "yes_if"
        assert reloaded["scenarios"]["recommended_path"]["capacity"] == "180"

    def test_missing_fields_default_to_empty(self):
        rec = SiteRecord.from_replacements(
            {"exec.c_answer": "Yes"},
            site_name="Austin",
            report_date="04/22/26",
            drive_folder_url="",
            dd_report_url="",
        )
        assert rec.slug == "austin"
        assert rec.marketing_name == ""
        assert rec.sources.sir == ""
        assert rec.classification.label == "yes"
        # Scenarios dict is fully populated with empty ScenarioRecords
        assert set(rec.scenarios.keys()) == {
            "recommended_path", "fastest_open", "max_capacity", "max_value",
        }
        assert rec.scenarios["recommended_path"].capacity == ""

    def test_slug_suffix_for_disambiguation(self):
        rec = SiteRecord.from_replacements(
            {},
            site_name="Palm Beach Gardens",
            report_date="04/22/26",
            drive_folder_url="",
            dd_report_url="",
            slug_suffix="main",
        )
        assert rec.slug == "palm-beach-gardens-main"

    def test_published_at_is_iso8601_utc(self):
        rec = SiteRecord.from_replacements(
            {},
            site_name="Austin",
            report_date="04/22/26",
            drive_folder_url="",
            dd_report_url="",
        )
        # Should end with +00:00 (UTC offset) and parse
        from datetime import datetime
        dt = datetime.fromisoformat(rec.published_at)
        assert dt.utcoffset() is not None
