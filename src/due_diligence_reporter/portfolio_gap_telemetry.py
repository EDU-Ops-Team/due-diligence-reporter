"""WorkflowRun telemetry for Portfolio Automation Gaps."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

WORKFLOW_ID = "portfolio-gaps"
WORKFLOW_NAME = "Portfolio Automation Gaps"
SOURCE_TYPE = "portfolio_automation_gaps"
DOCUMENT_GAP_REASONS = {
    "missing_current_milestone_documents",
    "missing_required_documents",
}
GAP_LABELS = {
    "missing_p1_dri": "Missing P1 DRI",
    "missing_drive_folder": "Missing Drive folder",
    "open_automation_failures": "Open automation failures",
    "pending_review_tasks": "Pending review tasks",
    "snapshot_read_errors": "Snapshot read errors",
}


def build_portfolio_gap_workflow_telemetry(
    snapshot: dict[str, Any],
    *,
    run_id: str,
    started_at: str,
    finished_at: str,
    trigger: str = "manual",
    workflow_run_url: str = "",
    notification_result: dict[str, Any] | None = None,
    source_status: str = "",
) -> dict[str, Any]:
    """Build a sanitized WorkflowRun v1 artifact from a Portfolio Gaps snapshot."""

    counts = _counts(snapshot)
    action_records = _action_records(snapshot, as_of=finished_at)
    status = _status(
        snapshot_status=str(snapshot.get("status") or ""),
        source_status=source_status,
        counts=counts,
    )
    notification = notification_result or _dict(
        snapshot.get("notification")
        or snapshot.get("notification_result")
        or snapshot.get("post_result")
    )
    telemetry = {
        "schema_version": "workflow_run.v1",
        "source_type": SOURCE_TYPE,
        "workflow_id": WORKFLOW_ID,
        "workflow_name": WORKFLOW_NAME,
        "run_id": run_id or _fallback_run_id(snapshot, finished_at),
        "source_ref": _source_ref(run_id),
        "trigger": trigger or "manual",
        "started_at": started_at or _snapshot_time(snapshot, finished_at),
        "finished_at": finished_at or _snapshot_time(snapshot, started_at),
        "status": status,
        "summary": _summary(counts, status, notification),
        "counts": counts,
        "steps": _steps(
            snapshot_status=str(snapshot.get("status") or ""),
            workflow_status=status,
            counts=counts,
            notification=notification,
        ),
        "action_records": action_records,
        "site_gaps": _site_gap_rows(snapshot, action_records=action_records, as_of=finished_at),
    }
    if workflow_run_url.strip():
        telemetry["artifacts"] = [
            {
                "label": "GitHub Actions run",
                "kind": "github_actions_run",
                "uri": workflow_run_url.strip(),
            }
        ]
    return telemetry


def _counts(snapshot: dict[str, Any]) -> dict[str, int]:
    totals = _dict(snapshot.get("totals"))
    sites = _list_dicts(snapshot.get("sites"))
    snapshot_read_errors = sum(1 for site in sites if _list(site.get("errors")))
    return {
        "sites": _int(totals.get("sites") or len(sites)),
        "sites_with_gaps": _int(totals.get("sites_with_gaps")),
        "missing_p1_dri": _int(totals.get("missing_p1_dri")),
        "missing_drive_folder": _int(totals.get("missing_drive_folder")),
        "open_automation_failures": _int(totals.get("open_automation_failures")),
        "pending_review_tasks": _int(totals.get("pending_review_tasks")),
        "snapshot_read_errors": _int(totals.get("snapshot_read_errors") or snapshot_read_errors),
    }


def _status(
    *,
    snapshot_status: str,
    source_status: str,
    counts: dict[str, int],
) -> str:
    raw_status = (source_status or snapshot_status or "success").strip().lower()
    if raw_status in {"failure", "failed", "error", "cancelled", "timed_out"}:
        return "failed"
    if snapshot_status and snapshot_status != "success":
        return "failed"
    if _issue_count(counts):
        return "needs_review"
    return "success"


def _summary(
    counts: dict[str, int],
    status: str,
    notification: dict[str, Any],
) -> str:
    notification_text = ""
    if notification:
        notification_status = str(notification.get("status") or "unknown")
        posted = _int(notification.get("posted"))
        notification_text = f" Chat notification {notification_status}; posted={posted}."
    return (
        "Portfolio Automation Gaps "
        f"{status.replace('_', ' ')}: scanned {counts['sites']} site(s), found "
        f"{counts['missing_p1_dri']} missing P1 DRI alert(s), "
        f"{counts['missing_drive_folder']} missing Drive folder alert(s), "
        f"{counts['open_automation_failures']} open automation failure(s), "
        f"{counts['pending_review_tasks']} pending review task(s), and "
        f"{counts['snapshot_read_errors']} Rhodes snapshot read error(s)."
        f"{notification_text}"
    )


def _steps(
    *,
    snapshot_status: str,
    workflow_status: str,
    counts: dict[str, int],
    notification: dict[str, Any],
) -> list[dict[str, Any]]:
    source_ok = workflow_status != "failed" and snapshot_status in {"", "success"}
    snapshot_status_value = "needs_review" if counts["snapshot_read_errors"] else "success"
    gap_status = "needs_review" if _issue_count(counts) else "success"
    notification_status = str(notification.get("status") or "not_emitted")
    notification_sent = notification_status in {"sent", "success"}
    return [
        {
            "key": "github_workflow",
            "label": "GitHub Actions workflow completed",
            "status": "success" if source_ok else "failed",
            "required": True,
            "nominal": source_ok,
            "category": "workflow",
            "detail": "Portfolio Automation Gaps scheduled workflow",
        },
        {
            "key": "rhodes_snapshot",
            "label": "Rhodes portfolio scanned",
            "status": snapshot_status_value if source_ok else "failed",
            "required": True,
            "nominal": source_ok and snapshot_status_value == "success",
            "category": "rhodes",
            "detail": f"sites={counts['sites']}; snapshot_read_errors={counts['snapshot_read_errors']}",
        },
        {
            "key": "gap_summary",
            "label": "Automation gaps summarized",
            "status": gap_status if source_ok else "failed",
            "required": True,
            "nominal": source_ok and gap_status == "success",
            "category": "analysis",
            "detail": f"sites_with_gaps={counts['sites_with_gaps']}",
        },
        {
            "key": "action_routing",
            "label": "Corrective actions routed",
            "status": gap_status if source_ok else "failed",
            "required": True,
            "nominal": source_ok and gap_status == "success",
            "category": "workflow",
            "detail": f"actionable_alerts={_issue_count(counts)}",
        },
        {
            "key": "chat_notification",
            "label": "Google Chat notification outcome captured",
            "status": "success" if notification_sent else notification_status,
            "required": False,
            "nominal": True,
            "category": "notification",
            "detail": _notification_detail(notification),
        },
        {
            "key": "telemetry_artifact",
            "label": "Workflow telemetry artifact emitted",
            "status": "success",
            "required": True,
            "nominal": True,
            "category": "artifact",
            "detail": "reports/telemetry/portfolio-automation-gaps-telemetry.json",
        },
    ]


def _action_records(snapshot: dict[str, Any], *, as_of: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for site in _list_dicts(snapshot.get("sites")):
        site_id = _site_id(site)
        site_name = _site_name(site)
        milestone = _current_milestone_label(site)
        for action in _list_dicts(site.get("remediation_actions")):
            gap_type = str(action.get("gap_type") or action.get("alert_type") or "").strip()
            if gap_type in DOCUMENT_GAP_REASONS:
                continue
            record = dict(action)
            record.setdefault("schema_version", "action_record.v1")
            record.setdefault("source_workflow", WORKFLOW_ID)
            record.setdefault("owning_workflow", str(record.get("workflow_owner") or WORKFLOW_ID))
            record.setdefault("workflow_owner", str(record.get("owning_workflow") or WORKFLOW_ID))
            record.setdefault("status", "awaiting_action_telemetry")
            record.setdefault("alert_type", gap_type or _action_alert_type(record))
            record.setdefault("alert", GAP_LABELS.get(gap_type, gap_type))
            record.setdefault("action_id", _action_id(site_id, site_name, gap_type or "alert"))
            record.setdefault("site_id", site_id)
            record.setdefault("site_name", site_name)
            record.setdefault("current_milestone", milestone)
            record.setdefault("as_of", as_of)
            record.setdefault("evidence_summary", "")
            record.setdefault("action_taken", str(record.get("remediation_summary") or ""))
            records.append(record)
    return records


def _site_gap_rows(
    snapshot: dict[str, Any],
    *,
    action_records: list[dict[str, Any]],
    as_of: str,
) -> list[dict[str, Any]]:
    action_index = {
        (
            str(action.get("site_id") or ""),
            _normalize_key(str(action.get("gap_type") or action.get("alert_type") or action.get("alert") or "")),
        ): action
        for action in action_records
    }
    rows: list[dict[str, Any]] = []
    for site in _list_dicts(snapshot.get("sites")):
        gaps = [
            GAP_LABELS.get(str(reason), str(reason))
            for reason in _list(site.get("gap_reasons"))
            if str(reason) not in DOCUMENT_GAP_REASONS
        ]
        if _list(site.get("errors")) and GAP_LABELS["snapshot_read_errors"] not in gaps:
            gaps.append(GAP_LABELS["snapshot_read_errors"])
        if not gaps:
            continue
        site_id = _site_id(site)
        milestone = _dict(site.get("current_milestone"))
        row = {
            "site_id": site_id,
            "site_name": _site_name(site),
            "current_milestone": _first_str(milestone, "label", "key"),
            "current_milestone_status": _first_str(milestone, "status"),
            "gap_count": len(gaps),
            "gaps": gaps,
            "alert_actions": [],
        }
        for gap_label in gaps:
            action_key = _normalize_key(_gap_key_from_label(gap_label))
            action = action_index.get((site_id, action_key)) or {}
            row["alert_actions"].append(
                {
                    "alert": gap_label,
                    "action_status": str(action.get("status") or "awaiting_action_telemetry"),
                    "action_as_of": str(action.get("as_of") or action.get("updated_at") or as_of),
                    "remediation_summary": str(
                        action.get("remediation_summary")
                        or action.get("action_taken")
                        or "No source action telemetry has been captured for this alert yet."
                    ),
                    "evidence_summary": str(action.get("evidence_summary") or ""),
                    "action_id": str(action.get("action_id") or ""),
                    "owning_workflow": str(action.get("owning_workflow") or ""),
                    "workflow_owner": str(action.get("workflow_owner") or ""),
                    "severity": str(action.get("severity") or ""),
                    "review_required": bool(action.get("review_required")),
                    "review_reason": str(action.get("review_reason") or ""),
                    "retryable": bool(action.get("retryable")),
                }
            )
        rows.append(row)
    return rows


def _issue_count(counts: dict[str, int]) -> int:
    return (
        counts["missing_p1_dri"]
        + counts["missing_drive_folder"]
        + counts["open_automation_failures"]
        + counts["pending_review_tasks"]
        + counts["snapshot_read_errors"]
    )


def _notification_detail(notification: dict[str, Any]) -> str:
    if not notification:
        return "notification_result=not_emitted"
    return "; ".join(
        f"{key}={value}"
        for key, value in sorted(notification.items())
        if key in {"posted", "reason", "sites_with_gaps", "status"}
    ) or "notification_result=captured"


def _source_ref(run_id: str) -> str:
    return f"GitHub Actions run {run_id}" if run_id else WORKFLOW_NAME


def _fallback_run_id(snapshot: dict[str, Any], finished_at: str) -> str:
    return str(snapshot.get("run_id") or f"portfolio-gaps-{finished_at}")


def _snapshot_time(snapshot: dict[str, Any], fallback: str) -> str:
    return str(snapshot.get("generated_at") or fallback or datetime.now(UTC).isoformat())


def _action_alert_type(action: dict[str, Any]) -> str:
    return _normalize_key(str(action.get("alert") or "portfolio_gap_alert")) or "portfolio_gap_alert"


def _action_id(site_id: str, site_name: str, gap_type: str) -> str:
    return "portfolio-gaps:" + ":".join(
        _action_id_token(part)
        for part in (site_id or site_name or "unknown-site", gap_type or "unknown-alert")
    )


def _action_id_token(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    return "-".join(part for part in cleaned.split("-") if part)[:80] or "unknown"


def _gap_key_from_label(label: str) -> str:
    for key, value in GAP_LABELS.items():
        if value == label:
            return key
    return label


def _current_milestone_label(site: dict[str, Any]) -> str:
    milestone = _dict(site.get("current_milestone"))
    return _first_str(milestone, "label", "key")


def _site_id(site: dict[str, Any]) -> str:
    return _first_str(site, "site_id", "siteId", "_id", "id")


def _site_name(site: dict[str, Any]) -> str:
    return _first_str(site, "site_name", "name", "title", "marketingName") or "Unknown site"


def _first_str(value: dict[str, Any], *keys: str) -> str:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _normalize_key(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _list_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
