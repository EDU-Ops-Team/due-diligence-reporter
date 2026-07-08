"""Durable log of successful DD field writes plus the daily operator digest.

Every successful ``updateDueDiligence`` outcome (direct update or
approval-queue proposal) is appended to a Firestore-backed write log so a
scheduled digest can tell the operating owner what the automation did that
day. Recording is strictly best-effort: a log failure must never fail the
write it describes.

Known exclusion: DD writes completed through the MCP-assisted resume path
(operator approves in the browser, DDR only verifies readback) are not
logged - the owner performed those interactively and already knows.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .firestore_state import (
    DEFAULT_FIRESTORE_DATABASE,
    alert_firestore_fallback,
    build_authorized_session,
    decode_firestore_fields,
    encode_firestore_fields,
)
from .utils import escape_html_text, sanitize_http_url

logger = logging.getLogger(__name__)

DEFAULT_WRITE_LOG_COLLECTION = "ddrDdWriteEvents"
DEFAULT_WRITE_LOG_FALLBACK_PATH = ".dd_write_log.json"
DIGEST_STATUSES = ("updated", "proposal_submitted")


def _env_chain(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def write_log_project_id() -> str:
    return _env_chain(
        "DD_WRITE_LOG_FIRESTORE_PROJECT_ID",
        "M2_DD_STATE_FIRESTORE_PROJECT_ID",
        "DD_REPUBLISH_STATE_FIRESTORE_PROJECT_ID",
    )


def write_log_database() -> str:
    return (
        _env_chain(
            "DD_WRITE_LOG_FIRESTORE_DATABASE",
            "M2_DD_STATE_FIRESTORE_DATABASE",
            "DD_REPUBLISH_STATE_FIRESTORE_DATABASE",
        )
        or DEFAULT_FIRESTORE_DATABASE
    )


def write_log_collection() -> str:
    return (
        os.environ.get("DD_WRITE_LOG_FIRESTORE_COLLECTION", "").strip()
        or DEFAULT_WRITE_LOG_COLLECTION
    )


def _documents_url(project_id: str, database: str, collection: str) -> str:
    return (
        "https://firestore.googleapis.com/v1/projects/"
        f"{project_id}/databases/{database}/documents/{collection}"
    )


def build_dd_write_event(
    *,
    site_id: str,
    status: str,
    fields: dict[str, Any],
    field_sources: dict[str, str] | None = None,
    review_url: str = "",
    run_source: str = "",
    created_at: str | None = None,
) -> dict[str, str]:
    """Build the flat event row recorded for one successful DD write."""

    return {
        "created_at": created_at or datetime.now(UTC).isoformat(),
        "site_id": site_id.strip(),
        "status": status.strip(),
        "fields": json.dumps(
            {key: str(value) for key, value in sorted(fields.items())},
            sort_keys=True,
        ),
        "field_sources": json.dumps(dict(sorted((field_sources or {}).items()))),
        "review_url": review_url.strip(),
        "run_source": run_source.strip()
        or os.environ.get("GITHUB_WORKFLOW", "").strip()
        or "local",
    }


def record_dd_write_event(event: dict[str, str]) -> None:
    """Persist one write event; best-effort, never raises."""

    try:
        project_id = write_log_project_id()
        if project_id:
            _save_event_to_firestore(event)
            return
        _append_event_to_fallback(event)
    except Exception as exc:  # noqa: BLE001 - logging must never fail the write
        logger.warning("Failed to record DD write event: %s", exc)
        alert_firestore_fallback("dd_write_log", "save", exc)
        try:
            _append_event_to_fallback(event)
        except Exception:  # noqa: BLE001
            logger.warning("DD write event fallback append also failed.")


def _event_document_id(event: dict[str, str]) -> str:
    seed = "|".join(
        (
            event.get("created_at", ""),
            event.get("site_id", ""),
            event.get("status", ""),
            event.get("fields", ""),
        )
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _save_event_to_firestore(event: dict[str, str]) -> None:
    session = build_authorized_session()
    url = _documents_url(
        write_log_project_id(), write_log_database(), write_log_collection()
    )
    response = session.patch(
        f"{url}/{_event_document_id(event)}",
        json={"fields": encode_firestore_fields(dict(event))},
        timeout=10,
    )
    response.raise_for_status()


def _fallback_path() -> Path:
    return Path(
        os.environ.get("DD_WRITE_LOG_FALLBACK_PATH", "").strip()
        or DEFAULT_WRITE_LOG_FALLBACK_PATH
    )


def _append_event_to_fallback(event: dict[str, str]) -> None:
    path = _fallback_path()
    events: list[dict[str, str]] = []
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                events = [item for item in payload if isinstance(item, dict)]
        except (OSError, ValueError):
            events = []
    events.append(event)
    path.write_text(json.dumps(events, indent=2, sort_keys=True), encoding="utf-8")


def collect_dd_write_events(*, since_iso: str) -> list[dict[str, str]]:
    """Return logged write events created at or after ``since_iso``."""

    events: list[dict[str, str]] = []
    project_id = write_log_project_id()
    if project_id:
        events.extend(_load_events_from_firestore())
    else:
        events.extend(_load_events_from_fallback())
    return sorted(
        (
            event
            for event in events
            if str(event.get("created_at") or "") >= since_iso
            and str(event.get("status") or "") in DIGEST_STATUSES
        ),
        key=lambda event: (
            str(event.get("site_id") or ""),
            str(event.get("created_at") or ""),
        ),
    )


def _load_events_from_firestore() -> list[dict[str, str]]:
    session = build_authorized_session()
    url = _documents_url(
        write_log_project_id(), write_log_database(), write_log_collection()
    )
    events: list[dict[str, str]] = []
    page_token = ""
    while True:
        params: dict[str, str] = {"pageSize": "300"}
        if page_token:
            params["pageToken"] = page_token
        response = session.get(url, params=params, timeout=15)
        if response.status_code == 404:
            raise RuntimeError(
                "DD write log Firestore path not found (404): check "
                "DD_WRITE_LOG_FIRESTORE_PROJECT_ID/DATABASE/COLLECTION - a "
                "missing collection lists as empty, so 404 means a bad "
                "project or database path."
            )
        response.raise_for_status()
        payload = response.json() if response.content else {}
        for document in payload.get("documents", []) or []:
            fields = document.get("fields")
            if isinstance(fields, dict):
                decoded = decode_firestore_fields(fields)
                events.append({key: str(value) for key, value in decoded.items()})
        page_token = str(payload.get("nextPageToken") or "")
        if not page_token:
            return events


def _load_events_from_fallback() -> list[dict[str, str]]:
    path = _fallback_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    return [
        {key: str(value) for key, value in item.items()}
        for item in payload
        if isinstance(item, dict)
    ]


def build_dd_write_digest(
    events: list[dict[str, str]],
    *,
    resolve_site_name: Callable[[str], str] | None = None,
    period_label: str = "last 24 hours",
) -> dict[str, Any]:
    """Compose the operator digest from logged write events.

    Returns ``{"subject", "text", "html", "event_count", "site_count"}``.
    """

    by_site: dict[str, list[dict[str, str]]] = {}
    for event in events:
        by_site.setdefault(str(event.get("site_id") or "unknown"), []).append(event)

    site_names: dict[str, str] = {}
    for site_id in by_site:
        name = ""
        if resolve_site_name is not None:
            try:
                name = str(resolve_site_name(site_id) or "").strip()
            except Exception:  # noqa: BLE001 - digest must render without lookups
                name = ""
        site_names[site_id] = name or site_id

    updated_count = sum(1 for event in events if event.get("status") == "updated")
    proposal_count = len(events) - updated_count
    subject = (
        f"DDR DD write digest: {len(events)} write(s) across "
        f"{len(by_site)} site(s) ({period_label})"
    )

    text_lines = [
        f"DDR due diligence write digest - {period_label}.",
        f"{updated_count} field update(s) applied, "
        f"{proposal_count} proposal(s) submitted for approval.",
        "",
    ]
    html_parts = [
        f"<p>DDR due diligence write digest &mdash; {period_label}.<br>"
        f"{updated_count} field update(s) applied, "
        f"{proposal_count} proposal(s) submitted for approval.</p>"
    ]
    for site_id in sorted(by_site, key=lambda key: site_names[key].lower()):
        site_label = site_names[site_id]
        text_lines.append(site_label)
        html_parts.append(f"<h3>{escape_html_text(site_label)}</h3><ul>")
        for event in by_site[site_id]:
            status = (
                "updated"
                if event.get("status") == "updated"
                else "submitted for approval"
            )
            try:
                fields = json.loads(event.get("fields") or "{}")
            except ValueError:
                fields = {}
            field_text = ", ".join(
                f"{key}={value}" for key, value in sorted(fields.items())
            )
            line = f"- {status}: {field_text}" if field_text else f"- {status}"
            review_url = sanitize_http_url(str(event.get("review_url") or "")) or ""
            if review_url:
                line += f" (review: {review_url})"
            text_lines.append(line)
            safe_field_text = escape_html_text(field_text)
            html_line = (
                f"<li>{status}: {safe_field_text}" if field_text else f"<li>{status}"
            )
            if review_url:
                html_line += (
                    f' &mdash; <a href="{escape_html_text(review_url)}">review</a>'
                )
            html_parts.append(html_line + "</li>")
        text_lines.append("")
        html_parts.append("</ul>")

    if not events:
        text_lines = [f"No DD field writes in the {period_label}."]
        html_parts = [f"<p>No DD field writes in the {period_label}.</p>"]
        subject = f"DDR DD write digest: no writes ({period_label})"

    return {
        "subject": subject,
        "text": "\n".join(text_lines).strip(),
        "html": "".join(html_parts),
        "event_count": len(events),
        "site_count": len(by_site),
    }
