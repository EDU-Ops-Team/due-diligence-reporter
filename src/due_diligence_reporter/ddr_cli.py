"""Operator CLI for DD pipeline manifests."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import time
from pathlib import Path
from types import ModuleType
from typing import Any

from .adhoc_runner import (
    add_run_site_parser,
    run_site_command,
)
from .adhoc_runner import (
    build_parser as build_run_site_parser,
)
from .m2_executor import execute_ready_m2_states
from .m2_pipeline import (
    M2EventQueueError,
    build_m2_event_queue_from_env,
    build_m2_state_store,
    consume_site_ready_event,
    m2_filter_summary,
    open_m2_site_ids,
    poll_m2_events,
    source_available_event_from_observation,
    watch_m2_source_event_queue,
    watch_m2_sources,
)
from .pipeline_manifest import load_run_manifest
from .portfolio_automation_gaps import build_portfolio_automation_gap_snapshot
from .review_execution import execute_ddr_review_requests
from .rhodes import add_rhodes_site_note, lookup_rhodes_site_owner
from .sir_review_queue import QUEUE_STATUSES, READY_STATUS, load_sir_review_queue
from .sir_trends import (
    DEFAULT_SINCE,
    append_review_outcome,
    format_monthly_learning_summary,
    load_review_outcomes,
    make_review_outcome,
    parse_since,
    summarize_sir_trends,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_repo_script(script_name: str) -> ModuleType:
    script_path = _repo_root() / "scripts" / f"{script_name}.py"
    spec = importlib.util.spec_from_file_location(f"_ddr_repo_script_{script_name}", script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load repo script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ddr", description="Inspect DD pipeline runs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show one run status")
    status_parser.add_argument("--run-id", required=True)

    trace_parser = subparsers.add_parser("trace", help="Show one run trace")
    trace_parser.add_argument("--run-id", required=True)
    trace_parser.add_argument("--failed-only", action="store_true")

    rerun_parser = subparsers.add_parser("rerun", help="Execute a supported step rerun")
    rerun_parser.add_argument("--run-id", required=True)
    rerun_parser.add_argument("--step", required=True)
    rerun_parser.add_argument("--max-attempts", type=int, default=3)
    rerun_parser.add_argument("--backoff-seconds", type=int, default=30)

    diagnose_parser = subparsers.add_parser("diagnose", help="Print diagnostic command")
    diagnose_parser.add_argument("--site", required=True)

    daily_check_parser = subparsers.add_parser(
        "daily-check",
        help="Run the repo-owned daily DD sweep",
    )
    daily_check_parser.add_argument("--site", default="", help="Optional site filter")

    source_sweep_parser = subparsers.add_parser(
        "source-sweep",
        help="Run the repo-owned M2 source document sweep",
    )
    source_sweep_parser.add_argument("--site", default="", help="Optional site filter")
    source_sweep_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect source events without applying republish decisions",
    )
    source_sweep_parser.add_argument(
        "--max-sites",
        type=int,
        default=0,
        help="Maximum matching sites to scan in this source sweep; 0 scans all",
    )

    m2_parser = subparsers.add_parser(
        "m2",
        help="Consume AADP site-ready events and watch open M2 source state",
    )
    m2_subparsers = m2_parser.add_subparsers(dest="m2_command", required=True)
    m2_consume = m2_subparsers.add_parser(
        "consume-event",
        help="Consume one aadp.site_ready_for_ddr.v1 JSON event",
    )
    m2_consume.add_argument("--input", required=True)
    m2_consume.add_argument("--state-store", default=None)
    m2_consume.add_argument("--dry-run", action="store_true")
    m2_consume.add_argument(
        "--skip-rhodes-readback",
        action="store_true",
        help="Validate only the event's own registration/readback proof",
    )

    m2_poll = m2_subparsers.add_parser(
        "poll-events",
        help="Poll Firestore m2DirectDdEvents for pending AADP events",
    )
    m2_poll.add_argument("--apply", action="store_true")
    m2_poll.add_argument("--limit", type=int, default=10)
    m2_poll.add_argument("--state-store", default=None)
    m2_poll.add_argument("--site-id", default="")
    m2_poll.add_argument("--event-id", default="")
    m2_poll.add_argument(
        "--skip-rhodes-readback",
        action="store_true",
        help="Validate only the event's own registration/readback proof",
    )

    m2_watch = m2_subparsers.add_parser(
        "source-watch",
        help="Watch only sites with open DDR M2 state for resume source arrivals",
    )
    m2_watch.add_argument("--apply", action="store_true")
    m2_watch.add_argument("--state-store", default=None)
    m2_watch.add_argument("--site-id", default="")
    m2_watch.add_argument("--event-id", default="")
    m2_watch.add_argument("--source-event-limit", type=int, default=50)

    m2_execute = m2_subparsers.add_parser(
        "execute-ready",
        help="Execute DDR-owned M2 states that are ready after source-watch",
    )
    m2_execute.add_argument("--apply", action="store_true")
    m2_execute.add_argument("--limit", type=int, default=10)
    m2_execute.add_argument("--state-store", default=None)
    m2_execute.add_argument("--site-id", default="")
    m2_execute.add_argument("--event-id", default="")

    notes_smoke_parser = subparsers.add_parser(
        "notes-smoke-test",
        help="Write and verify one headless Rhodes P1 review note",
    )
    notes_smoke_parser.add_argument("--site", default="")
    notes_smoke_parser.add_argument("--address", default="")
    notes_smoke_parser.add_argument("--site-id", default="")
    notes_smoke_parser.add_argument("--site-slug", default="")
    notes_smoke_parser.add_argument("--owner-user-id", default="")
    notes_smoke_parser.add_argument("--owner-email", default="")
    notes_smoke_parser.add_argument("--body", default="")

    add_run_site_parser(subparsers)

    review_parser = subparsers.add_parser("sir-review", help="Record SIR review outcomes")
    review_subparsers = review_parser.add_subparsers(dest="sir_review_command", required=True)
    review_add = review_subparsers.add_parser("add", help="Add one adjudicated SIR issue")
    review_add.add_argument("--site", required=True)
    review_add.add_argument("--section", required=True)
    review_add.add_argument("--gap-category", required=True)
    review_add.add_argument("--severity", required=True)
    review_add.add_argument("--ddr-impact", required=True)
    review_add.add_argument("--evidence-checked", required=True)
    review_add.add_argument("--learning-action", required=True)
    review_add.add_argument("--status", required=True)
    review_add.add_argument("--ai-sir", default="")
    review_add.add_argument("--cds-sir", default="")
    review_add.add_argument("--notes", default="")
    review_add.add_argument("--created-at", default=None)
    review_add.add_argument("--store", default=None)
    review_queue = review_subparsers.add_parser("queue", help="List SIR pairs ready for review")
    review_queue.add_argument(
        "--status",
        choices=[*QUEUE_STATUSES, "all"],
        default=READY_STATUS,
    )
    review_queue.add_argument("--limit", type=int, default=10)
    review_queue.add_argument("--include-reviewed", action="store_true")
    review_queue.add_argument("--manifest-dir", default=None)
    review_queue.add_argument("--store", default=None)

    trends_parser = subparsers.add_parser("sir-trends", help="Summarize SIR review trends")
    trends_parser.add_argument("--since", default=DEFAULT_SINCE)
    trends_parser.add_argument("--store", default=None)

    monthly_parser = subparsers.add_parser(
        "sir-monthly-summary",
        help="Print a monthly SIR learning summary",
    )
    monthly_parser.add_argument("--since", default=DEFAULT_SINCE)
    monthly_parser.add_argument("--store", default=None)

    gaps_parser = subparsers.add_parser(
        "portfolio-gaps",
        help="Print a Rhodes-backed portfolio automation gap snapshot",
    )
    gaps_parser.add_argument("--max-sites", type=int, default=100)
    gaps_parser.add_argument(
        "--include-clean",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Include sites with no detected gaps in the output",
    )
    gaps_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the raw snapshot JSON instead of the operator summary",
    )

    review_execution_parser = subparsers.add_parser(
        "review-execution",
        help="Consume dashboard review execution requests and emit DDR action readback",
    )
    review_execution_parser.add_argument("--review-requests", required=True)
    review_execution_parser.add_argument("--output", required=True)
    review_execution_parser.add_argument("--max-actions", type=int, default=0)
    review_execution_parser.add_argument("--dry-run", action="store_true")

    digest_parser = subparsers.add_parser(
        "dd-write-digest",
        help="Send the daily digest of successful DD field writes/proposals",
    )
    digest_parser.add_argument("--hours", type=int, default=24)
    digest_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the digest without sending email or Chat messages",
    )
    digest_parser.add_argument(
        "--skip-empty",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip sending when there were no writes in the period",
    )

    args = parser.parse_args(argv)
    if args.command == "status":
        _print_status(load_run_manifest(args.run_id))
    elif args.command == "trace":
        _print_trace(load_run_manifest(args.run_id), failed_only=args.failed_only)
    elif args.command == "rerun":
        return _execute_rerun(
            load_run_manifest(args.run_id),
            args.step,
            max_attempts=args.max_attempts,
            backoff_seconds=args.backoff_seconds,
        )
    elif args.command == "diagnose":
        print(f"uv run ddr run-site diagnose --site \"{args.site}\"")
    elif args.command == "daily-check":
        return _run_daily_check(args)
    elif args.command == "source-sweep":
        return _run_source_sweep(args)
    elif args.command == "m2":
        return _run_m2(args)
    elif args.command == "notes-smoke-test":
        return _run_notes_smoke_test(args)
    elif args.command == "run-site":
        exit_code, payload = run_site_command(args)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return exit_code
    elif args.command == "sir-review":
        _handle_sir_review(args)
    elif args.command == "sir-trends":
        _print_sir_trends(args)
    elif args.command == "sir-monthly-summary":
        _print_sir_monthly_summary(args)
    elif args.command == "portfolio-gaps":
        _print_portfolio_gaps(args)
    elif args.command == "review-execution":
        _run_review_execution(args)
    elif args.command == "dd-write-digest":
        return _run_dd_write_digest(args)
    return 0


def _print_status(manifest: dict[str, Any]) -> None:
    quality = manifest.get("quality") or {}
    sir_review = manifest.get("sir_learning_review") or {}
    print(f"Run: {manifest.get('run_id')}")
    print(f"Site: {manifest.get('site_title')}")
    print(f"Status: {manifest.get('final_status')}")
    print(f"Failed step: {manifest.get('failed_step') or '(none)'}")
    print(f"Quality: {quality.get('score')} ({quality.get('band')})")
    print(f"SIR review: {sir_review.get('status') or '(none)'}")
    print(f"Next action: {manifest.get('next_operator_action') or '(none)'}")
    print(f"Manifest: {manifest.get('manifest_path') or '(unknown)'}")


def _print_trace(manifest: dict[str, Any], *, failed_only: bool) -> None:
    for step in manifest.get("steps", []):
        status = step.get("status")
        if failed_only and status not in {"failed", "blocked"}:
            continue
        line = f"{step.get('step')}: {status} ({step.get('duration_ms')} ms)"
        error = step.get("error") or {}
        if error:
            line += f" - {error.get('code')}: {error.get('message')}"
        print(line)


def _print_rerun(manifest: dict[str, Any], step_name: str) -> None:
    for step in manifest.get("steps", []):
        if step.get("step") == step_name:
            print(step.get("rerun_command") or f"ddr rerun --run-id {manifest.get('run_id')} --step {step_name}")
            return
    print(f"Unknown step: {step_name}")


_RETRYABLE_GENERATION_MARKERS = (
    "529",
    "overloaded_error",
    "rate_limit",
    "rate limit",
    "429",
    "timeout",
    "temporarily",
    "service unavailable",
    "server overloaded",
)


def _execute_rerun(
    manifest: dict[str, Any],
    step_name: str,
    *,
    max_attempts: int,
    backoff_seconds: int,
) -> int:
    if max_attempts < 1:
        print("--max-attempts must be at least 1")
        return 2
    if _manifest_step(manifest, step_name) is None:
        print(f"Unknown step: {step_name}")
        return 2
    if step_name != "report.generate":
        print(
            "Executable rerun is currently supported only for report.generate. "
            f"Use the original workflow to retry {step_name}."
        )
        return 2

    launch_context = _launch_context_for_report_generate(manifest)
    if launch_context is None:
        print(
            "Run manifest is missing enough launch context to execute "
            "report.generate recovery. Rerun the original ddr run-site command."
        )
        return 2

    try:
        run_site_argv = _run_site_argv_from_launch_context(launch_context)
    except ValueError as exc:
        print(str(exc))
        return 2

    last_exit = 1
    parser = build_run_site_parser()
    for attempt in range(1, max_attempts + 1):
        print(f"Rerun attempt {attempt}/{max_attempts}: ddr run-site {' '.join(run_site_argv)}")
        parsed = parser.parse_args(run_site_argv)
        exit_code, payload = run_site_command(parsed)
        print(json.dumps(payload, indent=2, sort_keys=True))
        last_exit = exit_code
        if exit_code == 0:
            return 0
        if attempt == max_attempts or not _is_retryable_generation_payload(payload):
            return exit_code
        if backoff_seconds > 0:
            print(f"Retryable generation failure; waiting {backoff_seconds}s before retry.")
            time.sleep(backoff_seconds)
    return last_exit


def _manifest_step(manifest: dict[str, Any], step_name: str) -> dict[str, Any] | None:
    for step in manifest.get("steps", []):
        if isinstance(step, dict) and step.get("step") == step_name:
            return step
    return None


def _launch_context_for_report_generate(manifest: dict[str, Any]) -> dict[str, Any] | None:
    launch_context = manifest.get("launch_context")
    if isinstance(launch_context, dict):
        return launch_context

    site = _text(manifest.get("site_title"))
    if not site:
        return None
    return {
        "schema_version": "ddr_run_site_launch.inferred.v1",
        "mode": "first-publish",
        "site": site,
        "site_id": _text(manifest.get("site_id")),
        "address": "",
        "drive_folder_url": "",
        "notify": False,
        "sor_write_mode": "api",
        "mcp_write_completed": False,
        "document_first_on_sor_blocker": False,
    }


def _run_site_argv_from_launch_context(context: dict[str, Any]) -> list[str]:
    mode = _text(context.get("mode"))
    if mode not in {"first-publish", "force-regenerate", "source-republish"}:
        raise ValueError(f"Unsupported report.generate rerun mode: {mode or '(missing)'}")
    site = _text(context.get("site") or context.get("site_title"))
    if not site:
        raise ValueError("Run manifest launch context is missing site")

    argv = [mode, "--site", site]
    _extend_optional_arg(argv, "--address", context.get("address") or context.get("site_address"))
    _extend_optional_arg(argv, "--site-id", context.get("site_id"))
    _extend_optional_arg(argv, "--slug", context.get("slug"))
    _extend_optional_arg(argv, "--drive-folder-url", context.get("drive_folder_url"))

    sor_write_mode = _text(context.get("sor_write_mode")) or "api"
    if sor_write_mode not in {"api", "mcp-assisted"}:
        raise ValueError(f"Unsupported SOR write mode in launch context: {sor_write_mode}")
    if sor_write_mode != "api":
        argv.extend(["--sor-write-mode", sor_write_mode])
    if bool(context.get("mcp_write_completed")):
        argv.append("--mcp-write-completed")
    if bool(context.get("document_first_on_sor_blocker")):
        argv.append("--document-first-on-sor-blocker")
    if bool(context.get("notify")):
        argv.append("--notify")

    if mode == "source-republish":
        source_event = context.get("source_event")
        source = source_event if isinstance(source_event, dict) else context
        source_type = _text(source.get("source_type"))
        fingerprint = _text(source.get("fingerprint"))
        if not source_type or not fingerprint:
            raise ValueError(
                "source-republish rerun launch context is missing source_type or fingerprint"
            )
        argv.extend(["--source-type", source_type, "--fingerprint", fingerprint])
        for flag, key in (
            ("--doc-type", "doc_type"),
            ("--drive-file-id", "drive_file_id"),
            ("--drive-modified-time", "drive_modified_time"),
            ("--file-name", "file_name"),
            ("--drive-url", "drive_url"),
        ):
            _extend_optional_arg(argv, flag, source.get(key))
    return argv


def _extend_optional_arg(argv: list[str], flag: str, value: Any) -> None:
    text = _text(value)
    if text:
        argv.extend([flag, text])


def _is_retryable_generation_payload(payload: dict[str, Any]) -> bool:
    if _text(payload.get("status")) != "generation_failed":
        return False
    failed_step = _text(payload.get("failed_step"))
    if failed_step and failed_step != "report.generate":
        return False
    error_text = json.dumps(payload.get("error") or payload, sort_keys=True).lower()
    return any(marker in error_text for marker in _RETRYABLE_GENERATION_MARKERS)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _handle_sir_review(args: argparse.Namespace) -> None:
    if args.sir_review_command == "add":
        _record_sir_review(args)
    elif args.sir_review_command == "queue":
        _print_sir_review_queue(args)


def _record_sir_review(args: argparse.Namespace) -> None:
    outcome = make_review_outcome(
        site=args.site,
        ai_sir=args.ai_sir,
        cds_sir=args.cds_sir,
        section=args.section,
        gap_category=args.gap_category,
        severity=args.severity,
        ddr_impact=args.ddr_impact,
        evidence_checked=args.evidence_checked,
        learning_action=args.learning_action,
        status=args.status,
        notes=args.notes,
        created_at=args.created_at,
    )
    path = append_review_outcome(outcome, path=_store_path(args.store))
    print(f"Recorded SIR review issue: {outcome.review_id}")
    print(f"Store: {path}")


def _print_sir_review_queue(args: argparse.Namespace) -> None:
    statuses = QUEUE_STATUSES if args.status == "all" else (args.status,)
    queue = load_sir_review_queue(
        manifest_dir=_path_arg(args.manifest_dir),
        outcomes=load_review_outcomes(path=_store_path(args.store)),
        statuses=statuses,
        include_reviewed=args.include_reviewed,
        limit=args.limit,
    )
    reviewed_note = "including reviewed" if args.include_reviewed else "unreviewed only"
    print(f"SIR Review Queue ({args.status}, {reviewed_note})")
    print(f"Items: {len(queue)}")
    if not queue:
        return
    for index, item in enumerate(queue, 1):
        print(f"{index}. {item.site_title}")
        print(f"   Status: {item.status}")
        if item.reason:
            print(f"   Reason: {item.reason}")
        print(f"   AI SIR: {_doc_label(item.ai_sir_name, item.ai_sir_file_id, item.ai_sir_uri)}")
        print(f"   CDS SIR: {_doc_label(item.cds_sir_name, item.cds_sir_file_id, item.cds_sir_uri)}")
        print(f"   Reviewed: {'yes' if item.reviewed else 'no'}")
        print(f"   Run: {item.run_id}")
        print(f"   Manifest: {item.manifest_path}")


def _print_sir_trends(args: argparse.Namespace) -> None:
    since = parse_since(args.since)
    summary = summarize_sir_trends(
        load_review_outcomes(path=_store_path(args.store)),
        since=since,
    )
    print(f"SIR Trends since {args.since}")
    print(f"Issues: {summary['total_issues']}")
    print(f"Sites reviewed: {summary['sites_reviewed']}")
    print(f"SIR pairs reviewed: {summary['sir_pairs_reviewed']}")
    print(f"AI missed items/SIR: {summary['ai_missed_items_per_sir']}")
    print(f"AI unsupported claims/SIR: {summary['ai_unsupported_claims_per_sir']}")
    print(f"CDS missed items/SIR: {summary['cds_missed_items_per_sir']}")
    print(f"DDR-impacting findings: {summary['ddr_impacting_findings']}")
    print(f"Blocking/material findings: {summary['blocking_or_material_findings']}")
    _print_counter("Top categories", summary["by_category"])
    _print_counter("Top sections", summary["by_section"])
    _print_counter("Learning actions", summary["by_learning_action"])
    _print_counter("Accepted learning actions", summary["accepted_learning_actions"])
    _print_counter("Repeat issues", summary["repeat_issues"])


def _print_sir_monthly_summary(args: argparse.Namespace) -> None:
    since = parse_since(args.since)
    summary = summarize_sir_trends(
        load_review_outcomes(path=_store_path(args.store)),
        since=since,
    )
    print(format_monthly_learning_summary(summary, since_label=args.since))


def _print_portfolio_gaps(args: argparse.Namespace) -> None:
    snapshot = build_portfolio_automation_gap_snapshot(
        max_sites=args.max_sites,
        include_clean=args.include_clean,
    )
    if args.json:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
        return

    totals = snapshot.get("totals") or {}
    sites = snapshot.get("sites") or []
    print("Portfolio Automation Gaps")
    print(f"System of record: {snapshot.get('system_of_record')}")
    print(f"Generated at: {snapshot.get('generated_at')}")
    print(f"Sites returned: {totals.get('sites', 0)}")
    print(f"Sites with gaps: {totals.get('sites_with_gaps', 0)}")
    print(f"Missing P1 DRI: {totals.get('missing_p1_dri', 0)}")
    print(f"Missing Drive folder: {totals.get('missing_drive_folder', 0)}")
    print(f"Open automation failures: {totals.get('open_automation_failures', 0)}")
    print(f"Pending review tasks: {totals.get('pending_review_tasks', 0)}")
    if not sites:
        print("No automation gaps found.")
        return

    for index, site in enumerate(sites, 1):
        print(f"{index}. {site.get('site_name')}")
        print(f"   Site ID: {site.get('site_id') or '(missing)'}")
        print(f"   Owner routing: {site.get('owner_routing_status') or '(unknown)'}")
        _print_site_gap_line(site)
        _print_latest_ddr_line(site)
        _print_open_failures(site)
        _print_pending_tasks(site)
        _print_site_errors(site)


def _run_review_execution(args: argparse.Namespace) -> None:
    request_path = Path(args.review_requests)
    output_path = Path(args.output)
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    result = execute_ddr_review_requests(
        payload,
        max_actions=args.max_actions,
        dry_run=bool(args.dry_run),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    execution = result["execution"]
    print(
        "DDR review execution "
        f"status={execution['status']} "
        f"attempted={execution['attempted_count']} "
        f"success={execution['success_count']} "
        f"needs_review={execution['needs_review_count']} "
        f"blocked={execution['blocked_count']} "
        f"errors={execution['error_count']}"
    )


def _run_daily_check(args: argparse.Namespace) -> int:
    daily_dd_check = _load_repo_script("daily_dd_check")
    daily_dd_check.main(site_filter=str(args.site or "").strip() or None)
    return 0


def _run_source_sweep(args: argparse.Namespace) -> int:
    vendor_doc_republish_sweep = _load_repo_script("vendor_doc_republish_sweep")
    vendor_doc_republish_sweep.main(
        dry_run=bool(args.dry_run),
        site=str(args.site or "").strip(),
        max_sites=max(0, int(args.max_sites or 0)),
    )
    return 0


def _run_m2(args: argparse.Namespace) -> int:
    if args.m2_command == "consume-event":
        return _run_m2_consume_event(args)
    if args.m2_command == "poll-events":
        return _run_m2_poll_events(args)
    if args.m2_command == "source-watch":
        return _run_m2_source_watch(args)
    if args.m2_command == "execute-ready":
        return _run_m2_execute_ready(args)
    print(f"Unknown m2 command: {args.m2_command}")
    return 2


def _run_m2_consume_event(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    state_store = _m2_state_store(args)
    result = consume_site_ready_event(
        payload,
        state_store=state_store,
        apply=not bool(args.dry_run),
        verify_rhodes_readback=not bool(args.skip_rhodes_readback),
        document_lister=None if args.skip_rhodes_readback else _m2_rhodes_document_lister(),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _run_m2_poll_events(args: argparse.Namespace) -> int:
    state_store = _m2_state_store(args)
    result = poll_m2_events(
        event_queue=build_m2_event_queue_from_env(),
        state_store=state_store,
        apply=bool(args.apply),
        limit=int(args.limit),
        verify_rhodes_readback=not bool(args.skip_rhodes_readback),
        document_lister=None if args.skip_rhodes_readback else _m2_rhodes_document_lister(),
        site_id=str(args.site_id or ""),
        event_id=str(args.event_id or ""),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result.get("failed") else 0


def _run_m2_source_watch(args: argparse.Namespace) -> int:
    from .config import get_settings
    from .google_client import GoogleClient
    from .rhodes import list_rhodes_site_records
    from .vendor_doc_sweep import collect_core_source_events

    state_store = _m2_state_store(args)
    state = state_store.load()
    site_ids = open_m2_site_ids(
        state,
        site_id=str(args.site_id or ""),
        event_id=str(args.event_id or ""),
    )
    if not site_ids:
        result = {
            "status": "success",
            "apply": bool(args.apply),
            "filters": m2_filter_summary(
                site_id=str(args.site_id or ""),
                event_id=str(args.event_id or ""),
            ),
            "open_states_checked": 0,
            "resumed": 0,
            "rows": [],
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    settings = get_settings()
    gc = GoogleClient.from_oauth_config(
        client_config_path=str(settings.get_client_config_path()),
        token_file_path=str(settings.get_token_file_path()),
        oauth_port=settings.oauth_port,
        scopes=settings.google_scopes,
    )
    events_by_site: dict[str, list[dict[str, Any]]] = {}
    for site_record in list_rhodes_site_records(site_ids=site_ids):
        site_id = _site_record_id(site_record)
        if not site_id:
            continue
        observations = collect_core_source_events(
            gc,
            site_record,
            read_only=not bool(args.apply),
        )
        events_by_site[site_id] = [
            source_available_event_from_observation(
                site=site_record,
                observation=observation,
                producer={
                    "workflow": "m2-source-watch-drive-scan",
                    "artifact_type": "drive_scan_observation",
                },
            )
            for observation in observations
        ]

    try:
        result = watch_m2_source_event_queue(
            event_queue=build_m2_event_queue_from_env(),
            state_store=state_store,
            fallback_source_events_by_site=events_by_site,
            apply=bool(args.apply),
            limit=int(args.source_event_limit),
            site_id=str(args.site_id or ""),
            event_id=str(args.event_id or ""),
        )
    except M2EventQueueError as exc:
        result = watch_m2_sources(
            state_store=state_store,
            source_events_by_site=events_by_site,
            apply=bool(args.apply),
            site_id=str(args.site_id or ""),
            event_id=str(args.event_id or ""),
        )
        result["source_event_queue"] = {
            "status": "unavailable",
            "reason": str(exc),
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _run_m2_execute_ready(args: argparse.Namespace) -> int:
    result = execute_ready_m2_states(
        state_store=_m2_state_store(args),
        apply=bool(args.apply),
        limit=int(args.limit),
        site_id=str(args.site_id or ""),
        event_id=str(args.event_id or ""),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "success" else 1


def _m2_rhodes_document_lister() -> Any:
    from .rhodes import RhodesClient

    rhodes = RhodesClient()
    return lambda site_id, doc_type: rhodes.list_documents(
        site_id=site_id,
        doc_type=doc_type,
    )


def _m2_state_store(args: argparse.Namespace) -> Any:
    path = _path_arg(args.state_store)
    if path is not None:
        return build_m2_state_store(path)
    return build_m2_state_store()


def _site_record_id(site_record: dict[str, Any]) -> str:
    return _first_text_arg(site_record.get("id"), site_record.get("site_id"))


def _run_notes_smoke_test(args: argparse.Namespace) -> int:
    owner_context: dict[str, Any] = {}
    if not all(
        [
            str(args.site_id).strip() or str(args.site_slug).strip(),
            str(args.owner_user_id).strip(),
        ]
    ):
        owner_context = lookup_rhodes_site_owner(
            site_name=str(args.site or ""),
            site_address=str(args.address or ""),
            site_id=str(args.site_id or ""),
            slug=str(args.site_slug or ""),
        )

    site_id = _first_text_arg(args.site_id, owner_context.get("site_id"))
    site_slug = _first_text_arg(args.site_slug, owner_context.get("site_slug"))
    owner_user_id = _first_text_arg(
        args.owner_user_id,
        owner_context.get("p1_assignee_user_id"),
        _nested_text(owner_context.get("p1_dri"), "userId", "user_id", "_id", "id"),
    )
    owner_email = _first_text_arg(args.owner_email, owner_context.get("p1_assignee_email"))
    site_name = _first_text_arg(args.site, owner_context.get("site_name"), site_id, site_slug)
    body = str(args.body or "").strip() or _notes_smoke_body(
        site_name=site_name,
        site_id=site_id,
        site_slug=site_slug,
        owner_email=owner_email,
        owner_user_id=owner_user_id,
    )

    if not (site_id or site_slug):
        payload = {
            "status": "error",
            "reason": "missing_site_identity",
            "owner_lookup": _notes_smoke_owner_lookup_summary(owner_context),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    if not owner_user_id:
        payload = {
            "status": "error",
            "reason": "missing_owner_user_id",
            "site_id": site_id,
            "site_slug": site_slug,
            "owner_lookup": _notes_smoke_owner_lookup_summary(owner_context),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2

    note_result = add_rhodes_site_note(
        site_id=site_id,
        site_slug=site_slug,
        body=body,
        owner_user_id=owner_user_id,
        owner_email=owner_email,
        automation_source="due-diligence-reporter:notes-smoke-test",
    )
    success = (
        note_result.get("status") == "created"
        and note_result.get("owner_notification") == "mentioned"
        and bool(str(note_result.get("rhodes_note_id") or "").strip())
    )
    payload = {
        "status": "success" if success else "failed",
        "site": {
            "site_id": site_id,
            "site_slug": site_slug,
            "site_name": site_name,
        },
        "owner": {
            "user_id": owner_user_id,
            "email": owner_email,
        },
        "note": note_result,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if success else 1


def _notes_smoke_body(
    *,
    site_name: str,
    site_id: str,
    site_slug: str,
    owner_email: str,
    owner_user_id: str,
) -> str:
    del site_id, site_slug, owner_email, owner_user_id
    return "\n".join(
        [
            "Rhodes note smoke test",
            "Action needed: Confirm this test note reached the P1 owner review queue.",
            f"Site: {site_name or 'unknown'}",
            "Status: Headless note write test completed.",
            "Next steps:",
            "- Confirm the P1 owner mention appears on this note.",
        ]
    )


def _notes_smoke_owner_lookup_summary(owner_context: dict[str, Any]) -> dict[str, Any]:
    if not owner_context:
        return {}
    return {
        "status": owner_context.get("status"),
        "message": owner_context.get("message"),
        "site_id": owner_context.get("site_id"),
        "site_slug": owner_context.get("site_slug"),
        "p1_assignee_email": owner_context.get("p1_assignee_email"),
        "p1_assignee_user_id": owner_context.get("p1_assignee_user_id"),
    }


def _first_text_arg(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _nested_text(value: Any, *keys: str) -> str:
    if not isinstance(value, dict):
        return ""
    for key in keys:
        nested = value.get(key)
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return ""


def _print_site_gap_line(site: dict[str, Any]) -> None:
    reasons = site.get("gap_reasons") or []
    print(f"   Gaps: {', '.join(str(reason) for reason in reasons) if reasons else '(none)'}")
    drive_folder = site.get("drive_folder") or {}
    drive_status = drive_folder.get("status") or "(unknown)"
    drive_message = drive_folder.get("message") or drive_folder.get("url") or ""
    print(f"   Drive folder: {drive_status}{f' - {drive_message}' if drive_message else ''}")


def _print_latest_ddr_line(site: dict[str, Any]) -> None:
    latest_ddr = site.get("latest_ddr_status") or {}
    print(f"   Latest DDR: {latest_ddr.get('status') or '(unknown)'}")
    fingerprint = site.get("latest_source_event_fingerprint")
    if fingerprint:
        print(f"   Latest source event: {fingerprint}")


def _print_open_failures(site: dict[str, Any]) -> None:
    failures = site.get("open_automation_failures") or []
    if not failures:
        return
    print("   Open automation failures:")
    for failure in failures:
        print(
            "     - "
            f"{failure.get('kind') or '(unknown)'} "
            f"{failure.get('mutation_status') or '(unknown)'} "
            f"{failure.get('source_id') or ''}".rstrip()
        )


def _print_pending_tasks(site: dict[str, Any]) -> None:
    tasks = site.get("pending_review_tasks") or []
    if not tasks:
        return
    print("   Pending review tasks:")
    for task in tasks:
        print(
            "     - "
            f"{task.get('title') or '(untitled)'} "
            f"({task.get('status') or 'unknown'}, {task.get('task_id') or 'no task ID'})"
        )


def _print_site_errors(site: dict[str, Any]) -> None:
    errors = site.get("errors") or []
    if not errors:
        return
    print("   Read errors:")
    for error in errors:
        print(f"     - {error}")


def _print_counter(title: str, values: dict[str, int]) -> None:
    print(f"{title}:")
    if not values:
        print("  (none)")
        return
    for key, count in sorted(values.items(), key=lambda item: (-item[1], item[0]))[:10]:
        print(f"  {key}: {count}")


def _store_path(value: str | None) -> Path | None:
    return Path(value) if value else None


def _path_arg(value: str | None) -> Path | None:
    return Path(value) if value else None


def _doc_label(name: str, file_id: str, uri: str) -> str:
    label = name or file_id or uri
    if not label:
        return "(missing)"
    if file_id and name:
        return f"{name} ({file_id})"
    return label


if __name__ == "__main__":
    raise SystemExit(main())


def _run_dd_write_digest(args: argparse.Namespace) -> int:
    """Compose and deliver the daily DD write digest to the operating owner."""

    from datetime import UTC, datetime, timedelta

    from .config import get_settings
    from .dd_write_digest import (
        build_dd_write_digest,
        collect_dd_write_events,
        write_log_project_id,
    )
    from .utils import post_google_chat_message, send_email

    hours = max(int(args.hours), 1)
    if not write_log_project_id():
        print(
            "DD write log Firestore project is not configured "
            "(DD_WRITE_LOG/M2_DD_STATE/DD_REPUBLISH_STATE project vars all "
            "empty); the digest would silently read an empty local fallback."
        )
        if not args.dry_run:
            return 1
    since_iso = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    events = collect_dd_write_events(since_iso=since_iso)

    resolver = None
    try:
        from .rhodes import RhodesClient

        rhodes = RhodesClient()

        def _resolve(site_id: str) -> str:
            site = rhodes.get_site(site_id=site_id) or {}
            return str(site.get("name") or "")

        resolver = _resolve
    except Exception:  # noqa: BLE001 - digest renders with site IDs if lookup is down
        resolver = None

    digest = build_dd_write_digest(
        events,
        resolve_site_name=resolver,
        period_label=f"last {hours} hours",
    )
    print(digest["text"])
    print(
        f"\nDigest events={digest['event_count']} sites={digest['site_count']}"
    )

    if args.dry_run:
        print("Dry run: no email or Chat message sent.")
        return 0
    if digest["event_count"] == 0 and args.skip_empty:
        print("No writes in period: skipping delivery (--skip-empty).")
        return 0

    settings = get_settings()
    recipients = [
        addr.strip()
        for addr in os.environ.get(
            "DD_WRITE_DIGEST_RECIPIENTS", "greg.foote@trilogy.com"
        ).split(",")
        if addr.strip()
    ]
    delivered = 0
    delivery_failures: list[str] = []
    if settings.email_sender and settings.email_app_password and recipients:
        try:
            send_email(
                settings.email_sender,
                settings.email_app_password,
                recipients,
                digest["subject"],
                digest["html"],
            )
            delivered += 1
            print(f"Digest emailed to: {', '.join(recipients)}")
        except Exception as exc:  # noqa: BLE001 - report and continue to Chat
            delivery_failures.append(f"email: {exc}")
    else:
        delivery_failures.append("email: sender credentials not configured")
    webhook_url = str(getattr(settings, "google_chat_webhook_url", "") or "").strip()
    if webhook_url:
        try:
            post_google_chat_message(
                webhook_url, f"{digest['subject']}\n{digest['text']}"
            )
            delivered += 1
            print("Digest posted to Google Chat.")
        except Exception as exc:  # noqa: BLE001
            delivery_failures.append(f"chat: {exc}")
    else:
        delivery_failures.append("chat: webhook not configured")
    for failure in delivery_failures:
        print(f"Delivery issue: {failure}")
    if delivered == 0:
        print("Digest delivery failed: the owner was not informed.")
        return 1
    return 0
