"""Tests for the Firestore fallback degradation alert."""

from __future__ import annotations

from unittest.mock import patch

from due_diligence_reporter import firestore_state
from due_diligence_reporter.firestore_state import alert_firestore_fallback


def _reset_dedupe() -> None:
    firestore_state._FALLBACK_ALERTS_SENT.clear()


def test_alert_posts_once_per_store_and_operation(monkeypatch) -> None:
    _reset_dedupe()
    monkeypatch.setenv("GOOGLE_CHAT_WEBHOOK_URL", "https://chat.example/webhook")

    with patch("due_diligence_reporter.utils.post_google_chat_message") as mock_post:
        alert_firestore_fallback("dd_republish_state", "save", RuntimeError("404 Not Found"))
        alert_firestore_fallback("dd_republish_state", "save", RuntimeError("404 Not Found"))
        alert_firestore_fallback("dd_republish_state", "load", RuntimeError("404 Not Found"))

    assert mock_post.call_count == 2
    message = mock_post.call_args_list[0].args[1]
    assert "dd_republish_state" in message
    assert "save" in message
    assert "404 Not Found" in message
    assert "fallback" in message


def test_alert_skips_without_webhook(monkeypatch) -> None:
    _reset_dedupe()
    monkeypatch.delenv("GOOGLE_CHAT_WEBHOOK_URL", raising=False)

    with patch("due_diligence_reporter.utils.post_google_chat_message") as mock_post:
        alert_firestore_fallback("m2_state", "load", RuntimeError("boom"))

    mock_post.assert_not_called()


def test_alert_failure_never_raises(monkeypatch) -> None:
    _reset_dedupe()
    monkeypatch.setenv("GOOGLE_CHAT_WEBHOOK_URL", "https://chat.example/webhook")

    with patch(
        "due_diligence_reporter.utils.post_google_chat_message",
        side_effect=RuntimeError("webhook down"),
    ):
        alert_firestore_fallback("rhodes_retry_state", "save", RuntimeError("boom"))


def test_dd_republish_store_save_failure_triggers_alert(monkeypatch, tmp_path) -> None:
    _reset_dedupe()
    monkeypatch.setenv("GOOGLE_CHAT_WEBHOOK_URL", "https://chat.example/webhook")
    from due_diligence_reporter.dd_republish_state_store import (
        FirestoreDDRepublishStateStore,
        JsonDDRepublishStateStore,
    )

    store = FirestoreDDRepublishStateStore(
        project_id="proj",
        fallback=JsonDDRepublishStateStore(tmp_path / "state.json"),
    )

    with patch.object(
        store, "_save_firestore_state", side_effect=RuntimeError("404 Not Found")
    ), patch("due_diligence_reporter.utils.post_google_chat_message") as mock_post:
        store.save({})

    assert mock_post.call_count == 1
    assert "dd_republish_state" in mock_post.call_args.args[1]
