"""Tests for the Phase 4 ``dd_risk_flags`` canonicalizer.

Covers:
- Per-source ingesters (permit_history, e_occupancy, school_approval, sir_risk_watch)
- Severity rules per source
- Dedup on (category, source) with severity tie-break (higher wins)
- Caller-supplied flag normalization (drops invalid entries)
- Empty / missing-token paths
- Sort order is deterministic (severity desc, category asc, source asc)
"""

from __future__ import annotations

import pytest

from due_diligence_reporter.risk_flags import (
    derive_risk_flags,
    normalize_caller_flags,
)


# --- permit_history ingestion ---------------------------------------------


class TestPermitHistoryIngestion:
    def test_acquisition_condition_to_high(self) -> None:
        report = {
            "permit_history.risk_flags": [
                {
                    "flag_type": "OPEN_PERMIT",
                    "severity": "acquisition_condition",
                    "description": "2 open permits — must be resolved",
                    "evidence": "permit_active_count=2",
                }
            ]
        }
        flags = derive_risk_flags(report)
        assert len(flags) == 1
        assert flags[0]["category"] == "ahj_history"
        assert flags[0]["severity"] == "high"
        assert flags[0]["source"] == "permit_history"
        assert "open permits" in flags[0]["summary"]

    def test_risk_note_to_medium(self) -> None:
        report = {
            "permit_history.risk_flags": [
                {
                    "flag_type": "DEFERRED_MAINTENANCE",
                    "severity": "risk_note",
                    "description": "No permit activity in 10 years",
                    "evidence": "permit_count=0",
                }
            ]
        }
        flags = derive_risk_flags(report)
        assert len(flags) == 1
        assert flags[0]["severity"] == "medium"

    def test_info_severity_dropped(self) -> None:
        report = {
            "permit_history.risk_flags": [
                {
                    "flag_type": "ELECTRICAL_PERMIT",
                    "severity": "info",
                    "description": "Electrical permit on file",
                    "evidence": "permit type=electrical",
                }
            ]
        }
        assert derive_risk_flags(report) == []

    def test_multiple_high_flags_deduped_to_one(self) -> None:
        # Two acquisition_condition entries collapse to a single
        # ahj_history:high flag (merged summary, severity preserved).
        report = {
            "permit_history.risk_flags": [
                {
                    "flag_type": "OPEN_PERMIT",
                    "severity": "acquisition_condition",
                    "description": "2 open permits",
                    "evidence": "x",
                },
                {
                    "flag_type": "DEMO_PERMIT",
                    "severity": "acquisition_condition",
                    "description": "Demolition permit found 2023-01-15",
                    "evidence": "y",
                },
            ]
        }
        flags = derive_risk_flags(report)
        assert len(flags) == 1
        assert flags[0]["severity"] == "high"
        assert "open permits" in flags[0]["summary"]
        assert "Demolition" in flags[0]["summary"]

    def test_severity_tiebreak_higher_wins(self) -> None:
        # acquisition_condition (high) + risk_note (medium) → high wins.
        report = {
            "permit_history.risk_flags": [
                {
                    "flag_type": "DEFERRED_MAINTENANCE",
                    "severity": "risk_note",
                    "description": "No activity",
                    "evidence": "x",
                },
                {
                    "flag_type": "OPEN_PERMIT",
                    "severity": "acquisition_condition",
                    "description": "2 open",
                    "evidence": "y",
                },
            ]
        }
        flags = derive_risk_flags(report)
        assert len(flags) == 1
        assert flags[0]["severity"] == "high"

    def test_missing_token_returns_empty(self) -> None:
        assert derive_risk_flags({}) == []
        assert derive_risk_flags({"permit_history.risk_flags": None}) == []
        assert derive_risk_flags({"permit_history.risk_flags": "not a list"}) == []


# --- e_occupancy ingestion ------------------------------------------------


class TestEOccupancyIngestion:
    def test_structured_ibc_flags_categorized(self) -> None:
        report = {
            "q2.ibc_flags": [
                "Sprinkler retrofit required — building exceeds 12,000 sq ft fire area",
                "Travel distance fails — 280 ft over 250 ft IBC limit",
                "ADA accessible entrance not provided",
            ],
        }
        flags = derive_risk_flags(report)
        cats = {f["category"] for f in flags}
        assert "occupancy" in cats
        assert "accessibility" in cats
        # Hard-fail keywords ("fail", "exceeds") → high severity
        occupancy_flags = [f for f in flags if f["category"] == "occupancy"]
        assert all(f["severity"] == "high" for f in occupancy_flags)

    def test_ibc_summary_text_fallback(self) -> None:
        # No structured token; canonicalizer parses the human summary.
        report = {
            "q2.e_occupancy_ibc_summary": (
                "- Sprinkler required (building over 12,000 sq ft)\n"
                "- ADA ramp must be added at main entrance"
            ),
        }
        flags = derive_risk_flags(report)
        cats = {f["category"] for f in flags}
        assert "occupancy" in cats
        assert "accessibility" in cats

    def test_red_zone_emits_occupancy_flag_when_no_ibc_flags(self) -> None:
        report = {"q2.e_occupancy_zone": "Red"}
        flags = derive_risk_flags(report)
        assert len(flags) == 1
        assert flags[0]["category"] == "occupancy"
        assert flags[0]["severity"] == "high"
        assert flags[0]["source"] == "e_occupancy"

    def test_red_zone_does_not_double_emit_when_ibc_flag_present(self) -> None:
        # Structured IBC flag already covers occupancy → red-zone fallback
        # collapses into the same (category, source) pair via dedup.
        report = {
            "q2.e_occupancy_zone": "Red",
            "q2.ibc_flags": ["Travel distance fails 250 ft IBC limit"],
        }
        flags = derive_risk_flags(report)
        occupancy_flags = [f for f in flags if f["category"] == "occupancy"]
        assert len(occupancy_flags) == 1
        assert occupancy_flags[0]["severity"] == "high"

    def test_green_zone_no_flags(self) -> None:
        report = {"q2.e_occupancy_zone": "Green"}
        assert derive_risk_flags(report) == []

    def test_unrecognized_ibc_text_skipped(self) -> None:
        report = {"q2.ibc_flags": ["Some unrelated note about HVAC capacity"]}
        assert derive_risk_flags(report) == []


# --- school_approval ingestion --------------------------------------------


class TestSchoolApprovalIngestion:
    @pytest.mark.parametrize(
        ("zone", "expected_severity"),
        [
            ("red", "high"),
            ("Red", "high"),
            ("orange", "high"),
            ("yellow", "medium"),
            ("Yellow", "medium"),
        ],
    )
    def test_zone_to_severity(self, zone: str, expected_severity: str) -> None:
        report = {
            "q1.school_approval_zone": zone,
            "q1.school_approval_type": "CERTIFICATE_OR_APPROVAL_REQUIRED",
            "q1.school_approval_timeline_days": "90",
        }
        flags = derive_risk_flags(report)
        assert len(flags) == 1
        assert flags[0]["category"] == "ed_reg"
        assert flags[0]["source"] == "school_approval"
        assert flags[0]["severity"] == expected_severity
        assert "CERTIFICATE_OR_APPROVAL_REQUIRED" in flags[0]["summary"]
        assert "90-day" in flags[0]["summary"]

    def test_green_zone_no_flag(self) -> None:
        assert derive_risk_flags({"q1.school_approval_zone": "Green"}) == []

    def test_missing_zone_no_flag(self) -> None:
        # Without a zone token we don't fabricate a flag — the upstream
        # skill is responsible for surfacing it.
        report = {"q1.school_approval_type": "CERTIFICATE_OR_APPROVAL_REQUIRED"}
        assert derive_risk_flags(report) == []


# --- SIR Risk Watch ingestion ---------------------------------------------


class TestSirRiskWatchIngestion:
    def test_keyword_categorization(self) -> None:
        report = {
            "sir.risk_watch": [
                "Property is in FEMA flood zone AE",
                "Site requires zoning variance for educational use",
                "Wetland delineation needed before site work",
            ],
        }
        flags = derive_risk_flags(report)
        cats = {f["category"] for f in flags}
        assert cats == {"flood_zone", "zoning", "environmental"}
        assert all(f["source"] == "sir_risk_watch" for f in flags)
        # Default severity is medium absent high-severity keywords
        assert all(f["severity"] == "medium" for f in flags)

    def test_blocking_keyword_to_high(self) -> None:
        report = {
            "sir.risk_watch": [
                "Historic district designation is a deal-breaker for facade changes"
            ],
        }
        flags = derive_risk_flags(report)
        assert len(flags) == 1
        assert flags[0]["category"] == "historic_district"
        assert flags[0]["severity"] == "high"

    def test_septic_folds_into_environmental(self) -> None:
        # Phase 4 design lock: septic findings surface as environmental.
        report = {"sir.risk_watch": ["Site on private septic — perc test required"]}
        flags = derive_risk_flags(report)
        assert len(flags) == 1
        assert flags[0]["category"] == "environmental"

    def test_text_blob_fallback(self) -> None:
        report = {
            "sir.risk_watch_text": (
                "- Flood zone AE intersects parcel\n"
                "- Parking variance needed"
            ),
        }
        flags = derive_risk_flags(report)
        cats = {f["category"] for f in flags}
        assert "flood_zone" in cats
        assert "parking" in cats

    def test_unmatched_text_skipped(self) -> None:
        # A risk-watch entry that doesn't match any keyword silently
        # drops — better to omit than miscategorize.
        report = {"sir.risk_watch": ["General market softness in submarket"]}
        assert derive_risk_flags(report) == []


# --- Cross-source dedup + sort --------------------------------------------


class TestCrossSourceDedupAndSort:
    def test_same_category_different_source_kept_separate(self) -> None:
        # zoning from SIR + occupancy from e_occupancy → both kept
        # (dedup is on (category, source), not on category alone).
        report = {
            "sir.risk_watch": ["Zoning variance needed"],
            "q2.e_occupancy_zone": "Red",
        }
        flags = derive_risk_flags(report)
        assert len(flags) == 2
        keys = {(f["category"], f["source"]) for f in flags}
        assert keys == {
            ("zoning", "sir_risk_watch"),
            ("occupancy", "e_occupancy"),
        }

    def test_sort_severity_desc_then_category(self) -> None:
        report = {
            "sir.risk_watch": [
                "Parking variance needed",  # parking, medium
                "Flood zone is a fatal flaw",  # flood_zone, high
            ],
            "q1.school_approval_zone": "Yellow",  # ed_reg, medium
        }
        flags = derive_risk_flags(report)
        assert [f["severity"] for f in flags] == ["high", "medium", "medium"]
        # Within medium: alphabetical by category
        medium = [f for f in flags if f["severity"] == "medium"]
        assert [f["category"] for f in medium] == ["ed_reg", "parking"]


# --- Caller-supplied flag normalization -----------------------------------


class TestNormalizeCallerFlags:
    def test_valid_flags_pass_through(self) -> None:
        flags = [
            {
                "category": "zoning",
                "severity": "high",
                "source": "sir_risk_watch",
                "summary": "Manual override",
            }
        ]
        result = normalize_caller_flags(flags)
        assert result == flags

    def test_invalid_category_dropped(self) -> None:
        flags = [
            {
                "category": "made_up",
                "severity": "high",
                "source": "sir_risk_watch",
                "summary": "x",
            }
        ]
        assert normalize_caller_flags(flags) == []

    def test_invalid_severity_dropped(self) -> None:
        flags = [
            {
                "category": "zoning",
                "severity": "critical",
                "source": "sir_risk_watch",
                "summary": "x",
            }
        ]
        assert normalize_caller_flags(flags) == []

    def test_invalid_source_dropped(self) -> None:
        flags = [
            {
                "category": "zoning",
                "severity": "high",
                "source": "rumor",
                "summary": "x",
            }
        ]
        assert normalize_caller_flags(flags) == []

    def test_missing_summary_dropped(self) -> None:
        flags = [
            {
                "category": "zoning",
                "severity": "high",
                "source": "sir_risk_watch",
                "summary": "",
            }
        ]
        assert normalize_caller_flags(flags) == []

    def test_case_insensitive_normalization(self) -> None:
        flags = [
            {
                "category": "ZONING",
                "severity": "High",
                "source": "SIR_Risk_Watch",
                "summary": "Mixed case",
            }
        ]
        result = normalize_caller_flags(flags)
        assert result[0]["category"] == "zoning"
        assert result[0]["severity"] == "high"
        assert result[0]["source"] == "sir_risk_watch"

    def test_caller_flags_dedup(self) -> None:
        flags = [
            {
                "category": "zoning",
                "severity": "medium",
                "source": "sir_risk_watch",
                "summary": "First",
            },
            {
                "category": "zoning",
                "severity": "high",
                "source": "sir_risk_watch",
                "summary": "Second",
            },
        ]
        result = normalize_caller_flags(flags)
        assert len(result) == 1
        assert result[0]["severity"] == "high"

    def test_empty_or_none_input(self) -> None:
        assert normalize_caller_flags(None) == []
        assert normalize_caller_flags([]) == []

    def test_non_dict_entries_dropped(self) -> None:
        assert normalize_caller_flags(["not a dict", 42, None]) == []


# --- Summary length cap ---------------------------------------------------


class TestSummaryShortening:
    def test_long_summary_truncated_with_ellipsis(self) -> None:
        long_text = "Site requires zoning variance " * 20
        report = {"sir.risk_watch": [long_text]}
        flags = derive_risk_flags(report)
        assert len(flags) == 1
        # ~200 char cap with trailing ellipsis
        assert len(flags[0]["summary"]) <= 200
        assert flags[0]["summary"].endswith("…")

    def test_whitespace_collapsed(self) -> None:
        report = {"sir.risk_watch": ["Zoning   variance\n\n\nrequired"]}
        flags = derive_risk_flags(report)
        assert flags[0]["summary"] == "Zoning variance required"
