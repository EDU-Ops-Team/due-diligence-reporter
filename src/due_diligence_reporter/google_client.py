"""Google Client for Drive, Docs, and Gmail API operations with OAuth."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload

from .utils import escape_drive_query_literal

logger = logging.getLogger("[google_client]")


class GoogleClient:
    """Client for interacting with Drive, Docs, and Gmail APIs using OAuth."""

    def __init__(self, credentials: Credentials) -> None:
        self.credentials = credentials
        self.drive_service = build("drive", "v3", credentials=credentials)
        self.docs_service = build("docs", "v1", credentials=credentials)
        self.gmail_service = build("gmail", "v1", credentials=credentials)
        logger.info("Initialized GoogleClient with Drive v3, Docs v1, and Gmail v1 APIs")

    @classmethod
    def from_oauth_config(
        cls,
        client_config_path: str,
        token_file_path: str,
        oauth_port: int,
        scopes: list[str],
    ) -> GoogleClient:
        """Create client using OAuth flow with provided scopes."""
        credentials: Credentials | None = None
        token_file = Path(token_file_path)

        logger.info("Initializing OAuth flow with scopes: %s", scopes)

        if token_file.exists():
            logger.info("Loading existing credentials from: %s", token_file)
            credentials = Credentials.from_authorized_user_file(str(token_file), scopes)
            if credentials and not credentials.refresh_token:
                logger.warning(
                    "Loaded credentials missing refresh_token; forcing OAuth flow"
                )
                credentials = None

        if not credentials or not credentials.valid:
            if credentials and credentials.refresh_token:
                logger.info("Refreshing credentials (token missing or expired)")
                credentials.refresh(Request())
            else:
                logger.info("Starting OAuth flow — browser window will open")
                flow = InstalledAppFlow.from_client_secrets_file(
                    client_config_path, scopes
                )
                credentials = flow.run_local_server(
                    port=oauth_port,
                    access_type="offline",
                    prompt="consent",
                )

            if credentials is None:
                raise RuntimeError("Failed to obtain OAuth credentials")

            with open(token_file, "w") as token:
                token.write(credentials.to_json())
            logger.info("Saved new credentials to: %s", token_file)

        if credentials is None:
            raise RuntimeError("OAuth credentials are None after flow")

        return cls(credentials)

    # ---------- Drive API Methods ----------

    def list_files_in_folder(
        self,
        folder_id: str,
        *,
        include_trashed: bool = False,
    ) -> list[dict[str, Any]]:
        """
        List all files (not folders) directly inside a Drive folder.

        Returns list of dicts with keys: id, name, mimeType, modifiedTime, webViewLink.
        """
        trashed_clause = "" if include_trashed else " and trashed=false"
        query = (
            f"'{folder_id}' in parents"
            " and mimeType!='application/vnd.google-apps.folder'"
            f"{trashed_clause}"
        )

        logger.info("Listing files in Drive folder: %s", folder_id)

        try:
            files: list[dict[str, Any]] = []
            page_token: str | None = None

            while True:
                response = (
                    self.drive_service.files()
                    .list(
                        q=query,
                        fields="nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink)",
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                        pageToken=page_token,
                        orderBy="name_natural",
                    )
                    .execute()
                )
                files.extend(response.get("files", []))
                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            logger.info("Found %d files in folder %s", len(files), folder_id)
            return files

        except HttpError as error:
            logger.error("Failed to list files in folder %s: %s", folder_id, error)
            raise RuntimeError(f"Failed to list files in folder: {error}") from error

    def list_subfolders(self, folder_id: str) -> list[dict[str, Any]]:
        """
        List direct child folders inside a Drive folder.

        Returns list of dicts with keys: id, name, webViewLink.
        """
        query = (
            f"'{folder_id}' in parents"
            " and mimeType='application/vnd.google-apps.folder'"
            " and trashed=false"
        )

        logger.info("Listing subfolders of: %s", folder_id)

        try:
            folders: list[dict[str, Any]] = []
            page_token: str | None = None

            while True:
                response = (
                    self.drive_service.files()
                    .list(
                        q=query,
                        fields="nextPageToken,files(id,name,webViewLink)",
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                        pageToken=page_token,
                        orderBy="name_natural",
                    )
                    .execute()
                )
                folders.extend(response.get("files", []))
                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            logger.info("Found %d subfolders in folder %s", len(folders), folder_id)
            return folders

        except HttpError as error:
            logger.error("Failed to list subfolders: %s", error)
            raise RuntimeError(f"Failed to list subfolders: {error}") from error

    def list_files_recursive(
        self, folder_id: str, *, max_depth: int = 3
    ) -> list[dict[str, Any]]:
        """List all files recursively under a Drive folder, up to *max_depth* levels.

        Each returned file dict includes a ``folder_path`` key indicating its
        location relative to the root folder (e.g. ``"/Subfolder A"``).
        """
        all_files: list[dict[str, Any]] = []

        def _walk(fid: str, depth: int, path: str) -> None:
            files = self.list_files_in_folder(fid)
            for f in files:
                f["folder_path"] = path
            all_files.extend(files)

            if depth < max_depth:
                try:
                    subfolders = self.list_subfolders(fid)
                except Exception as e:
                    logger.warning("Failed to list subfolders of %s: %s", fid, e)
                    return
                for sf in subfolders:
                    sf_id = sf.get("id")
                    sf_name = sf.get("name", "")
                    if sf_id:
                        _walk(sf_id, depth + 1, f"{path}/{sf_name}")

        _walk(folder_id, 0, "")
        logger.info(
            "Recursive listing of %s found %d files (max_depth=%d)",
            folder_id, len(all_files), max_depth,
        )
        return all_files

    def find_subfolder_by_name(
        self, parent_id: str, subfolder_name: str
    ) -> dict[str, Any] | None:
        """Find a named direct child folder inside a parent Drive folder."""
        subfolders = self.list_subfolders(parent_id)
        for folder in subfolders:
            if folder.get("name", "") == subfolder_name:
                return folder
        return None

    def export_google_doc_as_text(self, file_id: str) -> str:
        """
        Export a Google Docs file as plain text.

        Uses the Drive API export endpoint (only works for Google Workspace files).
        Returns the text content as a string.
        """
        logger.info("Exporting Google Doc as text: %s", file_id)

        try:
            response = (
                self.drive_service.files()
                .export(fileId=file_id, mimeType="text/plain")
                .execute()
            )

            if isinstance(response, bytes):
                text = response.decode("utf-8", errors="replace")
            else:
                text = str(response)

            logger.info("Exported %d characters from Google Doc %s", len(text), file_id)
            return text

        except HttpError as error:
            logger.error("Failed to export Google Doc %s: %s", file_id, error)
            raise RuntimeError(f"Failed to export Google Doc: {error}") from error

    def download_file_bytes(self, file_id: str) -> bytes:
        """
        Download a file's raw bytes from Google Drive.

        Used for PDFs and other binary/non-Google-Workspace files.
        """
        logger.info("Downloading file bytes: %s", file_id)

        try:
            response = self.drive_service.files().get_media(fileId=file_id).execute()

            if isinstance(response, bytes):
                data = response
            else:
                data = bytes(response)

            logger.info("Downloaded %d bytes for file %s", len(data), file_id)
            return data

        except HttpError as error:
            logger.error("Failed to download file %s: %s", file_id, error)
            raise RuntimeError(f"Failed to download file: {error}") from error

    def copy_document(
        self,
        template_id: str,
        name: str,
        parent_folder_id: str,
    ) -> dict[str, Any]:
        """
        Copy a Google Docs template to a target folder.

        Returns the new document metadata including 'id' and 'webViewLink'.
        """
        logger.info(
            "Copying Docs template: %s (name: %s, parent: %s)",
            template_id,
            name,
            parent_folder_id,
        )

        body: dict[str, Any] = {
            "name": name,
            "parents": [parent_folder_id],
        }

        try:
            doc = (
                self.drive_service.files()
                .copy(
                    fileId=template_id,
                    body=body,
                    supportsAllDrives=True,
                    fields="id,webViewLink,name",
                )
                .execute()
            )
            logger.info(
                "Successfully copied document: %s (id: %s)",
                name,
                doc.get("id"),
            )
            return doc

        except HttpError as error:
            logger.error("Failed to copy document: %s", error)
            raise RuntimeError(f"Failed to copy document: {error}") from error

    # ---------- Docs API Methods ----------

    def batch_update_document(
        self,
        document_id: str,
        requests_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Apply a batch update to a Google Docs document.

        Args:
            document_id: The document ID to update.
            requests_list: List of Docs API request objects (e.g., replaceAllText).

        Returns:
            The batchUpdate API response.
        """
        logger.info(
            "Batch updating document: %s (%d requests)",
            document_id,
            len(requests_list),
        )

        try:
            response = (
                self.docs_service.documents()
                .batchUpdate(
                    documentId=document_id,
                    body={"requests": requests_list},
                )
                .execute()
            )
            logger.info("Successfully batch-updated document: %s", document_id)
            return response

        except HttpError as error:
            logger.error("Failed to batch update document %s: %s", document_id, error)
            raise RuntimeError(f"Failed to batch update document: {error}") from error

    def make_file_public(self, file_id: str) -> None:
        """Grant 'anyone with the link' read access to a Drive file.

        Required before using the file's URI with Google Docs
        ``insertInlineImage``, which fetches the image server-side without
        OAuth credentials.
        """
        logger.info("Setting public read permission on file: %s", file_id)
        try:
            self.drive_service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
                supportsAllDrives=True,
            ).execute()
        except HttpError as error:
            logger.error("Failed to make file %s public: %s", file_id, error)
            raise RuntimeError(f"Failed to set public permission: {error}") from error

    def get_document(self, document_id: str) -> dict[str, Any]:
        """Retrieve the full Google Docs document structure (body, lists, etc.).

        Returns the raw API response dict including ``body.content`` with
        character indices needed for insertInlineImage operations.
        """
        logger.info("Getting document structure: %s", document_id)
        try:
            doc = self.docs_service.documents().get(documentId=document_id).execute()
            return doc
        except HttpError as error:
            logger.error("Failed to get document %s: %s", document_id, error)
            raise RuntimeError(f"Failed to get document: {error}") from error

    # ---------- Docs: Create New Document ----------

    def create_document(
        self,
        name: str,
        folder_id: str,
        text_content: str,
    ) -> dict[str, Any]:
        """Create a new Google Doc with *text_content* in *folder_id*.

        Steps:
            1. ``documents().create()`` — creates a blank doc.
            2. ``documents().batchUpdate()`` with ``insertText`` — writes content.
            3. ``drive.files().update()`` — moves the doc into *folder_id*.

        Returns dict with keys: ``id``, ``name``, ``webViewLink``.
        """
        logger.info("Creating document '%s' in folder %s", name, folder_id)

        try:
            # 1. Create blank doc
            doc = (
                self.docs_service.documents()
                .create(body={"title": name})
                .execute()
            )
            doc_id: str = doc["documentId"]

            # 2. Insert text content
            if text_content:
                self.docs_service.documents().batchUpdate(
                    documentId=doc_id,
                    body={
                        "requests": [
                            {
                                "insertText": {
                                    "location": {"index": 1},
                                    "text": text_content,
                                }
                            }
                        ]
                    },
                ).execute()

            # 3. Move into target folder (remove default 'My Drive' parent, add folder_id)
            self.drive_service.files().update(
                fileId=doc_id,
                addParents=folder_id,
                removeParents="root",
                fields="id,name,webViewLink",
                supportsAllDrives=True,
            ).execute()

            # 4. Fetch final metadata
            result = (
                self.drive_service.files()
                .get(
                    fileId=doc_id,
                    fields="id,name,webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
            logger.info("Created document '%s' (id: %s)", name, doc_id)
            return result

        except HttpError as error:
            logger.error("Failed to create document '%s': %s", name, error)
            raise RuntimeError(f"Failed to create document: {error}") from error

    # ---------- Drive Upload ----------

    def upload_file_to_folder(
        self,
        folder_id: str,
        file_name: str,
        file_bytes: bytes,
        mime_type: str = "application/pdf",
    ) -> dict[str, Any]:
        """Upload a file to a specific Drive folder.

        Returns the new file metadata including 'id', 'name', 'webViewLink'.
        """
        logger.info("Uploading '%s' (%d bytes) to folder %s", file_name, len(file_bytes), folder_id)

        media = MediaInMemoryUpload(file_bytes, mimetype=mime_type, resumable=False)
        body: dict[str, Any] = {
            "name": file_name,
            "parents": [folder_id],
        }

        try:
            result = (
                self.drive_service.files()
                .create(
                    body=body,
                    media_body=media,
                    fields="id,name,webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
            logger.info("Uploaded file: %s (id: %s)", file_name, result.get("id"))
            return result

        except HttpError as error:
            logger.error("Failed to upload '%s': %s", file_name, error)
            raise RuntimeError(f"Failed to upload file: {error}") from error

    def file_exists_in_folder(self, folder_id: str, file_name: str) -> bool:
        """Check if a file with the exact name already exists in a folder."""
        escaped_file_name = escape_drive_query_literal(file_name)
        query = (
            f"'{folder_id}' in parents"
            f" and name='{escaped_file_name}'"
            " and trashed=false"
        )
        try:
            response = (
                self.drive_service.files()
                .list(
                    q=query,
                    fields="files(id)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            return len(response.get("files", [])) > 0
        except HttpError as error:
            logger.warning("Failed to check file existence: %s", error)
            return False

    # ---------- Gmail API Methods ----------

    def gmail_search(self, query: str, max_results: int = 50) -> list[dict[str, Any]]:
        """Search Gmail messages matching a query.

        Returns list of message stubs with 'id' and 'threadId'.
        """
        logger.info("Gmail search: %s (max %d)", query, max_results)

        try:
            messages: list[dict[str, Any]] = []
            page_token: str | None = None

            while len(messages) < max_results:
                response = (
                    self.gmail_service.users()
                    .messages()
                    .list(
                        userId="me",
                        q=query,
                        maxResults=min(max_results - len(messages), 100),
                        pageToken=page_token,
                    )
                    .execute()
                )
                messages.extend(response.get("messages", []))
                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            logger.info("Gmail search returned %d messages", len(messages))
            return messages[:max_results]

        except HttpError as error:
            logger.error("Gmail search failed: %s", error)
            raise RuntimeError(f"Gmail search failed: {error}") from error

    def gmail_get_message(self, message_id: str) -> dict[str, Any]:
        """Get a full Gmail message by ID (includes headers and parts)."""
        logger.info("Fetching Gmail message: %s", message_id)

        try:
            message = (
                self.gmail_service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            return message

        except HttpError as error:
            logger.error("Failed to get Gmail message %s: %s", message_id, error)
            raise RuntimeError(f"Failed to get Gmail message: {error}") from error

    def gmail_get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Download a Gmail attachment by message and attachment ID.

        Returns the raw attachment bytes.
        """
        logger.info("Downloading Gmail attachment: msg=%s, att=%s", message_id, attachment_id)

        try:
            attachment = (
                self.gmail_service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=attachment_id)
                .execute()
            )
            data = attachment.get("data", "")
            return base64.urlsafe_b64decode(data)

        except HttpError as error:
            logger.error("Failed to get attachment: %s", error)
            raise RuntimeError(f"Failed to get Gmail attachment: {error}") from error

    def gmail_modify_labels(
        self,
        message_id: str,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add or remove labels on a Gmail message."""
        body: dict[str, list[str]] = {
            "addLabelIds": add_labels or [],
            "removeLabelIds": remove_labels or [],
        }
        logger.info(
            "Modifying labels on %s: +%s -%s",
            message_id,
            add_labels or [],
            remove_labels or [],
        )

        try:
            result = (
                self.gmail_service.users()
                .messages()
                .modify(userId="me", id=message_id, body=body)
                .execute()
            )
            return result

        except HttpError as error:
            logger.error("Failed to modify labels on %s: %s", message_id, error)
            raise RuntimeError(f"Failed to modify Gmail labels: {error}") from error

    def gmail_get_or_create_label(self, label_name: str) -> str:
        """Find or create a Gmail label by name. Returns the label ID."""
        logger.info("Looking up Gmail label: %s", label_name)

        try:
            response = (
                self.gmail_service.users().labels().list(userId="me").execute()
            )
            for label in response.get("labels", []):
                if label.get("name") == label_name:
                    logger.info("Found existing label: %s (id: %s)", label_name, label["id"])
                    return label["id"]

            # Create the label
            body = {
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            }
            created = (
                self.gmail_service.users()
                .labels()
                .create(userId="me", body=body)
                .execute()
            )
            label_id = created["id"]
            logger.info("Created new label: %s (id: %s)", label_name, label_id)
            return label_id

        except HttpError as error:
            logger.error("Failed to get/create label '%s': %s", label_name, error)
            raise RuntimeError(f"Failed to get/create Gmail label: {error}") from error
