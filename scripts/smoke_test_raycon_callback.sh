#!/usr/bin/env bash
# Simulates the dispatch RayCon fires when a job completes.
#
# Usage: scripts/smoke_test_raycon_callback.sh <site_id> [run_id] [status]
# Requires: gh CLI authenticated with workflow scope on this repo.
#
# Replays the exact shape RayCon sends so we can verify the receiver
# end-to-end without coordinating with the RayCon team. See Rec. 2 in
# event-driven-ddr-recommendations.md and the brief at
# /home/user/workspace/raycon-callback-ask.md for the contract.
set -euo pipefail

SITE_ID="${1:?site_id required (use a site Drive folder id)}"
RUN_ID="${2:-smoke-$(date +%s)}"
STATUS="${3:-succeeded}"

gh api -X POST \
  repos/GFooteGK1/due-diligence-reporter/actions/workflows/raycon-followup.yml/dispatches \
  --input - <<JSON
{"ref":"main","inputs":{"site_id":"${SITE_ID}","run_id":"${RUN_ID}","status":"${STATUS}"}}
JSON

echo "Dispatched. Tail logs with:"
echo "  gh run watch --repo GFooteGK1/due-diligence-reporter \$(gh run list --workflow raycon-followup.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
