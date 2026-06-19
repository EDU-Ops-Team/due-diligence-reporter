"""Run AADP Portfolio Gaps remediation against a snapshot."""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

AADP_SOURCE = "alpha-analysis-downstream-processing"
PORTFOLIO_GAPS_SOURCE = "portfolio-gaps"
RHODES_SOURCE = "rhodes"
ACTION_LABELS = {
    "missing_p1_dri": "Missing P1 DRI",
    "missing_drive_folder": "Missing Drive folder",
}
RHODES_ACTION_LABELS = {
    "snapshot_read_errors": "Snapshot read errors",
}
MAX_ERROR_LENGTH = 240


def run_aadp_remediation(
    snapshot: dict[str, Any],
    *,
    aadp_repo: Path,
    drive_parent_folder_id: str,
    dry_run: bool = False,
    max_actions: int = 0,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    remediator = _load_aadp_remediator(aadp_repo)
    if remediator is None:
        return mark_aadp_remediation_unavailable(
            snapshot,
            as_of=_iso((now or _utc_now)()),
            status="blocked",
            summary=(
                "AADP remediation runner was not available; configure the AADP checkout "
                "before this alert can be corrected automatically."
            ),
        )

    try:
        return remediator(
            snapshot,
            drive_parent_folder_id=drive_parent_folder_id,
            dry_run=dry_run,
            max_actions=max_actions,
            now=now,
        )
    except Exception as exc:  # noqa: BLE001 - encode trigger failure in dashboard data
        return mark_aadp_remediation_unavailable(
            snapshot,
            as_of=_iso((now or _utc_now)()),
            status="error",
            summary=(
                "AADP remediation trigger failed before it could update Rhodes: "
                f"{_clean_error(exc)}"
            ),
        )


def mark_aadp_remediation_unavailable(
    snapshot: dict[str, Any],
    *,
    as_of: str,
    status: str,
    summary: str,
) -> dict[str, Any]:
    enriched = copy.deepcopy(snapshot)
    remediation: dict[str, Any] = {
        "source": AADP_SOURCE,
        "status": "skipped",
        "as_of": as_of,
        "dry_run": False,
        "attempted_count": 0,
        "success_count": 0,
        "skipped_count": 0,
        "needs_review_count": 0,
        "error_count": 0,
    }
    for site in _list_dicts(enriched.get("sites")):
        for gap_type in ACTION_LABELS:
            if gap_type not in _gap_reasons(site):
                continue
            site_id = _site_id(site)
            if not site_id:
                _replace_action(site, _missing_site_identity_action(site, gap_type, as_of=as_of))
                remediation["needs_review_count"] = int(remediation["needs_review_count"]) + 1
                continue
            remediation["attempted_count"] = int(remediation["attempted_count"]) + 1
            review_required = status in {"blocked", "needs_review", "error"}
            action_id = _action_id(site_id, _site_name(site), gap_type)
            action = {
                "schema_version": "action_record.v1",
                "source": AADP_SOURCE,
                "source_workflow": PORTFOLIO_GAPS_SOURCE,
                "owning_workflow": "aadp",
                "workflow_owner": "aadp",
                "gap_type": gap_type,
                "alert": ACTION_LABELS[gap_type],
                "status": status,
                "as_of": as_of,
                "site_id": site_id,
                "site_name": _site_name(site),
                "current_milestone": _current_milestone_label(site),
                "action_requested": ACTION_LABELS[gap_type],
                "action_taken": "" if review_required else summary,
                "remediation_summary": summary,
                "evidence_summary": (
                    "Portfolio Gaps found this alert and attempted to route it to AADP; "
                    "no AADP remediation or source-system readback has been verified yet."
                ),
                "review_required": review_required,
                "review_reason": summary if review_required else "",
                "error_summary": summary if status == "error" else "",
                "retryable": status in {"blocked", "error"},
                "action_id": action_id,
                "idempotency_key": action_id,
                "autonomy_mode": "automatic_candidate",
                "sor_system": _sor_system_for_gap(gap_type),
                "sor_write_status": "not_started",
                "sor_readback_status": "not_verified",
                "sor_readback_summary": "No AADP source-system readback has been verified yet.",
                "operating_note_status": "not_started",
                "p1_dri_route_status": (
                    "missing_owner" if gap_type == "missing_p1_dri" else "not_started"
                ),
                "failure_route": "",
                "next_step": (
                    "Restore the AADP remediation runner, then rerun Portfolio Gaps "
                    "or the AADP remediation trigger."
                ),
            }
            _replace_action(site, action)
            if status == "error":
                remediation["error_count"] = int(remediation["error_count"]) + 1
            else:
                remediation["needs_review_count"] = int(remediation["needs_review_count"]) + 1

    remediation["status"] = (
        "needs_review"
        if int(remediation["attempted_count"]) or int(remediation["needs_review_count"])
        else "skipped"
    )
    enriched["remediation"] = remediation
    return enriched


def _missing_site_identity_action(
    site: dict[str, Any],
    gap_type: str,
    *,
    as_of: str,
) -> dict[str, Any]:
    site_name = _site_name(site)
    action_id = _action_id("", site_name, gap_type)
    label = ACTION_LABELS[gap_type]
    summary = (
        f"Portfolio Gaps found {label} but did not route it to AADP because "
        "the Rhodes snapshot did not include a verified site ID."
    )
    return {
        "schema_version": "action_record.v1",
        "source": PORTFOLIO_GAPS_SOURCE,
        "source_workflow": PORTFOLIO_GAPS_SOURCE,
        "owning_workflow": PORTFOLIO_GAPS_SOURCE,
        "workflow_owner": PORTFOLIO_GAPS_SOURCE,
        "gap_type": gap_type,
        "alert": label,
        "status": "blocked",
        "as_of": as_of,
        "site_id": "",
        "site_name": site_name,
        "current_milestone": _current_milestone_label(site),
        "action_requested": "Resolve verified Rhodes site identity before routing remediation.",
        "action_taken": summary,
        "remediation_summary": summary,
        "evidence_summary": (
            "Portfolio Gaps requires a verified Rhodes site ID before an autonomous "
            "source workflow can mutate Rhodes or Drive for this site."
        ),
        "review_required": True,
        "review_reason": "Resolve the site identity in Rhodes/source data, then rerun Portfolio Gaps.",
        "error_summary": "",
        "retryable": True,
        "action_id": action_id,
        "idempotency_key": action_id,
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


def mark_rhodes_snapshot_read_actions(
    snapshot: dict[str, Any],
    *,
    as_of: str,
) -> dict[str, Any]:
    """Emit Rhodes-owned action telemetry for snapshot read errors."""

    enriched = copy.deepcopy(snapshot)
    remediation: dict[str, Any] = {
        "source": RHODES_SOURCE,
        "status": "skipped",
        "as_of": as_of,
        "attempted_count": 0,
        "needs_review_count": 0,
    }
    for site in _list_dicts(enriched.get("sites")):
        for gap_type, label in RHODES_ACTION_LABELS.items():
            if gap_type not in _gap_reasons(site):
                continue
            remediation["attempted_count"] = int(remediation["attempted_count"]) + 1
            error_count = len(_list_dicts(site.get("errors")))
            if error_count <= 0 and isinstance(site.get("errors"), list):
                error_count = len(site["errors"])
            action = {
                "schema_version": "action_record.v1",
                "source": RHODES_SOURCE,
                "source_workflow": PORTFOLIO_GAPS_SOURCE,
                "owning_workflow": "rhodes",
                "workflow_owner": "rhodes",
                "gap_type": gap_type,
                "alert": label,
                "status": "needs_review",
                "as_of": as_of,
                "site_id": _site_id(site),
                "site_name": _site_name(site),
                "current_milestone": _current_milestone_label(site),
                "action_requested": (
                    "Restore Portfolio Gaps Rhodes snapshot reads and rerun the snapshot."
                ),
                "action_taken": (
                    "Portfolio Gaps could not read one or more Rhodes snapshot sections "
                    "for this site; no verified readback has been captured yet."
                ),
                "remediation_summary": (
                    "Portfolio Gaps could not read one or more Rhodes snapshot sections "
                    "for this site; no verified readback has been captured yet."
                ),
                "evidence_summary": (
                    "Portfolio Gaps found a sanitized Rhodes snapshot read failure; "
                    "no successful Rhodes snapshot readback has been verified yet."
                ),
                "review_required": True,
                "review_reason": (
                    "Rhodes snapshot reads failed for this site; confirm the Rhodes API "
                    "read path and rerun Portfolio Gaps."
                ),
                "error_summary": (
                    f"Portfolio Gaps recorded {error_count} Rhodes snapshot read error(s)."
                ),
                "retryable": True,
            }
            _replace_action(site, action)
            remediation["needs_review_count"] = int(remediation["needs_review_count"]) + 1

    if int(remediation["attempted_count"]):
        remediation["status"] = "needs_review"
    enriched["snapshot_read_remediation"] = remediation
    return enriched


def _load_aadp_remediator(aadp_repo: Path) -> Callable[..., dict[str, Any]] | None:
    src = aadp_repo / "src"
    if not src.is_dir():
        return None
    sys.path.insert(0, str(src))
    try:
        module = importlib.import_module(
            "alpha_analysis_downstream_processing_mcp.portfolio_gap_remediation"
        )
    except Exception:  # noqa: BLE001 - caller records unavailable trigger status
        return None
    remediator = getattr(module, "remediate_portfolio_gap_snapshot", None)
    return cast(Callable[..., dict[str, Any]], remediator) if callable(remediator) else None


def _replace_action(site: dict[str, Any], action: dict[str, Any]) -> None:
    existing = _list_dicts(site.get("remediation_actions"))
    key = _action_key(action)
    source = str(action.get("source") or "")
    site["remediation_actions"] = [
        item
        for item in existing
        if not (
            _action_key(item) == key
            and str(item.get("source") or "") == source
        )
    ]
    site["remediation_actions"].append(action)


def _action_key(action: dict[str, Any]) -> str:
    return _normalize_key(str(action.get("gap_type") or action.get("alert") or ""))


def _gap_reasons(site: dict[str, Any]) -> set[str]:
    value = site.get("gap_reasons")
    return {str(item) for item in value} if isinstance(value, list) else set()


def _site_id(site: dict[str, Any]) -> str:
    return _first_str(site, "site_id", "siteId", "_id", "id")


def _site_name(site: dict[str, Any]) -> str:
    return _first_str(site, "site_name", "name", "title", "marketingName") or "Unknown site"


def _sor_system_for_gap(gap_type: str) -> str:
    if gap_type == "missing_drive_folder":
        return "drive,rhodes"
    return "rhodes"


def _action_id(site_id: str, site_name: str, gap_type: str) -> str:
    site_key = _action_id_part(site_id or site_name or "unknown-site")
    gap_key = _action_id_part(gap_type or "unknown-gap")
    return f"portfolio-gaps:{site_key}:{gap_key}"


def _action_id_part(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    return "-".join(part for part in cleaned.split("-") if part)[:80] or "unknown"


def _current_milestone_label(site: dict[str, Any]) -> str:
    current = _dict(site.get("current_milestone"))
    required_documents = _dict(site.get("required_documents"))
    milestone = current or _dict(required_documents.get("milestone"))
    return _first_str(milestone, "label", "key")


def _first_str(value: dict[str, Any], *keys: str) -> str:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _clean_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    text = re.sub(r"[\w.+-]+@[\w.-]+", "[email]", text)
    text = re.sub(r"https?://\S+", "[url]", text)
    return text[:MAX_ERROR_LENGTH] or exc.__class__.__name__


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _list_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat() if value.tzinfo else value.replace(tzinfo=UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Portfolio Gaps snapshot must be a JSON object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run-aadp-portfolio-gap-remediation",
        description="Run AADP remediation and enrich Portfolio Gaps action telemetry.",
    )
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", default="")
    parser.add_argument("--aadp-repo", default="aadp-remediation")
    parser.add_argument("--drive-parent-folder-id", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-actions", type=int, default=0)
    args = parser.parse_args(argv)

    snapshot_path = Path(args.snapshot)
    output_path = Path(args.output) if args.output else snapshot_path
    enriched = run_aadp_remediation(
        _read_json(snapshot_path),
        aadp_repo=Path(args.aadp_repo),
        drive_parent_folder_id=str(args.drive_parent_folder_id or ""),
        dry_run=bool(args.dry_run),
        max_actions=max(0, int(args.max_actions)),
    )
    enriched = mark_rhodes_snapshot_read_actions(enriched, as_of=_iso(_utc_now()))
    _write_json(output_path, enriched)
    remediation = _dict(enriched.get("remediation"))
    snapshot_read_remediation = _dict(enriched.get("snapshot_read_remediation"))
    print(
        "AADP remediation trigger "
        f"status={remediation.get('status', 'unknown')} "
        f"attempted={remediation.get('attempted_count', 0)} "
        f"success={remediation.get('success_count', 0)} "
        f"needs_review={remediation.get('needs_review_count', 0)} "
        f"errors={remediation.get('error_count', 0)}"
    )
    print(
        "Rhodes snapshot read action emission "
        f"status={snapshot_read_remediation.get('status', 'unknown')} "
        f"needs_review={snapshot_read_remediation.get('needs_review_count', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
