"""Tests for the DD write log and daily digest."""

from __future__ import annotations

import json
from unittest.mock import patch

from due_diligence_reporter.dd_write_digest import (
    build_dd_write_digest,
    build_dd_write_event,
    collect_dd_write_events,
    record_dd_write_event,
)


def test_build_dd_write_event_shape() -> None:
    event = build_dd_write_event(
        site_id=" SITE1 ",
        status="proposal_submitted",
        fields={"foCapacity": 36, "maxCapCapacity": 54},
        field_sources={"foCapacity": "Alpha Capacity Analysis"},
        review_url="https://locationos.example/review",
        run_source="M2 Direct DD Events",
        created_at="2026-07-08T18:00:00+00:00",
    )

    assert event["site_id"] == "SITE1"
    assert event["status"] == "proposal_submitted"
    assert json.loads(event["fields"]) == {"foCapacity": "36", "maxCapCapacity": "54"}
    assert json.loads(event["field_sources"]) == {
        "foCapacity": "Alpha Capacity Analysis"
    }
    assert event["review_url"] == "https://locationos.example/review"
    assert event["run_source"] == "M2 Direct DD Events"
    assert event["created_at"] == "2026-07-08T18:00:00+00:00"


def test_record_and_collect_via_json_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DD_WRITE_LOG_FIRESTORE_PROJECT_ID", raising=False)
    monkeypatch.delenv("M2_DD_STATE_FIRESTORE_PROJECT_ID", raising=False)
    monkeypatch.delenv("DD_REPUBLISH_STATE_FIRESTORE_PROJECT_ID", raising=False)
    monkeypatch.setenv("DD_WRITE_LOG_FALLBACK_PATH", str(tmp_path / "log.json"))

    early = build_dd_write_event(
        site_id="SITE1",
        status="updated",
        fields={"foCapacity": 36},
        created_at="2026-07-07T09:00:00+00:00",
    )
    recent = build_dd_write_event(
        site_id="SITE2",
        status="proposal_submitted",
        fields={"buildingScore": 1},
        created_at="2026-07-08T09:00:00+00:00",
    )
    failed = build_dd_write_event(
        site_id="SITE3",
        status="failed",
        fields={"foCapacity": 1},
        created_at="2026-07-08T10:00:00+00:00",
    )
    for event in (early, recent, failed):
        record_dd_write_event(event)

    events = collect_dd_write_events(since_iso="2026-07-08T00:00:00+00:00")

    assert [event["site_id"] for event in events] == ["SITE2"]
    assert events[0]["status"] == "proposal_submitted"


def test_record_never_raises_when_everything_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DD_WRITE_LOG_FIRESTORE_PROJECT_ID", "proj")
    monkeypatch.setenv("DD_WRITE_LOG_FALLBACK_PATH", str(tmp_path / "log.json"))

    with patch(
        "due_diligence_reporter.dd_write_digest._save_event_to_firestore",
        side_effect=RuntimeError("firestore down"),
    ), patch(
        "due_diligence_reporter.dd_write_digest.alert_firestore_fallback"
    ) as mock_alert:
        record_dd_write_event(
            build_dd_write_event(site_id="SITE1", status="updated", fields={"a": 1})
        )

    mock_alert.assert_called_once()
    payload = json.loads((tmp_path / "log.json").read_text(encoding="utf-8"))
    assert len(payload) == 1


def test_build_dd_write_digest_renders_sites_and_statuses() -> None:
    events = [
        build_dd_write_event(
            site_id="SITE1",
            status="updated",
            fields={"foCapacity": 36},
            created_at="2026-07-08T09:00:00+00:00",
        ),
        build_dd_write_event(
            site_id="SITE2",
            status="proposal_submitted",
            fields={"buildingScore": 1},
            review_url="https://locationos.example/review",
            created_at="2026-07-08T10:00:00+00:00",
        ),
    ]

    digest = build_dd_write_digest(
        events,
        resolve_site_name=lambda site_id: {
            "SITE1": "Alpha Keller",
            "SITE2": "Alpha Miami Beach",
        }.get(site_id, ""),
        period_label="last 24 hours",
    )

    assert digest["event_count"] == 2
    assert digest["site_count"] == 2
    assert "1 field update(s) applied, 1 proposal(s) submitted" in digest["text"]
    assert "Alpha Keller" in digest["text"]
    assert "updated: foCapacity=36" in digest["text"]
    assert "submitted for approval: buildingScore=1" in digest["text"]
    assert "review: https://locationos.example/review" in digest["text"]
    assert "<h3>Alpha Miami Beach</h3>" in digest["html"]


def test_build_dd_write_digest_empty() -> None:
    digest = build_dd_write_digest([], period_label="last 24 hours")
    assert digest["event_count"] == 0
    assert "No DD field writes" in digest["text"]
    assert "no writes" in digest["subject"]


def test_digest_html_escapes_values_and_sanitizes_review_url() -> None:
    events = [
        build_dd_write_event(
            site_id="SITE1",
            status="updated",
            fields={"buildingComment": '<img src=x onerror=alert(1)> "quoted"'},
            review_url='javascript:alert(1)',
            created_at="2026-07-08T09:00:00+00:00",
        ),
    ]

    digest = build_dd_write_digest(
        events,
        resolve_site_name=lambda _sid: "<b>Alpha</b> Site",
        period_label="last 24 hours",
    )

    assert "<img" not in digest["html"]
    assert "&lt;img" in digest["html"]
    assert "<b>Alpha</b>" not in digest["html"]
    assert "&lt;b&gt;Alpha&lt;/b&gt;" in digest["html"]
    assert "javascript:" not in digest["html"]
    assert "javascript:" not in digest["text"]


def test_firestore_list_404_raises_misconfiguration(monkeypatch) -> None:
    from unittest.mock import MagicMock


    monkeypatch.setenv("DD_WRITE_LOG_FIRESTORE_PROJECT_ID", "proj")
    session = MagicMock()
    session.get.return_value = MagicMock(status_code=404)

    with patch(
        "due_diligence_reporter.dd_write_digest.build_authorized_session",
        return_value=session,
    ):
        try:
            collect_dd_write_events(since_iso="2026-07-08T00:00:00+00:00")
        except RuntimeError as exc:
            assert "404" in str(exc)
        else:
            raise AssertionError("expected RuntimeError on 404")
