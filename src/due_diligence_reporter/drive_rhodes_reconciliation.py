"""Reconcile existing M1 Drive files into Rhodes document records."""

from __future__ import annotations

import logging
import re
from typing import Any

from .classifier import classify_document
from .google_client import GoogleClient
from .m1_lookup import M1_RECOGNIZED_DOC_TYPES, _resolve_m1_folder
from .rhodes import (
    RhodesClient,
    RhodesError,
    map_ddr_doc_type_to_rhodes,
    register_rhodes_document_for_upload,
)

logger = logging.getLogger("drive_rhodes_reconciliation")

RECONCILIATION_SOURCE = "drive_rhodes_reconciliation"
RECONCILIATION_WORKFLOW_ID = "drive-rhodes-reconciliation"
SOURCE_DOCUMENT_FOLLOW_UP_REASONS = {
    "missing_drive_folder_url",
    "m1_folder_missing",
    "no_recognized_m1_files",
}


def run_drive_rhodes_reconciliation(
    gc: GoogleClient,
    *,
    site_records: list[dict[str, Any]],
    dry_run: bool = False,
    rhodes_client: RhodesClient | None = None,
) -> dict[str, Any]:
    """Register existing recognized M1 Drive files on their Rhodes site records.

    This sweep is intentionally idempotent. Rhodes document registration is
    checked by Drive file ID before any write, so repeated runs become no-ops
    once each file has a Rhodes document record.
    """
    rows: list[dict[str, Any]] = []
    for site_record in site_records:
        site_summary = _site_summary(site_record)
        rows.extend(
            _reconcile_site(
                gc,
                site_summary=site_summary,
                dry_run=dry_run,
                rhodes_client=rhodes_client,
            )
        )

    return {
        "sites_scanned": len(site_records),
        "recognized_files": sum(1 for row in rows if row.get("drive_file_id")),
        "registered": sum(1 for row in rows if row.get("status") == "registered"),
        "registered_verified": sum(
            1
            for row in rows
            if row.get("status") == "registered"
            and row.get("rhodes_readback_status") == "verified"
        ),
        "registered_unverified": sum(
            1
            for row in rows
            if row.get("status") == "registered"
            and row.get("rhodes_readback_status") != "verified"
        ),
        "already_registered": sum(
            1 for row in rows if row.get("status") == "already_registered"
        ),
        "would_register": sum(1 for row in rows if row.get("status") == "would_register"),
        "skipped": sum(1 for row in rows if row.get("status") == "skipped"),
        "errors": sum(1 for row in rows if row.get("status") in {"error", "failed"}),
        "rows": rows,
    }


def _reconcile_site(
    gc: GoogleClient,
    *,
    site_summary: dict[str, str],
    dry_run: bool,
    rhodes_client: RhodesClient | None,
) -> list[dict[str, Any]]:
    site_id = site_summary["id"]
    site_title = site_summary["title"]
    drive_folder_url = site_summary["drive_folder_url"]
    base = {
        "site_id": site_id,
        "site_title": site_title,
    }
    if not site_id:
        return [{**base, "status": "skipped", "reason": "missing_site_id"}]
    if not drive_folder_url:
        return [{**base, "status": "skipped", "reason": "missing_drive_folder_url"}]

    try:
        m1_folder_id, _m1_folder_url = _resolve_m1_folder(
            gc,
            drive_folder_url,
            create_if_missing=False,
            allow_legacy_fallback=False,
        )
    except Exception as exc:  # noqa: BLE001 - one site should not stop the sweep
        logger.warning("Failed to resolve M1 for %s: %s", site_title or site_id, exc)
        return [
            {
                **base,
                "status": "error",
                "reason": "m1_resolution_failed",
                "error": str(exc),
            }
        ]
    if not m1_folder_id:
        return [{**base, "status": "skipped", "reason": "m1_folder_missing"}]

    try:
        files = gc.list_files_in_folder(m1_folder_id)
    except Exception as exc:  # noqa: BLE001 - one site should not stop the sweep
        logger.warning("Failed to list M1 files for %s: %s", site_title or site_id, exc)
        return [
            {
                **base,
                "status": "error",
                "reason": "m1_file_listing_failed",
                "error": str(exc),
            }
        ]

    rows: list[dict[str, Any]] = []
    for file_info in files:
        row = _reconcile_file(
            site_summary=site_summary,
            file_info=file_info,
            dry_run=dry_run,
            rhodes_client=rhodes_client,
        )
        if row is not None:
            rows.append(row)
    if not rows:
        rows.append({**base, "status": "skipped", "reason": "no_recognized_m1_files"})
    return rows


def _reconcile_file(
    *,
    site_summary: dict[str, str],
    file_info: dict[str, Any],
    dry_run: bool,
    rhodes_client: RhodesClient | None,
) -> dict[str, Any] | None:
    file_name = str(file_info.get("name") or "").strip()
    if not file_name:
        return None
    ddr_doc_type, confidence = classify_document(file_name)
    if ddr_doc_type not in M1_RECOGNIZED_DOC_TYPES:
        return None

    site_id = site_summary["id"]
    drive_file_id = str(file_info.get("id") or "").strip()
    drive_url = str(file_info.get("webViewLink") or "").strip()
    mime_type = str(file_info.get("mimeType") or "application/pdf").strip()
    mapping = map_ddr_doc_type_to_rhodes(ddr_doc_type)
    base: dict[str, Any] = {
        "site_id": site_id,
        "site_title": site_summary["title"],
        "drive_file_id": drive_file_id,
        "drive_file_name": file_name,
        "drive_link": drive_url,
        "ddr_doc_type": ddr_doc_type,
        "classification_confidence": confidence,
        "rhodes_doc_type": mapping.doc_type if mapping else "",
        "rhodes_milestone": mapping.milestone if mapping else "",
    }
    if not drive_file_id:
        return {**base, "status": "error", "reason": "missing_drive_file_id"}
    if mapping is None:
        return {**base, "status": "skipped", "reason": "unmapped_doc_type"}

    if dry_run:
        existing = _find_existing_document(
            rhodes_client,
            site_id=site_id,
            drive_file_id=drive_file_id,
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
        return {**base, "status": "would_register", "reason": "dry_run"}

    registration = register_rhodes_document_for_upload(
        site_id=site_id,
        ddr_doc_type=ddr_doc_type,
        title=file_name,
        drive_file_id=drive_file_id,
        drive_url=drive_url,
        mime_type=mime_type,
        original_filename=file_name,
        source=RECONCILIATION_SOURCE,
        client=rhodes_client,
    )
    row = {**base, **registration}
    if row.get("status") == "registered":
        verified = _find_existing_document(
            rhodes_client,
            site_id=site_id,
            drive_file_id=drive_file_id,
            doc_type=mapping.doc_type,
            milestone=mapping.milestone,
        )
        if verified is not None:
            row["rhodes_readback_status"] = "verified"
            row["rhodes_readback_document_id"] = _document_id(verified) or str(
                row.get("rhodes_document_id") or ""
            )
        else:
            row["rhodes_readback_status"] = "missing"
    elif row.get("status") == "already_registered":
        row["rhodes_readback_status"] = "verified"
    return row


def build_drive_rhodes_reconciliation_telemetry(
    result: dict[str, Any],
    *,
    run_id: str,
    started_at: str,
    finished_at: str,
    dry_run: bool = False,
    trigger: str = "",
    workflow_run_url: str = "",
) -> dict[str, Any]:
    """Build a sanitized dashboard telemetry artifact for reconciliation runs."""

    counts = _telemetry_counts(result)
    status = _telemetry_status(counts, dry_run=dry_run)
    public_rows = [_public_row(row) for row in _list_dicts(result.get("rows"))]
    return {
        "schema_version": "workflow_run.v1",
        "source_type": "drive_rhodes_reconciliation",
        "workflow_id": "ddr",
        "workflow_name": "Due Diligence Reporter",
        "subworkflow_id": RECONCILIATION_WORKFLOW_ID,
        "subworkflow_name": "Drive Rhodes Reconciliation",
        "run_id": run_id,
        "source_ref": "Drive Rhodes Reconciliation",
        "trigger": trigger or "manual",
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "summary": _telemetry_summary(counts, dry_run=dry_run),
        "counts": counts,
        "steps": _telemetry_steps(counts, dry_run=dry_run),
        "action_records": _telemetry_action_records(
            counts,
            run_id=run_id,
            as_of=finished_at,
            dry_run=dry_run,
            workflow_run_url=workflow_run_url,
            rows=public_rows,
        ),
        "artifacts": [
            {
                "label": "GitHub Actions run",
                "kind": "github_actions_run",
                "uri": workflow_run_url,
            }
        ] if workflow_run_url else [],
        "rows": public_rows,
    }


def _telemetry_counts(result: dict[str, Any]) -> dict[str, int]:
    return {
        "sites_scanned": _int(result.get("sites_scanned")),
        "recognized_files": _int(result.get("recognized_files")),
        "registered": _int(result.get("registered")),
        "registered_verified": _int(result.get("registered_verified")),
        "registered_unverified": _int(result.get("registered_unverified")),
        "already_registered": _int(result.get("already_registered")),
        "would_register": _int(result.get("would_register")),
        "skipped": _int(result.get("skipped")),
        "errors": _int(result.get("errors")),
    }


def _telemetry_status(counts: dict[str, int], *, dry_run: bool) -> str:
    if counts["errors"] or counts["registered_unverified"]:
        return "needs_review"
    if dry_run and counts["would_register"]:
        return "needs_review"
    return "success"


def _telemetry_summary(counts: dict[str, int], *, dry_run: bool) -> str:
    mode = "dry-run scanned" if dry_run else "scanned"
    return (
        f"Drive Rhodes Reconciliation {mode} {counts['sites_scanned']} site(s), "
        f"recognized {counts['recognized_files']} M1 source file(s), "
        f"registered {counts['registered']} document(s), verified "
        f"{counts['registered_verified']} new registration(s), found "
        f"{counts['already_registered']} already linked document(s), and recorded "
        f"{counts['errors']} error(s)."
    )


def _telemetry_steps(counts: dict[str, int], *, dry_run: bool) -> list[dict[str, Any]]:
    registration_status = "success"
    if counts["errors"]:
        registration_status = "failed"
    elif counts["registered_unverified"] or (dry_run and counts["would_register"]):
        registration_status = "needs_review"
    readback_status = "success" if not counts["registered_unverified"] else "needs_review"
    return [
        {
            "key": "site_scan",
            "label": "Rhodes sites scanned",
            "status": "success",
            "required": True,
            "nominal": True,
            "category": "rhodes",
            "detail": f"sites={counts['sites_scanned']}",
        },
        {
            "key": "m1_file_scan",
            "label": "M1 source files scanned",
            "status": "success",
            "required": True,
            "nominal": True,
            "category": "drive",
            "detail": f"recognized_files={counts['recognized_files']}",
        },
        {
            "key": "rhodes_registration",
            "label": "Rhodes document registration attempted",
            "status": registration_status,
            "required": True,
            "nominal": registration_status == "success",
            "category": "rhodes",
            "detail": (
                f"registered={counts['registered']}; already_registered="
                f"{counts['already_registered']}; errors={counts['errors']}"
            ),
        },
        {
            "key": "readback_verification",
            "label": "Rhodes document readback verified",
            "status": readback_status,
            "required": True,
            "nominal": readback_status == "success",
            "category": "rhodes",
            "detail": (
                f"verified={counts['registered_verified'] + counts['already_registered']}; "
                f"unverified={counts['registered_unverified']}"
            ),
        },
    ]


def _telemetry_action_records(
    counts: dict[str, int],
    *,
    run_id: str,
    as_of: str,
    dry_run: bool,
    workflow_run_url: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if counts["registered_verified"]:
        records.append(
            _action_record(
                run_id=run_id,
                alert_type="document_registration_verified",
                status="completed",
                severity="low",
                action_requested="Register recognized M1 source documents in Rhodes.",
                action_taken=(
                    "Drive Rhodes Reconciliation registered recognized M1 source "
                    "documents and verified Rhodes document readback."
                ),
                evidence_summary=(
                    f"Rhodes readback verified {counts['registered_verified']} new "
                    "document registration(s) by Drive file ID."
                ),
                as_of=as_of,
                retryable=False,
            )
        )
    if counts["already_registered"]:
        records.append(
            _action_record(
                run_id=run_id,
                alert_type="document_already_registered",
                status="skipped_already_corrected",
                severity="low",
                action_requested="Confirm recognized M1 source documents are linked in Rhodes.",
                action_taken=(
                    "Drive Rhodes Reconciliation found recognized M1 source documents "
                    "already registered in Rhodes."
                ),
                evidence_summary=(
                    f"Rhodes readback found {counts['already_registered']} existing "
                    "document link(s) by Drive file ID."
                ),
                as_of=as_of,
                retryable=False,
            )
        )
    if counts["registered_unverified"]:
        records.append(
            _action_record(
                run_id=run_id,
                alert_type="document_registration_readback_missing",
                status="needs_review",
                severity="high",
                owning_workflow="rhodes",
                workflow_owner="rhodes",
                action_requested=(
                    "Verify Rhodes document registration readback for recently "
                    "registered Drive files."
                ),
                action_taken=(
                    "Drive Rhodes Reconciliation registered recognized M1 source "
                    "documents, but follow-up Rhodes readback did not verify every "
                    "document association."
                ),
                evidence_summary=(
                    f"{counts['registered_unverified']} new registration(s) lacked "
                    "verified Rhodes readback by Drive file ID."
                ),
                as_of=as_of,
                retryable=True,
                review_reason="Rhodes document readback did not verify every new registration.",
            )
        )
    if dry_run and counts["would_register"]:
        records.append(
            _action_record(
                run_id=run_id,
                alert_type="document_registration_dry_run",
                status="queued",
                severity="medium",
                action_requested="Run Drive Rhodes Reconciliation without dry-run mode.",
                action_taken=(
                    "Drive Rhodes Reconciliation dry-run found recognized M1 source "
                    "documents that would be registered."
                ),
                evidence_summary=(
                    f"Dry-run found {counts['would_register']} document(s) that still "
                    "need Rhodes registration."
                ),
                as_of=as_of,
                retryable=True,
                review_reason="Dry-run mode did not mutate Rhodes.",
                review_url=workflow_run_url,
            )
        )
    if counts["errors"]:
        records.append(
            _action_record(
                run_id=run_id,
                alert_type="document_registration_failed",
                status="error",
                severity="high",
                action_requested="Repair the Drive/Rhodes reconciliation blocker and rerun.",
                action_taken=(
                    "Drive Rhodes Reconciliation hit sanitized Drive/Rhodes errors while "
                    "trying to reconcile source documents."
                ),
                evidence_summary=(
                    f"Reconciliation recorded {counts['errors']} sanitized error row(s); "
                    "raw dependency details are hidden from dashboard telemetry."
                ),
                as_of=as_of,
                retryable=True,
                review_reason="One or more site or document registration rows failed.",
            )
        )
    records.extend(
        _portfolio_gap_document_action_records(
            rows,
            run_id=run_id,
            as_of=as_of,
            dry_run=dry_run,
        )
    )
    return records


def _portfolio_gap_document_action_records(
    rows: list[dict[str, Any]],
    *,
    run_id: str,
    as_of: str,
    dry_run: bool,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        status = _portfolio_gap_document_action_status(row, dry_run=dry_run)
        if not status:
            continue
        review_required = status in {"queued", "needs_review", "blocked", "error"}
        owner = _portfolio_gap_document_owner(row, status)
        review_reason = _portfolio_gap_document_review_reason(row, status)
        site_id = str(row.get("site_id") or "")
        site_title = str(row.get("site_title") or "") or "Unknown site"
        milestone = _milestone_label(str(row.get("rhodes_milestone") or ""))
        doc_type = str(row.get("rhodes_doc_type") or row.get("ddr_doc_type") or "")
        records.append(
            {
                "schema_version": "action_record.v1",
                "action_id": (
                    "portfolio-gaps:"
                    f"{_action_token(site_id or site_title)}:"
                    f"{_action_token(str(row.get('rhodes_milestone') or 'milestone'))}:"
                    f"{_action_token(doc_type or str(index))}:"
                    "missing-current-milestone-documents"
                ),
                "source_workflow": "portfolio-gaps",
                "owning_workflow": owner,
                "workflow_owner": (
                    "rhodes" if owner == "rhodes" else RECONCILIATION_WORKFLOW_ID
                ),
                "alert_type": "missing_current_milestone_documents",
                "severity": (
                    "medium"
                    if status in {"completed", "skipped_already_corrected"}
                    else "high"
                ),
                "status": status,
                "site_name": site_title,
                "site_id": site_id,
                "current_milestone": milestone,
                "action_requested": (
                    "Associate current-milestone source documents in Rhodes and rerun "
                    "Portfolio Gaps."
                ),
                "action_taken": _portfolio_gap_document_action_taken(row, status),
                "as_of": as_of,
                "evidence_summary": _portfolio_gap_document_evidence(row, status),
                "review_required": review_required,
                "review_reason": review_reason,
                "error_summary": review_reason if status == "error" else "",
                "retryable": status in {"queued", "needs_review", "error"},
                "related_run_id": run_id,
            }
        )
    return records


def _portfolio_gap_document_action_status(
    row: dict[str, Any],
    *,
    dry_run: bool,
) -> str:
    status = str(row.get("status") or "")
    readback = str(row.get("rhodes_readback_status") or "")
    if status == "registered":
        return "completed" if readback == "verified" else "needs_review"
    if status == "already_registered":
        return "skipped_already_corrected"
    if status == "would_register" or (dry_run and status == "registered"):
        return "queued"
    if status in {"error", "failed"}:
        return "error"
    if status == "skipped" and row.get("reason") in SOURCE_DOCUMENT_FOLLOW_UP_REASONS:
        return "needs_review"
    return ""


def _portfolio_gap_document_owner(row: dict[str, Any], status: str) -> str:
    if status == "needs_review" and row.get("status") == "registered":
        return "rhodes"
    return "ddr"


def _portfolio_gap_document_action_taken(row: dict[str, Any], status: str) -> str:
    if status == "completed":
        return (
            "Drive Rhodes Reconciliation registered a current-milestone source "
            "document in Rhodes and verified document readback."
        )
    if status == "skipped_already_corrected":
        return (
            "Drive Rhodes Reconciliation found the current-milestone source "
            "document already associated in Rhodes."
        )
    if status == "queued":
        return (
            "Drive Rhodes Reconciliation dry-run found a current-milestone source "
            "document that would be associated in Rhodes."
        )
    if status == "needs_review":
        if row.get("status") == "skipped":
            return (
                "Drive Rhodes Reconciliation checked this site but did not find a "
                "recognized current-milestone source document to associate in Rhodes."
            )
        return (
            "Drive Rhodes Reconciliation registered a current-milestone source "
            "document, but Rhodes readback did not verify the association."
        )
    return (
        "Drive Rhodes Reconciliation hit a sanitized Drive/Rhodes error while "
        "associating a current-milestone source document."
    )


def _portfolio_gap_document_evidence(row: dict[str, Any], status: str) -> str:
    milestone = _milestone_label(str(row.get("rhodes_milestone") or ""))
    doc_type = str(row.get("rhodes_doc_type") or row.get("ddr_doc_type") or "document")
    readback = str(row.get("rhodes_readback_status") or "not_verified")
    status_text = str(row.get("status") or status)
    reason = str(row.get("reason") or "")
    reason_text = f"; reason={reason}" if reason else ""
    return (
        "Drive/Rhodes reconciliation readback reported "
        f"row_status={status_text}; rhodes_readback={readback}; "
        f"doc_type={doc_type}; milestone={milestone}{reason_text}."
    )


def _portfolio_gap_document_review_reason(row: dict[str, Any], status: str) -> str:
    if status == "queued":
        return "Dry-run mode did not mutate Rhodes; run reconciliation without dry-run."
    if status == "needs_review":
        if row.get("status") == "skipped":
            reason = str(row.get("reason") or "")
            if reason == "missing_drive_folder_url":
                return (
                    "DDR could not find a site Drive folder link to inspect; add or repair "
                    "the Drive folder reference, then rerun reconciliation."
                )
            if reason == "m1_folder_missing":
                return (
                    "DDR could not find the site M1 source folder; file current-milestone "
                    "source documents in M1, then rerun reconciliation."
                )
            return (
                "DDR found no recognized current-milestone source documents in the site "
                "M1 folder; collect or file the source documents, then rerun reconciliation."
            )
        return "Rhodes document readback did not verify the association."
    if status == "error":
        return "Drive/Rhodes reconciliation failed for this site; raw dependency detail is hidden."
    return ""


def _milestone_label(value: str) -> str:
    labels = {
        "acquireProperty": "Acquiring Property",
        "openSchool": "Opening School",
    }
    if value in labels:
        return labels[value]
    cleaned = value.strip()
    if not cleaned:
        return ""
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", cleaned)
    return " ".join(part.capitalize() for part in spaced.replace("_", " ").split())


def _action_record(
    *,
    run_id: str,
    alert_type: str,
    status: str,
    severity: str,
    action_requested: str,
    action_taken: str,
    evidence_summary: str,
    as_of: str,
    owning_workflow: str = "ddr",
    workflow_owner: str = RECONCILIATION_WORKFLOW_ID,
    retryable: bool,
    review_reason: str = "",
    review_url: str = "",
) -> dict[str, Any]:
    review_required = status in {"queued", "needs_review", "blocked", "error"}
    record = {
        "schema_version": "action_record.v1",
        "action_id": f"{RECONCILIATION_WORKFLOW_ID}:{_action_token(run_id)}:{alert_type}",
        "source_workflow": "ddr",
        "owning_workflow": owning_workflow,
        "workflow_owner": workflow_owner,
        "alert_type": alert_type,
        "severity": severity,
        "status": status,
        "site_name": "Portfolio",
        "site_id": "",
        "current_milestone": "",
        "action_requested": action_requested,
        "action_taken": action_taken,
        "as_of": as_of,
        "evidence_summary": evidence_summary,
        "review_required": review_required,
        "review_reason": review_reason,
        "error_summary": review_reason if status == "error" else "",
        "retryable": retryable,
    }
    if review_url:
        record["review_url"] = review_url
    return record


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "site_id": str(row.get("site_id") or ""),
        "site_title": str(row.get("site_title") or ""),
        "ddr_doc_type": str(row.get("ddr_doc_type") or ""),
        "rhodes_doc_type": str(row.get("rhodes_doc_type") or ""),
        "rhodes_milestone": str(row.get("rhodes_milestone") or ""),
        "status": str(row.get("status") or ""),
        "reason": _safe_reason(str(row.get("reason") or "")),
        "rhodes_readback_status": str(row.get("rhodes_readback_status") or ""),
    }


def _safe_reason(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.:-]", "_", value)[:120]


def _action_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return token[:120] or "unknown"


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _list_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _find_existing_document(
    rhodes_client: RhodesClient | None,
    *,
    site_id: str,
    drive_file_id: str,
    doc_type: str,
    milestone: str | None,
) -> dict[str, Any] | None:
    if rhodes_client is None:
        return None
    try:
        return rhodes_client.find_document_by_drive_file_id(
            site_id=site_id,
            drive_file_id=drive_file_id,
            doc_type=doc_type,
            milestone=milestone,
        )
    except RhodesError as exc:
        logger.warning(
            "Dry-run Rhodes document lookup failed for site=%s file=%s: %s",
            site_id,
            drive_file_id,
            exc,
        )
        return None


def _site_summary(site_record: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(site_record.get("id") or site_record.get("site_id") or "").strip(),
        "title": str(site_record.get("title") or site_record.get("name") or "").strip(),
        "drive_folder_url": str(site_record.get("drive_folder_url") or "").strip(),
    }


def _document_id(document: dict[str, Any]) -> str:
    for key in ("documentId", "_id", "id"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
