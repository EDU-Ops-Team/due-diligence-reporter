"""Unit tests for dashboard_aggregate.merge_payloads."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from due_diligence_reporter.dashboard_aggregate import (
    MANIFEST_SOURCE,
    CandidatePayload,
    merge_payloads,
    slug_from_filename,
)


def _candidate(
    slug: str,
    *,
    site_folder: str = "Site Folder",
    file_id: str = "f1",
    modified: str = "2026-04-22T10:00:00Z",
    payload: dict | None = None,
    override_payload_slug: str | None = ...,
) -> CandidatePayload:
    body = dict(payload) if payload is not None else {"slug": slug, "site_name": slug}
    if override_payload_slug is not ...:
        # Sentinel: allow tests to set/clear the inner slug explicitly.
        if override_payload_slug is None:
            body.pop("slug", None)
        else:
            body["slug"] = override_payload_slug
    return CandidatePayload(
        slug=slug,
        site_folder_name=site_folder,
        file_id=file_id,
        modified_time=modified,
        payload=body,
    )


class TestSlugFromFilename:
    def test_matches(self):
        assert slug_from_filename("palm-beach-gardens.dashboard.json") == "palm-beach-gardens"

    def test_rejects_non_matching(self):
        assert slug_from_filename("report.json") == ""
        assert slug_from_filename("palm-beach-gardens.json") == ""
        assert slug_from_filename("") == ""


class TestMergePayloads:
    def test_happy_path(self):
        cands = [
            _candidate("austin"),
            _candidate("miami-beach"),
            _candidate("greenwich"),
        ]
        result = merge_payloads(cands, generated_at=datetime(2026, 4, 22, 18, tzinfo=UTC))

        assert result.manifest["site_count"] == 3
        assert result.manifest["source"] == MANIFEST_SOURCE
        # Sorted by slug, deterministic
        slugs = [s["slug"] for s in result.manifest["sites"]]
        assert slugs == ["austin", "greenwich", "miami-beach"]
        assert result.duplicates_resolved == []
        assert result.skipped_invalid == []

    def test_generated_at_defaults_to_now(self):
        result = merge_payloads([_candidate("austin")])
        parsed = datetime.fromisoformat(result.manifest["generated_at"])
        # Just verify it's tz-aware and recent
        assert parsed.utcoffset() is not None
        assert (datetime.now(UTC) - parsed).total_seconds() < 5

    def test_manifest_bytes_are_valid_json(self):
        result = merge_payloads([_candidate("austin")])
        decoded = json.loads(result.manifest_bytes.decode("utf-8"))
        assert decoded["sites"][0]["slug"] == "austin"

    def test_duplicate_slug_newer_wins(self):
        older = _candidate(
            "austin", site_folder="Austin (stale)", file_id="old",
            modified="2026-04-20T10:00:00Z",
        )
        newer = _candidate(
            "austin", site_folder="Austin", file_id="new",
            modified="2026-04-22T10:00:00Z",
        )
        # Order: older first
        result = merge_payloads([older, newer])
        assert result.manifest["site_count"] == 1
        assert result.kept_by_slug["austin"].file_id == "new"
        assert len(result.duplicates_resolved) == 1
        assert "kept Austin/new" in result.duplicates_resolved[0]
        assert "dropped Austin (stale)/old" in result.duplicates_resolved[0]

    def test_duplicate_slug_older_stays_when_listed_second(self):
        """The 'incumbent' (first seen) keeps winning if it's actually newer."""
        newer_first = _candidate(
            "austin", site_folder="Austin", file_id="new",
            modified="2026-04-22T10:00:00Z",
        )
        older_second = _candidate(
            "austin", site_folder="Austin (stale)", file_id="old",
            modified="2026-04-20T10:00:00Z",
        )
        result = merge_payloads([newer_first, older_second])
        assert result.kept_by_slug["austin"].file_id == "new"
        assert len(result.duplicates_resolved) == 1

    def test_duplicate_slug_missing_timestamp_loses(self):
        """A candidate with no modified_time must not clobber one that has it."""
        has_ts = _candidate(
            "austin", file_id="ts", modified="2026-04-22T10:00:00Z",
        )
        no_ts = _candidate("austin", file_id="nots", modified="")
        # Regardless of input order, the timestamped one wins.
        r1 = merge_payloads([has_ts, no_ts])
        assert r1.kept_by_slug["austin"].file_id == "ts"
        r2 = merge_payloads([no_ts, has_ts])
        assert r2.kept_by_slug["austin"].file_id == "ts"

    def test_duplicate_slug_both_missing_timestamp_later_wins(self):
        """Tie-break falls back to list order so the result is deterministic."""
        first = _candidate("austin", file_id="first", modified="")
        second = _candidate("austin", file_id="second", modified="")
        result = merge_payloads([first, second])
        assert result.kept_by_slug["austin"].file_id == "second"

    def test_inner_slug_overrides_filename_slug(self):
        """The payload's 'slug' field is the canonical key; filename is a fallback."""
        c = _candidate(
            slug="wrong-from-filename",
            override_payload_slug="correct-from-payload",
        )
        result = merge_payloads([c])
        assert list(result.kept_by_slug.keys()) == ["correct-from-payload"]

    def test_invalid_payload_skipped(self):
        bad = CandidatePayload(
            slug="whatever", site_folder_name="Bad", file_id="b1",
            modified_time="", payload=None,  # type: ignore[arg-type]
        )
        good = _candidate("austin")
        result = merge_payloads([bad, good])
        assert result.manifest["site_count"] == 1
        assert len(result.skipped_invalid) == 1
        assert "Bad" in result.skipped_invalid[0]

    def test_missing_slug_everywhere_skipped(self):
        c = CandidatePayload(
            slug="", site_folder_name="Site", file_id="s1",
            modified_time="", payload={"site_name": "Austin"},  # no slug anywhere
        )
        result = merge_payloads([c])
        assert result.manifest["site_count"] == 0
        assert len(result.skipped_invalid) == 1

    def test_empty_input(self):
        result = merge_payloads([])
        assert result.manifest["site_count"] == 0
        assert result.manifest["sites"] == []
        assert result.duplicates_resolved == []
        assert result.skipped_invalid == []

    def test_bytes_are_deterministic_for_same_input_and_timestamp(self):
        t = datetime(2026, 4, 22, 18, tzinfo=UTC)
        cands = [_candidate("austin"), _candidate("miami-beach")]
        r1 = merge_payloads(cands, generated_at=t)
        r2 = merge_payloads(list(reversed(cands)), generated_at=t)
        # Sorted by slug internally, so input order doesn't matter.
        assert r1.manifest_bytes == r2.manifest_bytes
