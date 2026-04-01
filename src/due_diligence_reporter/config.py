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

    # DD Report Templates
    dd_template_v2_google_doc_id: str = Field(
        "",
        description="Google Doc ID of the V2 DD report template",
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

    # RayCon cost API
    pricing_api_url: str = Field(
        "https://raycon-api-738625530258.us-central1.run.app",
        description="Base URL for the RayCon pricing API",
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

    # Google Chat
    google_chat_webhook_url: str = Field(
        "", description="Comma-separated Google Chat incoming webhook URLs for notifications"
    )

    # Inbox Scanner
    inbox_scan_query: str = Field(
        "{to:edu.ops@trilogy.com cc:edu.ops@trilogy.com} has:attachment filename:pdf",
        description="Gmail search query for incoming DD documents (to or cc)",
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
