"""Shared Rhodes note and Google Chat helpers for AutomationEvent writes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .automation_event import AutomationEvent, render_automation_event_note
from .rhodes import add_rhodes_site_note
from .utils import post_google_chat_message

AddRhodesSiteNote = Callable[..., dict[str, Any]]
PostGoogleChatMessage = Callable[[str, str], None]


def record_rhodes_automation_event(
    event: AutomationEvent,
    *,
    owner_user_id: str = "",
    owner_email: str = "",
    body: str | None = None,
    add_note: AddRhodesSiteNote = add_rhodes_site_note,
) -> tuple[dict[str, Any], str]:
    """Write an AutomationEvent to Rhodes and return normalized status plus body."""

    rendered_body = body if body is not None else render_automation_event_note(event)
    if event.site_id:
        note_result = add_note(
            site_id=event.site_id,
            body=rendered_body,
            owner_user_id=owner_user_id,
            owner_email=owner_email,
        )
    else:
        note_result = {
            "status": "skipped",
            "reason": "missing_site_id",
            "owner_notification": "none",
        }

    return (
        {
            "event_type": event.event_type,
            "source_id": event.source_id,
            "decision_required": event.decision_required,
            **note_result,
        },
        rendered_body,
    )


def should_alert_google_chat(
    event_status: dict[str, Any],
    *,
    decision_required: bool = True,
) -> bool:
    """Return True when Rhodes did not notify an owner and Chat fallback is needed."""

    if not decision_required:
        return False
    return event_status.get("status") != "created" or (
        event_status.get("owner_notification") != "mentioned"
    )


def post_google_chat_to_configured_webhooks(
    webhook_urls: str,
    text: str,
    *,
    post_message: PostGoogleChatMessage = post_google_chat_message,
) -> dict[str, Any]:
    """Post text to one or more comma-separated Google Chat webhooks."""

    urls = [url.strip() for url in webhook_urls.split(",") if url.strip()]
    if not urls:
        return {"status": "skipped", "reason": "missing_google_chat_webhook_url"}

    posted = 0
    errors: list[str] = []
    for url in urls:
        try:
            post_message(url, text)
            posted += 1
        except Exception as exc:  # noqa: BLE001 - non-fatal notification side effect
            errors.append(str(exc))

    if errors:
        return {
            "status": "failed" if posted == 0 else "partial",
            "posted": posted,
            "errors": errors,
            "error_count": len(errors),
        }
    return {"status": "sent", "posted": posted}
