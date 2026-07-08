"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_dd_write_log(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Keep the DD write-log fallback out of the repo working directory."""

    log_dir = tmp_path_factory.mktemp("dd-write-log")
    monkeypatch.setenv("DD_WRITE_LOG_FALLBACK_PATH", str(log_dir / "log.json"))
