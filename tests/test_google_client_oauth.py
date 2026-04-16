"""Tests for GoogleClient.from_oauth_config OAuth flow logic."""

from __future__ import annotations

from unittest.mock import MagicMock, mock_open, patch

import pytest

from due_diligence_reporter.google_client import GoogleClient

# Common test constants
_SCOPES = ["https://www.googleapis.com/auth/drive"]
_CONFIG_PATH = "/fake/client_secrets.json"
_TOKEN_PATH = "/fake/token.json"
_PORT = 8765


def _patch_build():
    """Patch googleapiclient.discovery.build so __init__ doesn't hit real APIs."""
    return patch("due_diligence_reporter.google_client.build", return_value=MagicMock())


class TestFromOAuthConfigValidToken:
    """Token file exists with valid, non-expired credentials."""

    @_patch_build()
    @patch("due_diligence_reporter.google_client.Path")
    @patch("due_diligence_reporter.google_client.Credentials")
    def test_valid_cached_token(self, mock_creds_cls, mock_path_cls, mock_build):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_cls.return_value = mock_path_instance

        creds = MagicMock()
        creds.valid = True
        creds.refresh_token = "refresh_tok"
        mock_creds_cls.from_authorized_user_file.return_value = creds

        client = GoogleClient.from_oauth_config(
            _CONFIG_PATH, _TOKEN_PATH, _PORT, _SCOPES
        )

        assert isinstance(client, GoogleClient)
        creds.refresh.assert_not_called()


class TestFromOAuthConfigExpiredToken:
    """Token file exists, credentials expired but refresh_token present."""

    @_patch_build()
    @patch("builtins.open", mock_open())
    @patch("due_diligence_reporter.google_client.Request")
    @patch("due_diligence_reporter.google_client.Path")
    @patch("due_diligence_reporter.google_client.Credentials")
    def test_expired_token_refreshed(
        self, mock_creds_cls, mock_path_cls, mock_request_cls, mock_build
    ):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_cls.return_value = mock_path_instance

        creds = MagicMock()
        creds.valid = False
        creds.refresh_token = "refresh_tok"
        creds.to_json.return_value = '{"token": "new"}'
        mock_creds_cls.from_authorized_user_file.return_value = creds

        client = GoogleClient.from_oauth_config(
            _CONFIG_PATH, _TOKEN_PATH, _PORT, _SCOPES
        )

        creds.refresh.assert_called_once()
        assert isinstance(client, GoogleClient)


class TestFromOAuthConfigRefreshFailures:
    """Token refresh raises various exceptions -> RuntimeError."""

    @_patch_build()
    @patch("due_diligence_reporter.google_client.Request")
    @patch("due_diligence_reporter.google_client.Path")
    @patch("due_diligence_reporter.google_client.Credentials")
    def test_refresh_error(
        self, mock_creds_cls, mock_path_cls, mock_request_cls, mock_build
    ):
        from google.auth.exceptions import RefreshError

        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_cls.return_value = mock_path_instance

        creds = MagicMock()
        creds.valid = False
        creds.refresh_token = "refresh_tok"
        creds.refresh.side_effect = RefreshError("revoked")
        mock_creds_cls.from_authorized_user_file.return_value = creds

        with pytest.raises(RuntimeError, match="token refresh failed"):
            GoogleClient.from_oauth_config(_CONFIG_PATH, _TOKEN_PATH, _PORT, _SCOPES)

    @_patch_build()
    @patch("due_diligence_reporter.google_client.Request")
    @patch("due_diligence_reporter.google_client.Path")
    @patch("due_diligence_reporter.google_client.Credentials")
    def test_transport_error(
        self, mock_creds_cls, mock_path_cls, mock_request_cls, mock_build
    ):
        from google.auth.exceptions import TransportError

        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_cls.return_value = mock_path_instance

        creds = MagicMock()
        creds.valid = False
        creds.refresh_token = "refresh_tok"
        creds.refresh.side_effect = TransportError("network down")
        mock_creds_cls.from_authorized_user_file.return_value = creds

        with pytest.raises(RuntimeError):
            GoogleClient.from_oauth_config(_CONFIG_PATH, _TOKEN_PATH, _PORT, _SCOPES)

    @_patch_build()
    @patch("due_diligence_reporter.google_client.Request")
    @patch("due_diligence_reporter.google_client.Path")
    @patch("due_diligence_reporter.google_client.Credentials")
    def test_os_error(
        self, mock_creds_cls, mock_path_cls, mock_request_cls, mock_build
    ):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_cls.return_value = mock_path_instance

        creds = MagicMock()
        creds.valid = False
        creds.refresh_token = "refresh_tok"
        creds.refresh.side_effect = OSError("disk error")
        mock_creds_cls.from_authorized_user_file.return_value = creds

        with pytest.raises(RuntimeError):
            GoogleClient.from_oauth_config(_CONFIG_PATH, _TOKEN_PATH, _PORT, _SCOPES)


class TestFromOAuthConfigMissingRefreshToken:
    """Token file exists but credentials lack a refresh_token -> browser flow."""

    @_patch_build()
    @patch("builtins.open", mock_open())
    @patch("due_diligence_reporter.google_client.InstalledAppFlow")
    @patch("due_diligence_reporter.google_client.Path")
    @patch("due_diligence_reporter.google_client.Credentials")
    def test_missing_refresh_token_falls_to_oauth_flow(
        self, mock_creds_cls, mock_path_cls, mock_flow_cls, mock_build
    ):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_cls.return_value = mock_path_instance

        creds_from_file = MagicMock()
        creds_from_file.refresh_token = None  # missing
        mock_creds_cls.from_authorized_user_file.return_value = creds_from_file

        new_creds = MagicMock()
        new_creds.to_json.return_value = '{"token": "new"}'
        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = new_creds
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        client = GoogleClient.from_oauth_config(
            _CONFIG_PATH, _TOKEN_PATH, _PORT, _SCOPES
        )

        mock_flow_cls.from_client_secrets_file.assert_called_once_with(
            _CONFIG_PATH, _SCOPES
        )
        assert isinstance(client, GoogleClient)


class TestFromOAuthConfigNoTokenFile:
    """No token file exists -> start OAuth flow from scratch."""

    @_patch_build()
    @patch("builtins.open", mock_open())
    @patch("due_diligence_reporter.google_client.InstalledAppFlow")
    @patch("due_diligence_reporter.google_client.Path")
    def test_no_token_file_starts_oauth_flow(
        self, mock_path_cls, mock_flow_cls, mock_build
    ):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = False
        mock_path_cls.return_value = mock_path_instance

        new_creds = MagicMock()
        new_creds.to_json.return_value = '{"token": "fresh"}'
        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = new_creds
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        client = GoogleClient.from_oauth_config(
            _CONFIG_PATH, _TOKEN_PATH, _PORT, _SCOPES
        )

        mock_flow_cls.from_client_secrets_file.assert_called_once()
        assert isinstance(client, GoogleClient)


class TestFromOAuthConfigFlowReturnsNone:
    """OAuth flow returns None -> RuntimeError."""

    @_patch_build()
    @patch("due_diligence_reporter.google_client.InstalledAppFlow")
    @patch("due_diligence_reporter.google_client.Path")
    def test_oauth_flow_returns_none(self, mock_path_cls, mock_flow_cls, mock_build):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = False
        mock_path_cls.return_value = mock_path_instance

        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = None
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        with pytest.raises(RuntimeError, match="Failed to obtain OAuth credentials"):
            GoogleClient.from_oauth_config(_CONFIG_PATH, _TOKEN_PATH, _PORT, _SCOPES)
