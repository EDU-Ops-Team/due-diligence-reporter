"""Read-only Rhodes portfolio automation gap snapshot."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .rhodes import RhodesClient, RhodesError

REQUIRED_DD_DOC_TYPES = (
    "siteInvestigationReport",
    "propertyConditionAssessment",
    "floorPlan",
)

OPEN_TASK_STATUSES = {"new", "inProgress", "delayed", "escalatedBlocked"}
DDR_EVENT_KINDS = {
    "dd_report_created",
    "dd_report_updated",
    "dd_report_republish_failed",
    "source_review_required",
    "vendor_gate_review_required",
}
ROUTER_EVENT_KINDS = {
    "owner_added_to_thread",
    "owner_already_on_thread",
    "owner_missing",
    "owner_review_required",
}


def build_portfolio_automation_gap_snapshot(
    *,
    client: RhodesClient | None = None,
    max_sites: int = 100,
    include_clean: bool = True,
) -> dict[str, Any]:
    """Build a read-only Rhodes-backed portfolio automation gap snapshot."""

    rhodes = client or RhodesClient()
    sites = rhodes.list_sites(status="active")
    if max_sites > 0:
        sites = sites[:max_sites]

    rows = [
        _build_site_snapshot(rhodes, site)
        for site in sites
    ]
    if not include_clean:
        rows = [row for row in rows if row["gap_count"] > 0 or row["errors"]]

    rows.sort(key=lambda row: (-int(row["gap_count"]), row["site_name"].lower()))
    return {
        "status": "success",
        "system_of_record": "rhodes",
        "generated_at": datetime.now(UTC).isoformat(),
        "max_sites": max_sites,
        "include_clean": include_clean,
        "totals": _build_totals(rows),
        "sites": rows,
    }


def _build_site_snapshot(rhodes: RhodesClient, site: dict[str, Any]) -> dict[str, Any]:
    site_id = _record_id(site, ("siteId", "_id", "id"))
    site_name = _site_name(site)
    slug = _first_str(site, "slug")
    errors: list[str] = []

    documents = _call_list(
        errors,
        "documents",
        lambda: rhodes.list_documents(site_id=site_id),
    )
    notes = _call_list(
        errors,
        "notes",
        lambda: rhodes.list_notes(site_id=site_id, site_slug=slug, limit=50),
    )
    tasks = _call_list(
        errors,
        "tasks",
        lambda: rhodes.list_tasks(site_id=site_id),
    )
    drive_folder = _resolve_drive_folder(rhodes, site_id)
    if drive_folder["status"] != "linked":
        errors.append(str(drive_folder["message"]))

    events = [
        event for event in (_parse_automation_event_note(note) for note in notes)
        if event is not None
    ]
    open_failures = _open_automation_failures(events)
    pending_tasks = _pending_automation_tasks(tasks)
    required_docs = _required_document_coverage(documents)
    p1_dri = _user_ref(site.get("p1Dri") or site.get("p1_dri"))
    owner_routing = _owner_routing_status(p1_dri, events)
    latest_ddr = _latest_ddr_status(events)
    latest_event = _latest_event(events)

    gap_reasons = _gap_reasons(
        required_docs=required_docs,
        drive_folder=drive_folder,
        p1_dri=p1_dri,
        open_failures=open_failures,
        pending_tasks=pending_tasks,
        errors=errors,
    )
    return {
        "site_id": site_id,
        "site_slug": slug,
        "site_name": site_name,
        "stage": _first_str(site, "stage"),
        "status": _first_str(site, "status"),
        "p1_dri": p1_dri,
        "owner_routing_status": owner_routing,
        "drive_folder": drive_folder,
        "required_documents": required_docs,
        "latest_ddr_status": latest_ddr,
        "latest_source_event_fingerprint": _event_fingerprint(latest_event),
        "open_automation_failures": open_failures,
        "pending_review_tasks": pending_tasks,
        "gap_count": len(gap_reasons),
        "gap_reasons": gap_reasons,
        "errors": errors,
    }


def _call_list(
    errors: list[str],
    label: str,
    fn: Callable[[], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    try:
        return fn()
    except RhodesError as exc:
        errors.append(f"{label}: {exc}")
    except Exception as exc:  # noqa: BLE001 - read-only snapshot should continue per site
        errors.append(f"{label}: {exc}")
    return []


def _resolve_drive_folder(rhodes: RhodesClient, site_id: str) -> dict[str, str]:
    try:
        folder_id, folder_url = rhodes.resolve_drive_root(site_id=site_id)
    except Exception as exc:  # noqa: BLE001 - captured as site-level data quality gap
        return {"status": "missing", "message": str(exc)}
    return {"status": "linked", "folder_id": folder_id, "url": folder_url}


def _required_document_coverage(documents: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, dict[str, str]] = {}
    for document in documents:
        doc_type = _first_str(document, "docType", "doc_type")
        if doc_type and doc_type not in by_type:
            by_type[doc_type] = {
                "title": _first_str(document, "title", "name"),
                "document_id": _record_id(document, ("documentId", "_id", "id")),
                "drive_file_id": _first_str(document, "driveFileId", "drive_file_id", "fileId"),
            }
    missing = [doc_type for doc_type in REQUIRED_DD_DOC_TYPES if doc_type not in by_type]
    present = [doc_type for doc_type in REQUIRED_DD_DOC_TYPES if doc_type in by_type]
    return {
        "required": list(REQUIRED_DD_DOC_TYPES),
        "present": present,
        "missing": missing,
        "completion_percent": round(100 * len(present) / len(REQUIRED_DD_DOC_TYPES)),
        "documents": {doc_type: by_type[doc_type] for doc_type in present},
    }


def _parse_automation_event_note(note: dict[str, Any]) -> dict[str, Any] | None:
    body = _first_str(note, "body", "text")
    if not body.startswith("AutomationEvent v1"):
        return None
    values: dict[str, str] = {}
    for line in body.splitlines()[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[_normalize_key(key)] = value.strip()
    return {
        "note_id": _record_id(note, ("noteId", "_id", "id")),
        "kind": values.get("kind", ""),
        "source": values.get("source", ""),
        "source_id": values.get("source_id", ""),
        "decision_required": values.get("decision_required", "").lower() == "yes",
        "requested_decision": values.get("requested_decision", ""),
        "mutation_status": values.get("mutation_status", ""),
        "created_at": values.get("created_at") or _first_str(note, "createdAt", "created_at"),
    }


def _open_automation_failures(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in sorted(events, key=_event_sort_key, reverse=True):
        mutation_status = event["mutation_status"].lower()
        if not event["decision_required"] and mutation_status not in {"failed", "error", "blocked"}:
            continue
        rows.append(_event_summary(event))
    return rows[:5]


def _pending_automation_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for task in tasks:
        status = _first_str(task, "status")
        if status and status not in OPEN_TASK_STATUSES:
            continue
        description = _first_str(task, "description", "body")
        tag = _first_str(task, "tag")
        if "AutomationEvent v1" not in description and tag != "rhodes_data_repair":
            continue
        rows.append({
            "task_id": _record_id(task, ("taskId", "_id", "id")),
            "title": _first_str(task, "title", "name"),
            "status": status or "unknown",
            "tag": tag,
        })
    return rows[:5]


def _owner_routing_status(
    p1_dri: dict[str, str],
    events: list[dict[str, Any]],
) -> str:
    latest_router_event = _latest_event(
        [event for event in events if event["kind"] in ROUTER_EVENT_KINDS]
    )
    if not p1_dri["display"]:
        return "missing_owner"
    if latest_router_event and latest_router_event["kind"] in {
        "owner_added_to_thread",
        "owner_already_on_thread",
    }:
        return "owner_routed"
    return "owner_assigned"


def _latest_ddr_status(events: list[dict[str, Any]]) -> dict[str, str]:
    latest = _latest_event([event for event in events if event["kind"] in DDR_EVENT_KINDS])
    if latest is None:
        return {"status": "not_found"}
    status_by_kind = {
        "dd_report_created": "created",
        "dd_report_updated": "updated",
        "dd_report_republish_failed": "republish_failed",
        "source_review_required": "source_review_required",
        "vendor_gate_review_required": "vendor_gate_review_required",
    }
    return {
        "status": status_by_kind.get(latest["kind"], latest["kind"]),
        **_event_summary(latest),
    }


def _latest_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not events:
        return None
    return max(events, key=_event_sort_key)


def _event_summary(event: dict[str, Any]) -> dict[str, str]:
    return {
        "kind": str(event["kind"]),
        "source": str(event["source"]),
        "source_id": str(event["source_id"]),
        "mutation_status": str(event["mutation_status"]),
        "created_at": str(event["created_at"]),
        "note_id": str(event["note_id"]),
    }


def _event_fingerprint(event: dict[str, Any] | None) -> str:
    if event is None:
        return ""
    return ":".join(
        part for part in (
            str(event["source"]),
            str(event["kind"]),
            str(event["source_id"]),
        )
        if part
    )


def _gap_reasons(
    *,
    required_docs: dict[str, Any],
    drive_folder: dict[str, str],
    p1_dri: dict[str, str],
    open_failures: list[dict[str, str]],
    pending_tasks: list[dict[str, str]],
    errors: list[str],
) -> list[str]:
    reasons: list[str] = []
    if not p1_dri["display"]:
        reasons.append("missing_p1_dri")
    if drive_folder["status"] != "linked":
        reasons.append("missing_drive_folder")
    if required_docs["missing"]:
        reasons.append("missing_required_documents")
    if open_failures:
        reasons.append("open_automation_failures")
    if pending_tasks:
        reasons.append("pending_review_tasks")
    if errors:
        reasons.append("snapshot_read_errors")
    return reasons


def _build_totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "sites": len(rows),
        "sites_with_gaps": sum(1 for row in rows if row["gap_count"] > 0),
        "missing_p1_dri": sum(1 for row in rows if "missing_p1_dri" in row["gap_reasons"]),
        "missing_drive_folder": sum(1 for row in rows if "missing_drive_folder" in row["gap_reasons"]),
        "missing_required_documents": sum(
            1 for row in rows if "missing_required_documents" in row["gap_reasons"]
        ),
        "open_automation_failures": sum(len(row["open_automation_failures"]) for row in rows),
        "pending_review_tasks": sum(len(row["pending_review_tasks"]) for row in rows),
    }


def _user_ref(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {"user_id": "", "name": "", "email": "", "display": ""}
    user_id = _record_id(value, ("userId", "_id", "id"))
    name = _first_str(value, "name", "displayName")
    email = _first_str(value, "email")
    display = name or email or user_id
    return {"user_id": user_id, "name": name, "email": email, "display": display}


def _site_name(site: dict[str, Any]) -> str:
    return _first_str(site, "name", "title", "marketingName") or "Unknown site"


def _record_id(value: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(value, dict):
        return ""
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    for nested_key in ("site", "document", "note", "task", "user", "record", "result", "data"):
        nested_id = _record_id(value.get(nested_key), keys)
        if nested_id:
            return nested_id
    return ""


def _first_str(value: dict[str, Any], *keys: str) -> str:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _event_sort_key(event: dict[str, Any]) -> str:
    return str(event.get("created_at") or "")
