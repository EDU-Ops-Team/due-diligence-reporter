"""Aggregate per-site dashboard payloads into a single sites.json.

Walks the Drive "All Locations" tree, finds every ``*.dashboard.json``
published by ``dashboard_publish.publish_site_record``, and merges them
into a single dashboard-facing manifest.

The I/O layer (Drive walking, file download, GitHub push) lives in
``scripts/aggregate_dashboard.py``. This module is pure — it takes
already-loaded per-site records and returns the manifest bytes.
That keeps the merge logic trivially unit-testable and independent
of Drive/GitHub availability.

Manifest shape
--------------

The dashboard consumes a stable JSON shape::

    {
      "generated_at": "2026-04-22T18:30:00+00:00",
      "source": "due-diligence-reporter",
      "site_count": 12,
      "sites": [ <SiteRecord>, <SiteRecord>, ... ]
    }

Sites are sorted by slug for a deterministic diff across runs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

MANIFEST_SOURCE = "due-diligence-reporter"


@dataclass
class CandidatePayload:
    """One ``*.dashboard.json`` file as discovered by the walker."""

    slug: str                   # derived from filename, before the suffix
    site_folder_name: str       # name of the Drive subfolder it lives in
    file_id: str                # Drive file id, for debuggability
    modified_time: str          # ISO8601 from Drive (may be empty)
    payload: dict[str, Any]     # parsed JSON body

    def record_slug(self) -> str:
        """Slug as stored inside the payload itself — the canonical key.

        Falls back to the filename-derived slug if the payload is
        missing a slug field for any reason.
        """
        inner = self.payload.get("slug") if isinstance(self.payload, dict) else None
        if isinstance(inner, str) and inner.strip():
            return inner.strip()
        return self.slug


@dataclass
class MergeResult:
    manifest: dict[str, Any]
    manifest_bytes: bytes
    kept_by_slug: dict[str, CandidatePayload] = field(default_factory=dict)
    duplicates_resolved: list[str] = field(default_factory=list)
    skipped_invalid: list[str] = field(default_factory=list)


def _parse_modified(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        # Drive returns RFC3339 / ISO8601, e.g. "2026-04-22T18:30:00.000Z"
        normalized = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def merge_payloads(
    candidates: list[CandidatePayload],
    *,
    generated_at: datetime | None = None,
) -> MergeResult:
    """Produce the dashboard manifest from a list of candidate payloads.

    Duplicate slug resolution
    -------------------------
    If two candidates carry the same slug (e.g., a site folder and a
    stale copy elsewhere), the one with the newer ``modified_time``
    wins. If timestamps tie or are missing, the later entry in the
    input list wins — this matches Drive's own ``orderBy=name_natural``
    listing order so the result is deterministic.

    Invalid payloads
    ----------------
    Candidates without a usable slug, or whose payload is not a dict,
    are dropped and reported in ``MergeResult.skipped_invalid``.
    """
    generated_at = generated_at or datetime.now(UTC)
    kept: dict[str, CandidatePayload] = {}
    duplicates: list[str] = []
    skipped: list[str] = []

    for candidate in candidates:
        if not isinstance(candidate.payload, dict):
            skipped.append(f"{candidate.site_folder_name}/{candidate.slug} (non-dict payload)")
            continue
        slug = candidate.record_slug()
        if not slug:
            skipped.append(f"{candidate.site_folder_name}/{candidate.file_id} (missing slug)")
            continue

        incumbent = kept.get(slug)
        if incumbent is None:
            kept[slug] = candidate
            continue

        # Tiebreak by modified_time; later wins. Missing timestamps
        # are treated as older so explicit timestamps always win.
        new_ts = _parse_modified(candidate.modified_time)
        old_ts = _parse_modified(incumbent.modified_time)
        winner = candidate
        if old_ts is not None and (new_ts is None or new_ts < old_ts):
            winner = incumbent

        loser = incumbent if winner is candidate else candidate
        kept[slug] = winner
        duplicates.append(
            f"{slug}: kept {winner.site_folder_name}/{winner.file_id} "
            f"(modified={winner.modified_time or 'n/a'}), "
            f"dropped {loser.site_folder_name}/{loser.file_id} "
            f"(modified={loser.modified_time or 'n/a'})",
        )

    ordered = [kept[slug] for slug in sorted(kept.keys())]
    manifest = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "source": MANIFEST_SOURCE,
        "site_count": len(ordered),
        "sites": [c.payload for c in ordered],
    }
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")

    return MergeResult(
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        kept_by_slug=kept,
        duplicates_resolved=duplicates,
        skipped_invalid=skipped,
    )


def slug_from_filename(filename: str) -> str:
    """Extract the slug from a ``{slug}.dashboard.json`` filename.

    Returns an empty string if the filename does not match the pattern.
    """
    suffix = ".dashboard.json"
    if not filename.endswith(suffix):
        return ""
    return filename[: -len(suffix)]
