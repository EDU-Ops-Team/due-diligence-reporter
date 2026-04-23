"""Configuration management for Google OAuth and APIs."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

# Load environment variables from .env file at project root
load_dotenv(Path(__file__).parent.parent.parent / ".env")


class Settings(BaseSettings):
    """Application settings."""

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

    # RayCon cost API
    pricing_api_url: str = Field(
        "https://raycon-api-738625530258.us-central1.run.app",
        description="Base URL for the RayCon pricing API",
    )

    # Shovels.ai permit history API
    shovels_api_key: str = Field(
        "",
        description="Shovels.ai API key for permit history lookups",
    )
    shovels_api_base_url: str = Field(
        "https://api.shovels.ai/v2",
        description="Base URL for the Shovels.ai API",
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
    inbox_scan_query: str = Field(
        "{to:edu.ops@trilogy.com cc:edu.ops@trilogy.com} has:attachment filename:pdf",
        description="Gmail search query for incoming DD documents (to or cc)",
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
