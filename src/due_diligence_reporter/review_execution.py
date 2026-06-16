from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

WORKFLOW_ID = "ddr"
WORKFLOW_NAME = "Due Diligence Reporter"
SUBWORKFLOW_ID = "ddr-review-execution"
SUBWORKFLOW_NAME = "DDR Review Execution"
EXECUTABLE_REVIEW_DECISIONS = {
    "approve",
    "assignowner",
    "correctsitematch",
    "rerunworkflow",
    "marknotapplicable",
}
DDR_OWNER_KEYS = {"ddr", "duediligencereporter", "due-diligence-reporter"}
MISSING_DRIVE_FOLDER_ALERT = "missing_drive_folder_url"
MAX_ACTION_TEXT_LENGTH = 360
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s)\]}>,]+", re.IGNORECASE)
_REQUEST_ID_RE = re.compile(
    r"\b(?:request[_\s-]*id|x-request-id)\s*[:=]?\s*[A-Za-z0-9][A-Za-z0-9._-]{5,}",
    re.IGNORECASE,
)
_LOCAL_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s)\]}>,]+")
_CREDENTIAL_NAME_RE = re.compile(
    r"\b[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD|WEBHOOK|CREDENTIAL|CREDENTIALS)[A-Z0-9_]*\b"
)


def execute_ddr_review_requests(
    payload: object,
    *,
    now: datetime | None = None,
    max_actions: int = 0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Consume dashboard-approved DDR review requests and emit source-owned readback.

    This intentionally does not run report generation, publish documents, or write
    Rhodes/Drive. It gives the dashboard a source-owned execution result while
    preserving the boundary that real DDR mutations need the DDR runtime context.
    """

    input_payload = _as_dict(payload)
    as_of = _iso(now or datetime.now(UTC))
    requests = [_as_dict(item) for item in _as_list(input_payload.get("requests"))]
    enriched_requests = [_sanitize_public_payload(request) for request in requests]
    max_action_count = max(0, int(max_actions or 0))
    action_records: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "source": WORKFLOW_ID,
        "input_schema_version": _str(input_payload.get("schema_version")),
        "status": "skipped",
        "as_of": as_of,
        "dry_run": bool(dry_run),
        "request_count": len(requests),
        "eligible_count": 0,
        "attempted_count": 0,
        "success_count": 0,
        "skipped_count": 0,
        "needs_review_count": 0,
        "blocked_count": 0,
        "error_count": 0,
    }

    for index, request in enumerate(requests):
        if not _is_ddr_owned(request):
            continue
        summary["eligible_count"] += 1

        decision = _normalize_key(_str(request.get("decision")))
        if decision not in EXECUTABLE_REVIEW_DECISIONS:
            action = _build_execution_action(
                request,
                as_of=as_of,
                status="skipped_already_corrected",
                action_taken=(
                    "DDR skipped this review request because the recorded decision is "
                    "not executable by the source workflow."
                ),
                review_required=False,
                retryable=False,
            )
            _attach_execution_result(enriched_requests[index], action)
            action_records.append(action)
            _increment_summary(summary, action["status"])
            continue

        if max_action_count > 0 and summary["attempted_count"] >= max_action_count:
            continue
        summary["attempted_count"] += 1

        action = _action_for_request(request, as_of=as_of, decision=decision, dry_run=dry_run)
        _attach_execution_result(enriched_requests[index], action)
        action_records.append(action)
        _increment_summary(summary, action["status"])

    summary["status"] = _execution_status(summary)
    return {
        "schema_version": "ddr_review_execution_result.v1",
        "execution": summary,
        "requests": enriched_requests,
        "action_records": action_records,
        "runs": [
            _build_review_execution_run(
                as_of=as_of,
                summary=summary,
                action_records=action_records,
            )
        ],
    }


def _action_for_request(
    request: dict[str, Any],
    *,
    as_of: str,
    decision: str,
    dry_run: bool,
) -> dict[str, Any]:
    alert_type = _alert_type(request)
    workflow_owner = _workflow_owner(request, alert_type=alert_type)

    if decision == "marknotapplicable":
        return _build_execution_action(
            request,
            as_of=as_of,
            status="skipped_already_corrected",
            action_taken=(
                "DDR marked the approved review request not applicable; no report, "
                "Drive, Rhodes, or Chat mutation was attempted."
            ),
            review_required=False,
            retryable=False,
            workflow_owner=workflow_owner,
        )

    if alert_type == MISSING_DRIVE_FOLDER_ALERT:
        return _missing_drive_folder_action(
            request,
            as_of=as_of,
            dry_run=dry_run,
            workflow_owner=workflow_owner,
        )

    if dry_run:
        return _build_execution_action(
            request,
            as_of=as_of,
            status="needs_review",
            action_taken=(
                "DDR received the approved review request in dry-run mode; no source "
                "workflow mutation was attempted."
            ),
            review_required=True,
            review_reason="Disable dry-run and rerun the DDR review execution bridge after confirming the source runtime context.",
            retryable=True,
            workflow_owner=workflow_owner,
        )

    if _requires_source_runtime(alert_type):
        return _build_execution_action(
            request,
            as_of=as_of,
            status="needs_review",
            action_taken=(
                "DDR received the approved review request, but this action still needs "
                "source document/run context before report generation or source repair "
                "can safely rerun."
            ),
            review_required=True,
            review_reason=_source_runtime_reason(alert_type, workflow_owner=workflow_owner),
            retryable=True,
            workflow_owner=workflow_owner,
        )

    return _build_execution_action(
        request,
        as_of=as_of,
        status="blocked",
        action_taken=(
            "DDR received the approved review request, but no source-specific execution "
            "handler exists yet for this alert type."
        ),
        review_required=True,
        review_reason=(
            "Add a DDR source-workflow execution handler for this alert type before it "
            "can run automatically."
        ),
        error_summary="DDR review execution has no handler for this action.",
        retryable=False,
        workflow_owner=workflow_owner,
    )


def _missing_drive_folder_action(
    request: dict[str, Any],
    *,
    as_of: str,
    dry_run: bool,
    workflow_owner: str,
) -> dict[str, Any]:
    if dry_run:
        return _build_execution_action(
            request,
            as_of=as_of,
            status="needs_review",
            action_taken=(
                "DDR dry-run confirmed this approved request is blocked by missing "
                "site Drive folder context; no Drive, Rhodes, report, or Chat mutation "
                "was attempted."
            ),
            review_required=True,
            review_reason=(
                "Create or link the site Drive folder through the AADP/Rhodes "
                "folder-provisioning path, then rerun DDR readiness without dry-run."
            ),
            retryable=True,
            workflow_owner=workflow_owner,
        )

    return _build_execution_action(
        request,
        as_of=as_of,
        status="needs_review",
        action_taken=(
            "DDR handled the approved request as a missing Drive folder prerequisite. "
            "DDR did not create or link the folder; AADP/Rhodes folder provisioning "
            "must create or link the site Drive folder before DDR readiness can rerun."
        ),
        review_required=True,
        review_reason=(
            "Missing Drive folder URL is prerequisite site context, not a DDR report "
            "generation step. Route to AADP/Rhodes folder provisioning and verify "
            "Rhodes exposes the site Drive folder URL before rerunning DDR."
        ),
        retryable=True,
        workflow_owner=workflow_owner,
    )


def _build_execution_action(
    request: dict[str, Any],
    *,
    as_of: str,
    status: str,
    action_taken: str,
    review_required: bool,
    retryable: bool,
    workflow_owner: str | None = None,
    review_reason: str = "",
    error_summary: str = "",
) -> dict[str, Any]:
    action_id = _str(request.get("action_id"))
    decision_id = _str(request.get("decision_id"))
    request_id = _str(request.get("request_id"))
    alert_type = _alert_type(request)
    owner = workflow_owner or _workflow_owner(request, alert_type=alert_type)
    fallback_action_id = f"ddr-review:{request_id or decision_id or alert_type}"
    return {
        "schema_version": "action_record.v1",
        "action_id": _safe_token(action_id or fallback_action_id),
        "source_workflow": WORKFLOW_ID,
        "owning_workflow": WORKFLOW_ID,
        "workflow_owner": owner,
        "alert_type": alert_type,
        "severity": _severity_for_status(status),
        "status": status,
        "site_name": _safe_text(_str(request.get("site_name"))),
        "current_milestone": _safe_text(_str(request.get("current_milestone"))),
        "site": {
            "site_id": _safe_token(_str(request.get("site_id"))),
            "name": _safe_text(_str(request.get("site_name")) or "Portfolio"),
            "current_milestone": _safe_text(_str(request.get("current_milestone"))),
        },
        "action_requested": _safe_text(
            _str(request.get("action_requested")) or _default_action_requested(alert_type)
        ),
        "routing_instruction": _safe_text(_str(request.get("routing_instruction"))),
        "action_taken": _safe_text(action_taken),
        "as_of": as_of,
        "owner": {"workflow_owner": owner, "human_owner": "DDR operator"},
        "human_owner": "DDR operator",
        "evidence": {
            "readback": _safe_text(
                _str(request.get("evidence_summary"))
                or "DDR consumed a sanitized dashboard review execution request."
            )
        },
        "evidence_summary": _safe_text(
            _str(request.get("evidence_summary"))
            or "DDR consumed a sanitized dashboard review execution request."
        ),
        "review": {
            "required": review_required,
            "reason": _safe_text(review_reason or _str(request.get("review_reason"))),
            "review_url": "",
        },
        "review_required": review_required,
        "review_reason": _safe_text(review_reason or _str(request.get("review_reason"))),
        "error": {"summary": _safe_text(error_summary), "retryable": retryable},
        "error_summary": _safe_text(error_summary),
        "retryable": retryable,
        "decision_id": _safe_token(decision_id),
        "review_request_id": _safe_token(request_id),
        "source_action_id": _safe_token(action_id),
        "review_decision": _safe_category(_str(request.get("decision"))),
        "source_action_status": _safe_category(_str(request.get("source_action_status"))),
    }


def _attach_execution_result(request: dict[str, Any], action: dict[str, Any]) -> None:
    remediation_summary = (
        action.get("action_taken")
        or action.get("review_reason")
        or action.get("error_summary")
        or ""
    )
    request["action_taken"] = action.get("action_taken", "")
    request["review_reason"] = action.get("review_reason", "")
    request["error_summary"] = action.get("error_summary", "")
    request["execution_status"] = _execution_action_status(action["status"])
    request["execution_summary"] = remediation_summary
    request["execution_action"] = {
        "schema_version": "review_execution_action.v1",
        "status": _execution_action_status(action["status"]),
        "action_status": action["status"],
        "decision_id": action.get("decision_id", ""),
        "review_request_id": action.get("review_request_id", ""),
        "source_action_id": action.get("source_action_id", ""),
        "action_id": action.get("action_id", ""),
        "alert_type": action.get("alert_type", ""),
        "workflow_owner": action.get("workflow_owner", ""),
        "remediation_summary": remediation_summary,
        "action_taken": action.get("action_taken", ""),
        "routing_instruction": action.get("routing_instruction", ""),
        "review_required": action.get("review_required", False),
        "review_reason": action.get("review_reason", ""),
        "error_summary": action.get("error_summary", ""),
    }


def _build_review_execution_run(
    *,
    as_of: str,
    summary: dict[str, Any],
    action_records: list[dict[str, Any]],
) -> dict[str, Any]:
    unresolved = (
        int(summary["needs_review_count"])
        + int(summary["blocked_count"])
        + int(summary["error_count"])
    )
    processed = int(summary["success_count"]) + int(summary["skipped_count"])
    run_status = _run_status(summary)
    return {
        "schema_version": "workflow_run.v1",
        "source": "review_execution_result",
        "source_type": "review_execution_result",
        "workflow_id": WORKFLOW_ID,
        "workflow_name": WORKFLOW_NAME,
        "subworkflow_id": SUBWORKFLOW_ID,
        "subworkflow_name": SUBWORKFLOW_NAME,
        "run_id": _stable_run_id(as_of, action_records),
        "source_ref": "Workflow telemetry review execution requests",
        "trigger": "review_execution_requests",
        "started_at": as_of,
        "finished_at": as_of,
        "status": run_status,
        "summary": _run_summary(summary),
        "counts": {
            "requests": int(summary["request_count"]),
            "eligible": int(summary["eligible_count"]),
            "attempted": int(summary["attempted_count"]),
            "processed": processed,
            "unresolved": unresolved,
            "errors": int(summary["error_count"]),
        },
        "steps": [
            {
                "key": "review_request_export",
                "label": "Dashboard review requests consumed",
                "status": "success" if summary["eligible_count"] else "skipped",
                "required": True,
                "nominal": bool(summary["eligible_count"]),
                "category": "review",
                "detail": f"eligible={summary['eligible_count']}; attempted={summary['attempted_count']}",
                "completed_at": as_of,
            },
            {
                "key": "source_runtime_execution",
                "label": "DDR source runtime execution checked",
                "status": run_status,
                "required": True,
                "nominal": unresolved == 0 and int(summary["error_count"]) == 0,
                "category": "workflow",
                "detail": f"processed={processed}; unresolved={unresolved}",
                "completed_at": as_of,
            },
        ],
        "action_records": action_records,
        "review_execution": summary,
    }


def _is_ddr_owned(request: dict[str, Any]) -> bool:
    for key in ("owning_workflow", "workflow_owner", "execution_owner"):
        if _normalize_key(_str(request.get(key))) in DDR_OWNER_KEYS:
            return True
    action_id = _str(request.get("action_id")).lower()
    return action_id.startswith("ddr:")


def _requires_source_runtime(alert_type: str) -> bool:
    return alert_type in {
        "report_generation_failed",
        "source_read_issue",
        "daily_dd_check_errors",
        "daily_dd_report_incomplete",
        "inbox_scan_manual_review",
        "inbox_scan_errors",
        "raycon_followup_alerts",
        "raycon_followup_errors",
        "raycon_followup_notification_failure",
        "vendor_doc_republish_errors",
        "vendor_doc_republish_dry_run",
        "drive_rhodes_reconciliation",
        "rhodes_registration",
    }


def _source_runtime_reason(alert_type: str, *, workflow_owner: str) -> str:
    if alert_type in {"report_generation_failed", "daily_dd_check_errors"}:
        return "Rerun the DDR source workflow with the original site/run context and emit a fresh WorkflowRun artifact."
    if alert_type == "source_read_issue":
        return "Resolve the missing or unreadable source evidence, then rerun DDR from the source workflow."
    if alert_type == "daily_dd_report_incomplete":
        return "Repair the incomplete report inputs or unresolved template tokens before rerunning DDR."
    if alert_type.startswith("inbox_scan"):
        return "Review the source inbox-routing row and rerun Inbox Scan or DDR after correcting the routing blocker."
    if alert_type.startswith("raycon_followup"):
        return "Review the RayCon follow-up source row and rerun the RayCon follow-up workflow after correcting the blocker."
    if alert_type.startswith("vendor_doc"):
        return "Rerun the Vendor Doc Republish Sweep from DDR after confirming source document context."
    if workflow_owner == "drive-rhodes-reconciliation":
        return "Rerun Drive Rhodes Reconciliation after the source Drive/Rhodes readback blocker is corrected."
    return "Rerun the owning DDR source workflow after confirming the required source context."


def _workflow_owner(request: dict[str, Any], *, alert_type: str) -> str:
    owner = _safe_category(_str(request.get("workflow_owner")))
    if owner and owner != WORKFLOW_ID:
        return owner
    if alert_type.startswith("daily_dd"):
        return "daily-dd-check"
    if alert_type.startswith("inbox_scan"):
        return "inbox-scan"
    if alert_type.startswith("raycon_followup"):
        return "raycon-followup"
    if alert_type.startswith("vendor_doc"):
        return "vendor-doc-republish-sweep"
    if alert_type in {"drive_rhodes_reconciliation", "rhodes_registration"}:
        return "drive-rhodes-reconciliation"
    return WORKFLOW_ID


def _alert_type(request: dict[str, Any]) -> str:
    return _safe_category(_str(request.get("alert_type")) or _str(request.get("alert")) or "review_request")


def _default_action_requested(alert_type: str) -> str:
    if alert_type == MISSING_DRIVE_FOLDER_ALERT:
        return "Create or link the site's Google Drive folder in Rhodes, then rerun DDR readiness."
    if alert_type == "report_generation_failed":
        return "Review the failed DDR generation step and rerun after correcting the source blocker."
    if alert_type == "source_read_issue":
        return "Review DDR source-read failures and repair missing or unreadable source evidence."
    return "Review the DDR action and provide a source-specific execution path."


def _increment_summary(summary: dict[str, Any], status: str) -> None:
    if status == "completed":
        summary["success_count"] += 1
    elif status == "skipped_already_corrected":
        summary["skipped_count"] += 1
    elif status == "blocked":
        summary["blocked_count"] += 1
    elif status == "error":
        summary["error_count"] += 1
    else:
        summary["needs_review_count"] += 1


def _execution_status(summary: dict[str, Any]) -> str:
    if summary["error_count"] or summary["blocked_count"] or summary["needs_review_count"]:
        return "needs_review"
    if summary["success_count"]:
        return "success"
    return "skipped"


def _run_status(summary: dict[str, Any]) -> str:
    if summary["error_count"]:
        return "failed"
    if summary["blocked_count"]:
        return "blocked"
    if summary["needs_review_count"]:
        return "needs_review"
    if summary["success_count"]:
        return "success"
    return "skipped"


def _run_summary(summary: dict[str, Any]) -> str:
    return (
        "DDR review execution consumed "
        f"{summary['eligible_count']} eligible request(s), attempted "
        f"{summary['attempted_count']}, and returned "
        f"{summary['needs_review_count']} needs-review, "
        f"{summary['blocked_count']} blocked, "
        f"{summary['error_count']} error, and "
        f"{summary['skipped_count']} skipped action(s)."
    )


def _execution_action_status(status: str) -> str:
    if status == "completed":
        return "success"
    if status == "skipped_already_corrected":
        return "skipped"
    if status in {"needs_review", "blocked", "error"}:
        return status
    return "needs_review"


def _severity_for_status(status: str) -> str:
    if status in {"error", "blocked"}:
        return "high"
    if status in {"completed", "skipped_already_corrected"}:
        return "low"
    return "medium"


def _stable_run_id(as_of: str, actions: list[dict[str, Any]]) -> str:
    hash_input = [
        [
            _str(action.get("review_request_id")),
            _str(action.get("source_action_id")),
            _str(action.get("status")),
        ]
        for action in actions
    ]
    digest = hashlib.sha256(repr(hash_input).encode("utf-8")).hexdigest()[:10]
    return _safe_token(f"ddr-review-execution-{as_of}-{len(actions)}-{digest}")


def _sanitize_public_payload(value: object) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_public_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_public_payload(item) for item in value]
    if isinstance(value, str):
        return _safe_text(value)
    return value


def _safe_text(value: str) -> str:
    return " ".join(_sanitize_public_text(value).split()).strip()[:MAX_ACTION_TEXT_LENGTH]


def _safe_token(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9:._-]+", "-", value)).strip("-")[:180]


def _safe_category(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9_-]+", "_", value.lower())).strip("_")[:80]


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _str(value: object) -> str:
    return str(value or "").strip()


def _iso(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.isoformat()


def _sanitize_public_text(value: str) -> str:
    sanitized = _CREDENTIAL_NAME_RE.sub("[credential name removed]", value)
    sanitized = _REQUEST_ID_RE.sub("[request id removed]", sanitized)
    sanitized = _EMAIL_RE.sub("[email removed]", sanitized)
    sanitized = _URL_RE.sub("[private URL removed]", sanitized)
    return _LOCAL_PATH_RE.sub("[local path removed]", sanitized)
