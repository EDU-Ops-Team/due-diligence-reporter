#!/usr/bin/env python3
"""Generate runtime OAuth credentials for the Due Diligence Reporter.

Run from project root:
    uv run python scripts/generate_oauth_token.py

Signs in via browser, then writes the authorized-user JSON used by runtime
GoogleClient auth to the configured GOOGLE_TOKEN_FILE path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv

load_dotenv(_project_root / ".env")

from due_diligence_reporter.config import get_settings


def main() -> None:
    settings = get_settings()
    client_config_path = _project_root / settings.get_client_config_path()
    token_file_path = _project_root / settings.get_token_file_path()

    with client_config_path.open(encoding="utf-8") as f:
        config = json.load(f)

    if "web" in config:
        config = {"installed": config.pop("web")}

    flow = InstalledAppFlow.from_client_config(config, scopes=settings.google_scopes)
    creds = flow.run_local_server(
        port=settings.oauth_port,
        access_type="offline",
        prompt="consent",
    )

    token_file_path.parent.mkdir(parents=True, exist_ok=True)
    token_file_path.write_text(creds.to_json(), encoding="utf-8")

    print()
    print(f"Saved OAuth credentials to {token_file_path}")
    print("This token file is the runtime auth artifact used by GoogleClient.")


if __name__ == "__main__":
    main()
