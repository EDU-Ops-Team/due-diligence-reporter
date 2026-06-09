from __future__ import annotations

from due_diligence_reporter.config import Settings


def test_google_chat_webhook_reads_ddr_specific_env(monkeypatch) -> None:
    monkeypatch.setenv("DDR_GOOGLE_CHAT_WEBHOOK_URL", "https://chat.example/ddr")
    monkeypatch.delenv("GOOGLE_CHAT_WEBHOOK_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.google_chat_webhook_url == "https://chat.example/ddr"


def test_google_chat_webhook_ignores_generic_ops_skill_env(monkeypatch) -> None:
    monkeypatch.delenv("DDR_GOOGLE_CHAT_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("GOOGLE_CHAT_WEBHOOK_URL", "https://chat.example/ops-skill")

    settings = Settings(_env_file=None)

    assert settings.google_chat_webhook_url == ""
