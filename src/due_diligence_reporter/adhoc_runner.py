"""Operator-safe ad-hoc runner for one DDR site."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

SUPPRESSED_NOTIFICATION_ENV = (
    "DDR_GOOGLE_CHAT_WEBHOOK_URL",
    "DD_REPORT_EMAIL_RECIPIENTS",
    "EMAIL_SENDER",
    "EMAIL_APP_PASSWORD",
    "GLOBAL_EMAIL_CC",
)


def add_run_site_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "run-site",
        help="Run or diagnose one DDR site without BrainTrust",
    )
    parser.set_defaults(command="run-site")
    _add_mode_parsers(parser)


def build_parser(prog: str = "ddr run-site") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Run or diagnose one DDR site")
    parser.set_defaults(command="run-site")
    _add_mode_parsers(parser)
    return parser


def _add_mode_parsers(parser: argparse.ArgumentParser) -> None:
    mode_subparsers = parser.add_subparsers(dest="mode", required=True)

    diagnose = mode_subparsers.add_parser("diagnose", help="Read-only readiness diagnosis")
    _add_common_args(diagnose)

    first_publish = mode_subparsers.add_parser(
        "first-publish",
        help="Run the first-publish pipeline path",
    )
    _add_common_args(first_publish)

    force = mode_subparsers.add_parser(
        "force-regenerate",
        help="Run the pipeline with force_regenerate=True",
    )
    _add_common_args(force)

    sweep = mode_subparsers.add_parser(
        "source-sweep",
        help="Run the existing source document republish sweep",
    )
    _add_common_args(sweep)
    sweep_mode = sweep.add_mutually_exclusive_group()
    sweep_mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Detect source events without republishing. Default.",
    )
    sweep_mode.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Apply source-triggered republish decisions.",
    )

    source = mode_subparsers.add_parser(
        "source-republish",
        help="Run force_regenerate=True with explicit source-event metadata",
    )
    _add_common_args(source)
    source.add_argument("--source-type", required=True)
    source.add_argument("--fingerprint", required=True)
    source.add_argument("--doc-type", default="")
    source.add_argument("--drive-file-id", default="")
    source.add_argument("--drive-modified-time", default="")
    source.add_argument("--file-name", default="")
    source.add_argument("--drive-url", default="")

    resume = mode_subparsers.add_parser(
        "resume-mcp-write",
        help="Resume a manifest-bound LocationOS MCP-assisted SOR write",
    )
    resume.add_argument("--run-id", required=True, help="Source run manifest ID")
    resume.add_argument(
        "--notify",
        action="store_true",
        help="Enable normal Chat/email notifications. Default suppresses them.",
    )


def run_site_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    _apply_notification_policy(notify=bool(args.notify))
    if (
        bool(getattr(args, "mcp_write_completed", False))
        and getattr(args, "sor_write_mode", "api") != "mcp-assisted"
    ):
        return 2, {
            "status": "error",
            "message": "--mcp-write-completed requires --sor-write-mode mcp-assisted",
        }
    if bool(getattr(args, "mcp_write_completed", False)) and args.mode == "source-sweep":
        return 2, {
            "status": "error",
            "message": "--mcp-write-completed is not supported for source-sweep",
        }
    if args.mode == "diagnose":
        return _run_diagnose(args)
    if args.mode == "first-publish":
        return _run_pipeline_mode(args, force_regenerate=False)
    if args.mode == "force-regenerate":
        return _run_pipeline_mode(args, force_regenerate=True)
    if args.mode == "source-sweep":
        return _run_source_sweep(args)
    if args.mode == "source-republish":
        return _run_pipeline_mode(args, force_regenerate=True)
    if args.mode == "resume-mcp-write":
        return _run_mcp_resume(args)
    return 2, {"status": "error", "message": f"Unsupported mode: {args.mode}"}


def main(argv: list[str] | None = None) -> int:
    if argv and argv[0] == "run-site":
        argv = argv[1:]
    parser = build_parser()
    parsed = parser.parse_args(argv)
    code, payload = run_site_command(parsed)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return code


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--site", required=True, help="Site name or substring")
    parser.add_argument("--address", default="", help="Optional full site address")
    parser.add_argument("--site-id", default="", help="Optional Rhodes site ID")
    parser.add_argument("--slug", default="", help="Optional Rhodes site slug")
    parser.add_argument(
        "--drive-folder-url",
        default="",
        help="Optional site Drive folder URL; otherwise resolved from Rhodes",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Enable normal Chat/email notifications. Default suppresses them.",
    )
    parser.add_argument(
        "--sor-write-mode",
        choices=("api", "mcp-assisted"),
        default="api",
        help=(
            "How to satisfy the LocationOS due-diligence SOR write. "
            "Default api uses the configured bearer token; mcp-assisted emits "
            "an OAuth-backed LocationOS MCP handoff when elicitation is required."
        ),
    )
    parser.add_argument(
        "--mcp-write-completed",
        action="store_true",
        help=(
            "For --sor-write-mode mcp-assisted, verify the completed OAuth MCP "
            "write by readback and continue instead of calling updateDueDiligence."
        ),
    )


def _apply_notification_policy(*, notify: bool) -> None:
    if notify:
        return
    for key in SUPPRESSED_NOTIFICATION_ENV:
        os.environ[key] = ""


def _run_diagnose(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    from due_diligence_reporter.server import diagnose_site_readiness

    site_context = _site_context(args) if args.site_id or args.slug else None
    site_name = args.site
    site_address = args.address
    drive_folder_url = args.drive_folder_url
    if site_context is not None:
        site_name = site_context["site_title"] or site_name
        site_address = site_context["site_address"] or site_address
        drive_folder_url = drive_folder_url or site_context["drive_folder_url"]
    payload = asyncio.run(
        diagnose_site_readiness(
            site_name=site_name,
            drive_folder_url=drive_folder_url,
            site_address=site_address,
        )
    )
    payload["mode"] = "diagnose"
    payload["notifications"] = "suppressed"
    if site_context is not None:
        payload["resolved_site_context"] = site_context
    status = str(payload.get("status") or "")
    return (0 if status == "success" else 1), payload


def _run_pipeline_mode(
    args: argparse.Namespace,
    *,
    force_regenerate: bool,
) -> tuple[int, dict[str, Any]]:
    from due_diligence_reporter.config import get_settings
    from due_diligence_reporter.open_questions import CORE_SOURCE_TYPES
    from due_diligence_reporter.report_pipeline import (
        list_shared_folders_once,
        post_pipeline_result,
        process_site_pipeline,
    )
    from due_diligence_reporter.utils import build_site_match_terms

    if args.mode == "source-republish" and args.source_type not in CORE_SOURCE_TYPES:
        supported = ", ".join(sorted(CORE_SOURCE_TYPES))
        return 2, {
            "status": "error",
            "message": (
                f"Unsupported --source-type {args.source_type!r}. "
                f"Supported values: {supported}"
            ),
        }

    settings = get_settings()
    site = _site_context(args)
    gc = _make_google_client()
    shared_cache = list_shared_folders_once(gc)
    source_event = None
    if args.mode == "source-republish":
        source_event = {
            "source_type": args.source_type,
            "fingerprint": args.fingerprint,
            "doc_type": args.doc_type or args.source_type,
            "drive_file_id": args.drive_file_id,
            "drive_modified_time": args.drive_modified_time,
            "file_name": args.file_name,
            "drive_url": args.drive_url,
            "observed_at": datetime.now(UTC).isoformat(),
        }
    result = process_site_pipeline(
        gc,
        site["site_title"],
        site["drive_folder_url"],
        build_site_match_terms(site["site_title"], site["site_address"] or None),
        shared_cache,
        _load_prompt(),
        settings,
        p1_email=site["p1_email"] or None,
        site_address=site["site_address"] or None,
        p1_name=site["p1_name"] or None,
        site_created_at=site["site_created_at"] or None,
        site_id=site["site_id"] or None,
        source_event=source_event,
        force_regenerate=force_regenerate,
        due_diligence_write_mode=_pipeline_sor_write_mode(args),
        locationos_mcp_write_completed=bool(args.mcp_write_completed),
    )
    if args.notify and settings.google_chat_webhook_url:
        post_pipeline_result(
            settings.google_chat_webhook_url,
            result,
            site["drive_folder_url"],
        )
    payload = _result_payload(
        result,
        mode=args.mode,
        notify=args.notify,
        sor_write_mode=args.sor_write_mode,
        mcp_write_completed=bool(args.mcp_write_completed),
    )
    _attach_mcp_resume_command(payload, args=args, site=site)
    payload["resolved_site_context"] = site
    return _exit_code_for_status(str(payload.get("status") or "")), payload


def _run_source_sweep(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    from due_diligence_reporter.config import get_settings
    from due_diligence_reporter.dd_republish_state_store import (
        build_dd_republish_state_store,
    )
    from due_diligence_reporter.report_pipeline import (
        list_shared_folders_once,
        process_site_pipeline,
    )
    from due_diligence_reporter.rhodes import list_rhodes_site_records
    from due_diligence_reporter.vendor_doc_sweep import run_vendor_doc_republish_sweep

    settings = get_settings()
    gc = _make_google_client()
    shared_cache = list_shared_folders_once(gc)
    store = build_dd_republish_state_store()
    republish_state = store.load()
    site_records = list_rhodes_site_records()
    needles = [
        value.strip().lower()
        for value in (
            args.site,
            args.address,
            args.site_id,
            args.slug,
            args.drive_folder_url,
        )
        if value.strip()
    ]
    if needles:
        site_records = [
            record
            for record in site_records
            if _record_matches_any(
                record,
                needles,
            )
        ]
    result = run_vendor_doc_republish_sweep(
        gc,
        settings=settings,
        system_prompt=_load_prompt(),
        shared_cache=shared_cache,
        republish_state=republish_state,
        site_records=site_records,
        dry_run=args.dry_run,
        pipeline_runner=_source_sweep_pipeline_runner(args, process_site_pipeline),
    )
    if not args.dry_run:
        store.save(republish_state)
    payload = {
        "mode": "source-sweep",
        "notifications": "enabled" if args.notify else "suppressed",
        "dry_run": args.dry_run,
        **result,
    }
    return (1 if int(result.get("errors") or 0) else 0), payload


def _run_mcp_resume(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    from due_diligence_reporter.config import get_settings
    from due_diligence_reporter.report_pipeline import (
        post_pipeline_result,
        resume_locationos_mcp_write_from_manifest,
    )

    settings = get_settings()
    result = resume_locationos_mcp_write_from_manifest(
        args.run_id,
        settings=settings,
    )
    resume_payload = getattr(result, "locationos_mcp_resume", None)
    drive_folder_url = ""
    if isinstance(resume_payload, dict):
        drive_folder_url = str(resume_payload.get("drive_folder_url") or "")
    if args.notify and settings.google_chat_webhook_url:
        post_pipeline_result(
            settings.google_chat_webhook_url,
            result,
            drive_folder_url,
        )
    payload = _result_payload(
        result,
        mode=args.mode,
        notify=args.notify,
        sor_write_mode="mcp-assisted",
        mcp_write_completed=True,
    )
    payload["source_run_id"] = args.run_id
    return _exit_code_for_status(str(payload.get("status") or "")), payload


def _record_matches_any(record: dict[str, Any], needles: list[str]) -> bool:
    haystack = " ".join(
        str(record.get(key) or "").lower()
        for key in (
            "id",
            "site_id",
            "title",
            "name",
            "slug",
            "address",
            "site_address",
            "drive_folder_url",
        )
    )
    return any(needle in haystack for needle in needles)


def _load_prompt() -> str:
    from due_diligence_reporter.pipeline_manifest import PROJECT_ROOT

    return (PROJECT_ROOT / "docs" / "prompts" / "prompt_v4.md").read_text(
        encoding="utf-8"
    )


def _make_google_client() -> Any:
    from due_diligence_reporter.config import get_settings
    from due_diligence_reporter.google_client import GoogleClient

    settings = get_settings()
    return GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )


def _site_context(args: argparse.Namespace) -> dict[str, str]:
    context = _lookup_context(args)
    site_title = _context_value(context, "site_name") or args.site
    site_address = _context_value(context, "site_address") or args.address
    drive_folder_url = args.drive_folder_url or _context_value(context, "drive_folder_url")
    site_id = args.site_id or _context_value(context, "site_id")
    return {
        "site_title": site_title,
        "site_address": site_address,
        "drive_folder_url": drive_folder_url,
        "site_id": site_id,
        "p1_name": _context_value(context, "p1_assignee_name"),
        "p1_email": _context_value(context, "p1_assignee_email"),
        "site_created_at": _report_field(context, "site_created_at"),
        "rhodes_status": _context_value(context, "status"),
        "rhodes_message": _context_value(context, "message"),
    }


def _lookup_context(args: argparse.Namespace) -> dict[str, Any]:
    from due_diligence_reporter.rhodes import lookup_rhodes_site_owner

    return lookup_rhodes_site_owner(
        site_name=args.site,
        site_address=args.address,
        site_id=args.site_id,
        slug=args.slug,
    )


def _context_value(context: dict[str, Any], key: str) -> str:
    value = context.get(key)
    return value.strip() if isinstance(value, str) else ""


def _report_field(context: dict[str, Any], key: str) -> str:
    fields = context.get("report_data_fields")
    if not isinstance(fields, dict):
        return ""
    value = fields.get(key)
    return value.strip() if isinstance(value, str) else ""


def _pipeline_sor_write_mode(args: argparse.Namespace) -> str:
    return "mcp_assisted" if args.sor_write_mode == "mcp-assisted" else "api"


def _source_sweep_pipeline_runner(args: argparse.Namespace, pipeline_runner: Any) -> Any:
    def _runner(*runner_args: Any, **runner_kwargs: Any) -> Any:
        runner_kwargs["due_diligence_write_mode"] = _pipeline_sor_write_mode(args)
        runner_kwargs["locationos_mcp_write_completed"] = False
        return pipeline_runner(*runner_args, **runner_kwargs)

    return _runner


def _result_payload(
    result: Any,
    *,
    mode: str,
    notify: bool,
    sor_write_mode: str,
    mcp_write_completed: bool,
) -> dict[str, Any]:
    from due_diligence_reporter.pipeline_contracts import next_operator_action

    payload = {
        "mode": mode,
        "notifications": "enabled" if notify else "suppressed",
        "sor_write_mode": sor_write_mode,
        "mcp_write_completed": mcp_write_completed,
        "site_title": getattr(result, "site_title", ""),
        "status": getattr(result, "status", ""),
        "run_id": getattr(result, "run_id", None),
        "manifest_path": getattr(result, "manifest_path", None),
        "doc_id": getattr(result, "doc_id", None),
        "doc_url": getattr(result, "doc_url", None),
        "failed_step": getattr(result, "failed_step", None),
        "quality_score": getattr(result, "quality_score", None),
        "quality_band": getattr(result, "quality_band", None),
        "missing_docs": getattr(result, "missing_docs", []),
        "unresolved_tokens": getattr(result, "unresolved_tokens", []),
        "pending_count": getattr(result, "pending_count", 0),
        "error": getattr(result, "error", None),
        "rhodes_due_diligence_update": getattr(
            result,
            "rhodes_due_diligence_update",
            None,
        ),
        "rhodes_report_event": getattr(result, "rhodes_report_event", None),
        "republish_summary": getattr(result, "republish_summary", None),
        "open_question_count": len(getattr(result, "open_questions", []) or []),
        "closed_open_question_count": len(
            getattr(result, "closed_open_questions", []) or []
        ),
        "next_operator_action": next_operator_action(
            getattr(result, "steps", []) or []
        ),
    }
    mcp_request = _locationos_mcp_write_request(result)
    if mcp_request is not None:
        payload["locationos_mcp_write_request"] = mcp_request
    resume_payload = getattr(result, "locationos_mcp_resume", None)
    if isinstance(resume_payload, dict):
        payload["locationos_mcp_resume"] = {
            "schema_version": resume_payload.get("schema_version"),
            "source_run_id": resume_payload.get("source_run_id"),
            "site_id": resume_payload.get("site_id"),
            "site_title": resume_payload.get("site_title"),
        }
    return payload


def _locationos_mcp_write_request(result: Any) -> dict[str, Any] | None:
    update_status = getattr(result, "rhodes_due_diligence_update", None)
    if not isinstance(update_status, dict):
        return None
    request = update_status.get("locationos_mcp_write_request")
    return request if isinstance(request, dict) else None


def _attach_mcp_resume_command(
    payload: dict[str, Any],
    *,
    args: argparse.Namespace,
    site: dict[str, str],
) -> None:
    request = payload.get("locationos_mcp_write_request")
    if not isinstance(request, dict):
        return
    command = _mcp_resume_command(request=request, fallback_run_id=payload.get("run_id"))
    request["resume_command"] = command
    payload["mcp_resume_command"] = command


def _mcp_resume_command(
    *,
    request: dict[str, Any],
    fallback_run_id: Any,
) -> list[str]:
    run_id = str(request.get("run_id") or fallback_run_id or "").strip()
    command = [
        "uv",
        "run",
        "ddr",
        "run-site",
        "resume-mcp-write",
    ]
    if run_id:
        command.extend(["--run-id", run_id])
    return command


def _exit_code_for_status(status: str) -> int:
    if status in {"error", "generation_failed", "report_incomplete"}:
        return 1
    return 0
