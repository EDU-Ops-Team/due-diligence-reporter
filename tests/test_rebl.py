from __future__ import annotations

import pytest

from due_diligence_reporter.rebl import ReblResolution, resolve_address, resolve_addresses
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
