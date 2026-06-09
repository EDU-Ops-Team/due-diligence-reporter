#!/usr/bin/env python3
"""Build dashboard WorkflowRun telemetry for Portfolio Automation Gaps."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "src"))

from due_diligence_reporter.portfolio_gap_telemetry import (  # noqa: E402
    build_portfolio_gap_workflow_telemetry,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="Portfolio gap JSON snapshot path")
    parser.add_argument("--output", required=True, help="Telemetry JSON output path")
    parser.add_argument("--run-id", default="", help="Workflow run id")
    parser.add_argument("--started-at", default="", help="Workflow start timestamp")
    parser.add_argument("--finished-at", default="", help="Workflow finish timestamp")
    parser.add_argument("--trigger", default="manual", help="Workflow trigger")
    parser.add_argument("--workflow-run-url", default="", help="GitHub Actions run URL")
    parser.add_argument("--notification-result", default="", help="Notification result JSON path")
    parser.add_argument("--source-status", default="", help="Source job status")
    args = parser.parse_args(argv)

    finished_at = args.finished_at or datetime.now(UTC).isoformat()
    snapshot = _read_json(Path(args.snapshot))
    if not snapshot:
        snapshot = {
            "status": "failed",
            "generated_at": finished_at,
            "totals": {},
            "sites": [],
        }

    telemetry = build_portfolio_gap_workflow_telemetry(
        snapshot,
        run_id=args.run_id,
        started_at=args.started_at or str(snapshot.get("generated_at") or finished_at),
        finished_at=finished_at,
        trigger=args.trigger,
        workflow_run_url=args.workflow_run_url,
        notification_result=_read_json(Path(args.notification_result))
        if args.notification_result
        else None,
        source_status=args.source_status,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(telemetry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "Portfolio Gaps telemetry "
        f"status={telemetry.get('status')} "
        f"run_id={telemetry.get('run_id')} "
        f"actions={len(telemetry.get('action_records') or [])}"
    )
    return 0


def _read_json(path: Path) -> dict[str, Any]:
    if not str(path).strip() or not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
