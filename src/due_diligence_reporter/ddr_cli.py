"""Operator CLI for DD pipeline manifests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .adhoc_runner import add_run_site_parser, run_site_command
from .pipeline_manifest import load_run_manifest
from .portfolio_automation_gaps import build_portfolio_automation_gap_snapshot
from .review_execution import execute_ddr_review_requests
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ddr", description="Inspect DD pipeline runs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show one run status")
    status_parser.add_argument("--run-id", required=True)

    trace_parser = subparsers.add_parser("trace", help="Show one run trace")
    trace_parser.add_argument("--run-id", required=True)
    trace_parser.add_argument("--failed-only", action="store_true")

    rerun_parser = subparsers.add_parser("rerun", help="Print the step rerun command")
    rerun_parser.add_argument("--run-id", required=True)
    rerun_parser.add_argument("--step", required=True)

    diagnose_parser = subparsers.add_parser("diagnose", help="Print diagnostic command")
    diagnose_parser.add_argument("--site", required=True)

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

    args = parser.parse_args(argv)
    if args.command == "status":
        _print_status(load_run_manifest(args.run_id))
    elif args.command == "trace":
        _print_trace(load_run_manifest(args.run_id), failed_only=args.failed_only)
    elif args.command == "rerun":
        _print_rerun(load_run_manifest(args.run_id), args.step)
    elif args.command == "diagnose":
        print(f"uv run ddr run-site diagnose --site \"{args.site}\"")
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
