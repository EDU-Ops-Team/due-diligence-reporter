from __future__ import annotations

from typing import Any

from due_diligence_reporter.automation_event import AutomationEvent
from due_diligence_reporter.rhodes_events import (
    post_google_chat_to_configured_webhooks,
    record_rhodes_automation_event,
    should_alert_google_chat,
)


def _event(*, site_id: str = "SITE1", decision_required: bool = True) -> AutomationEvent:
    return AutomationEvent(
        source_system="due-diligence-reporter",
        source_id="run-1",
        site_id=site_id,
        site_name="Alpha Test",
        event_type="test_event",
        decision_required=decision_required,
        created_at="2026-05-28T12:00:00+00:00",
    )


def test_record_rhodes_automation_event_writes_note_with_owner_context() -> None:
    calls: list[dict[str, Any]] = []

    def add_note(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "status": "created",
            "rhodes_note_id": "NOTE1",
            "owner_notification": "mentioned",
        }

    result, body = record_rhodes_automation_event(
        _event(),
        owner_user_id="USER1",
        owner_email="owner@example.com",
        add_note=add_note,
    )

    assert result == {
        "event_type": "test_event",
        "source_id": "run-1",
        "decision_required": True,
        "status": "created",
        "rhodes_note_id": "NOTE1",
        "owner_notification": "mentioned",
    }
    assert calls == [
        {
            "site_id": "SITE1",
            "body": body,
            "owner_user_id": "USER1",
            "owner_email": "owner@example.com",
        }
    ]
    assert "AutomationEvent v1" in body
    assert "Kind: test_event" in body


def test_record_rhodes_automation_event_passes_extra_mentions() -> None:
    calls: list[dict[str, Any]] = []

    def add_note(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "status": "created",
            "rhodes_note_id": "NOTE1",
            "owner_notification": "mentioned",
        }

    record_rhodes_automation_event(
        _event(),
        owner_user_id="USER1",
        extra_mention_user_ids=["USER2", " ", "USER3"],
        add_note=add_note,
    )

    assert calls[0]["extra_mention_user_ids"] == ["USER2", "USER3"]


def test_record_rhodes_automation_event_skips_missing_site_id() -> None:
    def add_note(**_: Any) -> dict[str, Any]:
        raise AssertionError("add_note should not be called without a site ID")

    result, body = record_rhodes_automation_event(_event(site_id=""), add_note=add_note)

    assert result["status"] == "skipped"
    assert result["reason"] == "missing_site_id"
    assert result["owner_notification"] == "none"
    assert "Site ID: unknown" in body


def test_should_alert_google_chat_requires_decision_and_owner_notification() -> None:
    assert not should_alert_google_chat(
        {
            "status": "created",
            "owner_notification": "mentioned",
            "rhodes_note_id": "NOTE1",
        }
    )
    assert should_alert_google_chat(
        {
            "status": "created",
            "owner_notification": "mentioned",
            "rhodes_note_id": "",
        }
    )
    assert should_alert_google_chat({"status": "failed", "owner_notification": "none"})
    assert should_alert_google_chat(
        {"status": "created", "owner_notification": "none"},
        decision_required=False,
    ) is False


def test_post_google_chat_to_configured_webhooks_reports_partial_failure() -> None:
    calls: list[tuple[str, str]] = []

    def post_message(url: str, text: str) -> None:
        calls.append((url, text))
        if "bad" in url:
            raise RuntimeError("chat down")

    result = post_google_chat_to_configured_webhooks(
        "https://chat.example/good, https://chat.example/bad",
        "event body",
        post_message=post_message,
    )

    assert result == {
        "status": "partial",
        "posted": 1,
        "errors": ["chat down"],
        "error_count": 1,
    }
    assert calls == [
        ("https://chat.example/good", "event body"),
        ("https://chat.example/bad", "event body"),
    ]


def test_post_google_chat_to_configured_webhooks_skips_missing_config() -> None:
    assert post_google_chat_to_configured_webhooks(" ", "event body") == {
        "status": "skipped",
        "reason": "missing_google_chat_webhook_url",
    }
