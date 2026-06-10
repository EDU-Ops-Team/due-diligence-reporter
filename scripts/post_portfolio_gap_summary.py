"""Post a Google Chat summary for portfolio automation gaps."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from due_diligence_reporter.portfolio_gap_notifications import (
    post_portfolio_gap_chat_summary,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="Path to portfolio gap JSON snapshot")
    parser.add_argument("--webhook-url", default="", help="Google Chat webhook URL(s)")
    parser.add_argument("--run-url", default="", help="GitHub Actions run URL")
    parser.add_argument("--max-sites", type=int, default=5)
    parser.add_argument("--result-output", default="", help="Optional notification result JSON path")
    args = parser.parse_args()

    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    result = post_portfolio_gap_chat_summary(
        snapshot,
        webhook_urls=args.webhook_url,
        run_url=args.run_url,
        max_sites=args.max_sites,
    )
    if args.result_output:
        output_path = Path(args.result_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
