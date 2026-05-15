"""Operator CLI for DD pipeline manifests."""

from __future__ import annotations

import argparse
from typing import Any

from .pipeline_manifest import load_run_manifest


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

    args = parser.parse_args(argv)
    if args.command == "status":
        _print_status(load_run_manifest(args.run_id))
    elif args.command == "trace":
        _print_trace(load_run_manifest(args.run_id), failed_only=args.failed_only)
    elif args.command == "rerun":
        _print_rerun(load_run_manifest(args.run_id), args.step)
    elif args.command == "diagnose":
        print(f"uv run python scripts/daily_dd_check.py --site \"{args.site}\"")


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


if __name__ == "__main__":
    main()
