"""Unit tests for site_record classification and projection."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from due_diligence_reporter.site_record import (
    CLASSIFICATIONS,
    SiteRecord,
    classify_site,
    site_slug,
)


class TestSiteSlug:
    def test_basic(self) -> None:
        assert site_slug("Palm Beach Gardens") == "palm-beach-gardens"

    def test_with_suffix(self) -> None:
        assert site_slug("Palm Beach Gardens", suffix="main") == "palm-beach-gardens-main"

    def test_strips_punctuation(self) -> None:
        assert site_slug("St. Paul's - Building 2") == "st-paul-s-building-2"

    def test_empty_fallback(self) -> None:
        assert site_slug("") == "unknown-site"
        assert site_slug("   ") == "unknown-site"

    def test_case_insensitive(self) -> None:
        assert site_slug("MIAMI BEACH") == site_slug("miami beach") == "miami-beach"


class TestClassifySite:
    def test_clear_yes(self) -> None:
        label, confidence, _signals = classify_site({
            "exec.c_answer": "Yes",
            "exec.acquisition_conditions": "Standard lease protections.",
            "exec.tradeoffs_and_deficiencies": "",
        })
        assert label == "yes"
        assert confidence >= 0.85

    def test_clear_no(self) -> None:
        label, confidence, _signals = classify_site({
            "exec.c_answer": "No",
            "exec.tradeoffs_and_deficiencies": "Site does not meet occupancy requirements.",
        })
        assert label == "no"
        assert confidence >= 0.85

    def test_yes_see_notes_is_yes_if(self) -> None:
        label, confidence, signals = classify_site({
            "exec.c_answer": "Yes see notes",
            "exec.tradeoffs_and_deficiencies": "Tradeoff: smaller outdoor space than spec.",
        })
        assert label == "yes_if"
        assert confidence >= 0.80
        assert any("yes_if_phrase:tradeoff" == signal for signal in signals)

    def test_yes_with_no_phrase_goes_to_review(self) -> None:
        label, confidence, signals = classify_site({
            "exec.c_answer": "Yes",
            "exec.tradeoffs_and_deficiencies": "Fatal: zoning does not allow schools.",
        })
        assert label == "review"
        assert confidence < 0.5
        assert any(signal.startswith("conflict_no_phrase:") for signal in signals)

    def test_phrase_only_result_downgrades_below_threshold(self) -> None:
        label, confidence, signals = classify_site({
            "exec.tradeoffs_and_deficiencies": "Not feasible without full rebuild.",
        })
        assert label == "review"
        assert confidence == 0.55
        assert "below_threshold:0.70" in signals

    def test_phrase_only_result_can_pass_with_lower_threshold(self) -> None:
        label, confidence, _signals = classify_site(
            {"exec.tradeoffs_and_deficiencies": "Not feasible without full rebuild."},
            threshold=0.5,
        )
        assert label == "no"
        assert confidence == 0.55

    def test_case_insensitive(self) -> None:
        label, _confidence, _signals = classify_site({
            "exec.c_answer": "yes",
            "exec.tradeoffs_and_deficiencies": "NEEDS TO GO RIGHT: permit on time.",
        })
        assert label == "yes_if"

    @pytest.mark.parametrize("label", CLASSIFICATIONS)
    def test_all_labels_are_strings(self, label: str) -> None:
        assert isinstance(label, str)


def _full_replacements() -> dict[str, str]:
    data: dict[str, str] = {
        "meta.site_name": "Palm Beach Gardens",
        "meta.marketing_name": "Alpha PBG",
        "meta.city_state_zip": "Palm Beach Gardens, FL 33410",
        "meta.school_type": "K-8",
        "meta.report_date": "04/22/26",
        "meta.prepared_by": "Greg Foote",
        "meta.drive_folder_url": "https://drive.google.com/drive/folders/ABC",
        "exec.c_answer": "Yes see notes",
        "exec.c_edreg": "Approved - FL nonpublic registration",
        "exec.c_occupancy": "E via minor TI",
        "exec.c_zoning": "Permitted",
        "exec.c_permit_timeline": "8-10 weeks",
        "exec.c_construction_timeline": "12 weeks",
        "exec.direct_viable_buildout": "Fastest Open",
        "exec.alpha_fit": "Yes",
        "exec.acquisition_conditions": (
            "Condition lease on permit approval by June 15.\n"
            "Require landlord to deliver exclusive parking rights."
        ),
        "exec.tradeoffs_and_deficiencies": (
            "Tradeoff: smaller outdoor space than spec.\n"
            "Not close enough to a park."
        ),
        "sources.sir_link": "https://drive.google.com/sir",
        "sources.inspection_link": "https://drive.google.com/inspection",
        "sources.block_plan_link": "https://drive.google.com/block-plan",
        "sources.e_occupancy_link": "https://drive.google.com/eo",
        "sources.school_approval_link": "https://drive.google.com/sa",
        "sources.opening_plan_link": "https://drive.google.com/op",
        "sources.trace_link": "https://drive.google.com/trace.json",
    }
    for scenario in ("fastest_open", "max_capacity"):
        data[f"exec.{scenario}_capacity"] = "180"
        data[f"exec.{scenario}_open_date"] = "08/12/26"
        data[f"exec.{scenario}_capex"] = "$1.2M"
    data["exec.cost_demolition_fastest_open"] = "$50k"
    data["exec.cost_grand_total_fastest_open"] = "$1,200,000"
    return data


class TestFromReplacements:
    def test_happy_path(self) -> None:
        record = SiteRecord.from_replacements(
            _full_replacements(),
            site_name="Palm Beach Gardens",
            report_date="04/22/26",
            drive_folder_url="https://drive.google.com/drive/folders/ABC",
            dd_report_url="https://docs.google.com/document/d/XYZ",
        )
        assert record.slug == "palm-beach-gardens"
        assert record.site_name == "Palm Beach Gardens"
        assert record.marketing_name == "Alpha PBG"
        assert record.city_state_zip == "Palm Beach Gardens, FL 33410"
        assert record.school_type == "K-8"
        assert record.prepared_by == "Greg Foote"
        assert record.report_date == "04/22/26"
        assert record.can_we_open == "Yes see notes"
        assert record.direct_viable_buildout == "Fastest Open"
        assert record.alpha_fit == "Yes"
        assert record.classification.label == "yes_if"
        assert any(
            "smaller outdoor space than spec." in item
            for item in record.classification.tradeoffs
        )
        assert any("permit approval by June 15" in item for item in record.classification.needs_to_go_right)
        assert record.tradeoffs_and_deficiencies.startswith("Tradeoff:")

    def test_scenarios_follow_current_schema(self) -> None:
        record = SiteRecord.from_replacements(
            _full_replacements(),
            site_name="Palm Beach Gardens",
            report_date="04/22/26",
            drive_folder_url="",
            dd_report_url="",
        )
        assert set(record.scenarios.keys()) == {"fastest_open", "max_capacity"}
        assert record.scenarios["fastest_open"].capacity == "180"
        assert record.scenarios["fastest_open"].costs["cost_grand_total"] == "$1,200,000"
        assert record.scenarios["max_capacity"].capacity == "180"

    def test_sources_wired(self) -> None:
        record = SiteRecord.from_replacements(
            _full_replacements(),
            site_name="Palm Beach Gardens",
            report_date="04/22/26",
            drive_folder_url="https://drive.google.com/drive/folders/ABC",
            dd_report_url="https://docs.google.com/document/d/XYZ",
        )
        assert record.sources.sir == "https://drive.google.com/sir"
        assert record.sources.block_plan == "https://drive.google.com/block-plan"
        assert record.sources.dd_report == "https://docs.google.com/document/d/XYZ"
        assert record.sources.drive_folder == "https://drive.google.com/drive/folders/ABC"

    def test_to_dict_is_json_serializable(self) -> None:
        record = SiteRecord.from_replacements(
            _full_replacements(),
            site_name="Palm Beach Gardens",
            report_date="04/22/26",
            drive_folder_url="",
            dd_report_url="",
        )
        reloaded = json.loads(json.dumps(record.to_dict()))
        assert reloaded["slug"] == "palm-beach-gardens"
        assert reloaded["classification"]["label"] == "yes_if"
        assert reloaded["scenarios"]["fastest_open"]["capacity"] == "180"
        assert reloaded["sources"]["block_plan"] == "https://drive.google.com/block-plan"

    def test_missing_fields_default_to_empty(self) -> None:
        record = SiteRecord.from_replacements(
            {"exec.c_answer": "Yes"},
            site_name="Austin",
            report_date="04/22/26",
            drive_folder_url="",
            dd_report_url="",
        )
        assert record.slug == "austin"
        assert record.marketing_name == ""
        assert record.sources.sir == ""
        assert record.classification.label == "yes"
        assert set(record.scenarios.keys()) == {"fastest_open", "max_capacity"}
        assert record.scenarios["fastest_open"].capacity == ""

    def test_slug_suffix_for_disambiguation(self) -> None:
        record = SiteRecord.from_replacements(
            {},
            site_name="Palm Beach Gardens",
            report_date="04/22/26",
            drive_folder_url="",
            dd_report_url="",
            slug_suffix="main",
        )
        assert record.slug == "palm-beach-gardens-main"

    def test_published_at_is_iso8601_utc(self) -> None:
        record = SiteRecord.from_replacements(
            {},
            site_name="Austin",
            report_date="04/22/26",
            drive_folder_url="",
            dd_report_url="",
        )
        parsed = datetime.fromisoformat(record.published_at)
        assert parsed.utcoffset() is not None
