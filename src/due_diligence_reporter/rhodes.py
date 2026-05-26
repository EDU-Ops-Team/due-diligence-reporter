"""Read-only Rhodes/LocationOS lookup helpers for DDR generation."""

from __future__ import annotations

import json
import os
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
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {}
        if status:
            payload["status"] = status
        if stage:
            payload["stage"] = stage
        return _coerce_site_list(self.call_tool("listSites", payload))


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
            if resolved_id and "p1Dri" not in site:
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


def list_rhodes_site_records(
    *,
    status: str = "active",
    client: RhodesClient | None = None,
) -> list[dict[str, Any]]:
    """Return Rhodes site records shaped for inbox attachment matching.

    The inbox scanner needs a compact local list to match filenames and email
    subjects before it uploads vendor documents. Each returned record includes
    the Rhodes site ID, title, address when available, and the linked Rhodes
    Google Drive root folder URL.
    """
    try:
        rhodes = client or RhodesClient()
        site_summaries = rhodes.list_sites(status=status)
    except RhodesError:
        raise

    records: list[dict[str, Any]] = []
    for summary in site_summaries:
        site_id = _site_id(summary)
        if not site_id:
            continue
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

        name = _site_name(site) or _site_name(summary)
        if not name:
            continue
        records.append(
            {
                "id": site_id,
                "site_id": site_id,
                "title": name,
                "name": name,
                "slug": site.get("slug") or summary.get("slug") or "",
                "address": _site_address(site) or _site_address(summary),
                "drive_folder_id": drive_folder_id,
                "drive_folder_url": drive_folder_url,
                "rhodes_status": site.get("status") or summary.get("status") or "",
            }
        )
    return records
