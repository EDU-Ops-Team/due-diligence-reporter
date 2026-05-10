"""Tests for the get_site_record MCP tool's disambiguation payload shapes."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from due_diligence_reporter import server


ADDRESS_FIELD_ID = "IEAGN6I6JUAIKSH3"


def _record(record_id: str, title: str, address: str = "") -> dict[str, Any]:
    return {
        "id": record_id,
        "title": title,
        "customFields": [{"id": ADDRESS_FIELD_ID, "value": address}],
    }


def _run_get_site_record(query: str) -> dict[str, Any]:
    """Drive the async MCP tool to a result dict in the test event loop."""
    return asyncio.run(server.get_site_record(query))


@pytest.fixture(autouse=True)
def _disable_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


@pytest.fixture
def _stub_wrike(monkeypatch: pytest.MonkeyPatch):
    """Stub Wrike I/O so the tool can run without real credentials.

    Yields a closure to set the records returned by ``_get_all_site_records``.
    Patches both the compat shim ``find_site_record`` (used by the
    happy-path lookup) and ``_get_all_site_records`` (used by the
    disambiguation re-resolve).
    """
    monkeypatch.setattr(server, "load_wrike_config", lambda: object())

    state: dict[str, Any] = {"records": []}

    monkeypatch.setattr(server, "_get_all_site_records", lambda *, cfg: state["records"])
    monkeypatch.setattr(server, "enrich_custom_fields_with_names", lambda r: r)

    # ``find_site_record`` is the happy-path entry point in
    # ``_resolve_site_for_tool``. Mirror its behavior using ``resolve_site``
    # over the stubbed records so a clean match returns the dict and any
    # ambiguous/not_found case returns None — matching production semantics.
    def _stub_find_site_record(*, site_name_or_id: str, cfg: Any = None) -> dict[str, Any] | None:
        from due_diligence_reporter.site_matching import resolve_site as _resolve

        resolution = _resolve(site_name_or_id, site_records=state["records"])
        if resolution.status == "matched" and resolution.match is not None:
            return resolution.match
        return None

    monkeypatch.setattr(server, "find_site_record", _stub_find_site_record)

    # Build a minimal site summary that matches the production shape enough
    # for the success assertions in this test module.
    def _summary(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": record.get("id"),
            "title": record.get("title"),
            "stage": record.get("stage", ""),
            "address": "",
        }

    monkeypatch.setattr(server, "build_site_summary", _summary)

    yield state


def test_matched_payload_unchanged(_stub_wrike) -> None:
    _stub_wrike["records"] = [
        _record("A1", "Alpha Austin Demo"),
        _record("B2", "Beta Boston"),
    ]
    result = _run_get_site_record("Alpha Austin Demo")
    assert result["status"] == "success"
    assert result["site"]["id"] == "A1"
    assert "Found Site Record" in result["message"]


def test_ambiguous_payload_shape(_stub_wrike) -> None:
    _stub_wrike["records"] = [
        _record("A1", "Alpha"),
        _record("A2", "Alpha"),
    ]
    result = _run_get_site_record("Alpha")
    assert result["status"] == "ambiguous"
    assert result["error"] == "Multiple sites match"
    assert "Reply with the Wrike ID" in result["message"]
    assert result["query"] == "Alpha"
    assert isinstance(result["candidates"], list)
    assert len(result["candidates"]) == 2
    for candidate in result["candidates"]:
        assert set(candidate.keys()) == {"id", "title", "address", "score"}


def test_not_found_payload_shape(_stub_wrike) -> None:
    _stub_wrike["records"] = [
        _record("A1", "Alpha Austin Demo"),
        _record("B2", "Beta Boston"),
    ]
    result = _run_get_site_record("Zzz Bogus Site")
    assert result["status"] == "not_found"
    assert result["error"] == "No site found"
    assert "did_you_mean" in result
    assert isinstance(result["did_you_mean"], list)


def test_legacy_try_using_exact_message_is_gone(_stub_wrike) -> None:
    """Greg's friction: the old error told users to 'use the exact site name'."""
    _stub_wrike["records"] = []
    result = _run_get_site_record("anything")
    assert "Try using the exact site name" not in result.get("message", "")
