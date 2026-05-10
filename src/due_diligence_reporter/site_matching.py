"""Site record fuzzy matching with disambiguation support.

Single canonical resolver used by every MCP tool and script that needs
to turn a user-supplied site name/ID/permalink into a Wrike Site Record.
Replaces the rigid single-winner LLM matcher with a scoring cascade
that surfaces ambiguity to the caller.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Literal

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

EXACT_MATCH_SCORE = 100
HIGH_CONFIDENCE_SCORE = 92
TOP_N_FLOOR = 70
AMBIGUOUS_GAP = 5
CONFIDENT_LEAD = 8
TOP_N_LIMIT = 5
INBOX_AUTO_MATCH_SCORE = 92
INBOX_AUTO_MATCH_LEAD = 10

ResolutionStatus = Literal["matched", "ambiguous", "not_found"]


@dataclass
class ScoredCandidate:
    id: str
    title: str
    address: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "address": self.address,
            "score": round(self.score, 1),
        }


@dataclass
class SiteResolution:
    status: ResolutionStatus
    query: str
    match: dict[str, Any] | None = None
    candidates: list[ScoredCandidate] = field(default_factory=list)
    reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        """Render as the JSON payload returned by ambiguous/not_found MCP tools."""
        return {
            "status": self.status,
            "query": self.query,
            "reason": self.reason,
            "candidates": [c.to_dict() for c in self.candidates],
        }


def _haystack(record: dict[str, Any], extract_address) -> str:
    title = record.get("title") or ""
    address = extract_address(record) or ""
    if address and address.lower() not in title.lower():
        return f"{title} {address}".strip()
    return title


def resolve_site(
    query: str,
    *,
    site_records: list[dict[str, Any]] | None = None,
) -> SiteResolution:
    """Resolve a site query (name | ID | permalink) to a Wrike record.

    Cascade:
        1. Wrike ID short-circuit (handled by caller before this).
        2. Permalink short-circuit (handled by caller).
        3. Exact title match (case-insensitive, trimmed) — multiple → ambiguous.
        4. Token-set ratio scoring against title+address.
        5. LLM tiebreak when top-2 scores are within AMBIGUOUS_GAP.
        6. Otherwise: matched / ambiguous / not_found per thresholds.
    """
    # Lazy import to avoid circular dep (wrike.py imports site_matching.py).
    from .wrike import (
        _get_all_site_records,
        extract_address_from_record,
        load_wrike_config,
    )
    from .wrike import (
        _match_site_with_llm as _llm_tiebreak,
    )

    q = (query or "").strip()
    if not q:
        return SiteResolution(status="not_found", query=query, reason="empty query")

    if site_records is None:
        site_records = _get_all_site_records(cfg=load_wrike_config())

    if not site_records:
        return SiteResolution(status="not_found", query=q, reason="no site records loaded")

    q_lower = q.lower()

    exact_hits = [
        r for r in site_records
        if (r.get("title") or "").strip().lower() == q_lower
    ]
    if len(exact_hits) == 1:
        return SiteResolution(
            status="matched",
            query=q,
            match=exact_hits[0],
            reason="exact title match",
        )
    if len(exact_hits) > 1:
        candidates = [
            ScoredCandidate(
                id=r.get("id", ""),
                title=r.get("title", ""),
                address=extract_address_from_record(r) or "",
                score=EXACT_MATCH_SCORE,
            )
            for r in exact_hits[:TOP_N_LIMIT]
        ]
        return SiteResolution(
            status="ambiguous",
            query=q,
            candidates=candidates,
            reason=f"{len(exact_hits)} records share this exact title",
        )

    haystacks = [(_haystack(r, extract_address_from_record), r) for r in site_records]
    scored: list[ScoredCandidate] = []
    for haystack, record in haystacks:
        if not haystack:
            continue
        score = fuzz.token_set_ratio(q, haystack)
        if score >= TOP_N_FLOOR:
            scored.append(ScoredCandidate(
                id=record.get("id", ""),
                title=record.get("title", ""),
                address=extract_address_from_record(record) or "",
                score=float(score),
            ))

    scored.sort(key=lambda c: c.score, reverse=True)

    if not scored:
        below_threshold: list[ScoredCandidate] = []
        for haystack, record in haystacks:
            if not haystack:
                continue
            below_threshold.append(ScoredCandidate(
                id=record.get("id", ""),
                title=record.get("title", ""),
                address=extract_address_from_record(record) or "",
                score=float(fuzz.token_set_ratio(q, haystack)),
            ))
        below_threshold.sort(key=lambda c: c.score, reverse=True)
        return SiteResolution(
            status="not_found",
            query=q,
            candidates=below_threshold[:3],
            reason="no candidate scored above match threshold",
        )

    top = scored[0]
    if len(scored) == 1:
        return SiteResolution(
            status="matched",
            query=q,
            match=_record_by_id(site_records, top.id),
            reason=f"single candidate (score={top.score:.0f})",
            candidates=[top],
        )

    second = scored[1]
    gap = top.score - second.score

    if top.score >= HIGH_CONFIDENCE_SCORE and gap >= CONFIDENT_LEAD:
        return SiteResolution(
            status="matched",
            query=q,
            match=_record_by_id(site_records, top.id),
            reason=f"clear winner (score={top.score:.0f}, lead={gap:.0f})",
            candidates=scored[:TOP_N_LIMIT],
        )

    if gap <= AMBIGUOUS_GAP:
        if os.getenv("OPENAI_API_KEY"):
            llm_candidates = [_record_by_id(site_records, c.id) for c in scored[:TOP_N_LIMIT]]
            llm_candidates = [r for r in llm_candidates if r is not None]
            try:
                llm_pick = _llm_tiebreak(query=q, site_records=llm_candidates)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("LLM tiebreak failed: %s", exc)
                llm_pick = None
            if llm_pick is not None:
                return SiteResolution(
                    status="matched",
                    query=q,
                    match=llm_pick,
                    reason=f"LLM tiebreak among {len(scored[:TOP_N_LIMIT])} close candidates",
                    candidates=scored[:TOP_N_LIMIT],
                )
        return SiteResolution(
            status="ambiguous",
            query=q,
            candidates=scored[:TOP_N_LIMIT],
            reason=f"top {min(len(scored), TOP_N_LIMIT)} candidates within {AMBIGUOUS_GAP} points",
        )

    return SiteResolution(
        status="matched",
        query=q,
        match=_record_by_id(site_records, top.id),
        reason=f"leading candidate (score={top.score:.0f}, lead={gap:.0f})",
        candidates=scored[:TOP_N_LIMIT],
    )


def _record_by_id(records: list[dict[str, Any]], record_id: str) -> dict[str, Any] | None:
    for r in records:
        if r.get("id") == record_id:
            return r
    return None
