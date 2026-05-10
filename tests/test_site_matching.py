"""Unit tests for fuzzy site matching cascade in site_matching.resolve_site."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from due_diligence_reporter.site_matching import (
    AMBIGUOUS_GAP,
    HIGH_CONFIDENCE_SCORE,
    SiteResolution,
    resolve_site,
)

ADDRESS_FIELD_ID = "IEAGN6I6JUAIKSH3"


def _record(record_id: str, title: str, address: str = "") -> dict[str, Any]:
    return {
        "id": record_id,
        "title": title,
        "customFields": [{"id": ADDRESS_FIELD_ID, "value": address}],
    }


@pytest.fixture(autouse=True)
def _disable_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests should never hit the LLM tiebreaker; clear OPENAI_API_KEY."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_exact_title_match() -> None:
    records = [
        _record("A1", "Alpha Austin Demo", "100 Main St, Austin TX"),
        _record("B2", "Beta Boston", "200 Beacon St, Boston MA"),
    ]
    resolution = resolve_site("Alpha Austin Demo", site_records=records)
    assert resolution.status == "matched"
    assert resolution.match is not None
    assert resolution.match["id"] == "A1"
    assert "exact" in resolution.reason.lower()


def test_exact_title_match_case_insensitive_and_trimmed() -> None:
    records = [_record("A1", "Alpha Austin Demo")]
    resolution = resolve_site("  alpha austin demo  ", site_records=records)
    assert resolution.status == "matched"
    assert resolution.match is not None
    assert resolution.match["id"] == "A1"


def test_exact_title_collision() -> None:
    records = [
        _record("A1", "Alpha", "100 Main St"),
        _record("A2", "Alpha", "200 Other Ave"),
        _record("B1", "Beta", "300 Test Rd"),
    ]
    resolution = resolve_site("Alpha", site_records=records)
    assert resolution.status == "ambiguous"
    assert {c.id for c in resolution.candidates} == {"A1", "A2"}
    assert "exact title" in resolution.reason.lower()


def test_token_set_ratio_typo() -> None:
    records = [
        _record("A1", "Alpha Austin Demo", "100 Main St"),
        _record("B2", "Beta Boston", "200 Beacon"),
    ]
    resolution = resolve_site("Alpha Austn Demo", site_records=records)
    assert resolution.status == "matched"
    assert resolution.match is not None
    assert resolution.match["id"] == "A1"


def test_ambiguous_top_two_close() -> None:
    # Two near-twin records that should land within AMBIGUOUS_GAP without an
    # LLM available. Use fuzz scoring to guarantee proximity by sharing all
    # tokens but differing in a small detail.
    records = [
        _record("A1", "Alpha Austin Demo Site"),
        _record("A2", "Alpha Austin Demo Place"),
    ]
    resolution = resolve_site("Alpha Austin Demo", site_records=records)
    assert resolution.status == "ambiguous"
    assert len(resolution.candidates) == 2
    top, second = resolution.candidates
    assert top.score - second.score <= AMBIGUOUS_GAP


def test_clear_winner_high_score_high_lead() -> None:
    # Non-exact query (avoids the exact-match short-circuit) where one record
    # is a tight token-set match and the other shares no tokens at all.
    # token_set_ratio of "Alpha Austin Demo" against "Alpha Austin Demo
    # School Site" is 100 (query tokens are a subset); against the unrelated
    # record it's near zero.
    records = [
        _record("A1", "Alpha Austin Demo School Site", "100 Main St"),
        _record("B2", "Zeta Tampa Place", "Other address"),
    ]
    resolution = resolve_site("Alpha Austin Demo", site_records=records)
    assert resolution.status == "matched"
    assert resolution.match is not None
    assert resolution.match["id"] == "A1"
    assert resolution.candidates[0].score >= HIGH_CONFIDENCE_SCORE
    if len(resolution.candidates) > 1:
        gap = resolution.candidates[0].score - resolution.candidates[1].score
        assert gap >= 8


def test_not_found_with_did_you_mean() -> None:
    records = [
        _record("A1", "Alpha Austin Demo"),
        _record("B2", "Beta Boston"),
        _record("C3", "Gamma Galveston"),
    ]
    resolution = resolve_site("Zzz Bogus Site", site_records=records)
    assert resolution.status == "not_found"
    # did-you-mean payload includes top-3 below threshold.
    assert 1 <= len(resolution.candidates) <= 3


def test_empty_records() -> None:
    resolution = resolve_site("Anything", site_records=[])
    assert resolution.status == "not_found"
    assert "no site records" in resolution.reason.lower()


def test_empty_query() -> None:
    records = [_record("A1", "Alpha")]
    resolution = resolve_site("", site_records=records)
    assert resolution.status == "not_found"
    assert resolution.reason == "empty query"


def test_to_payload_shape() -> None:
    records = [
        _record("A1", "Alpha"),
        _record("A2", "Alpha"),
    ]
    resolution = resolve_site("Alpha", site_records=records)
    payload = resolution.to_payload()
    assert payload["status"] == "ambiguous"
    assert payload["query"] == "Alpha"
    assert isinstance(payload["candidates"], list)
    for candidate in payload["candidates"]:
        assert set(candidate.keys()) == {"id", "title", "address", "score"}


def test_address_in_haystack_helps_match() -> None:
    records = [
        _record("A1", "Site Number One", "1234 Magnolia Lane Austin TX 78701"),
        _record("B2", "Site Number Two", "9999 Other Way"),
    ]
    # Query is the address — token-set ratio against title+address should win.
    resolution = resolve_site("1234 Magnolia Lane Austin", site_records=records)
    assert resolution.status == "matched"
    assert resolution.match is not None
    assert resolution.match["id"] == "A1"


def test_llm_tiebreak_skipped_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Already cleared by autouse fixture; verify ambiguous path still fires.
    assert "OPENAI_API_KEY" not in os.environ
    records = [
        _record("A1", "Alpha Austin Demo Site"),
        _record("A2", "Alpha Austin Demo Place"),
    ]
    resolution = resolve_site("Alpha Austin Demo", site_records=records)
    assert resolution.status == "ambiguous"


def test_llm_tiebreak_invoked_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the LLM key is set and top two are close, _match_site_with_llm fires."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    records = [
        _record("A1", "Alpha Austin Demo Site"),
        _record("A2", "Alpha Austin Demo Place"),
    ]
    with patch("due_diligence_reporter.wrike._match_site_with_llm") as mocked:
        mocked.return_value = records[1]  # LLM picks A2.
        resolution: SiteResolution = resolve_site("Alpha Austin Demo", site_records=records)
    assert resolution.status == "matched"
    assert resolution.match is not None
    assert resolution.match["id"] == "A2"
    assert "tiebreak" in resolution.reason.lower()
    mocked.assert_called_once()


def test_llm_tiebreak_returning_none_falls_back_to_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    records = [
        _record("A1", "Alpha Austin Demo Site"),
        _record("A2", "Alpha Austin Demo Place"),
    ]
    with patch("due_diligence_reporter.wrike._match_site_with_llm", return_value=None):
        resolution = resolve_site("Alpha Austin Demo", site_records=records)
    assert resolution.status == "ambiguous"
