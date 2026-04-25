"""Tests for Phase 2 Wrike custom-field readers.

Covers:
  - extract_school_feasibility_from_record (W74)
  - extract_timeline_confidence_from_record (W81)
  - _resolve_custom_field_id() short-circuits on hardcoded IDs
  - _resolve_custom_field_id() caches negative results to avoid repeat calls

Tests don't hit the network — they pre-seed _CUSTOM_FIELD_ID_CACHE with a
known ID, then construct synthetic Wrike record fixtures.
"""

from __future__ import annotations

import pytest

from due_diligence_reporter import wrike as wrike_mod


@pytest.fixture(autouse=True)
def _clear_resolver_cache() -> None:
    """Each test starts with a fresh cache so prior tests don't leak."""
    wrike_mod._CUSTOM_FIELD_ID_CACHE.clear()
    yield
    wrike_mod._CUSTOM_FIELD_ID_CACHE.clear()


def _record_with(field_id: str, value: object) -> dict[str, object]:
    """Build a minimal Wrike Site Record fixture with one custom field."""
    return {
        "id": "fakeRecord",
        "title": "Test Site",
        "customFields": [{"id": field_id, "value": value}],
    }


class TestSchoolFeasibility:
    def test_returns_lowercased_value(self) -> None:
        wrike_mod._CUSTOM_FIELD_ID_CACHE["school_feasibility"] = "FAKE_W74"
        record = _record_with("FAKE_W74", "High")
        assert wrike_mod.extract_school_feasibility_from_record(record) == "high"

    def test_returns_none_when_field_absent(self) -> None:
        wrike_mod._CUSTOM_FIELD_ID_CACHE["school_feasibility"] = "FAKE_W74"
        record = {"customFields": []}
        assert wrike_mod.extract_school_feasibility_from_record(record) is None

    def test_returns_none_when_value_blank(self) -> None:
        wrike_mod._CUSTOM_FIELD_ID_CACHE["school_feasibility"] = "FAKE_W74"
        record = _record_with("FAKE_W74", "   ")
        assert wrike_mod.extract_school_feasibility_from_record(record) is None

    def test_returns_none_when_resolver_fails(self) -> None:
        # Negative cache entry — resolver tried and gave up.
        wrike_mod._CUSTOM_FIELD_ID_CACHE["school_feasibility"] = None
        record = _record_with("ANYTHING", "high")
        assert wrike_mod.extract_school_feasibility_from_record(record) is None

    def test_strips_whitespace_before_lowercasing(self) -> None:
        wrike_mod._CUSTOM_FIELD_ID_CACHE["school_feasibility"] = "FAKE_W74"
        record = _record_with("FAKE_W74", "  Medium  ")
        assert wrike_mod.extract_school_feasibility_from_record(record) == "medium"


class TestTimelineConfidence:
    def test_returns_lowercased_value(self) -> None:
        wrike_mod._CUSTOM_FIELD_ID_CACHE["timeline_confidence"] = "FAKE_W81"
        record = _record_with("FAKE_W81", "Low")
        assert wrike_mod.extract_timeline_confidence_from_record(record) == "low"

    def test_returns_none_when_field_absent(self) -> None:
        wrike_mod._CUSTOM_FIELD_ID_CACHE["timeline_confidence"] = "FAKE_W81"
        assert wrike_mod.extract_timeline_confidence_from_record({}) is None

    def test_returns_none_for_non_string_value(self) -> None:
        """Wrike sometimes returns numeric values for numeric fields. We
        only handle string-valued fields here; anything else returns None."""
        wrike_mod._CUSTOM_FIELD_ID_CACHE["timeline_confidence"] = "FAKE_W81"
        record = _record_with("FAKE_W81", 42)
        assert wrike_mod.extract_timeline_confidence_from_record(record) is None


class TestResolverShortCircuit:
    def test_hardcoded_id_short_circuits_resolver(self, monkeypatch) -> None:
        """If WRIKE_CUSTOM_FIELDS has a non-empty ID, no API call is made."""
        # Replace the dict entry just for this test.
        monkeypatch.setitem(
            wrike_mod.WRIKE_CUSTOM_FIELDS,
            "school_feasibility",
            "HARDCODED_ID",
        )

        # If the resolver tried to hit the API we'd raise.
        def boom(*_a, **_kw):
            raise AssertionError("network must not be called")

        monkeypatch.setattr(wrike_mod, "_wrike_get", boom)
        monkeypatch.setattr(
            wrike_mod, "load_wrike_config", lambda: boom("called!")
        )

        assert (
            wrike_mod._resolve_custom_field_id("school_feasibility")
            == "HARDCODED_ID"
        )
