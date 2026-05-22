"""SIR review queue helpers built from local pipeline manifests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .pipeline_manifest import RUN_MANIFEST_DIR

READY_STATUS = "ready_for_review"
WAITING_STATUSES = ("waiting_for_cds_sir", "waiting_for_ai_sir")
QUEUE_STATUSES = (READY_STATUS, *WAITING_STATUSES)


@dataclass(frozen=True)
class SirReviewQueueItem:
    """One site/run that has SIR learning-review metadata."""

    run_id: str
    site_title: str
    status: str
    reason: str
    started_at: str
    manifest_path: str
    ai_sir_name: str
    ai_sir_file_id: str
    ai_sir_uri: str
    cds_sir_name: str
    cds_sir_file_id: str
    cds_sir_uri: str
    reviewed: bool


def load_sir_review_queue(
    *,
    manifest_dir: Path | None = None,
    outcomes: list[dict[str, Any]] | None = None,
    statuses: tuple[str, ...] = (READY_STATUS,),
    include_reviewed: bool = False,
    limit: int | None = None,
) -> list[SirReviewQueueItem]:
    """Load SIR review candidates from pipeline manifests."""
    base = manifest_dir or RUN_MANIFEST_DIR
    if not base.exists():
        return []

    review_outcomes = outcomes or []
    items = [
        item
        for path in base.glob("*.json")
        if (item := _item_from_manifest_path(path, review_outcomes)) is not None
        and item.status in statuses
    ]
    deduped = _dedupe_latest(items)
    if not include_reviewed:
        deduped = [item for item in deduped if not item.reviewed]
    ordered = sorted(deduped, key=_queue_sort_key, reverse=True)
    return ordered[:limit] if limit is not None else ordered


def _item_from_manifest_path(
    path: Path,
    outcomes: list[dict[str, Any]],
) -> SirReviewQueueItem | None:
    try:
        payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None
    review = payload.get("sir_learning_review")
    if not isinstance(review, dict):
        return None
    status = str(review.get("status") or "").strip()
    if not status:
        return None
    ai_sir = _candidate(review.get("ai_sir"))
    cds_sir = _candidate(review.get("cds_sir"))
    item = SirReviewQueueItem(
        run_id=str(payload.get("run_id") or path.stem),
        site_title=str(payload.get("site_title") or "(unknown site)"),
        status=status,
        reason=str(review.get("reason") or ""),
        started_at=str(payload.get("started_at") or ""),
        manifest_path=str(path),
        ai_sir_name=ai_sir["name"],
        ai_sir_file_id=ai_sir["file_id"],
        ai_sir_uri=ai_sir["uri"],
        cds_sir_name=cds_sir["name"],
        cds_sir_file_id=cds_sir["file_id"],
        cds_sir_uri=cds_sir["uri"],
        reviewed=False,
    )
    return _mark_reviewed(item, outcomes)


def _candidate(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {"name": "", "file_id": "", "uri": ""}
    return {
        "name": str(value.get("name") or ""),
        "file_id": str(value.get("file_id") or ""),
        "uri": str(value.get("uri") or ""),
    }


def _mark_reviewed(
    item: SirReviewQueueItem,
    outcomes: list[dict[str, Any]],
) -> SirReviewQueueItem:
    reviewed = any(_matches_outcome(item, outcome) for outcome in outcomes)
    return SirReviewQueueItem(
        run_id=item.run_id,
        site_title=item.site_title,
        status=item.status,
        reason=item.reason,
        started_at=item.started_at,
        manifest_path=item.manifest_path,
        ai_sir_name=item.ai_sir_name,
        ai_sir_file_id=item.ai_sir_file_id,
        ai_sir_uri=item.ai_sir_uri,
        cds_sir_name=item.cds_sir_name,
        cds_sir_file_id=item.cds_sir_file_id,
        cds_sir_uri=item.cds_sir_uri,
        reviewed=reviewed,
    )


def _matches_outcome(item: SirReviewQueueItem, outcome: dict[str, Any]) -> bool:
    ai_value = _normalize(str(outcome.get("ai_sir") or ""))
    cds_value = _normalize(str(outcome.get("cds_sir") or ""))
    site_value = _normalize(str(outcome.get("site") or ""))
    ai_matches = not ai_value or ai_value in _sir_keys(
        item.ai_sir_name,
        item.ai_sir_file_id,
        item.ai_sir_uri,
    )
    cds_matches = not cds_value or cds_value in _sir_keys(
        item.cds_sir_name,
        item.cds_sir_file_id,
        item.cds_sir_uri,
    )
    site_matches = not site_value or site_value == _normalize(item.site_title)
    has_match_value = bool(ai_value or cds_value or site_value)
    return site_matches and ai_matches and cds_matches and has_match_value


def _sir_keys(*values: str) -> set[str]:
    return {_normalize(value) for value in values if _normalize(value)}


def _dedupe_latest(items: list[SirReviewQueueItem]) -> list[SirReviewQueueItem]:
    latest: dict[tuple[str, str, str, str], SirReviewQueueItem] = {}
    for item in items:
        key = (
            _normalize(item.status),
            _normalize(item.site_title),
            _normalize(item.ai_sir_file_id or item.ai_sir_name),
            _normalize(item.cds_sir_file_id or item.cds_sir_name),
        )
        existing = latest.get(key)
        if existing is None or _parse_timestamp(item.started_at) > _parse_timestamp(
            existing.started_at
        ):
            latest[key] = item
    return list(latest.values())


def _queue_sort_key(item: SirReviewQueueItem) -> tuple[datetime, str]:
    return (_parse_timestamp(item.started_at), item.site_title)


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())
