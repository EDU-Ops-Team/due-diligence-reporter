"""Rhodes/LocationOS lookup and document registration helpers for DDR."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import requests

DEFAULT_RHODES_MCP_URL = "https://location-os-mcp.ephor.workers.dev/mcp"
MCP_PROTOCOL_VERSION = "2025-03-26"
RHODES_TIMEOUT_SECONDS = 20.0
DRIVE_FOLDER_URL_PREFIX = "https://drive.google.com/drive/folders/"


class RhodesError(RuntimeError):
    """Raised when the Rhodes MCP client cannot complete a lookup."""


@dataclass(frozen=True)
class RhodesConfig:
    """Rhodes MCP configuration."""

    mcp_url: str
    api_key: str


def load_rhodes_config() -> RhodesConfig:
    """Load Rhodes MCP configuration from environment variables."""
    api_key = (os.getenv("RHODES_API_KEY") or "").strip()
    if not api_key:
        raise RhodesError("Missing RHODES_API_KEY env var")
    return RhodesConfig(
        mcp_url=(os.getenv("RHODES_MCP_URL") or DEFAULT_RHODES_MCP_URL).strip(),
        api_key=api_key,
    )


def _rhodes_headers(api_key: str, *, session_id: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _parse_json_rpc_response(resp: requests.Response) -> dict[str, Any]:
    if not resp.text:
        return {}
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in resp.text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("data:"):
                continue
            payload = stripped.removeprefix("data:").strip()
            if payload == "[DONE]":
                continue
            data = json.loads(payload)
            if isinstance(data, dict):
                return data
        return {}
    try:
        data = resp.json()
    except ValueError as exc:
        raise RhodesError(f"Rhodes MCP returned non-JSON response: {resp.text[:200]}") from exc
    if not isinstance(data, dict):
        raise RhodesError("Rhodes MCP returned a non-object JSON response")
    return data


def _raise_for_rhodes_error(resp: requests.Response) -> None:
    if resp.ok:
        return
    body = resp.text[:500] if resp.text else ""
    method = getattr(resp.request, "method", "REQUEST")
    url = getattr(resp.request, "url", "")
    raise RhodesError(f"Rhodes MCP {method} {url} returned {resp.status_code}: {body}")


def _extract_tool_payload(json_rpc: dict[str, Any]) -> Any:
    if "error" in json_rpc:
        raise RhodesError(f"Rhodes MCP error: {json_rpc['error']}")

    result = json_rpc.get("result", {})
    if not isinstance(result, dict):
        return result
    if result.get("isError"):
        raise RhodesError(f"Rhodes tool returned error: {result}")

    structured = result.get("structuredContent")
    if structured is not None:
        return structured

    content = result.get("content")
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
        joined = "\n".join(text_parts).strip()
        if not joined:
            return {}
        try:
            return json.loads(joined)
        except ValueError:
            return {"text": joined}
    return result


def _unwrap_single(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return payload


def _coerce_site(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        first = next((item for item in payload if isinstance(item, dict)), {})
        return first
    if isinstance(payload, dict):
        return _unwrap_single(payload, "site", "record", "data")
    return {}


def _coerce_site_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("sites", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _coerce_document(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        first = next((item for item in payload if isinstance(item, dict)), {})
        return first
    if isinstance(payload, dict):
        return _unwrap_single(payload, "document", "record", "data")
    return {}


def _coerce_document_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("documents", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _coerce_note(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        first = next((item for item in payload if isinstance(item, dict)), {})
        return first
    if isinstance(payload, dict):
        return _unwrap_single(payload, "note", "record", "data")
    return {}


def _coerce_note_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("notes", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _coerce_task_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("tasks", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _coerce_user(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        first = next((item for item in payload if isinstance(item, dict)), {})
        return first
    if isinstance(payload, dict):
        return _unwrap_single(payload, "user", "record", "data")
    return {}


def _document_id(document: dict[str, Any]) -> str:
    for key in ("documentId", "_id", "id"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _document_drive_file_id(document: dict[str, Any]) -> str:
    for key in ("driveFileId", "drive_file_id", "fileId", "googleDriveFileId"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    drive_file = document.get("driveFile") or document.get("googleDriveFile")
    if isinstance(drive_file, dict):
        for key in ("id", "fileId", "driveFileId"):
            value = drive_file.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _response_id(payload: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for nested_key in ("note", "user", "record", "result", "data"):
        nested_id = _response_id(payload.get(nested_key), keys)
        if nested_id:
            return nested_id
    return ""


def _note_id(note: dict[str, Any]) -> str:
    return _response_id(note, ("noteId", "_id", "id"))


def _user_id(user: dict[str, Any]) -> str:
    return _response_id(user, ("userId", "_id", "id"))


@dataclass(frozen=True)
class RhodesDocumentMapping:
    """DDR inbox doc-type mapping to Rhodes document metadata."""

    doc_type: str
    milestone: str | None = None
    quality_bar: str | None = None


DDR_DOC_TYPE_TO_RHODES: dict[str, RhodesDocumentMapping] = {
    "sir": RhodesDocumentMapping("siteInvestigationReport", milestone="acquireProperty"),
    "building_inspection": RhodesDocumentMapping(
        "propertyConditionAssessment",
        milestone="acquireProperty",
    ),
    "block_plan": RhodesDocumentMapping("floorPlan", milestone="acquireProperty"),
    "isp": RhodesDocumentMapping("other", milestone="acquireProperty"),
    "opening_plan_report": RhodesDocumentMapping(
        "other",
        milestone="acquireProperty",
    ),
    "alpha_phasing_plan_report": RhodesDocumentMapping(
        "other",
        milestone="acquireProperty",
    ),
}


def _site_id(site: dict[str, Any]) -> str:
    for key in ("siteId", "_id", "id"):
        value = site.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _site_name(site: dict[str, Any]) -> str:
    for key in ("name", "title", "marketingName", "marketing_name"):
        value = site.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _site_address(site: dict[str, Any]) -> str:
    for key in ("address", "siteAddress", "site_address", "propertyAddress"):
        value = site.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_p1_dri(site: dict[str, Any]) -> dict[str, str]:
    owner: dict[str, str] = {}
    p1_dri = site.get("p1Dri")
    if isinstance(p1_dri, dict):
        for target, keys in {
            "name": ("name", "fullName", "displayName"),
            "email": ("email", "emailAddress", "primaryEmail"),
            "userId": ("userId", "_id", "id"),
        }.items():
            for key in keys:
                value = p1_dri.get(key)
                if isinstance(value, str) and value.strip():
                    owner[target] = value.strip()
                    break

    for target, keys in {
        "name": ("p1DriName", "p1_dri_name", "p1AssigneeName"),
        "email": ("p1DriEmail", "p1_dri_email", "p1AssigneeEmail"),
        "userId": ("p1DriUserId", "p1_dri_user_id", "p1AssigneeUserId"),
    }.items():
        if owner.get(target):
            continue
        for key in keys:
            value = site.get(key)
            if isinstance(value, str) and value.strip():
                owner[target] = value.strip()
                break
    return owner


def _drive_folder_url(folder_id: str) -> str:
    return f"{DRIVE_FOLDER_URL_PREFIX}{folder_id.strip()}"


def _extract_drive_folder(site: dict[str, Any]) -> tuple[str, str]:
    """Return ``(folder_id, folder_url)`` from known Rhodes site fields."""
    folder_id = ""
    folder_url = ""
    for key in (
        "driveFolderId",
        "googleDriveFolderId",
        "drive_folder_id",
        "google_drive_folder_id",
    ):
        value = site.get(key)
        if isinstance(value, str) and value.strip():
            folder_id = value.strip()
            break

    for key in (
        "driveFolderUrl",
        "googleDriveFolderUrl",
        "drive_folder_url",
        "google_drive_folder_url",
        "driveUrl",
    ):
        value = site.get(key)
        if isinstance(value, str) and value.strip():
            folder_url = value.strip()
            break

    drive_folder = site.get("driveFolder") or site.get("googleDriveFolder")
    if isinstance(drive_folder, dict):
        if not folder_id:
            for key in ("id", "folderId", "driveFolderId"):
                value = drive_folder.get(key)
                if isinstance(value, str) and value.strip():
                    folder_id = value.strip()
                    break
        if not folder_url:
            for key in ("url", "webViewLink", "driveFolderUrl"):
                value = drive_folder.get(key)
                if isinstance(value, str) and value.strip():
                    folder_url = value.strip()
                    break

    if folder_id and not folder_url:
        folder_url = _drive_folder_url(folder_id)
    if folder_url and not folder_id and DRIVE_FOLDER_URL_PREFIX in folder_url:
        folder_id = folder_url.rsplit("/", 1)[-1].split("?", 1)[0].strip()
    return folder_id, folder_url


def _site_created_date(site: dict[str, Any], summary: dict[str, Any]) -> str:
    return str(site.get("createdDate") or summary.get("createdDate") or "")


def _site_status(
    site: dict[str, Any],
    summary: dict[str, Any],
    fallback_status: str | None,
) -> str:
    return str(site.get("status") or summary.get("status") or fallback_status or "")


def _site_custom_fields(site: dict[str, Any]) -> list[Any]:
    custom_fields = site.get("customFields")
    return custom_fields if isinstance(custom_fields, list) else []


def _site_summary_is_drive_ready(summary: dict[str, Any]) -> bool:
    """Return True when a listSites row already has the fields callers need.

    This avoids paying a getSite call for APIs that already return rich site
    summaries while preserving hydration for older/leaner listSites payloads.
    """
    _folder_id, folder_url = _extract_drive_folder(summary)
    owner = _extract_p1_dri(summary)
    return bool(
        _site_id(summary)
        and _site_name(summary)
        and _site_address(summary)
        and folder_url
        and (owner.get("name") or owner.get("email"))
    )


def _should_try_active_location_lookup(*, name: str, address: str) -> bool:
    """Return True for broad name-only lookups like "Houston"."""
    clean_name = name.strip()
    if not clean_name or address.strip():
        return False
    if any(char.isdigit() for char in clean_name):
        return False
    return len(clean_name.split()) <= 3


def _record_from_site_payload(
    site: dict[str, Any],
    *,
    summary: dict[str, Any] | None = None,
    status: str | None,
) -> dict[str, Any] | None:
    summary = summary or {}
    site_id = _site_id(site) or _site_id(summary)
    name = _site_name(site) or _site_name(summary)
    if not site_id or not name:
        return None
    drive_folder_id, drive_folder_url = _extract_drive_folder(site)
    owner = _extract_p1_dri(site)
    return {
        "id": site_id,
        "site_id": site_id,
        "title": name,
        "name": name,
        "slug": site.get("slug") or summary.get("slug") or "",
        "address": _site_address(site) or _site_address(summary),
        "drive_folder_id": drive_folder_id,
        "drive_folder_url": drive_folder_url,
        "p1_assignee_name": owner.get("name", ""),
        "p1_assignee_email": owner.get("email", ""),
        "p1_assignee_user_id": owner.get("userId", ""),
        "created_date": _site_created_date(site, summary),
        "status": _site_status(site, summary, status),
        "rhodes_status": site.get("status") or summary.get("status") or "",
        "customFields": _site_custom_fields(site),
    }


class RhodesClient:
    """Small HTTP JSON-RPC client for read-only Rhodes site lookups."""

    def __init__(self, cfg: RhodesConfig | None = None) -> None:
        self.cfg = cfg or load_rhodes_config()
        self._session = requests.Session()
        self._session_id: str | None = None
        self._initialized = False
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _post_json_rpc(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._session.post(
                self.cfg.mcp_url,
                headers=_rhodes_headers(self.cfg.api_key, session_id=self._session_id),
                json=payload,
                timeout=RHODES_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RhodesError(f"Rhodes MCP request failed: {exc}") from exc

        if not self._session_id:
            self._session_id = (
                resp.headers.get("Mcp-Session-Id")
                or resp.headers.get("mcp-session-id")
                or resp.headers.get("MCP-Session-Id")
            )

        _raise_for_rhodes_error(resp)
        if resp.status_code == 202 and not resp.text:
            return {}
        return _parse_json_rpc_response(resp)

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        init_response = self._post_json_rpc(
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "due-diligence-reporter",
                        "version": "0.2.0",
                    },
                },
                "id": self._next_id(),
            }
        )
        if "error" in init_response:
            raise RhodesError(f"Rhodes MCP initialize failed: {init_response['error']}")
        self._post_json_rpc(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )
        self._initialized = True

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call a Rhodes MCP tool and return the decoded tool payload."""
        self._ensure_initialized()
        response = self._post_json_rpc(
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments,
                },
                "id": self._next_id(),
            }
        )
        return _extract_tool_payload(response)

    def get_site(self, *, site_id: str | None = None, slug: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if site_id:
            payload["siteId"] = site_id
        elif slug:
            payload["slug"] = slug
        else:
            raise RhodesError("site_id or slug is required")
        site = _coerce_site(self.call_tool("getSite", payload))
        if not site:
            raise RhodesError("Rhodes site not found")
        return site

    def resolve_drive_root(self, *, site_id: str) -> tuple[str, str]:
        """Resolve the linked Rhodes site Drive root folder."""
        if not site_id.strip():
            raise RhodesError("site_id is required")
        payload = self.call_tool(
            "driveResolveSiteFolderPath",
            {"siteId": site_id.strip(), "folderPath": ""},
        )
        if not isinstance(payload, dict):
            raise RhodesError("Rhodes Drive resolver returned a non-object payload")
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            raise RhodesError(error.strip())
        folder_id = payload.get("folderId")
        if not isinstance(folder_id, str) or not folder_id.strip():
            raise RhodesError("Rhodes site has no linked Google Drive folder")
        return folder_id.strip(), _drive_folder_url(folder_id)

    def resolve_site(self, *, name: str = "", address: str = "") -> dict[str, Any] | None:
        lookup = name.strip() or address.strip()
        if lookup:
            if _should_try_active_location_lookup(name=name, address=address):
                try:
                    matches = self.list_sites(status="active", location=name.strip())
                except RhodesError:
                    matches = []
                if len(matches) == 1 and _site_id(matches[0]):
                    return matches[0]
                exact_active_matches = [
                    match
                    for match in matches
                    if _site_name(match).casefold() == name.strip().casefold()
                ]
                if len(exact_active_matches) == 1 and _site_id(exact_active_matches[0]):
                    return exact_active_matches[0]

            try:
                site = _coerce_site(self.call_tool("resolveSite", {"name": lookup}))
            except RhodesError as exc:
                msg = str(exc).lower()
                if "not found" not in msg and "no site" not in msg and "no match" not in msg:
                    raise
                site = {}
            if _site_id(site):
                return site

        if address.strip():
            matches = _coerce_site_list(self.call_tool("listSites", {"location": address.strip()}))
            return matches[0] if matches else None
        return None

    def list_sites(
        self,
        *,
        status: str | None = "active",
        stage: str | None = None,
        location: str | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {}
        if status:
            payload["status"] = status
        if stage:
            payload["stage"] = stage
        if location:
            payload["location"] = location
        return _coerce_site_list(self.call_tool("listSites", payload))

    def list_documents(
        self,
        *,
        site_id: str,
        doc_type: str | None = None,
        milestone: str | None = None,
    ) -> list[dict[str, Any]]:
        if not site_id.strip():
            raise RhodesError("site_id is required")
        payload: dict[str, Any] = {"siteId": site_id.strip()}
        if doc_type:
            payload["docType"] = doc_type
        if milestone:
            payload["milestone"] = milestone
        return _coerce_document_list(self.call_tool("listDocuments", payload))

    def get_missing_documents(
        self,
        *,
        site_id: str,
    ) -> dict[str, Any]:
        if not site_id.strip():
            raise RhodesError("site_id is required")
        payload = self.call_tool("getMissingDocuments", {"siteId": site_id.strip()})
        return payload if isinstance(payload, dict) else {}

    def register_document(
        self,
        *,
        site_id: str,
        title: str,
        doc_type: str,
        drive_file_id: str,
        drive_url: str = "",
        mime_type: str = "",
        milestone: str | None = None,
        quality_bar: str | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        if not site_id.strip():
            raise RhodesError("site_id is required")
        if not title.strip():
            raise RhodesError("title is required")
        if not doc_type.strip():
            raise RhodesError("doc_type is required")
        if not drive_file_id.strip():
            raise RhodesError("drive_file_id is required")

        payload: dict[str, Any] = {
            "siteId": site_id.strip(),
            "title": title.strip(),
            "docType": doc_type.strip(),
            "driveFileId": drive_file_id.strip(),
        }
        if drive_url.strip():
            payload["driveUrl"] = drive_url.strip()
        if mime_type.strip():
            payload["mimeType"] = mime_type.strip()
        if milestone:
            payload["milestone"] = milestone
        if quality_bar:
            payload["qualityBar"] = quality_bar
        if notes.strip():
            payload["notes"] = notes.strip()
        return _coerce_document(self.call_tool("registerDocument", payload))

    def add_site_note(
        self,
        *,
        site_id: str = "",
        site_slug: str = "",
        body: str,
        mentions: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        clean_site_id = site_id.strip()
        clean_site_slug = site_slug.strip()
        if not clean_site_id and not clean_site_slug:
            raise RhodesError("site_id or site_slug is required")
        if not body.strip():
            raise RhodesError("body is required")

        payload: dict[str, Any] = {
            "anchorType": "site",
            "body": body.strip(),
        }
        if clean_site_id:
            payload["siteId"] = clean_site_id
            payload["anchorId"] = clean_site_id
        else:
            payload["siteSlug"] = clean_site_slug
        clean_mentions = [m.strip() for m in (mentions or []) if m.strip()]
        if clean_mentions:
            payload["mentions"] = clean_mentions
        return _coerce_note(self.call_tool("addNote", payload))

    def list_notes(
        self,
        *,
        site_id: str = "",
        site_slug: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clean_site_id = site_id.strip()
        clean_site_slug = site_slug.strip()
        if not clean_site_id and not clean_site_slug:
            raise RhodesError("site_id or site_slug is required")
        payload: dict[str, Any] = {"limit": limit}
        if clean_site_id:
            payload["siteId"] = clean_site_id
        else:
            payload["siteSlug"] = clean_site_slug
        return _coerce_note_list(self.call_tool("listNotes", payload))

    def list_tasks(
        self,
        *,
        site_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if not site_id.strip():
            raise RhodesError("site_id is required")
        payload: dict[str, Any] = {"siteId": site_id.strip()}
        if status:
            payload["status"] = status
        return _coerce_task_list(self.call_tool("listTasks", payload))

    def find_site_note_by_body(
        self,
        *,
        site_id: str = "",
        site_slug: str = "",
        body: str,
    ) -> dict[str, Any] | None:
        clean_body = body.strip()
        if not clean_body:
            return None
        notes = self.list_notes(site_id=site_id, site_slug=site_slug, limit=50)
        for note in notes:
            if str(note.get("body") or "").strip() == clean_body:
                return note
        return None

    def get_user(
        self,
        *,
        email: str = "",
        user_id: str = "",
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {}
        if user_id.strip():
            payload["userId"] = user_id.strip()
        elif email.strip():
            payload["email"] = email.strip()
        else:
            raise RhodesError("email or user_id is required")
        user = _coerce_user(self.call_tool("getUser", payload))
        return user or None

    def find_document_by_drive_file_id(
        self,
        *,
        site_id: str,
        drive_file_id: str,
        doc_type: str | None = None,
        milestone: str | None = None,
    ) -> dict[str, Any] | None:
        target = drive_file_id.strip()
        if not target:
            return None
        for document in self.list_documents(
            site_id=site_id,
            doc_type=doc_type,
            milestone=milestone,
        ):
            if _document_drive_file_id(document) == target:
                return document
        return None


def lookup_rhodes_site_owner(
    *,
    site_name: str = "",
    site_address: str = "",
    site_id: str = "",
    slug: str = "",
    client: RhodesClient | None = None,
) -> dict[str, Any]:
    """Resolve a site's P1 DRI from Rhodes without mutating Rhodes."""
    if not any(value.strip() for value in (site_name, site_address, site_id, slug)):
        return {
            "status": "error",
            "message": "Provide site_name, site_address, site_id, or slug.",
            "report_data_fields": {},
        }

    try:
        rhodes = client or RhodesClient()
    except RhodesError as exc:
        return {
            "status": "not_configured",
            "message": str(exc),
            "report_data_fields": {},
        }

    try:
        if site_id.strip() or slug.strip():
            site = rhodes.get_site(site_id=site_id.strip() or None, slug=slug.strip() or None)
        else:
            site = rhodes.resolve_site(name=site_name, address=site_address) or {}
            resolved_id = _site_id(site)
            if resolved_id and not _site_summary_is_drive_ready(site):
                site = rhodes.get_site(site_id=resolved_id)
    except RhodesError as exc:
        return {
            "status": "error",
            "message": str(exc),
            "report_data_fields": {},
        }

    if not site:
        return {
            "status": "not_found",
            "message": "No Rhodes site matched the supplied site context.",
            "report_data_fields": {},
        }

    owner = _extract_p1_dri(site)
    resolved_site_id = _site_id(site)
    drive_folder_id, drive_folder_url = _extract_drive_folder(site)
    drive_folder_status = "missing"
    drive_folder_message = ""
    if drive_folder_url:
        drive_folder_status = "found"
    elif resolved_site_id:
        try:
            drive_folder_id, drive_folder_url = rhodes.resolve_drive_root(
                site_id=resolved_site_id
            )
            drive_folder_status = "found"
        except RhodesError as exc:
            drive_folder_status = "missing"
            drive_folder_message = str(exc)
    report_fields: dict[str, str] = {}
    owner_name = owner.get("name", "")
    owner_email = owner.get("email", "")
    if owner_name:
        report_fields["p1_assignee_name"] = owner_name
        report_fields["site.p1_assignee_name"] = owner_name
        report_fields["meta.prepared_by"] = owner_name
    if owner_email:
        report_fields["p1_assignee_email"] = owner_email
        report_fields["site.p1_assignee_email"] = owner_email

    resolved_address = site.get("address")
    if isinstance(resolved_address, str) and resolved_address.strip():
        report_fields["site.address"] = resolved_address.strip()
        report_fields["site.site_address"] = resolved_address.strip()

    created_date = site.get("createdDate")
    if isinstance(created_date, str) and created_date.strip():
        report_fields["site_created_at"] = created_date.strip()
    if drive_folder_url:
        report_fields["meta.drive_folder_url"] = drive_folder_url
        report_fields["site.drive_folder_url"] = drive_folder_url

    result = {
        "status": "found" if owner_name or owner_email else "owner_missing",
        "site_id": resolved_site_id,
        "site_name": site.get("name") or site_name,
        "site_slug": site.get("slug") or slug,
        "site_address": site.get("address") or site_address,
        "drive_folder_status": drive_folder_status,
        "drive_folder_id": drive_folder_id,
        "drive_folder_url": drive_folder_url,
        "p1_assignee_name": owner_name,
        "p1_assignee_email": owner_email,
        "p1_dri": owner,
        "report_data_fields": report_fields,
    }
    if drive_folder_message:
        result["drive_folder_message"] = drive_folder_message
    if result["status"] == "owner_missing":
        result["message"] = "Rhodes site exists, but p1Dri is not assigned."
    return result


def add_rhodes_site_note(
    *,
    site_id: str,
    site_slug: str = "",
    body: str,
    owner_user_id: str = "",
    owner_email: str = "",
    extra_mention_user_ids: Iterable[str] | None = None,
    client: RhodesClient | None = None,
) -> dict[str, Any]:
    """Create a Rhodes site note and mention the owner when possible."""
    clean_site_id = site_id.strip()
    clean_site_slug = site_slug.strip()
    clean_body = body.strip()
    clean_owner_user_id = owner_user_id.strip()
    clean_owner_email = owner_email.strip()
    base = {
        "rhodes_site_id": clean_site_id,
        "rhodes_site_slug": clean_site_slug,
        "owner_user_id": clean_owner_user_id,
        "owner_email": clean_owner_email,
        "owner_notification": "none",
    }
    if not clean_site_id and not clean_site_slug:
        return {**base, "status": "skipped", "reason": "missing_site_identity"}
    if not clean_body:
        return {**base, "status": "skipped", "reason": "missing_body"}

    try:
        rhodes = client or RhodesClient()
        resolved_owner_user_id = clean_owner_user_id
        owner_resolution = "provided" if resolved_owner_user_id else "none"
        if not resolved_owner_user_id and clean_owner_email:
            try:
                user = rhodes.get_user(email=clean_owner_email)
            except RhodesError:
                user = None
                owner_resolution = "lookup_failed"
            else:
                resolved_owner_user_id = _user_id(user or {})
                owner_resolution = "resolved_from_email" if resolved_owner_user_id else "not_found"
        mention_user_ids = _unique_nonempty(
            [
                resolved_owner_user_id,
                *(extra_mention_user_ids or []),
            ]
        )
        note_response_summaries: list[dict[str, Any]] = []
        note = rhodes.add_site_note(
            site_id=clean_site_id,
            site_slug=clean_site_slug,
            body=clean_body,
            mentions=mention_user_ids,
        )
        note_response_summaries.append(
            _summarize_note_response(
                note,
                attempt="site_id" if clean_site_id else "site_slug",
            )
        )
        note_id = _note_id(note)
        if not note_id:
            note = _recover_note_without_id(
                rhodes,
                site_id=clean_site_id,
                site_slug=clean_site_slug,
                body=clean_body,
                mentions=mention_user_ids,
                response_summaries=note_response_summaries,
            )
            note_id = _note_id(note)
    except RhodesError as exc:
        return {**base, "status": "failed", "reason": "rhodes_error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - non-fatal scanner side effect
        return {**base, "status": "failed", "reason": "unexpected_error", "error": str(exc)}

    if not note_id:
        return {
            **base,
            "status": "failed",
            "reason": "missing_note_id",
            "error": "Rhodes addNote returned no note ID; delivery could not be verified",
            "rhodes_note_id": "",
            "owner_user_id": resolved_owner_user_id,
            "owner_resolution": owner_resolution,
            "mentioned_user_ids": mention_user_ids,
            "note_response_summaries": note_response_summaries,
        }

    return {
        **base,
        "status": "created",
        "reason": "ok",
        "rhodes_note_id": note_id,
        "owner_user_id": resolved_owner_user_id,
        "owner_resolution": owner_resolution,
        "owner_notification": "mentioned" if resolved_owner_user_id else "none",
        "mentioned_user_ids": mention_user_ids,
    }


def _recover_note_without_id(
    rhodes: RhodesClient,
    *,
    site_id: str,
    site_slug: str,
    body: str,
    mentions: Iterable[str],
    response_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recover a note ID after an empty addNote response, then retry by slug."""

    recovered = _find_note_after_empty_response(
        rhodes,
        site_id=site_id,
        site_slug=site_slug,
        body=body,
    )
    if recovered is not None:
        return recovered

    if site_slug:
        note = rhodes.add_site_note(site_slug=site_slug, body=body, mentions=mentions)
        response_summaries.append(
            _summarize_note_response(note, attempt="site_slug_retry")
        )
        if _note_id(note):
            return note
        recovered = _find_note_after_empty_response(
            rhodes,
            site_id="",
            site_slug=site_slug,
            body=body,
        )
        if recovered is not None:
            return recovered
    return {}


def _find_note_after_empty_response(
    rhodes: RhodesClient,
    *,
    site_id: str,
    site_slug: str,
    body: str,
) -> dict[str, Any] | None:
    try:
        return rhodes.find_site_note_by_body(
            site_id=site_id,
            site_slug=site_slug,
            body=body,
        )
    except RhodesError:
        return None


def _summarize_note_response(
    note: dict[str, Any],
    *,
    attempt: str,
) -> dict[str, Any]:
    """Return a safe shape summary for diagnosing no-ID addNote responses."""

    summary: dict[str, Any] = {
        "attempt": attempt,
        "type": type(note).__name__,
        "has_note_id": bool(_note_id(note)),
    }
    if isinstance(note, dict):
        summary["keys"] = sorted(str(key) for key in note.keys())
        for key in ("status", "reason", "rejectionReason", "message", "error"):
            value = _safe_note_response_scalar(note.get(key))
            if value:
                summary[key] = value
        text = note.get("text")
        if isinstance(text, str) and text.strip():
            summary["text_prefix"] = text.strip()[:240]
        nested_note = note.get("note")
        if isinstance(nested_note, dict):
            summary["note_keys"] = sorted(str(key) for key in nested_note.keys())
    return summary


def _safe_note_response_scalar(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()[:240]
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float):
        return str(value)
    return ""


def _unique_nonempty(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        cleaned.append(clean)
    return cleaned


def map_ddr_doc_type_to_rhodes(ddr_doc_type: str) -> RhodesDocumentMapping | None:
    return DDR_DOC_TYPE_TO_RHODES.get(ddr_doc_type.strip())


def register_rhodes_document_for_upload(
    *,
    site_id: str,
    ddr_doc_type: str,
    title: str,
    drive_file_id: str,
    drive_url: str = "",
    mime_type: str = "application/pdf",
    original_filename: str = "",
    source: str = "inbox_scanner",
    message_id: str = "",
    attachment_id: str = "",
    client: RhodesClient | None = None,
) -> dict[str, Any]:
    """Idempotently register a DDR-uploaded Drive file on a Rhodes site.

    This helper is intentionally non-raising for scanner callers: Drive filing
    is the primary action, and Rhodes registration is a follow-on system-of-
    record link. Failures are returned as structured status rows.
    """
    clean_site_id = site_id.strip()
    clean_drive_file_id = drive_file_id.strip()
    mapping = map_ddr_doc_type_to_rhodes(ddr_doc_type)
    base = {
        "rhodes_site_id": clean_site_id,
        "drive_file_id": clean_drive_file_id,
        "ddr_doc_type": ddr_doc_type,
        "rhodes_doc_type": mapping.doc_type if mapping else "",
        "rhodes_milestone": mapping.milestone if mapping else "",
    }

    if not clean_site_id:
        return {**base, "status": "skipped", "reason": "missing_site_id"}
    if not clean_drive_file_id:
        return {**base, "status": "skipped", "reason": "missing_drive_file_id"}
    if mapping is None:
        return {**base, "status": "skipped", "reason": "unmapped_doc_type"}

    try:
        rhodes = client or RhodesClient()
        existing = rhodes.find_document_by_drive_file_id(
            site_id=clean_site_id,
            drive_file_id=clean_drive_file_id,
            doc_type=mapping.doc_type,
            milestone=mapping.milestone,
        )
        if existing is not None:
            return {
                **base,
                "status": "already_registered",
                "reason": "already_linked",
                "rhodes_document_id": _document_id(existing),
            }

        registered = rhodes.register_document(
            site_id=clean_site_id,
            title=title,
            doc_type=mapping.doc_type,
            drive_file_id=clean_drive_file_id,
            drive_url=drive_url,
            mime_type=mime_type,
            milestone=mapping.milestone,
            quality_bar=mapping.quality_bar,
            notes=_build_registration_notes(
                source=source,
                ddr_doc_type=ddr_doc_type,
                original_filename=original_filename,
                drive_file_id=clean_drive_file_id,
                drive_url=drive_url,
                message_id=message_id,
                attachment_id=attachment_id,
            ),
        )
    except RhodesError as exc:
        return {**base, "status": "failed", "reason": "rhodes_error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - non-fatal scanner side effect
        return {**base, "status": "failed", "reason": "unexpected_error", "error": str(exc)}

    return {
        **base,
        "status": "registered",
        "reason": "ok",
        "rhodes_document_id": _document_id(registered),
    }


def _build_registration_notes(
    *,
    source: str,
    ddr_doc_type: str,
    original_filename: str,
    drive_file_id: str,
    drive_url: str,
    message_id: str,
    attachment_id: str,
) -> str:
    parts = [
        f"Registered by DDR {source}.",
        f"DDR doc type: {ddr_doc_type}.",
        f"Drive file ID: {drive_file_id}.",
    ]
    if original_filename.strip():
        parts.append(f"Original filename: {original_filename.strip()}.")
    if drive_url.strip():
        parts.append(f"Drive URL: {drive_url.strip()}.")
    if message_id.strip():
        parts.append(f"Gmail message ID: {message_id.strip()}.")
    if attachment_id.strip():
        parts.append(f"Gmail attachment ID: {attachment_id.strip()}.")
    return "\n".join(parts)


def list_rhodes_site_records(
    *,
    status: str | None = "active",
    site_ids: Iterable[str] | None = None,
    client: RhodesClient | None = None,
) -> list[dict[str, Any]]:
    """Return Rhodes site records shaped for inbox attachment matching.

    The inbox scanner needs a compact local list to match filenames and email
    subjects before it uploads vendor documents. By default this returns active
    sites; pass ``status=None`` when historical/cancelled Rhodes records should
    also be eligible for matching. Each returned record includes the Rhodes site
    ID, title, address when available, and the linked Rhodes Google Drive root
    folder URL.
    """
    try:
        rhodes = client or RhodesClient()
        target_site_ids = [
            str(site_id).strip()
            for site_id in (site_ids or [])
            if str(site_id).strip()
        ]
        if target_site_ids:
            site_summaries = [
                rhodes.get_site(site_id=site_id) for site_id in target_site_ids
            ]
        else:
            site_summaries = rhodes.list_sites(status=status)
    except RhodesError:
        raise

    records: list[dict[str, Any]] = []
    for summary in site_summaries:
        site_id = _site_id(summary)
        if not site_id:
            continue
        site = summary
        if not target_site_ids and not _site_summary_is_drive_ready(summary):
            try:
                site = rhodes.get_site(site_id=site_id)
            except RhodesError:
                site = summary

        drive_folder_id, drive_folder_url = _extract_drive_folder(site)
        if not drive_folder_url:
            try:
                drive_folder_id, drive_folder_url = rhodes.resolve_drive_root(
                    site_id=site_id
                )
            except RhodesError:
                drive_folder_id = drive_folder_id or ""
                drive_folder_url = ""

        enriched_site = dict(site)
        if drive_folder_id:
            enriched_site.setdefault("driveFolderId", drive_folder_id)
        if drive_folder_url:
            enriched_site.setdefault("driveFolderUrl", drive_folder_url)
        record = _record_from_site_payload(enriched_site, summary=summary, status=status)
        if record is None:
            continue
        records.append(record)
    return records
