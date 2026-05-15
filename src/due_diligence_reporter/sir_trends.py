"""Structured SIR review outcomes and 30-day trend summaries."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .pipeline_manifest import PROJECT_ROOT

REVIEW_OUTCOME_PATH = PROJECT_ROOT / ".ddr-runs" / "sir-review-outcomes.jsonl"
DEFAULT_SINCE = "30d"


@dataclass(frozen=True)
class SirReviewOutcome:
    """One adjudicated issue from an AI/CDS SIR comparison."""

    review_id: str
    created_at: str
    site: str
    ai_sir: str
    cds_sir: str
    section: str
    gap_category: str
    severity: str
    ddr_impact: str
    evidence_checked: str
    learning_action: str
    status: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "created_at": self.created_at,
            "site": self.site,
            "ai_sir": self.ai_sir,
            "cds_sir": self.cds_sir,
            "section": self.section,
            "gap_category": self.gap_category,
            "severity": self.severity,
            "ddr_impact": self.ddr_impact,
            "evidence_checked": self.evidence_checked,
            "learning_action": self.learning_action,
            "status": self.status,
            "notes": self.notes,
        }


def make_review_outcome(
    *,
    site: str,
    section: str,
    gap_category: str,
    severity: str,
    ddr_impact: str,
    evidence_checked: str,
    learning_action: str,
    status: str,
    ai_sir: str = "",
    cds_sir: str = "",
    notes: str = "",
    created_at: str | None = None,
) -> SirReviewOutcome:
    """Build a review outcome with normalized, queryable fields."""
    return SirReviewOutcome(
        review_id=uuid4().hex,
        created_at=created_at or datetime.now(UTC).isoformat(),
        site=_required(site, "site"),
        ai_sir=ai_sir.strip(),
        cds_sir=cds_sir.strip(),
        section=_required(section, "section"),
        gap_category=_required(gap_category, "gap_category"),
        severity=_required(severity, "severity"),
        ddr_impact=_required(ddr_impact, "ddr_impact"),
        evidence_checked=_required(evidence_checked, "evidence_checked"),
        learning_action=_required(learning_action, "learning_action"),
        status=_required(status, "status"),
        notes=notes.strip(),
    )


def append_review_outcome(
    outcome: SirReviewOutcome,
    *,
    path: Path | None = None,
) -> Path:
    """Append one review outcome to the JSONL store."""
    target = path or REVIEW_OUTCOME_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(outcome.to_dict(), sort_keys=True) + "\n")
    return target


def load_review_outcomes(*, path: Path | None = None) -> list[dict[str, Any]]:
    """Load review outcomes from the JSONL store."""
    target = path or REVIEW_OUTCOME_PATH
    if not target.exists():
        return []

    outcomes: list[dict[str, Any]] = []
    for line_number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {target}:{line_number}") from exc
        if isinstance(payload, dict):
            outcomes.append(payload)
    return outcomes


def parse_since(value: str, *, now: datetime | None = None) -> datetime:
    """Parse a relative day window like ``30d`` or an ISO timestamp."""
    raw = value.strip() or DEFAULT_SINCE
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)

    if raw.endswith("d") and raw[:-1].isdigit():
        return current - timedelta(days=int(raw[:-1]))

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("since must be a day window like 30d or an ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def summarize_sir_trends(
    outcomes: list[dict[str, Any]],
    *,
    since: datetime,
) -> dict[str, Any]:
    """Aggregate review outcomes since the supplied timestamp."""
    filtered = [outcome for outcome in outcomes if _created_at(outcome) >= since]
    sites = {str(item.get("site", "")).strip() for item in filtered if item.get("site")}
    pair_keys = {
        (
            str(item.get("site", "")).strip(),
            str(item.get("ai_sir", "")).strip(),
            str(item.get("cds_sir", "")).strip(),
        )
        for item in filtered
    }
    pair_count = len(pair_keys)
    denominator = max(pair_count, len(sites), 1)

    by_category = _counter(filtered, "gap_category")
    repeat_issues = {
        key: count
        for key, count in Counter(
            f"{item.get('section', '')} | {item.get('gap_category', '')}"
            for item in filtered
        ).items()
        if count > 1
    }

    return {
        "since": since.isoformat(),
        "total_issues": len(filtered),
        "sites_reviewed": len(sites),
        "sir_pairs_reviewed": pair_count,
        "ai_missed_items_per_sir": _rate(by_category.get("AI missed item", 0), denominator),
        "ai_unsupported_claims_per_sir": _rate(
            by_category.get("AI unsupported claim", 0), denominator
        ),
        "cds_missed_items_per_sir": _rate(by_category.get("CDS missed item", 0), denominator),
        "ddr_impacting_findings": sum(1 for item in filtered if _has_ddr_impact(item)),
        "blocking_or_material_findings": sum(
            1
            for item in filtered
            if str(item.get("severity", "")).strip().lower() in {"blocking", "material"}
        ),
        "by_category": dict(by_category),
        "by_section": dict(_counter(filtered, "section")),
        "by_severity": dict(_counter(filtered, "severity")),
        "by_status": dict(_counter(filtered, "status")),
        "by_learning_action": dict(_counter(filtered, "learning_action")),
        "repeat_issues": repeat_issues,
    }


def _required(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} is required")
    return stripped


def _created_at(outcome: dict[str, Any]) -> datetime:
    raw = str(outcome.get("created_at", ""))
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _counter(outcomes: list[dict[str, Any]], field: str) -> Counter[str]:
    return Counter(
        str(item.get(field, "")).strip() or "(blank)"
        for item in outcomes
    )


def _rate(count: int, denominator: int) -> float:
    return round(count / denominator, 2)


def _has_ddr_impact(outcome: dict[str, Any]) -> bool:
    impact = str(outcome.get("ddr_impact", "")).strip().lower()
    return impact not in {"", "none", "no", "n/a"}
