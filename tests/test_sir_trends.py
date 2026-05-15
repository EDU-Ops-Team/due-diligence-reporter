from __future__ import annotations

from datetime import UTC, datetime

from due_diligence_reporter.sir_trends import (
    append_review_outcome,
    load_review_outcomes,
    make_review_outcome,
    parse_since,
    summarize_sir_trends,
)


def test_review_outcome_round_trips_jsonl(tmp_path) -> None:
    store = tmp_path / "sir-review-outcomes.jsonl"
    outcome = make_review_outcome(
        site="Alpha Keller",
        ai_sir="ai-doc",
        cds_sir="cds-doc",
        section="Zoning",
        gap_category="AI missed item",
        severity="material",
        ddr_impact="exec.c_zoning",
        evidence_checked="city code",
        learning_action="retrieval rule",
        status="accepted",
        created_at="2026-05-01T00:00:00+00:00",
    )

    append_review_outcome(outcome, path=store)

    loaded = load_review_outcomes(path=store)
    assert loaded[0]["site"] == "Alpha Keller"
    assert loaded[0]["gap_category"] == "AI missed item"


def test_summarizes_30_day_trends() -> None:
    outcomes = [
        {
            "created_at": "2026-05-01T00:00:00+00:00",
            "site": "Alpha Keller",
            "ai_sir": "ai-1",
            "cds_sir": "cds-1",
            "section": "Zoning",
            "gap_category": "AI missed item",
            "severity": "material",
            "ddr_impact": "exec.c_zoning",
            "learning_action": "retrieval rule",
            "status": "accepted",
        },
        {
            "created_at": "2026-05-02T00:00:00+00:00",
            "site": "Alpha Keller",
            "ai_sir": "ai-1",
            "cds_sir": "cds-1",
            "section": "Zoning",
            "gap_category": "AI unsupported claim",
            "severity": "cleanup",
            "ddr_impact": "none",
            "learning_action": "prompt update",
            "status": "open",
        },
        {
            "created_at": "2026-05-03T00:00:00+00:00",
            "site": "Alpha Keller",
            "ai_sir": "ai-1",
            "cds_sir": "cds-1",
            "section": "Zoning",
            "gap_category": "AI missed item",
            "severity": "blocking",
            "ddr_impact": "exec.c_zoning",
            "learning_action": "retrieval rule",
            "status": "accepted",
        },
        {
            "created_at": "2026-03-01T00:00:00+00:00",
            "site": "Old Site",
            "section": "Permits",
            "gap_category": "CDS missed item",
            "severity": "material",
            "ddr_impact": "exec.c_permit_timeline",
            "learning_action": "QC checklist item",
            "status": "accepted",
        },
    ]

    summary = summarize_sir_trends(
        outcomes,
        since=datetime(2026, 4, 15, tzinfo=UTC),
    )

    assert summary["total_issues"] == 3
    assert summary["sites_reviewed"] == 1
    assert summary["sir_pairs_reviewed"] == 1
    assert summary["ai_missed_items_per_sir"] == 2.0
    assert summary["ai_unsupported_claims_per_sir"] == 1.0
    assert summary["ddr_impacting_findings"] == 2
    assert summary["repeat_issues"]["Zoning | AI missed item"] == 2


def test_parse_since_defaults_to_day_window() -> None:
    now = datetime(2026, 5, 15, tzinfo=UTC)

    assert parse_since("30d", now=now) == datetime(2026, 4, 15, tzinfo=UTC)
