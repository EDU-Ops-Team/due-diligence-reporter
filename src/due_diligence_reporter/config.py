"""Configuration management for Google OAuth and APIs."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve the canonical .env path (repo root, two levels up from this file).
# Loaded two ways:
#   1. ``load_dotenv(...)`` so plain ``os.getenv`` calls anywhere in the
#      codebase pick up the same values.
#   2. ``model_config.env_file`` so pydantic-settings reads .env directly
#      even if a caller imports ``Settings`` before ``load_dotenv`` runs
#      (e.g. test harnesses, scripts that change cwd).
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_ENV_PATH)


class Settings(BaseSettings):
    """Application settings."""

    # Belt-and-suspenders: read the same .env file pydantic-settings does
    # not depend on load_dotenv side effects. ``case_sensitive=False`` lets
    # field ``shovels_api_key`` pick up ``SHOVELS_API_KEY`` regardless of
    # casing in CI or shell exports. ``extra="ignore"`` keeps unrelated env
    # vars from breaking instantiation when .env carries unrelated keys.
    model_config = SettingsConfigDict(
        env_file=str(_ENV_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Google Cloud Configuration
    google_client_config: str = Field(
        "credentials/client_secrets.json",
        description="OAuth 2.0 client configuration file",
    )
    google_token_file: str = Field(
        ".gcp-saved-tokens.json", description="Path to store user OAuth tokens"
    )
    oauth_port: int = Field(
        8765,
        description="Port for OAuth callback server",
    )

    # Google API Scopes — Drive (read/write) + Documents (create/edit) + Gmail (modify)
    google_scopes: list[str] = Field(
        default=[
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/documents",
            "https://www.googleapis.com/auth/gmail.modify",
        ],
        description="OAuth scopes for Drive, Docs, and Gmail operations",
    )

    # DD Report Templates (deprecated — reports are now built programmatically)
    dd_template_v3_google_doc_id: str = Field(
        "",
        description="Deprecated: Google Doc ID of the V3 DD report template. "
        "Reports are now built programmatically via google_doc_builder.",
    )
    dd_template_v2_google_doc_id: str = Field(
        "",
        description="Deprecated: Legacy fallback Google Doc ID for DD report template.",
    )

    # Google Drive root folder containing all site folders
    google_drive_root_folder_id: str = Field(
        "",
        description="Parent Drive folder ID that contains all site folders",
    )

    # Shared Drive folder IDs (SIR, ISP, Building Inspection under "All Locations")
    sir_folder_id: str = Field(
        "1TTjxOEfjeJZoXMAeGueJ1QbVBzXBDE4C",
        description="Drive folder ID for shared SIR documents",
    )
    isp_folder_id: str = Field(
        "1E9RXgVeKxeITUdFw5lvyolCx6CJLEFUg",
        description="Drive folder ID for shared ISP documents",
    )
    building_inspection_folder_id: str = Field(
        "15dfKaAnic9VRKhp_-vFSpTr7uPk_hhKo",
        description="Drive folder ID for shared Building Inspection documents",
    )

    # DD Dashboard aggregation + deploy
    dashboard_output_path: str = Field(
        "dist/sites.json",
        description="Local filesystem path (relative to repo root) where the aggregated sites.json is written.",
    )
    dashboard_drive_folder_id: str = Field(
        "",
        description="Optional Drive folder ID. When set, the aggregated sites.json is also uploaded there.",
    )
    dashboard_repo: str = Field(
        "",
        description="Optional GitHub repo (owner/name) to push sites.json to. When set, the aggregator runs gh to commit and push.",
    )
    dashboard_repo_path: str = Field(
        "public/sites.json",
        description="Path inside DASHBOARD_REPO where sites.json should live.",
    )
    dashboard_repo_branch: str = Field(
        "main",
        description="Branch to push sites.json to.",
    )

    # RayCon async hand-off (inbox scanner pings RayCon when a Block Plan
    # lands; RayCon writes raycon_scenario.json into the site's M1 folder).
    raycon_jobs_url: str = Field(
        "https://raycon-api-738625530258.us-central1.run.app/v1/jobs",
        description="Endpoint that accepts RayCon scenario job requests.",
    )
    raycon_api_key: str = Field(
        "",
        description=(
            "Optional API key sent in X-RayCon-API-Key for RayCon's Firebase "
            "auth path. Gated by RAYCON_REQUIRE_FIREBASE_AUTH=true on RayCon's "
            "side (currently disabled), so leaving this blank is fine."
        ),
    )
    raycon_webhook_secret: str = Field(
        "",
        description=(
            "Optional shared secret. When set, post_raycon_job HMAC-SHA256-signs "
            "the raw /v1/jobs request body with it and sends X-RayCon-Signature. "
            "RayCon's /v1/jobs is currently public under the /v1/* rollout and "
            "does not validate the header, but signing keeps the canonical path "
            "exercised so we're ready the day verification is turned on."
        ),
    )

    # Shovels.ai permit history API — DEPRECATED for DDR.
    # The Shovels integration has been moved upstream to the AI SIR /
    # source-evidence build, which now supplies pre-computed permit
    # history risk flags via the ``permit_history.risk_flags`` token.
    # DDR no longer initiates live Shovels API calls during report
    # generation. The helpers and ``get_permit_history`` tool remain in
    # the codebase for legacy callers but are not advertised as MCP
    # tools unless ``DDR_ENABLE_SHOVELS`` is explicitly enabled.
    ddr_enable_shovels: bool = Field(
        False,
        description=(
            "Legacy escape hatch. When True, the deprecated "
            "get_permit_history MCP tool is registered so legacy "
            "callers can still invoke it. Default False — DDR does not "
            "call Shovels during normal report generation."
        ),
    )
    shovels_api_key: str = Field(
        "",
        description=(
            "DEPRECATED for DDR. Only used by the legacy "
            "get_permit_history helper when DDR_ENABLE_SHOVELS=true. "
            "Permit history evidence is built upstream now."
        ),
    )
    shovels_api_base_url: str = Field(
        "https://api.shovels.ai/v2",
        description=(
            "DEPRECATED for DDR. Base URL for the legacy Shovels.ai "
            "client; only used when DDR_ENABLE_SHOVELS=true."
        ),
    )
    rebl_base_url: str = Field(
        "https://rebl3.vercel.app",
        description="Base URL for resolving canonical REBL site IDs from addresses.",
    )

    # LLM model IDs
    openai_filename_model: str = Field(
        "gpt-4o-mini",
        description="OpenAI model used for filename classification",
    )
    openai_content_model: str = Field(
        "gpt-4o-mini",
        description="OpenAI model used for PDF content classification",
    )
    openai_site_match_model: str = Field(
        "gpt-4o-mini",
        description="OpenAI model used for shared-folder site matching",
    )
    anthropic_report_model: str = Field(
        "claude-sonnet-4-6",
        description="Anthropic model used for DD report generation",
    )
    ops_skills_repo_path: str = Field(
        "",
        description=(
            "Optional path to the ops skills repo root or its skills/ directory. "
            "Used to load shared skill context such as capacity-brainlift."
        ),
    )

    # Email (Gmail SMTP with App Password)
    email_sender: str = Field("", description="Gmail address for sending DD report emails")
    email_app_password: str = Field("", description="Gmail App Password for the sender account")
    dd_report_email_recipients: str = Field(
        "", description="Comma-separated list of recipient email addresses"
    )
    sir_notification_recipients: str = Field(
        "",
        description="Comma-separated recipient email addresses for SIR arrival notifications",
    )
    cds_notification_recipients: str = Field(
        "",
        description="Comma-separated email addresses for CDS verification report delivery",
    )
    global_email_cc: str = Field(
        "",
        description="Comma-separated email addresses added to every outbound email (e.g. a manager CC)",
    )

    # P1 Assignment Engine
    serpapi_key: str = Field(
        "", description="SerpAPI key for Google Flights search (used by P1 assignment engine)"
    )
    p1_team_config: str = Field(
        "",
        description=(
            "JSON array of team member objects for P1 assignment. "
            'Each entry: {"name", "email", "home_airport", "home_state", '
            '"preferred_airline", "strongly_preferred_airline"}. '
            "preferred_airline and strongly_preferred_airline are optional."
        ),
    )
    p1_disabled_names: str = Field(
        "Andrea",
        description=(
            "Comma-separated assignee names excluded from P1 assignment. "
            "Used to block former team members even if they still appear in P1_TEAM_CONFIG."
        ),
    )
    p1_disabled_emails: str = Field(
        "",
        description=(
            "Comma-separated assignee email addresses excluded from P1 assignment. "
            "Checked in addition to p1_disabled_names."
        ),
    )

    # Google Chat
    google_chat_webhook_url: str = Field(
        "", description="Comma-separated Google Chat incoming webhook URLs for notifications"
    )

    # Inbox Scanner
    # The scanner runs OAuth-authed AS edu.ops@trilogy.com (the shared inbox
    # that receives all DD deliveries from CDS, brokers, vendors). Confirmed
    # via diagnostic run 25069756638 (getProfile emailAddress was redacted by
    # the EMAIL_SENDER secret mask, and messagesTotal=1226 matches a small
    # operational mailbox; all scanner-sent automated emails come from
    # edu.ops@trilogy.com).
    #
    # History of this query:
    #   v1: {to:edu.ops cc:edu.ops} ...           # silently dropped Forums tab
    #   v2: ... category:{primary forums updates} # Gmail UI syntax, REST API
    #                                              ignored it -> 0 results
    #   v3: {to:edu.ops cc:edu.ops} -category:promotions -category:social
    #                                              # negative form is REST-API
    #                                              # safe BUT to:/cc:self-
    #                                              # reference returns 0 when
    #                                              # run from inside that
    #                                              # mailbox (especially for
    #                                              # Group-routed mail where
    #                                              # the recipient address is
    #                                              # in Delivered-To, not
    #                                              # rendered in To/Cc the way
    #                                              # the API matcher expects).
    #   v4: in:inbox + filename:(pdf OR docx) + negative-category exclusions.
    #       Worked for discovery, but the downstream attachment processor only
    #       handles PDF, so the docx matches surfaced as 10 errors. Reverted
    #       to pdf-only here; docx support is a separate follow-up that needs
    #       to touch the classifier, drive uploader, and reporter.
    #   v5 (current): in:inbox + filename:pdf + negative-category exclusions.
    #                 Since the scanner runs AS the recipient mailbox,
    #                 in:inbox is sufficient and reliable for Group-routed mail.
    inbox_scan_query: str = Field(
        "in:inbox has:attachment filename:pdf "
        "-category:promotions -category:social",
        description=(
            "Gmail search query for incoming DD documents. Scanner is authed "
            "as the recipient mailbox, so in:inbox + attachment filters is "
            "sufficient. Negative category exclusions cover Forums/Updates "
            "(REST-API safe) without dragging in marketing/social noise."
        ),
    )
    inbox_internal_sender_domains: str = Field(
        "trilogy.com",
        description=(
            "Comma-separated email domains treated as internal. "
            "Attachments from these senders are skipped by the inbox scanner "
            "to prevent AI-generated documents from creating false readiness."
        ),
    )
    inbox_internal_sender_addresses: str = Field(
        "",
        description=(
            "Comma-separated full email addresses treated as internal "
            "(for service accounts or addresses on non-internal domains). "
            "Checked in addition to inbox_internal_sender_domains."
        ),
    )
    inbox_processed_label: str = Field(
        "DD-Processed",
        description="Gmail label applied to processed inbox emails",
    )
    inbox_manual_review_label: str = Field(
        "DD-Manual-Review",
        description="Gmail label applied to emails needing human review",
    )
    inbox_internal_skip_label: str = Field(
        "DD-Internal-Skipped",
        description=(
            "Gmail label applied to emails skipped by the internal-sender "
            "heuristic. Kept distinct from DD-Processed so heuristic bugs do "
            "not burn real DD deliveries: if the heuristic later flips for "
            "a sender, only this label needs clearing, not DD-Processed."
        ),
    )
    inbox_scan_max_results: int = Field(
        50,
        description="Maximum number of emails to process per inbox scan run",
    )

    # Logging
    log_level: str = Field("INFO", description="Logging level")

    def get_client_config_path(self) -> Path:
        """Get the path to OAuth client configuration."""
        return Path(self.google_client_config)

    def get_token_file_path(self) -> Path:
        """Get the path to the token storage file."""
        return Path(self.google_token_file)


def get_settings() -> Settings:
    """Get application settings."""
    try:
        return Settings()
    except Exception as e:
        raise ValueError(
            f"Configuration error: {e}. "
            f"Please ensure Google OAuth client config and token paths are valid. "
            f"Current working directory: {os.getcwd()}. "
            f"GOOGLE_CLIENT_CONFIG: {os.getenv('GOOGLE_CLIENT_CONFIG')}, "
            f"GOOGLE_TOKEN_FILE: {os.getenv('GOOGLE_TOKEN_FILE')}"
        ) from e


# Placeholder values that the .env.example template ships with. Treat these
# as "not configured" so a freshly-copied .env doesn't look configured but
# fails at the API edge with a 401.
_SHOVELS_PLACEHOLDER_VALUES = frozenset({
    "your_shovels_api_key_here",
    "your-shovels-api-key-here",
    "changeme",
    "todo",
    "xxx",
})


def shovels_status(settings: Settings | None = None) -> dict[str, object]:
    """Preflight status of the Shovels.ai integration.

    Returns a dict suitable for surfacing in logs, the trace report, or a
    startup diagnostic. Never includes the raw key — only whether it is
    configured and why we think so. Safe to log.

    Shape::

        {
            "configured": bool,
            "reason": "ok" | "missing" | "placeholder" | "whitespace_only",
            "base_url": "https://api.shovels.ai/v2",
        }
    """
    s = settings if settings is not None else get_settings()
    raw = s.shovels_api_key or ""
    stripped = raw.strip()
    if not stripped:
        # Distinguish "env var unset" from "set to whitespace" so an operator
        # who pasted a tab/newline into .env gets a useful hint.
        reason = "whitespace_only" if raw else "missing"
        return {"configured": False, "reason": reason, "base_url": s.shovels_api_base_url}
    if stripped.lower() in _SHOVELS_PLACEHOLDER_VALUES:
        return {"configured": False, "reason": "placeholder", "base_url": s.shovels_api_base_url}
    return {"configured": True, "reason": "ok", "base_url": s.shovels_api_base_url}
