"""Exit-code and delivery tests for the dd-write-digest CLI."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from due_diligence_reporter.ddr_cli import main


def _run_cli(monkeypatch, tmp_path, argv, *, settings, chat_ok=True, email_ok=True):
    monkeypatch.setenv("DD_WRITE_LOG_FALLBACK_PATH", str(tmp_path / "log.json"))
    monkeypatch.setenv("DD_WRITE_LOG_FIRESTORE_PROJECT_ID", "proj")
    events = [
        {
            "created_at": "2099-01-01T00:00:00+00:00",
            "site_id": "SITE1",
            "status": "updated",
            "fields": '{"foCapacity": "36"}',
            "field_sources": "{}",
            "review_url": "",
            "run_source": "test",
        }
    ]
    send_email = MagicMock(side_effect=None if email_ok else RuntimeError("smtp down"))
    post_chat = MagicMock(side_effect=None if chat_ok else RuntimeError("chat down"))
    with patch(
        "due_diligence_reporter.dd_write_digest._load_events_from_firestore",
        return_value=events,
    ), patch("due_diligence_reporter.utils.send_email", send_email), patch(
        "due_diligence_reporter.utils.post_google_chat_message", post_chat
    ), patch("due_diligence_reporter.config.get_settings", return_value=settings), patch(
        "due_diligence_reporter.rhodes.RhodesClient", side_effect=RuntimeError("no mcp")
    ):
        code = main(argv)
    return code, send_email, post_chat


def test_digest_cli_fails_when_nothing_delivered(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(
        email_sender="", email_app_password="", google_chat_webhook_url=""
    )
    code, send_email, post_chat = _run_cli(
        monkeypatch, tmp_path, ["dd-write-digest", "--hours", "24"], settings=settings
    )
    assert code == 1
    send_email.assert_not_called()
    post_chat.assert_not_called()


def test_digest_cli_succeeds_with_one_channel(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(
        email_sender="",
        email_app_password="",
        google_chat_webhook_url="https://chat.example/webhook",
    )
    code, _send_email, post_chat = _run_cli(
        monkeypatch, tmp_path, ["dd-write-digest", "--hours", "24"], settings=settings
    )
    assert code == 0
    post_chat.assert_called_once()


def test_digest_cli_fails_without_store_config(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DD_WRITE_LOG_FIRESTORE_PROJECT_ID", raising=False)
    monkeypatch.delenv("M2_DD_STATE_FIRESTORE_PROJECT_ID", raising=False)
    monkeypatch.delenv("DD_REPUBLISH_STATE_FIRESTORE_PROJECT_ID", raising=False)
    monkeypatch.setenv("DD_WRITE_LOG_FALLBACK_PATH", str(tmp_path / "log.json"))

    code = main(["dd-write-digest", "--hours", "24"])

    assert code == 1
