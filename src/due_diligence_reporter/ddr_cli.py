"""Operator CLI for DD pipeline manifests."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .pipeline_manifest import load_run_manifest
from .sir_trends import (
    DEFAULT_SINCE,
    append_review_outcome,
    load_review_outcomes,
    make_review_outcome,
    parse_since,
    summarize_sir_trends,
)


def main(argv: list[str] | None = None) -> None:
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

    trends_parser = subparsers.add_parser("sir-trends", help="Summarize SIR review trends")
    trends_parser.add_argument("--since", default=DEFAULT_SINCE)
    trends_parser.add_argument("--store", default=None)

    args = parser.parse_args(argv)
    if args.command == "status":
        _print_status(load_run_manifest(args.run_id))
    elif args.command == "trace":
        _print_trace(load_run_manifest(args.run_id), failed_only=args.failed_only)
    elif args.command == "rerun":
        _print_rerun(load_run_manifest(args.run_id), args.step)
    elif args.command == "diagnose":
        print(f"uv run python scripts/daily_dd_check.py --site \"{args.site}\"")
    elif args.command == "sir-review":
        _record_sir_review(args)
    elif args.command == "sir-trends":
        _print_sir_trends(args)


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
    _print_counter("Repeat issues", summary["repeat_issues"])


def _print_counter(title: str, values: dict[str, int]) -> None:
    print(f"{title}:")
    if not values:
        print("  (none)")
        return
    for key, count in sorted(values.items(), key=lambda item: (-item[1], item[0]))[:10]:
        print(f"  {key}: {count}")


def _store_path(value: str | None) -> Path | None:
    return Path(value) if value else None


if __name__ == "__main__":
    main()
