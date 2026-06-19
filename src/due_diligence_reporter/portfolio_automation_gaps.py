"""Read-only Rhodes portfolio automation gap snapshot."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .rhodes import RhodesClient, RhodesError

P1_MILESTONE_ORDER = (
    "prospecting",
    "conductingDiligence",
    "acquireProperty",
    "constructionPermits",
    "certificateOfOccupancy",
    "educationRegulatoryApproval",
    "preparingToOpen",
    "readyToOpen",
    "postOpen",
)
MILESTONE_LABELS = {
    "prospecting": "Prospecting",
    "conductingDiligence": "Conducting Diligence",
    "acquireProperty": "Acquiring Property",
    "constructionPermits": "Obtaining Permits",
    "certificateOfOccupancy": "Executing Buildout",
    "educationRegulatoryApproval": "Gaining Edu Approval",
    "preparingToOpen": "Preparing to Open",
    "readyToOpen": "Ready to Open",
    "postOpen": "Operating",
}
STAGE_MILESTONE_FALLBACK = {
    "diligence": "conductingDiligence",
    "buildout": "constructionPermits",
    "operating": "postOpen",
}

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
PORTFOLIO_GAPS_SOURCE = "portfolio-gaps"
ACTION_LABELS = {
    "missing_p1_dri": "Missing P1 DRI",
    "missing_drive_folder": "Missing Drive folder",
    "open_automation_failures": "Open automation failures",
    "pending_review_tasks": "Pending review tasks",
    "snapshot_read_errors": "Snapshot read errors",
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
    generated_at = datetime.now(UTC).isoformat()
    for row in rows:
        _attach_source_action_records(row, as_of=generated_at)
    if not include_clean:
        rows = [row for row in rows if row["gap_count"] > 0 or row["errors"]]

    rows.sort(key=lambda row: (-int(row["gap_count"]), row["site_name"].lower()))
    return {
        "status": "success",
        "system_of_record": "rhodes",
        "generated_at": generated_at,
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

    site_detail = _call_record(
        errors,
        "site",
        lambda: rhodes.get_site(site_id=site_id),
    )
    site_context = site_detail or site
    missing_document_snapshot = _call_record(
        errors,
        "missing_documents",
        lambda: rhodes.get_missing_documents(site_id=site_id),
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

    events = [
        event for event in (_parse_automation_event_note(note) for note in notes)
        if event is not None
    ]
    open_failures = _open_automation_failures(events)
    pending_tasks = _pending_automation_tasks(tasks)
    current_milestone = _current_milestone(site_context)
    required_docs = _milestone_document_coverage(
        missing_document_snapshot,
        current_milestone=current_milestone,
    )
    p1_dri = _user_ref(site_context.get("p1Dri") or site_context.get("p1_dri"))
    owner_routing = _owner_routing_status(p1_dri, events)
    latest_ddr = _latest_ddr_status(events)
    latest_event = _latest_event(events)

    gap_reasons = _gap_reasons(
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
        "current_milestone": current_milestone,
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


def _call_record(
    errors: list[str],
    label: str,
    fn: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    try:
        result = fn()
        return result if isinstance(result, dict) else {}
    except RhodesError as exc:
        errors.append(f"{label}: {exc}")
    except Exception as exc:  # noqa: BLE001 - read-only snapshot should continue per site
        errors.append(f"{label}: {exc}")
    return {}


def _resolve_drive_folder(rhodes: RhodesClient, site_id: str) -> dict[str, str]:
    try:
        folder_id, folder_url = rhodes.resolve_drive_root(site_id=site_id)
    except Exception as exc:  # noqa: BLE001 - captured as site-level data quality gap
        return {"status": "missing", "message": str(exc)}
    return {"status": "linked", "folder_id": folder_id, "url": folder_url}


def _milestone_document_coverage(
    missing_document_snapshot: dict[str, Any],
    *,
    current_milestone: dict[str, str],
) -> dict[str, Any]:
    milestone_key = current_milestone.get("key", "")
    milestone_row = _missing_documents_milestone(
        missing_document_snapshot,
        milestone_key=milestone_key,
    )
    missing_details = _document_requirement_rows(milestone_row.get("missingRequired"))
    present_details = _document_requirement_rows(milestone_row.get("presentRequired"))
    missing = [row["doc_type"] for row in missing_details]
    present = [row["doc_type"] for row in present_details]
    required = [row["doc_type"] for row in [*present_details, *missing_details]]
    required_count = _int(milestone_row.get("requiredCount"))
    if required_count <= 0:
        required_count = len(required)
    present_count = _int(milestone_row.get("presentRequiredCount"))
    if present_count <= 0:
        present_count = len(present)
    completion_percent = 100 if required_count <= 0 else round(100 * present_count / required_count)
    return {
        "milestone": current_milestone,
        "required": required,
        "present": present,
        "missing": missing,
        "missing_details": missing_details,
        "present_details": present_details,
        "required_count": required_count,
        "present_required_count": present_count,
        "completion_percent": completion_percent,
    }


def _missing_documents_milestone(
    missing_document_snapshot: dict[str, Any],
    *,
    milestone_key: str,
) -> dict[str, Any]:
    milestones = missing_document_snapshot.get("milestones")
    if not isinstance(milestones, list):
        return {}
    for milestone in milestones:
        if not isinstance(milestone, dict):
            continue
        if _first_str(milestone, "key") == milestone_key:
            return milestone
    return {}


def _document_requirement_rows(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        doc_type = _first_str(item, "docType", "doc_type")
        if not doc_type:
            continue
        rows.append({
            "doc_type": doc_type,
            "label": _first_str(item, "label", "title", "name") or doc_type,
        })
    return rows


def _current_milestone(site: dict[str, Any]) -> dict[str, str]:
    milestones = site.get("milestones")
    if isinstance(milestones, dict):
        active = _milestone_by_status(milestones, "active")
        if active:
            return active
        next_open = _first_incomplete_milestone(milestones)
        if next_open:
            return next_open

    stage = _first_str(site, "stage")
    key = STAGE_MILESTONE_FALLBACK.get(stage, "")
    return _milestone_ref(key, status=_first_str(site, "status")) if key else _milestone_ref("")


def _milestone_by_status(milestones: dict[str, Any], status: str) -> dict[str, str]:
    for key in P1_MILESTONE_ORDER:
        milestone = milestones.get(key)
        if isinstance(milestone, dict) and _first_str(milestone, "status") == status:
            return _milestone_ref(key, status=status)
    return {}


def _first_incomplete_milestone(milestones: dict[str, Any]) -> dict[str, str]:
    for key in P1_MILESTONE_ORDER:
        milestone = milestones.get(key)
        if not isinstance(milestone, dict):
            continue
        status = _first_str(milestone, "status")
        if status != "completed":
            return _milestone_ref(key, status=status)
    return {}


def _milestone_ref(key: str, *, status: str = "") -> dict[str, str]:
    return {
        "key": key,
        "label": MILESTONE_LABELS.get(key, key),
        "status": status,
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
        "open_automation_failures": sum(len(row["open_automation_failures"]) for row in rows),
        "pending_review_tasks": sum(len(row["pending_review_tasks"]) for row in rows),
    }


def _attach_source_action_records(site: dict[str, Any], *, as_of: str) -> None:
    actions = [
        action
        for action in (
            _source_action_record(site, gap_type, as_of=as_of)
            for gap_type in site.get("gap_reasons", [])
        )
        if action
    ]
    if actions:
        site["remediation_actions"] = actions


def _source_action_record(site: dict[str, Any], gap_type: Any, *, as_of: str) -> dict[str, Any]:
    gap = str(gap_type)
    label = ACTION_LABELS.get(gap, gap)
    route = _action_route(gap, site)
    if not route:
        return {}
    source = str(route["source"])
    owner = str(route["owning_workflow"])
    status = str(route["status"])
    site_id = str(site.get("site_id") or "")
    site_name = str(site.get("site_name") or "Unknown site")
    action_id = _action_id(site_id, site_name, gap)
    record = {
        "schema_version": "action_record.v1",
        "source": source,
        "source_workflow": PORTFOLIO_GAPS_SOURCE,
        "owning_workflow": owner,
        "workflow_owner": str(route.get("workflow_owner") or owner),
        "gap_type": gap,
        "alert": label,
        "status": status,
        "as_of": as_of,
        "site_id": site_id,
        "site_name": site_name,
        "current_milestone": _current_milestone_label(site),
        "severity": str(route.get("severity") or "medium"),
        "action_requested": str(route["action_requested"]),
        "action_taken": str(route["action_taken"]),
        "remediation_summary": str(route["action_taken"]),
        "evidence_summary": str(route["evidence_summary"]),
        "review_required": bool(route.get("review_required")),
        "review_reason": str(route.get("review_reason") or ""),
        "error_summary": str(route.get("error_summary") or ""),
        "retryable": bool(route.get("retryable")),
        "action_id": action_id,
        "idempotency_key": str(route.get("idempotency_key") or action_id),
    }
    for key in (
        "autonomy_mode",
        "sor_system",
        "sor_write_status",
        "sor_readback_status",
        "sor_readback_summary",
        "operating_note_status",
        "p1_dri_route_status",
        "failure_route",
        "next_step",
    ):
        if key in route:
            record[key] = str(route.get(key) or "")
    return record


def _action_route(gap: str, site: dict[str, Any]) -> dict[str, Any]:
    if gap == "missing_p1_dri":
        if not str(site.get("site_id") or "").strip():
            return _missing_site_identity_route(gap)
        return {
            "source": "alpha-analysis-downstream-processing",
            "owning_workflow": "aadp",
            "status": "queued",
            "severity": "high",
            "action_requested": "Assign the site's P1 DRI in Rhodes.",
            "action_taken": (
                "Portfolio Gaps routed the missing P1 DRI alert to AADP; "
                "awaiting AADP remediation telemetry and Rhodes readback."
            ),
            "evidence_summary": "Rhodes snapshot did not expose a current P1 DRI for this site.",
            "review_required": False,
            "retryable": True,
            "autonomy_mode": "automatic_candidate",
            "sor_system": "rhodes",
            "sor_write_status": "not_started",
            "sor_readback_status": "not_verified",
            "sor_readback_summary": "Awaiting AADP remediation and Rhodes readback.",
            "operating_note_status": "not_started",
            "p1_dri_route_status": "missing_owner",
            "failure_route": "",
            "next_step": (
                "AADP should assign the deterministic P1 DRI, verify Rhodes readback, "
                "write an operating note, and emit final action status."
            ),
        }
    if gap == "missing_drive_folder":
        if not str(site.get("site_id") or "").strip():
            return _missing_site_identity_route(gap)
        return {
            "source": "alpha-analysis-downstream-processing",
            "owning_workflow": "aadp",
            "status": "queued",
            "severity": "high",
            "action_requested": "Create or link the site's Google Drive folder in Rhodes.",
            "action_taken": (
                "Portfolio Gaps routed the missing Drive folder alert to AADP; "
                "awaiting AADP remediation telemetry and Drive/Rhodes readback."
            ),
            "evidence_summary": "Rhodes snapshot did not expose a linked site Drive folder.",
            "review_required": False,
            "retryable": True,
            "autonomy_mode": "automatic_candidate",
            "sor_system": "drive,rhodes",
            "sor_write_status": "not_started",
            "sor_readback_status": "not_verified",
            "sor_readback_summary": "Awaiting AADP remediation and Drive/Rhodes readback.",
            "operating_note_status": "not_started",
            "p1_dri_route_status": "not_started",
            "failure_route": "",
            "next_step": (
                "AADP should create or link the Drive folder, verify Drive/Rhodes readback, "
                "write an operating note, and emit final action status."
            ),
        }
    if gap == "snapshot_read_errors":
        return {
            "source": "rhodes",
            "owning_workflow": "rhodes",
            "status": "needs_review",
            "severity": "high",
            "action_requested": "Restore Portfolio Gaps Rhodes snapshot reads and rerun the portfolio scan.",
            "action_taken": (
                "Portfolio Gaps routed a Rhodes snapshot read error to Rhodes; "
                "no verified readback has been captured yet."
            ),
            "evidence_summary": "Portfolio Gaps recorded a sanitized Rhodes snapshot read error for this site.",
            "review_required": True,
            "review_reason": "Confirm the Rhodes API can read this site snapshot, then rerun Portfolio Gaps.",
            "error_summary": f"Portfolio Gaps recorded {len(site.get('errors') or [])} snapshot read error(s).",
            "retryable": True,
        }
    if gap == "open_automation_failures":
        owner = _owner_for_open_failures(site)
        return {
            "source": owner,
            "owning_workflow": owner,
            "status": "needs_review",
            "severity": "high",
            "action_requested": "Review the failed automation event and rerun or escalate the owning workflow.",
            "action_taken": (
                "Portfolio Gaps routed open automation failure follow-up to the owning workflow; "
                "no verified rerun/readback has been captured yet."
            ),
            "evidence_summary": (
                f"Rhodes snapshot reported {len(site.get('open_automation_failures') or [])} "
                "open automation failure(s)."
            ),
            "review_required": True,
            "review_reason": "A previous automation event is still failed, blocked, or decision-required.",
            "retryable": True,
        }
    if gap == "pending_review_tasks":
        return {
            "source": "rhodes",
            "owning_workflow": "rhodes",
            "status": "needs_review",
            "severity": "medium",
            "action_requested": "Assign or close the pending automation review task in Rhodes.",
            "action_taken": (
                "Portfolio Gaps routed pending automation review task follow-up to Rhodes; "
                "task closure readback has not been captured yet."
            ),
            "evidence_summary": (
                f"Rhodes snapshot reported {len(site.get('pending_review_tasks') or [])} "
                "pending automation review task(s)."
            ),
            "review_required": True,
            "review_reason": "A Rhodes automation review task is still open for this site.",
            "retryable": False,
        }
    return {}


def _missing_site_identity_route(gap: str) -> dict[str, Any]:
    label = ACTION_LABELS.get(gap, gap)
    return {
        "source": PORTFOLIO_GAPS_SOURCE,
        "owning_workflow": PORTFOLIO_GAPS_SOURCE,
        "workflow_owner": PORTFOLIO_GAPS_SOURCE,
        "status": "blocked",
        "severity": "high",
        "action_requested": "Resolve verified Rhodes site identity before routing remediation.",
        "action_taken": (
            f"Portfolio Gaps found {label} but did not route it to AADP because "
            "the Rhodes snapshot did not include a verified site ID."
        ),
        "evidence_summary": (
            "Portfolio Gaps requires a verified Rhodes site ID before an autonomous "
            "source workflow can mutate Rhodes or Drive for this site."
        ),
        "review_required": True,
        "review_reason": "Resolve the site identity in Rhodes/source data, then rerun Portfolio Gaps.",
        "retryable": True,
        "autonomy_mode": "source_context_blocked",
        "sor_system": "rhodes",
        "sor_write_status": "blocked",
        "sor_readback_status": "not_verified",
        "sor_readback_summary": "No source-system readback was attempted because site ID is missing.",
        "operating_note_status": "not_started",
        "p1_dri_route_status": "not_started",
        "failure_route": PORTFOLIO_GAPS_SOURCE,
        "next_step": "Resolve verified Rhodes site ID, then rerun Portfolio Gaps before AADP remediation.",
    }


def _owner_for_open_failures(site: dict[str, Any]) -> str:
    source_owners = {
        "alpha-analysis-downstream-processing": "aadp",
        "aadp": "aadp",
        "due-diligence-reporter": "ddr",
        "ddr": "ddr",
        "edu-ops-email-router": "email-router",
        "email-router": "email-router",
        "final-lease-monitor": "final-lease",
        "final-lease": "final-lease",
    }
    owners = {
        source_owners.get(str(item.get("source") or ""), "")
        for item in site.get("open_automation_failures", [])
        if isinstance(item, dict)
    }
    owners.discard("")
    return owners.pop() if len(owners) == 1 else PORTFOLIO_GAPS_SOURCE


def _current_milestone_label(site: dict[str, Any]) -> str:
    milestone = site.get("current_milestone")
    if not isinstance(milestone, dict):
        milestone = {}
    return _first_str(milestone, "label", "key")


def _action_id(site_id: str, site_name: str, gap: str) -> str:
    site_key = _action_id_part(site_id or site_name or "unknown-site")
    gap_key = _action_id_part(gap or "unknown-gap")
    return f"portfolio-gaps:{site_key}:{gap_key}"


def _action_id_part(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    return "-".join(part for part in cleaned.split("-") if part)[:80] or "unknown"


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


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
