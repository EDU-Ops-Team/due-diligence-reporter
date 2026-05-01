from __future__ import annotations

import pytest

from due_diligence_reporter.rebl import (
    ReblResolution,
    canonical_slug_for_address,
    canonical_slugs_for_addresses,
    resolve_address,
    resolve_addresses,
)
from due_diligence_reporter.server import _build_report_trace_data


class _FakeResponse:
    def __init__(self, *, ok: bool, status_code: int, body, text: str = "") -> None:
        self.ok = ok
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        return self._body


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, *, json, headers, timeout: float):
        self.calls.append({
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        })
        return self.response


class TestResolveAddresses:
    def test_batches_addresses_and_preserves_order(self) -> None:
        session = _FakeSession(_FakeResponse(
            ok=True,
            status_code=200,
            body=[
                {"site_id": "first-site", "url": "https://rebl3.vercel.app/site/first-site", "matched_by": "slug"},
                {"site_id": "", "url": "", "matched_by": "none"},
            ],
        ))

        result = resolve_addresses(
            ["123 Main St, Austin, TX", "500 Elm St, Dallas, TX"],
            session=session,
        )

        assert [item.site_id for item in result] == ["first-site", ""]
        assert result[0].resolution_status == "resolved"
        assert result[1].resolution_status == "not_found"
        assert session.calls[0]["json"] == [
            {"address": "123 Main St, Austin, TX"},
            {"address": "500 Elm St, Dallas, TX"},
        ]

    def test_empty_address_short_circuits(self) -> None:
        result = resolve_address("")
        assert result == ReblResolution.missing_address()

    def test_http_error_raises(self) -> None:
        session = _FakeSession(_FakeResponse(
            ok=False,
            status_code=503,
            body={"error": "down"},
            text="service unavailable",
        ))

        with pytest.raises(RuntimeError, match="REBL resolve 503"):
            resolve_addresses(["123 Main St, Austin, TX"], session=session)


class TestCanonicalSlugForAddress:
    """Convenience wrapper used by the publisher and downstream callers.

    Returns the Rebl ``site_id`` for resolved addresses; gracefully falls
    back to a caller-supplied default for empty inputs, network failures,
    or empty Rebl responses — never raises.
    """

    def test_returns_site_id_when_resolved(self) -> None:
        session = _FakeSession(_FakeResponse(
            ok=True,
            status_code=200,
            body=[{"site_id": "123-main-st-austin-tx", "url": "", "matched_by": "slug"}],
        ))
        slug = canonical_slug_for_address(
            "123 Main St, Austin, TX",
            fallback="austin",
            session=session,
        )
        assert slug == "123-main-st-austin-tx"

    def test_returns_fallback_when_address_empty(self) -> None:
        # No HTTP call should be attempted.
        session = _FakeSession(_FakeResponse(ok=True, status_code=200, body=[]))
        slug = canonical_slug_for_address("   ", fallback="austin", session=session)
        assert slug == "austin"
        assert session.calls == []

    def test_returns_fallback_when_rebl_returns_empty_site_id(self) -> None:
        session = _FakeSession(_FakeResponse(
            ok=True,
            status_code=200,
            body=[{"site_id": "", "url": "", "matched_by": "none"}],
        ))
        slug = canonical_slug_for_address(
            "unknown address",
            fallback="my-fallback",
            session=session,
        )
        assert slug == "my-fallback"

    def test_returns_fallback_on_network_error(self) -> None:
        # Resolver raises (HTTP 5xx etc.) — must not propagate.
        class _RaisingSession:
            def post(self, *args, **kwargs):
                raise RuntimeError("network down")

        slug = canonical_slug_for_address(
            "123 Main St, Austin, TX",
            fallback="austin",
            session=_RaisingSession(),
        )
        assert slug == "austin"


class TestCanonicalSlugsForAddresses:
    """Batch variant used by reconcile_dashboard for many sites at once."""

    def test_returns_only_resolved_addresses(self) -> None:
        session = _FakeSession(_FakeResponse(
            ok=True,
            status_code=200,
            body=[
                {"site_id": "123-main-st-austin-tx", "url": "", "matched_by": "slug"},
                {"site_id": "", "url": "", "matched_by": "none"},
            ],
        ))
        result = canonical_slugs_for_addresses(
            ["123 Main St, Austin, TX", "500 Unknown Way"],
            session=session,
        )
        # Empty site_id is dropped; only resolved entries are returned.
        assert result == {"123 Main St, Austin, TX": "123-main-st-austin-tx"}

    def test_empty_input_returns_empty_dict(self) -> None:
        assert canonical_slugs_for_addresses([]) == {}
        assert canonical_slugs_for_addresses(["", "   "]) == {}

    def test_returns_empty_on_network_error(self) -> None:
        class _RaisingSession:
            def post(self, *args, **kwargs):
                raise RuntimeError("network down")

        result = canonical_slugs_for_addresses(
            ["123 Main St, Austin, TX"],
            session=_RaisingSession(),
        )
        assert result == {}


class TestReportTraceRebl:
    def test_build_report_trace_includes_rebl_block(self) -> None:
        rebl = ReblResolution(
            address_submitted="123 Main St, Austin, TX",
            resolution_status="resolved",
            site_id="123-main-st-austin-tx",
            url="https://rebl3.vercel.app/site/123-main-st-austin-tx",
            matched_by="slug",
            scored=True,
        )

        trace = _build_report_trace_data(
            site_name="Austin",
            report_date="04/23/2026",
            doc_id="doc123",
            doc_url="https://docs.google.com/document/d/doc123",
            replacements={"meta.site_name": "Austin"},
            unfilled=["meta.rebl_site_id"],
            unmatched=[],
            hyperlink_trace={"applied": 0},
            token_evidence=None,
            rebl_resolution=rebl,
        )

        assert trace["rebl"]["site_id"] == "123-main-st-austin-tx"
        assert trace["rebl"]["url"] == "https://rebl3.vercel.app/site/123-main-st-austin-tx"
        assert trace["rebl"]["resolution_status"] == "resolved"
