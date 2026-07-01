"""Rhodes/LocationOS lookup and document registration helpers for DDR."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import requests

DEFAULT_RHODES_MCP_URL = "https://location-os-mcp.ephor.workers.dev/mcp"
DEFAULT_AERIE_API_BASE_URL = "https://edu-ops.klair.ai/api"
MCP_PROTOCOL_VERSION = "2025-03-26"
RHODES_TIMEOUT_SECONDS = 20.0
DRIVE_FOLDER_URL_PREFIX = "https://drive.google.com/drive/folders/"
LOCATIONOS_NOT_CONFIGURED_REASON = "locationos_mcp_not_configured"
NOTES_API_NOT_CONFIGURED_REASON = "aerie_notes_api_not_configured"
DOCUMENT_REGISTRATION_PENDING_USER_ACTION = "pending_user_action"
DOCUMENT_REGISTRATION_FOLLOWUP_TYPE = "document_registration"
DOCUMENT_REGISTRATION_FALLBACK_OWNER_EMAIL = "greg.foote@trilogy.com"
REQUEST_ID_RE = re.compile(r"Request ID:\s*([A-Za-z0-9-]+)")
DOCUMENT_REGISTRATION_HANDOFF_ERROR_MARKERS = (
    "approval",
    "confirmation",
    "elicitation",
    "oauth",
    "user cancelled",
    "permission denied",
    "not configured",
    "missing locationos mcp bearer token",
)


class RhodesError(RuntimeError):
    """Raised when the Rhodes MCP client cannot complete a lookup."""


@dataclass(frozen=True)
class RhodesConfig:
    """Rhodes MCP configuration."""

    mcp_url: str
    api_key: str


@dataclass(frozen=True)
class AerieApiConfig:
    """Aerie API configuration for headless Rhodes note writes."""

    base_url: str
    api_key: str


def load_rhodes_config() -> RhodesConfig:
    """Load Rhodes MCP configuration from environment variables."""
    api_key = (
        os.getenv("LOCATIONOS_MCP_API_KEY")
        or os.getenv("RHODES_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise RhodesError(
            "Missing LocationOS MCP bearer token env var "
            "(LOCATIONOS_MCP_API_KEY or RHODES_API_KEY)"
        )
    return RhodesConfig(
        mcp_url=(os.getenv("RHODES_MCP_URL") or DEFAULT_RHODES_MCP_URL).strip(),
        api_key=api_key,
    )


def load_aerie_api_config() -> AerieApiConfig:
    """Load Aerie API configuration from environment variables."""
    api_key = os.getenv("AERIE_API_KEY", "").strip()
    if not api_key:
        raise RhodesError("Missing Aerie API bearer token env var (AERIE_API_KEY)")
    return AerieApiConfig(
        base_url=(os.getenv("AERIE_API_BASE_URL") or DEFAULT_AERIE_API_BASE_URL).strip(),
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


def _aerie_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


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
        "phasing",
        milestone="acquireProperty",
    ),
    "alpha_capacity_analysis": RhodesDocumentMapping(
        "capacityCalculation",
        milestone="acquireProperty",
    ),
    "cost_timeline_estimate": RhodesDocumentMapping(
        "initialCostEstimate",
        milestone="acquireProperty",
    ),
    "capacity_brainlift_report": RhodesDocumentMapping(
        "capacityCalculation",
        milestone="acquireProperty",
    ),
    "e_occupancy_report": RhodesDocumentMapping(
        "other",
        milestone="acquireProperty",
    ),
    "outdoor_play_space_report": RhodesDocumentMapping(
        "other",
        milestone="acquireProperty",
        quality_bar="outdoorRecreation",
    ),
    "security_due_diligence_report": RhodesDocumentMapping(
        "other",
        milestone="acquireProperty",
    ),
    "school_approval_report": RhodesDocumentMapping(
        "regulatoryApproval",
        milestone="acquireProperty",
    ),
    "certificate_of_occupancy": RhodesDocumentMapping(
        "certificateOfOccupancy",
        milestone="acquireProperty",
    ),
    "permit_of_record": RhodesDocumentMapping("permit", milestone="acquireProperty"),
    "measured_floor_plan": RhodesDocumentMapping("floorPlan", milestone="acquireProperty"),
    "floor_plan": RhodesDocumentMapping("floorPlan", milestone="acquireProperty"),
    "lidar": RhodesDocumentMapping("lidar", milestone="acquireProperty"),
    "traffic_analysis": RhodesDocumentMapping(
        "other",
        milestone="acquireProperty",
        quality_bar="transportation",
    ),
}


def _site_id(site: dict[str, Any]) -> str:
    for key in ("siteId", "site_id", "_id", "id"):
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


def _normalize_location_text(value: str) -> str:
    parts: list[str] = []
    previous_was_space = False
    for char in value.casefold():
        if char.isalnum():
            parts.append(char)
            previous_was_space = False
            continue
        if not previous_was_space:
            parts.append(" ")
            previous_was_space = True
    return " ".join("".join(parts).split())


def _location_field(site: dict[str, Any], key: str) -> str:
    value = site.get(key)
    return value.strip() if isinstance(value, str) else ""


def _contains_location_phrase(value: str, lookup: str) -> bool:
    return f" {lookup} " in f" {value} "


def _active_location_match_score(site: dict[str, Any], lookup: str) -> int:
    normalized_lookup = _normalize_location_text(lookup)
    if not normalized_lookup:
        return 0

    score = 0
    site_name = _normalize_location_text(_site_name(site))
    if site_name == normalized_lookup or site_name == f"alpha {normalized_lookup}":
        score += 120
    elif site_name.startswith(f"alpha {normalized_lookup} "):
        score += 90
    elif _contains_location_phrase(site_name, normalized_lookup):
        score += 60

    for key in ("market", "marketId", "region"):
        field = _normalize_location_text(_location_field(site, key))
        if field == normalized_lookup:
            score += 70
        elif _contains_location_phrase(field, normalized_lookup):
            score += 35

    address = _normalize_location_text(_site_address(site))
    if _contains_location_phrase(address, normalized_lookup):
        score += 40

    metro = _normalize_location_text(_location_field(site, "metroId"))
    if metro == normalized_lookup:
        score += 15

    slug = _normalize_location_text(_location_field(site, "slug"))
    if _contains_location_phrase(slug, normalized_lookup):
        score += 10

    return score


def _pick_unique_active_location_match(
    *,
    name: str,
    matches: list[dict[str, Any]],
) -> dict[str, Any] | None:
    scored_matches = [
        (_active_location_match_score(match, name), match)
        for match in matches
        if _site_id(match)
    ]
    scored_matches = [
        (score, match)
        for score, match in scored_matches
        if score > 0
    ]
    if not scored_matches:
        return None

    scored_matches.sort(key=lambda item: item[0], reverse=True)
    best_score, best_match = scored_matches[0]
    if len(scored_matches) == 1 or best_score > scored_matches[1][0]:
        return best_match
    return None


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
    """Small HTTP JSON-RPC client for Rhodes site lookups and scoped writes."""

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
                unique_location_match = _pick_unique_active_location_match(
                    name=name,
                    matches=matches,
                )
                if unique_location_match is not None:
                    return unique_location_match

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

    def update_due_diligence(
        self,
        *,
        site_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        clean_site_id = site_id.strip()
        if not clean_site_id:
            raise RhodesError("site_id is required")
        clean_fields = _clean_due_diligence_fields(fields)
        if not clean_fields:
            raise RhodesError("at least one due diligence field is required")

        payload: dict[str, Any] = {"siteId": clean_site_id}
        payload.update(clean_fields)
        result = self.call_tool("updateDueDiligence", payload)
        return result if isinstance(result, dict) else {"result": result}

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


class AerieNotesClient:
    """Headless Aerie API client for Rhodes site-note writes and readback."""

    def __init__(
        self,
        cfg: AerieApiConfig | None = None,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self.cfg = cfg or load_aerie_api_config()
        self.session = session or requests.Session()

    def _url(self, path: str) -> str:
        return f"{self.cfg.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self.session.request(
                method,
                self._url(path),
                headers=_aerie_headers(self.cfg.api_key),
                json=json_body,
                params=params,
                timeout=RHODES_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RhodesError(f"Aerie notes API request failed: {exc}") from exc

        if not response.ok:
            request_id = response.headers.get("X-Request-Id", "")
            detail = _aerie_error_detail(response)
            suffix = f" request_id={request_id}" if request_id else ""
            raise RhodesError(
                f"Aerie notes API {method} {path} returned {response.status_code}: "
                f"{detail}{suffix}"
            )

        try:
            payload = response.json() if response.text else {}
        except ValueError as exc:
            raise RhodesError(
                f"Aerie notes API returned non-JSON response: {response.text[:200]}"
            ) from exc
        if not isinstance(payload, dict):
            raise RhodesError("Aerie notes API returned a non-object JSON response")
        return payload

    def create_site_note(
        self,
        *,
        site_id: str = "",
        site_slug: str = "",
        body: str,
        mentions: Iterable[str] | None = None,
        automation_source: str = "due-diligence-reporter",
        decisionmaker_user_id: str = "",
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
        if automation_source.strip():
            payload["automationSource"] = automation_source.strip()[:120]
        if decisionmaker_user_id.strip():
            payload["decisionmakerUserId"] = decisionmaker_user_id.strip()

        response = self._request(
            "POST",
            "/v1/operations/rhodes/notes",
            json_body=payload,
        )
        data = response.get("data")
        return data if isinstance(data, dict) else {}

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
        params: dict[str, Any] = {"limit": limit}
        if clean_site_id:
            params["siteId"] = clean_site_id
        else:
            params["siteSlug"] = clean_site_slug
        response = self._request("GET", "/v1/operations/rhodes/notes", params=params)
        return _coerce_note_list(response)

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
        for note in self.list_notes(site_id=site_id, site_slug=site_slug, limit=50):
            if str(note.get("body") or "").strip() == clean_body:
                return note
        return None


def _aerie_error_detail(response: requests.Response) -> str:
    if not response.text:
        return ""
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    if not isinstance(payload, dict):
        return response.text[:500]
    for key in ("message", "error", "detail"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("message", "code"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:500]
    return response.text[:500]


def _clean_due_diligence_fields(fields: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        clean_key = str(key).strip()
        if not clean_key or clean_key == "siteId":
            continue
        if value is None:
            continue
        if isinstance(value, str):
            clean_value = value.strip()
            if not clean_value:
                continue
            clean[clean_key] = clean_value
        else:
            clean[clean_key] = value
    return clean


def update_rhodes_due_diligence(
    *,
    site_id: str,
    fields: dict[str, Any],
    client: RhodesClient | None = None,
) -> dict[str, Any]:
    """Update Rhodes due diligence fields through LocationOS."""

    clean_site_id = site_id.strip()
    clean_fields = _clean_due_diligence_fields(fields)
    write_request = _due_diligence_write_request(clean_site_id, clean_fields)
    base = {
        "rhodes_site_id": clean_site_id,
        "updated_fields": sorted(clean_fields),
        "write_request": write_request,
        "readback_request": _due_diligence_readback_request(clean_site_id, clean_fields),
    }
    if not clean_site_id:
        return {**base, "status": "skipped", "reason": "missing_site_id"}
    if not clean_fields:
        return {**base, "status": "skipped", "reason": "missing_due_diligence_fields"}

    try:
        rhodes = client or RhodesClient()
    except RhodesError as exc:
        reason = (
            LOCATIONOS_NOT_CONFIGURED_REASON
            if _is_locationos_not_configured_error(exc)
            else "locationos_mcp_error"
        )
        status = "skipped" if reason == LOCATIONOS_NOT_CONFIGURED_REASON else "failed"
        return {**base, "status": status, "reason": reason, "error": str(exc)}

    try:
        response = rhodes.update_due_diligence(site_id=clean_site_id, fields=clean_fields)
    except RhodesError as exc:
        readback = _verify_due_diligence_readback(
            rhodes,
            site_id=clean_site_id,
            fields=clean_fields,
        )
        return {
            **base,
            "status": "failed",
            "reason": "rhodes_error",
            "error": str(exc),
            "error_summary": _summarize_locationos_write_error(str(exc)),
            "readback": readback,
        }
    except Exception as exc:  # noqa: BLE001 - workflow side effect should report cleanly
        return {
            **base,
            "status": "failed",
            "reason": "unexpected_error",
            "error": str(exc),
            "error_summary": _summarize_locationos_write_error(str(exc)),
        }

    response_error = _due_diligence_response_error(response)
    if response_error:
        readback = _verify_due_diligence_readback(
            rhodes,
            site_id=clean_site_id,
            fields=clean_fields,
        )
        return {
            **base,
            "status": "failed",
            "reason": "write_rejected",
            "error": response_error,
            "error_summary": _summarize_locationos_write_error(response_error),
            "response": _summarize_due_diligence_response(response),
            "readback": readback,
        }

    readback = _verify_due_diligence_readback(
        rhodes,
        site_id=clean_site_id,
        fields=clean_fields,
    )
    if readback["status"] != "verified":
        return {
            **base,
            "status": "failed",
            "reason": "readback_failed",
            "error": _due_diligence_readback_error(readback),
            "response": _summarize_due_diligence_response(response),
            "readback": readback,
        }

    return {
        **base,
        "status": "updated",
        "reason": "ok",
        "response": _summarize_due_diligence_response(response),
        "readback": readback,
    }


def verify_rhodes_due_diligence_fields(
    *,
    site_id: str,
    fields: dict[str, Any],
    client: RhodesClient | None = None,
) -> dict[str, Any]:
    """Verify that LocationOS already has the expected due diligence fields."""

    clean_site_id = site_id.strip()
    clean_fields = _clean_due_diligence_fields(fields)
    base = {
        "rhodes_site_id": clean_site_id,
        "verified_fields": sorted(clean_fields),
    }
    if not clean_site_id:
        return {**base, "status": "failed", "reason": "missing_site_id"}
    if not clean_fields:
        return {**base, "status": "failed", "reason": "missing_due_diligence_fields"}

    try:
        rhodes = client or RhodesClient()
    except RhodesError as exc:
        reason = (
            LOCATIONOS_NOT_CONFIGURED_REASON
            if _is_locationos_not_configured_error(exc)
            else "locationos_mcp_error"
        )
        return {**base, "status": "failed", "reason": reason, "error": str(exc)}

    readback = _verify_due_diligence_readback(
        rhodes,
        site_id=clean_site_id,
        fields=clean_fields,
    )
    if readback["status"] != "verified":
        return {
            **base,
            "status": "failed",
            "reason": "readback_failed",
            "error": _due_diligence_readback_error(readback),
            "readback": readback,
        }
    return {
        **base,
        "status": "verified",
        "reason": "ok",
        "readback": readback,
    }


def _is_locationos_not_configured_error(exc: Exception) -> bool:
    return "LocationOS MCP bearer token" in str(exc)


def _verify_due_diligence_readback(
    rhodes: RhodesClient,
    *,
    site_id: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    try:
        site = rhodes.get_site(site_id=site_id)
    except RhodesError as exc:
        return {"status": "failed", "reason": "get_site_failed", "error": str(exc)}

    mismatches: list[dict[str, str]] = []
    verified_fields: list[str] = []
    for key, expected in fields.items():
        actual = _due_diligence_readback_value(site, key)
        if _readback_values_match(expected, actual):
            verified_fields.append(key)
            continue
        mismatches.append(
            {
                "field": key,
                "expected": _safe_readback_value(expected),
                "actual": _safe_readback_value(actual),
            }
        )

    if mismatches:
        return {
            "status": "failed",
            "reason": "field_mismatch",
            "mismatches": mismatches,
            "verified_fields": sorted(verified_fields),
        }
    return {
        "status": "verified",
        "verified_fields": sorted(verified_fields),
    }


def _due_diligence_readback_value(site: dict[str, Any], key: str) -> Any:
    for container_key in ("dueDiligence", "due_diligence", "dueDiligenceFields"):
        container = site.get(container_key)
        if isinstance(container, dict) and key in container:
            return container.get(key)
    if key in site:
        return site.get(key)
    return None


def _readback_values_match(expected: Any, actual: Any) -> bool:
    if actual is None:
        return False
    return _normalize_readback_value(expected) == _normalize_readback_value(actual)


def _normalize_readback_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _safe_readback_value(value: Any) -> str:
    text = _normalize_readback_value(value)
    return text if len(text) <= 160 else f"{text[:157]}..."


def _due_diligence_readback_error(readback: dict[str, Any]) -> str:
    reason = str(readback.get("reason") or "readback_failed")
    mismatches = readback.get("mismatches")
    if isinstance(mismatches, list) and mismatches:
        fields = [
            str(item.get("field") or "").strip()
            for item in mismatches
            if isinstance(item, dict)
        ]
        fields = [field for field in fields if field]
        if fields:
            return f"LocationOS readback mismatch for {', '.join(fields)}"
    error = str(readback.get("error") or "").strip()
    return f"LocationOS readback failed: {error or reason}"


def _due_diligence_write_request(site_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    arguments: dict[str, Any] = {"siteId": site_id}
    arguments.update(fields)
    return {
        "server": "locationos",
        "tool": "updateDueDiligence",
        "arguments": arguments,
    }


def _due_diligence_readback_request(site_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "server": "locationos",
        "tool": "getSite",
        "arguments": {"siteId": site_id},
        "verify_fields": sorted(fields),
    }


def _summarize_locationos_write_error(error: str) -> str:
    clean_error = str(error or "").strip()
    request_ids = REQUEST_ID_RE.findall(clean_error)
    unique_request_ids = list(dict.fromkeys(request_ids))
    lowered = clean_error.lower()
    if "server error" in lowered:
        summary = "LocationOS updateDueDiligence returned a server error"
    elif "elicitation_unsupported" in lowered:
        summary = "LocationOS updateDueDiligence requires OAuth-backed approval"
    elif "confirmation" in lowered or "requires approval" in lowered:
        summary = "LocationOS updateDueDiligence requires approval"
    elif clean_error:
        summary = clean_error
    else:
        summary = "LocationOS updateDueDiligence failed"
    if unique_request_ids:
        summary = f"{summary}. Request IDs: {', '.join(unique_request_ids)}"
    return summary


def _due_diligence_response_error(response: dict[str, Any]) -> str:
    status = str(response.get("status") or "").strip().lower()
    if status in {"error", "failed", "rejected"}:
        return str(
            response.get("error")
            or response.get("message")
            or response.get("rejectionReason")
            or status
        )
    if response.get("success") is False:
        return str(response.get("error") or response.get("message") or "success=false")
    error = response.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    return ""


def _summarize_due_diligence_response(response: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "type": type(response).__name__,
        "keys": sorted(str(key) for key in response.keys())[:20],
    }
    for key in ("status", "success", "message", "error", "id", "_id", "siteId"):
        value = response.get(key)
        if isinstance(value, str | bool | int | float) or value is None:
            summary[key] = value
    return summary


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
        "p1_assignee_user_id": owner.get("userId", ""),
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
    notes_client: AerieNotesClient | None = None,
    automation_source: str = "due-diligence-reporter",
) -> dict[str, Any]:
    """Create a headless Rhodes site note and mention the P1 owner."""
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
    if client is not None:
        return _add_rhodes_site_note_with_mcp_client(
            site_id=clean_site_id,
            site_slug=clean_site_slug,
            body=clean_body,
            owner_user_id=clean_owner_user_id,
            owner_email=clean_owner_email,
            extra_mention_user_ids=extra_mention_user_ids,
            client=client,
            base=base,
        )

    if not clean_owner_user_id:
        return {
            **base,
            "status": "failed",
            "reason": "missing_owner_user_id",
            "error": "P1 owner user ID is required for headless review-queue note delivery",
            "rhodes_note_id": "",
            "owner_resolution": "missing_user_id",
            "mentioned_user_ids": _unique_nonempty(extra_mention_user_ids or []),
            "write_path": "aerie_notes_api",
        }

    mention_user_ids = _unique_nonempty(
        [
            clean_owner_user_id,
            *(extra_mention_user_ids or []),
        ]
    )

    try:
        notes = notes_client or AerieNotesClient()
    except RhodesError as exc:
        return {
            **base,
            "status": "failed",
            "reason": NOTES_API_NOT_CONFIGURED_REASON,
            "error": str(exc),
            "rhodes_note_id": "",
            "owner_resolution": "provided",
            "mentioned_user_ids": mention_user_ids,
            "write_path": "aerie_notes_api",
        }

    try:
        existing = notes.find_site_note_by_body(
            site_id=clean_site_id,
            site_slug=clean_site_slug,
            body=clean_body,
        )
        if existing is not None and _note_mentions_cover(existing, mention_user_ids):
            note_id = _note_id(existing)
            return {
                **base,
                "status": "created",
                "reason": "already_exists",
                "rhodes_note_id": note_id,
                "owner_user_id": clean_owner_user_id,
                "owner_resolution": "provided",
                "owner_notification": "mentioned",
                "mentioned_user_ids": _note_mentioned_user_ids(existing),
                "readback": {
                    "status": "verified",
                    "rhodes_note_id": note_id,
                    "matched_by": "body",
                    "mentioned_user_ids": _note_mentioned_user_ids(existing),
                },
                "write_path": "aerie_notes_api",
                "idempotency_status": "matched_existing",
            }

        note = notes.create_site_note(
            site_id=clean_site_id,
            site_slug=clean_site_slug,
            body=clean_body,
            mentions=mention_user_ids,
            automation_source=automation_source,
            decisionmaker_user_id=clean_owner_user_id,
        )
        note_id = _note_id(note)
    except RhodesError as exc:
        return {
            **base,
            "status": "failed",
            "reason": "note_api_error",
            "error": str(exc),
            "rhodes_note_id": "",
            "owner_user_id": clean_owner_user_id,
            "owner_resolution": "provided",
            "mentioned_user_ids": mention_user_ids,
            "write_path": "aerie_notes_api",
        }
    except Exception as exc:  # noqa: BLE001 - non-fatal scanner side effect
        return {
            **base,
            "status": "failed",
            "reason": "unexpected_error",
            "error": str(exc),
            "rhodes_note_id": "",
            "owner_user_id": clean_owner_user_id,
            "owner_resolution": "provided",
            "mentioned_user_ids": mention_user_ids,
            "write_path": "aerie_notes_api",
        }

    note_response_summaries = [
        _summarize_note_response(note, attempt="aerie_notes_api")
    ]
    if not note_id:
        try:
            recovered = notes.find_site_note_by_body(
                site_id=clean_site_id,
                site_slug=clean_site_slug,
                body=clean_body,
            )
        except RhodesError:
            recovered = None
        if recovered is not None and _note_mentions_cover(recovered, mention_user_ids):
            note = recovered
            note_id = _note_id(recovered)

    if not note_id:
        return {
            **base,
            "status": "failed",
            "reason": "missing_note_id",
            "error": "Aerie notes API returned no note ID; delivery could not be verified",
            "rhodes_note_id": "",
            "owner_user_id": clean_owner_user_id,
            "owner_resolution": "provided",
            "mentioned_user_ids": mention_user_ids,
            "note_response_summaries": note_response_summaries,
            "write_path": "aerie_notes_api",
        }

    readback = _verify_note_readback(
        notes,
        site_id=clean_site_id,
        site_slug=clean_site_slug,
        body=clean_body,
        note_id=note_id,
        expected_mention_user_ids=mention_user_ids,
    )
    if readback["status"] != "verified":
        return {
            **base,
            "status": "failed",
            "reason": "note_readback_failed",
            "error": _note_readback_error(readback),
            "rhodes_note_id": note_id,
            "owner_user_id": clean_owner_user_id,
            "owner_resolution": "provided",
            "mentioned_user_ids": mention_user_ids,
            "readback": readback,
            "note_response_summaries": note_response_summaries,
            "write_path": "aerie_notes_api",
        }

    verified_note_id = str(readback.get("rhodes_note_id") or note_id).strip() or note_id
    verified_mentions = _unique_nonempty(
        _note_mentioned_user_ids(note) or readback.get("mentioned_user_ids") or mention_user_ids
    )

    return {
        **base,
        "status": "created",
        "reason": "ok",
        "rhodes_note_id": verified_note_id,
        "owner_user_id": clean_owner_user_id,
        "owner_resolution": "provided",
        "owner_notification": "mentioned",
        "mentioned_user_ids": verified_mentions,
        "readback": readback,
        "write_path": "aerie_notes_api",
        "idempotency_status": "created",
    }


def _add_rhodes_site_note_with_mcp_client(
    *,
    site_id: str,
    site_slug: str,
    body: str,
    owner_user_id: str,
    owner_email: str,
    extra_mention_user_ids: Iterable[str] | None,
    client: RhodesClient,
    base: dict[str, Any],
) -> dict[str, Any]:
    """Create a note through an injected MCP-compatible client for tests/fallbacks."""

    try:
        rhodes = client
        resolved_owner_user_id = owner_user_id
        owner_resolution = "provided" if resolved_owner_user_id else "none"
        if not resolved_owner_user_id and owner_email:
            try:
                user = rhodes.get_user(email=owner_email)
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
            site_id=site_id,
            site_slug=site_slug,
            body=body,
            mentions=mention_user_ids,
        )
        note_response_summaries.append(
            _summarize_note_response(
                note,
                attempt="site_id" if site_id else "site_slug",
            )
        )
        note_id = _note_id(note)
        if not note_id:
            note = _recover_note_without_id(
                rhodes,
                site_id=site_id,
                site_slug=site_slug,
                body=body,
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

    readback = _verify_note_readback(
        rhodes,
        site_id=site_id,
        site_slug=site_slug,
        body=body,
        note_id=note_id,
        expected_mention_user_ids=mention_user_ids,
    )
    if readback["status"] != "verified":
        return {
            **base,
            "status": "failed",
            "reason": "note_readback_failed",
            "error": _note_readback_error(readback),
            "rhodes_note_id": note_id,
            "owner_user_id": resolved_owner_user_id,
            "owner_resolution": owner_resolution,
            "mentioned_user_ids": mention_user_ids,
            "readback": readback,
            "note_response_summaries": note_response_summaries,
        }
    verified_note_id = str(readback.get("rhodes_note_id") or note_id).strip() or note_id

    return {
        **base,
        "status": "created",
        "reason": "ok",
        "rhodes_note_id": verified_note_id,
        "owner_user_id": resolved_owner_user_id,
        "owner_resolution": owner_resolution,
        "owner_notification": "mentioned" if resolved_owner_user_id else "none",
        "mentioned_user_ids": mention_user_ids,
        "readback": readback,
    }


def _verify_note_readback(
    rhodes: Any,
    *,
    site_id: str,
    site_slug: str,
    body: str,
    note_id: str,
    expected_mention_user_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    try:
        notes = rhodes.list_notes(site_id=site_id, site_slug=site_slug, limit=50)
    except RhodesError as exc:
        return {"status": "failed", "reason": "list_notes_failed", "error": str(exc)}

    clean_body = body.strip()
    expected_mentions = _unique_nonempty(expected_mention_user_ids or [])
    body_match_id = ""
    body_match_mentions: list[str] = []
    for note in notes:
        current_id = _note_id(note)
        current_body = str(note.get("body") or "").strip()
        if current_id and current_id == note_id:
            if current_body and current_body != clean_body:
                return {
                    "status": "failed",
                    "reason": "note_body_mismatch",
                    "rhodes_note_id": note_id,
                }
            mentioned_user_ids = _note_mentioned_user_ids(note)
            missing_mentions = [
                user_id
                for user_id in expected_mentions
                if user_id not in set(mentioned_user_ids)
            ]
            if missing_mentions:
                return {
                    "status": "failed",
                    "reason": "note_mentions_missing",
                    "rhodes_note_id": note_id,
                    "missing_user_ids": missing_mentions,
                    "mentioned_user_ids": mentioned_user_ids,
                }
            return {
                "status": "verified",
                "rhodes_note_id": note_id,
                "matched_by": "note_id",
                "mentioned_user_ids": mentioned_user_ids,
            }
        if current_body == clean_body and current_id:
            body_match_id = current_id
            body_match_mentions = _note_mentioned_user_ids(note)

    if body_match_id:
        missing_mentions = [
            user_id
            for user_id in expected_mentions
            if user_id not in set(body_match_mentions)
        ]
        if missing_mentions:
            return {
                "status": "failed",
                "reason": "note_mentions_missing",
                "rhodes_note_id": body_match_id,
                "missing_user_ids": missing_mentions,
                "mentioned_user_ids": body_match_mentions,
            }
        return {
            "status": "verified",
            "rhodes_note_id": body_match_id,
            "matched_by": "body",
            "mentioned_user_ids": body_match_mentions,
        }
    return {
        "status": "failed",
        "reason": "note_not_found",
        "rhodes_note_id": note_id,
    }


def _note_readback_error(readback: dict[str, Any]) -> str:
    reason = str(readback.get("reason") or "note_readback_failed")
    error = str(readback.get("error") or "").strip()
    if reason == "note_mentions_missing":
        missing = readback.get("missing_user_ids")
        if isinstance(missing, list) and missing:
            return "LocationOS note readback missing mentions for " + ", ".join(
                str(user_id) for user_id in missing
            )
    return f"LocationOS note readback failed: {error or reason}"


def _note_mentioned_user_ids(note: dict[str, Any]) -> list[str]:
    user_ids: list[str] = []
    mentioned_user_ids = note.get("mentionedUserIds")
    if isinstance(mentioned_user_ids, list):
        user_ids.extend(str(value) for value in mentioned_user_ids)
    mentions = note.get("mentions")
    if isinstance(mentions, list):
        for mention in mentions:
            if isinstance(mention, str):
                user_ids.append(mention)
            elif isinstance(mention, dict):
                user_ids.append(_user_id(mention))
    nested_note = note.get("note")
    if isinstance(nested_note, dict):
        user_ids.extend(_note_mentioned_user_ids(nested_note))
    return _unique_nonempty(user_ids)


def _note_mentions_cover(note: dict[str, Any], expected_user_ids: Iterable[str]) -> bool:
    expected = _unique_nonempty(expected_user_ids)
    if not expected:
        return True
    mentioned = set(_note_mentioned_user_ids(note))
    return all(user_id in mentioned for user_id in expected)


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


def _registration_error_is_handoff_eligible(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(marker in message for marker in DOCUMENT_REGISTRATION_HANDOFF_ERROR_MARKERS)


def _build_document_registration_handoff_note_body(
    *,
    site_name: str,
    site_address: str,
    documents: Iterable[dict[str, str]],
) -> str:
    lines = [
        f"Site: {site_name.strip()}",
        f"Address: {site_address.strip()}",
        "Documents to register:",
    ]
    for document in documents:
        lines.append(str(document.get("display_name") or "").strip())
        lines.append(f"  Drive: {str(document.get('url') or '').strip()}")
    return "\n".join(lines)


def _resolve_document_registration_handoff_owner(
    *,
    site: dict[str, Any],
    owner_user_id: str,
    owner_email: str,
    fallback_owner_email: str,
    rhodes: RhodesClient | None,
) -> dict[str, Any]:
    clean_owner_user_id = owner_user_id.strip()
    clean_owner_email = owner_email.strip()
    fallback_used = False

    site_owner = _extract_p1_dri(site)
    if not clean_owner_user_id:
        clean_owner_user_id = site_owner.get("userId", "")
    if not clean_owner_email:
        clean_owner_email = site_owner.get("email", "")

    if not clean_owner_user_id and clean_owner_email and rhodes is not None:
        try:
            clean_owner_user_id = _user_id(rhodes.get_user(email=clean_owner_email) or {})
        except RhodesError:
            clean_owner_user_id = ""

    if not clean_owner_user_id and fallback_owner_email.strip():
        fallback_used = True
        clean_owner_email = fallback_owner_email.strip()
        if rhodes is not None:
            try:
                clean_owner_user_id = _user_id(
                    rhodes.get_user(email=clean_owner_email) or {}
                )
            except RhodesError:
                clean_owner_user_id = ""

    return {
        "owner_user_id": clean_owner_user_id,
        "owner_email": clean_owner_email,
        "fallback_owner_used": fallback_used,
    }


def _create_document_registration_handoff_for_documents(
    *,
    site_id: str,
    site_name: str,
    site_address: str,
    site_slug: str,
    documents: list[dict[str, Any]],
    owner_user_id: str,
    owner_email: str,
    fallback_owner_email: str,
    rhodes: RhodesClient | None,
    mcp_note_client: RhodesClient | None,
    notes_client: AerieNotesClient | None,
) -> dict[str, Any]:
    clean_site_id = site_id.strip()
    if not documents:
        return {
            "status": "skipped",
            "reason": "no_documents",
            "error": "Document registration handoff requires at least one document.",
        }
    missing_urls = [
        str(document.get("display_name") or document.get("file_name") or "").strip()
        for document in documents
        if not str(document.get("url") or "").strip()
    ]
    if missing_urls:
        return {
            "status": "failed",
            "reason": "missing_artifact_url",
            "error": "Document registration handoff requires a human-openable artifact URL.",
            "documents": documents,
            "missing_url_documents": missing_urls,
        }

    site: dict[str, Any] = {}
    site_lookup_error = ""
    if rhodes is not None:
        try:
            site = rhodes.get_site(
                site_id=clean_site_id or None,
                slug=site_slug.strip() or None,
            )
        except RhodesError as exc:
            site_lookup_error = str(exc)

    resolved_site_name = site_name.strip() or _site_name(site)
    resolved_site_address = site_address.strip() or _site_address(site)
    resolved_site_slug = site_slug.strip() or str(site.get("slug") or "").strip()
    if not resolved_site_name:
        return {
            "status": "failed",
            "reason": "missing_site_name",
            "error": "Document registration handoff requires a site name.",
            "site_lookup_error": site_lookup_error,
        }
    if not resolved_site_address:
        return {
            "status": "failed",
            "reason": "missing_site_address",
            "error": "Document registration handoff requires a site address.",
            "site_lookup_error": site_lookup_error,
        }

    owner = _resolve_document_registration_handoff_owner(
        site=site,
        owner_user_id=owner_user_id,
        owner_email=owner_email,
        fallback_owner_email=fallback_owner_email,
        rhodes=rhodes,
    )
    if not owner["owner_user_id"]:
        return {
            "status": "failed",
            "reason": "owner_route_unresolved",
            "error": "Document registration handoff requires a P1/site owner or fallback owner user ID.",
            "owner_email": owner["owner_email"],
            "fallback_owner_used": owner["fallback_owner_used"],
            "site_lookup_error": site_lookup_error,
        }

    note_body = _build_document_registration_handoff_note_body(
        site_name=resolved_site_name,
        site_address=resolved_site_address,
        documents=[
            {
                "display_name": str(document.get("display_name") or "").strip(),
                "url": str(document.get("url") or "").strip(),
            }
            for document in documents
        ],
    )
    note = add_rhodes_site_note(
        site_id=clean_site_id,
        site_slug=resolved_site_slug,
        body=note_body,
        owner_user_id=owner["owner_user_id"],
        owner_email=owner["owner_email"],
        client=mcp_note_client,
        notes_client=notes_client,
        automation_source="document_registration_handoff",
    )
    if note.get("status") != "created":
        return {
            "status": "failed",
            "reason": "handoff_note_failed",
            "error": str(note.get("error") or note.get("reason") or "note_write_failed"),
            "note": note,
            "note_body": note_body,
            "documents": documents,
            "site_lookup_error": site_lookup_error,
        }

    result = {
        "status": "created",
        "reason": "handoff_note_created",
        "note_status": note.get("status"),
        "note_readback_status": str(_dict_get(note.get("readback"), "status") or ""),
        "rhodes_note_id": str(note.get("rhodes_note_id") or ""),
        "readback": note.get("readback"),
        "mentioned_owner_user_ids": _unique_nonempty(
            str(value) for value in note.get("mentioned_user_ids", [])
        )
        if isinstance(note.get("mentioned_user_ids"), list)
        else [],
        "owner_user_id": owner["owner_user_id"],
        "owner_email": owner["owner_email"],
        "fallback_owner_used": owner["fallback_owner_used"],
        "document_count": len(documents),
        "documents": documents,
        "human_followup_required": True,
        "human_followup_type": DOCUMENT_REGISTRATION_FOLLOWUP_TYPE,
        "rhodes_registration_status": DOCUMENT_REGISTRATION_PENDING_USER_ACTION,
        "remaining_work": [],
        "note_body": note_body,
        "site_name": resolved_site_name,
        "site_address": resolved_site_address,
        "site_slug": resolved_site_slug,
        "site_lookup_error": site_lookup_error,
    }
    message_ids = _unique_nonempty(str(document.get("message_id") or "") for document in documents)
    attachment_ids = _unique_nonempty(
        str(document.get("attachment_id") or "") for document in documents
    )
    if message_ids:
        result["message_ids"] = message_ids
    if attachment_ids:
        result["attachment_ids"] = attachment_ids
    if len(documents) == 1:
        result["message_id"] = str(documents[0].get("message_id") or "").strip()
        result["attachment_id"] = str(documents[0].get("attachment_id") or "").strip()
    return result


def _document_registration_handoff_item(
    *,
    ddr_doc_type: str,
    mapping: RhodesDocumentMapping,
    title: str,
    drive_file_id: str,
    drive_url: str,
    mime_type: str,
    original_filename: str,
    source: str,
    message_id: str,
    attachment_id: str,
    registration_error: Exception | str,
) -> dict[str, Any]:
    clean_title = title.strip() or original_filename.strip() or ddr_doc_type.strip()
    return {
        "display_name": clean_title,
        "url": drive_url.strip(),
        "file_id": drive_file_id.strip(),
        "file_name": original_filename.strip() or clean_title,
        "ddr_doc_type": ddr_doc_type.strip(),
        "docType": mapping.doc_type,
        "milestone": mapping.milestone or "",
        "quality_bar": mapping.quality_bar or "",
        "mime_type": mime_type.strip(),
        "task_key": source.strip(),
        "message_id": message_id.strip(),
        "attachment_id": attachment_id.strip(),
        "registration_blocker": str(registration_error),
        "registration_status": DOCUMENT_REGISTRATION_PENDING_USER_ACTION,
        "human_followup_required": True,
        "human_followup_type": DOCUMENT_REGISTRATION_FOLLOWUP_TYPE,
    }


def _create_document_registration_handoff(
    *,
    site_id: str,
    site_name: str,
    site_address: str,
    site_slug: str,
    ddr_doc_type: str,
    mapping: RhodesDocumentMapping,
    title: str,
    drive_file_id: str,
    drive_url: str,
    mime_type: str,
    original_filename: str,
    source: str,
    message_id: str,
    attachment_id: str,
    registration_error: Exception,
    owner_user_id: str,
    owner_email: str,
    fallback_owner_email: str,
    rhodes: RhodesClient | None,
    mcp_note_client: RhodesClient | None,
    notes_client: AerieNotesClient | None,
) -> dict[str, Any]:
    return _create_document_registration_handoff_for_documents(
        site_id=site_id,
        site_name=site_name,
        site_address=site_address,
        site_slug=site_slug,
        documents=[
            _document_registration_handoff_item(
                ddr_doc_type=ddr_doc_type,
                mapping=mapping,
                title=title,
                drive_file_id=drive_file_id,
                drive_url=drive_url,
                mime_type=mime_type,
                original_filename=original_filename,
                source=source,
                message_id=message_id,
                attachment_id=attachment_id,
                registration_error=registration_error,
            )
        ],
        owner_user_id=owner_user_id,
        owner_email=owner_email,
        fallback_owner_email=fallback_owner_email,
        rhodes=rhodes,
        mcp_note_client=mcp_note_client,
        notes_client=notes_client,
    )


def create_document_registration_handoff_for_uploads(
    *,
    site_id: str,
    documents: Iterable[dict[str, Any]],
    site_name: str = "",
    site_address: str = "",
    site_slug: str = "",
    owner_user_id: str = "",
    owner_email: str = "",
    fallback_owner_email: str = DOCUMENT_REGISTRATION_FALLBACK_OWNER_EMAIL,
    notes_client: AerieNotesClient | None = None,
    client: RhodesClient | None = None,
) -> dict[str, Any]:
    """Write one grouped human handoff note for eligible registration failures."""

    handoff_documents: list[dict[str, Any]] = []
    for raw_document in documents:
        registration = _dict_get(raw_document, "registration") or _dict_get(
            raw_document, "rhodes_registration"
        )
        if not isinstance(registration, dict):
            registration = raw_document
        status = str(registration.get("status") or raw_document.get("status") or "").strip()
        error = str(registration.get("error") or raw_document.get("error") or "").strip()
        if (
            status != "failed"
            or not _registration_error_is_handoff_eligible(Exception(error))
        ):
            continue

        ddr_doc_type = str(
            raw_document.get("ddr_doc_type")
            or registration.get("ddr_doc_type")
            or raw_document.get("source_type")
            or ""
        ).strip()
        mapping = map_ddr_doc_type_to_rhodes(ddr_doc_type)
        if mapping is None:
            rhodes_doc_type = str(
                raw_document.get("rhodes_doc_type")
                or registration.get("rhodes_doc_type")
                or ""
            ).strip()
            if not rhodes_doc_type:
                return {
                    "status": "failed",
                    "reason": "missing_document_metadata",
                    "error": "Document registration handoff requires a Rhodes doc type.",
                    "documents": [raw_document],
                }
            mapping = RhodesDocumentMapping(
                rhodes_doc_type,
                milestone=str(
                    raw_document.get("rhodes_milestone")
                    or registration.get("rhodes_milestone")
                    or ""
                ).strip()
                or None,
                quality_bar=str(
                    raw_document.get("rhodes_quality_bar")
                    or registration.get("rhodes_quality_bar")
                    or ""
                ).strip()
                or None,
            )

        handoff_documents.append(
            _document_registration_handoff_item(
                ddr_doc_type=ddr_doc_type,
                mapping=mapping,
                title=str(raw_document.get("title") or raw_document.get("name") or "").strip(),
                drive_file_id=str(
                    raw_document.get("drive_file_id")
                    or registration.get("drive_file_id")
                    or ""
                ).strip(),
                drive_url=str(
                    raw_document.get("drive_url")
                    or raw_document.get("url")
                    or registration.get("drive_url")
                    or ""
                ).strip(),
                mime_type=str(
                    raw_document.get("mime_type")
                    or registration.get("mime_type")
                    or ""
                ).strip(),
                original_filename=str(
                    raw_document.get("original_filename")
                    or raw_document.get("file_name")
                    or ""
                ).strip(),
                source=str(raw_document.get("source") or registration.get("source") or "").strip(),
                message_id=str(
                    raw_document.get("message_id")
                    or registration.get("message_id")
                    or ""
                ).strip(),
                attachment_id=str(
                    raw_document.get("attachment_id")
                    or registration.get("attachment_id")
                    or ""
                ).strip(),
                registration_error=error,
            )
        )

    if not handoff_documents:
        return {"status": "skipped", "reason": "no_eligible_registration_failures"}

    rhodes: RhodesClient | None = None
    rhodes_error = ""
    try:
        rhodes = client or RhodesClient()
    except RhodesError as exc:
        rhodes_error = str(exc)

    handoff = _create_document_registration_handoff_for_documents(
        site_id=site_id,
        site_name=site_name,
        site_address=site_address,
        site_slug=site_slug,
        documents=handoff_documents,
        owner_user_id=owner_user_id,
        owner_email=owner_email,
        fallback_owner_email=fallback_owner_email,
        rhodes=rhodes,
        mcp_note_client=(
            client
            if client is not None and not isinstance(client, RhodesClient)
            else None
        ),
        notes_client=notes_client,
    )
    if rhodes_error:
        handoff["rhodes_client_error"] = rhodes_error
    return handoff


def _dict_get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def register_rhodes_document_for_upload(
    *,
    site_id: str,
    ddr_doc_type: str,
    title: str,
    drive_file_id: str,
    drive_url: str = "",
    site_name: str = "",
    site_address: str = "",
    site_slug: str = "",
    mime_type: str = "application/pdf",
    original_filename: str = "",
    source: str = "inbox_scanner",
    message_id: str = "",
    attachment_id: str = "",
    owner_user_id: str = "",
    owner_email: str = "",
    fallback_owner_email: str = DOCUMENT_REGISTRATION_FALLBACK_OWNER_EMAIL,
    notes_client: AerieNotesClient | None = None,
    handoff_on_registration_failure: bool = True,
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
        "rhodes_quality_bar": mapping.quality_bar if mapping else "",
    }

    if not clean_site_id:
        return {**base, "status": "skipped", "reason": "missing_site_id"}
    if not clean_drive_file_id:
        return {**base, "status": "skipped", "reason": "missing_drive_file_id"}
    if mapping is None:
        return {**base, "status": "skipped", "reason": "unmapped_doc_type"}

    rhodes: RhodesClient | None = None
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
        if (
            handoff_on_registration_failure
            and drive_url.strip()
            and _registration_error_is_handoff_eligible(exc)
        ):
            handoff = _create_document_registration_handoff(
                site_id=clean_site_id,
                site_name=site_name,
                site_address=site_address,
                site_slug=site_slug,
                ddr_doc_type=ddr_doc_type,
                mapping=mapping,
                title=title,
                drive_file_id=clean_drive_file_id,
                drive_url=drive_url,
                mime_type=mime_type,
                original_filename=original_filename,
                source=source,
                message_id=message_id,
                attachment_id=attachment_id,
                registration_error=exc,
                owner_user_id=owner_user_id,
                owner_email=owner_email,
                fallback_owner_email=fallback_owner_email,
                rhodes=rhodes,
                mcp_note_client=(
                    client
                    if client is not None and not isinstance(client, RhodesClient)
                    else None
                ),
                notes_client=notes_client,
            )
            if handoff.get("status") == "created":
                return {
                    **base,
                    "status": DOCUMENT_REGISTRATION_PENDING_USER_ACTION,
                    "reason": "handoff_note_created",
                    "error": str(exc),
                    "rhodes_registration_status": DOCUMENT_REGISTRATION_PENDING_USER_ACTION,
                    "human_followup_required": True,
                    "human_followup_type": DOCUMENT_REGISTRATION_FOLLOWUP_TYPE,
                    "remaining_work": [],
                    "document_registration_handoff": handoff,
                }
            return {
                **base,
                "status": "failed",
                "reason": "registration_handoff_failed",
                "error": str(exc),
                "rhodes_registration_status": "failed",
                "human_followup_required": True,
                "human_followup_type": DOCUMENT_REGISTRATION_FOLLOWUP_TYPE,
                "remaining_work": [
                    {
                        "type": DOCUMENT_REGISTRATION_FOLLOWUP_TYPE,
                        "status": "blocked",
                        "reason": str(handoff.get("reason") or "handoff_failed"),
                    }
                ],
                "document_registration_handoff": handoff,
            }
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
