# RayCon Rebuild Package For DDR

Audience: RayCon development team
Prepared for: DDR / EDU Ops integration
Status: Developer handoff spec
Primary goal: rebuild RayCon so it works as a deterministic scenario engine for Due Diligence Reporter (DDR), not just as a chat UI.

## Sendable Summary

RayCon should be rebuilt around one production contract: DDR sends a site/job event, RayCon computes defensible school-opening scenarios, writes a single `raycon_scenario.json` file back into the site's `M1 - Acquire Property` Drive folder, and triggers DDR follow-up. DDR then publishes or republishes the DD Report from that structured JSON.

The most important design shift is that DDR must not depend on RayCon narrative text for numbers. Chat and document export can remain useful product surfaces, but DDR needs a deterministic async API, stable JSON schema, strict provenance, and predictable failure handling.

## Product Requirements

1. RayCon must support an async job workflow for DDR.
2. RayCon must accept Block Plan arrivals and general folder-update pings.
3. RayCon must be idempotent so DDR can safely retry from scheduled workflows.
4. RayCon must write exactly one canonical output file named `raycon_scenario.json` to the supplied M1 Drive folder.
5. RayCon must include enough provenance for DDR operators to know which input files and calculation version produced the result.
6. RayCon must distinguish "not computed" from "$0" so DDR never publishes a failed run as a zero-cost scenario.
7. RayCon must notify DDR when jobs finish, while DDR cron remains the safety net.
8. RayCon's chat/SSE API may continue, but it should call the same deterministic scenario engine used by the async job path.

## Current DDR Integration Shape

DDR currently treats Rhodes / LocationOS as the source of truth for site ID, Drive folder URL, site owner, and registered document links.

The current RayCon flow is:

1. A Block Plan lands in a site's `M1 - Acquire Property` folder.
2. DDR dispatches RayCon through `POST /v1/jobs`.
3. RayCon computes the scenarios asynchronously.
4. RayCon writes `raycon_scenario.json` back into the same M1 folder.
5. DDR's `raycon-followup.yml` workflow runs every 5 minutes and also supports a RayCon callback.
6. DDR reads `raycon_scenario.json`, publishes a RayCon Scenario Assessment doc, and republishes the DD Report in place when an existing DDR is present.

For controlled post-deploy proof runs, `raycon-followup.yml` has an optional
`workflow_dispatch` input named `require_raycon_git_commit`. The workflow passes
that value to `scripts/raycon_followup.py` as `--require-raycon-git-commit`.
When set, DDR checks RayCon `/version` before Drive, Rhodes, Alpha Capacity
artifact, or RayCon job mutations; a mismatch exits before dispatch. Use this
guard for the first live Miami Beach proof after deploying RayCon capacity
ingestion changes.

## Primary API: `POST /v1/jobs`

`POST /v1/jobs` is the primary DDR-facing endpoint.

Expected response code: `202 Accepted`

Content type: `application/json`

### Block Plan Job Payload

RayCon receives this when DDR has a specific Block Plan file to price.

```json
{
  "schema_version": "1.0",
  "site_id": "rhodes-site-id",
  "site_name": "Alpha Austin",
  "address": "123 Main St, Austin, TX",
  "drive_folder_url": "https://drive.google.com/drive/folders/site-root-folder-id",
  "m1_folder_id": "m1-drive-folder-id",
  "block_plan_file_id": "block-plan-drive-file-id",
  "block_plan_url": "https://drive.google.com/file/d/block-plan-drive-file-id/view",
  "total_building_sf": 8400,
  "capacity_analysis_file_id": "alpha-capacity-analysis-json-file-id",
  "capacity_analysis": {
    "source_label": "Alpha Capacity Analysis",
    "ruleset": "Microschool v2",
    "strict": {
      "capacity_students": 36
    },
    "max": {
      "capacity_students": 54
    }
  },
  "callback_marker": "raycon_scenario.json",
  "requested_at": "2026-06-01T19:00:00Z"
}
```

Required fields:

- `schema_version`
- `site_id`
- `site_name`
- `address`
- `drive_folder_url`
- `m1_folder_id`
- `block_plan_file_id`
- `block_plan_url`
- `callback_marker`
- `requested_at`

Optional fields:

- `total_building_sf`: if present, must be a positive number. Do not require it, because DDR omits the field when it has no reliable SF value.
- `capacity_analysis_file_id`: Drive file id for the machine-readable Alpha Capacity Analysis artifact.
- `capacity_analysis`: inline Alpha Capacity Analysis artifact. When present, `strict` / `fastest_open` is authoritative for `analysis.fastest_open.capacity_students`, and `max` / `max_capacity` is authoritative for `analysis.max_capacity.capacity_students`. RayCon may run its own capacity calculator as an audit/fallback, but it must not override the published Alpha capacity count.

DDR Block Plan dispatch resolves the site's M1 folder, looks for the latest Alpha Capacity Analysis or legacy Capacity Brainlift artifact, and attaches it when DDR can read a valid JSON payload or clearly labeled Strict/Max totals from the saved skill report. If no reliable artifact is present, DDR runs `ops-skills:alpha-capacity-analysis` against the extracted Block Plan text plus the Block Plan PDF evidence when file bytes are available, saves a machine-readable JSON artifact in M1 when both Strict/Fast Path and Max Capacity counts are produced, and sends that JSON to RayCon. If the skill cannot produce both counts, DDR skips the RayCon job with `dispatch_skipped=capacity_analysis_not_available` instead of sending a no-capacity request. RayCon's internal capacity calculator remains audit/fallback evidence inside RayCon, but DDR's automated Block Plan path must not ask RayCon to own published capacity.

### Folder Ping Payload

DDR sends this when a relevant document lands but no single Block Plan job should be forced yet.

```json
{
  "schema_version": "1.0",
  "site_id": "rhodes-site-id",
  "site_name": "Alpha Austin",
  "address": "123 Main St, Austin, TX",
  "drive_folder_url": "https://drive.google.com/drive/folders/site-root-folder-id",
  "m1_folder_id": "m1-drive-folder-id",
  "event": "folder_updated",
  "doc_type": "sir",
  "file_id": "source-drive-file-id",
  "file_url": "https://drive.google.com/file/d/source-drive-file-id/view",
  "callback_marker": "raycon_scenario.json",
  "requested_at": "2026-06-01T19:00:00Z"
}
```

Required fields:

- `schema_version`
- `site_id`
- `site_name`
- `address`
- `drive_folder_url`
- `m1_folder_id`
- `event`
- `callback_marker`
- `requested_at`

Optional hint fields:

- `doc_type`
- `file_id`
- `file_url`

RayCon should distinguish a folder ping from a Block Plan job by the absence of `block_plan_file_id`.

### Accepted Response Shape

```json
{
  "status": "queued",
  "job_id": "job_abc123",
  "raycon_run_id": "rc_20260601190000_abc123",
  "idempotency_key": "block_plan|rhodes-site-id|block-plan-drive-file-id",
  "retry_after_seconds": 30,
  "status_url": "https://raycon.example/v1/jobs/status/job_abc123?token=opaque",
  "cached": false
}
```

Required response fields:

- `status`
- `job_id`
- `raycon_run_id`
- `idempotency_key`
- `retry_after_seconds`
- `status_url`
- `cached`

Valid initial statuses:

- `queued`
- `running`
- `completed` only when RayCon is returning a cached completed result

## Idempotency Rules

Block Plan jobs must be idempotent on `block_plan_file_id`.

Folder pings should be idempotent on:

- `site_id + file_id`, when `file_id` is present
- otherwise `site_id + m1_folder_id + source-folder-state-hash`

Duplicate requests must not create duplicate output files. A duplicate request can return the existing job metadata with `cached: true`.

DDR may redispatch the same Block Plan after 30 minutes as a safety net. RayCon must treat that as safe.

## Job Status Endpoint

`GET status_url` should return non-sensitive job metadata.

```json
{
  "status": "completed",
  "job_id": "job_abc123",
  "raycon_run_id": "rc_20260601190000_abc123",
  "idempotency_key": "block_plan|rhodes-site-id|block-plan-drive-file-id",
  "retry_after_seconds": 30,
  "result_filename": "raycon_scenario.json",
  "drive_action": "updated",
  "drive_file": {
    "id": "raycon-json-drive-file-id",
    "name": "raycon_scenario.json",
    "modifiedTime": "2026-06-01T19:04:00Z"
  }
}
```

Valid in-progress statuses:

- `queued`
- `running`

Valid terminal statuses:

- `completed`
- `validation_failed`
- `failed`

## Required Output File

RayCon must write one UTF-8 JSON object named exactly:

```text
raycon_scenario.json
```

Destination:

```text
m1_folder_id
```

Preferred Drive behavior:

- Create the file if it does not exist.
- Update or replace the existing file if it already exists.
- Do not create timestamped duplicates.
- Updating the content must update Drive `modifiedTime`, because DDR uses `raycon_run_id + modifiedTime` for republish dedupe.

## Successful `raycon_scenario.json`

```json
{
  "schema_version": "1.0",
  "raycon_run_id": "rc_20260601190000_abc123",
  "status": "completed",
  "site": {
    "site_id": "rhodes-site-id",
    "site_name": "Alpha Austin",
    "address": "123 Main St, Austin, TX",
    "total_building_sf": 8400
  },
  "analysis": {
    "summary": "Scenario pricing completed.",
    "rooms": [],
    "fastest_open": {
      "capacity_students": 36,
      "grand_total": 412000,
      "timeline_weeks": 14,
      "soft_costs": 32000,
      "gc_fee": 28000,
      "contingency": 18000,
      "furniture": 24000,
      "categories": [
        { "category": "Demolition", "subtotal": 12000 },
        { "category": "MEP / Fire / Life Safety", "subtotal": 86000 }
      ]
    },
    "max_capacity": {
      "capacity_students": 54,
      "grand_total": 587000,
      "timeline_weeks": 22,
      "soft_costs": 44000,
      "gc_fee": 39000,
      "contingency": 26000,
      "categories": []
    },
    "max_value": {
      "capacity_students": 48,
      "grand_total": 470000,
      "timeline_weeks": 18,
      "soft_costs": 36000,
      "gc_fee": 32000,
      "contingency": 22000,
      "categories": []
    },
    "ray_review": {
      "summary": "Fastest Open is viable if the AHJ accepts cosmetic TI without full change-of-use scope.",
      "key_risks": [
        "Confirm restroom fixture count against target student load.",
        "Confirm fire/life-safety scope with AHJ."
      ],
      "assumptions": [
        "Existing HVAC remains serviceable.",
        "No structural work required."
      ]
    }
  },
  "provenance": {
    "selected_block_plan": {
      "id": "block-plan-drive-file-id",
      "name": "Block Plan.pdf",
      "modifiedTime": "2026-06-01T18:00:00Z"
    },
    "source_documents": [
      {
        "id": "sir-drive-file-id",
        "name": "Site Investigation Report.pdf",
        "role": "sir",
        "modifiedTime": "2026-06-01T17:00:00Z"
      }
    ],
    "input_hash": "sha256:...",
    "calculation_version": "raycon-engine-2.0.0"
  },
  "validation": {
    "passed": true,
    "errors": [],
    "warnings": []
  }
}
```

Current DDR consumes `analysis.fastest_open` and `analysis.max_capacity` for capacity, cost, and timeline. Capacity should be sourced from Alpha Capacity Analysis when the artifact is present; RayCon owns construction cost, schedule, and category rationale. RayCon should still emit `max_value` so DDR can adopt the third scenario without another RayCon schema change.

## Scenario Field Rules

Each scenario should use the same core fields:

- `capacity_students`: integer. For automated DDR Block Plan runs, source is Alpha Capacity Analysis or a sourced gap label; DDR should not publish RayCon internal capacity fallback as the capacity source of truth. Legacy/manual RayCon jobs without `capacity_analysis` may still expose RayCon calculator capacity, but those counts must be caveated in `capacity_trace` and should not satisfy DDR's Alpha-sourced capacity requirement.
- `grand_total`: number, whole dollars preferred
- `timeline_weeks`: positive integer
- `soft_costs`: number
- `gc_fee`: number
- `contingency`: number
- `furniture`: number, optional when already represented in categories
- `categories`: array of hard-cost category rows

### Category Vocabulary

Use this closed vocabulary for `categories[].category`:

- `Demolition`
- `Framing / Doors`
- `MEP / Fire / Life Safety`
- `Plumbing / Bathrooms`
- `Finish Work`
- `Furniture`
- `Tech / Security / Signage`
- `Other Hard Costs`

Do not put `Soft Costs`, `GC Fee`, `Contingency`, or `Grand Total` inside `categories`. Those belong at scenario top level to avoid double counting.

Each category row:

```json
{
  "category": "Finish Work",
  "subtotal": 42000,
  "note": "Paint, drywall patching, cleaning, and selective repairs."
}
```

Required:

- `category`
- `subtotal`

Optional:

- `note`
- `source`
- `line_items`

## Failed Or Non-Defensible Output

If RayCon cannot compute defensible scenarios, it should still write `raycon_scenario.json`. This lets DDR render and alert the failure instead of waiting indefinitely.

```json
{
  "schema_version": "1.0",
  "raycon_run_id": "rc_20260601190000_abc123",
  "status": "failed",
  "site": {
    "site_id": "rhodes-site-id",
    "site_name": "Alpha Austin",
    "address": "123 Main St, Austin, TX"
  },
  "analysis": {
    "summary": "RayCon could not complete scenario pricing. See validation errors.",
    "fastest_open": null,
    "max_capacity": null,
    "max_value": null,
    "ray_review": null
  },
  "provenance": {
    "selected_block_plan": {
      "id": "block-plan-drive-file-id"
    },
    "source_documents": [],
    "input_hash": "sha256:...",
    "calculation_version": "raycon-engine-2.0.0"
  },
  "validation": {
    "passed": false,
    "errors": [
      "capacity_not_defensible"
    ],
    "warnings": []
  }
}
```

DDR treats the following as failed:

- `status: "failed"`
- `status: "validation_failed"`
- `status: "error"`
- `validation.passed: false`

On failure, do not emit scenario totals as `0`. Empty/null scenarios plus clear validation errors are the correct shape.

## Validation Error Responses

Non-accepted API errors should be JSON:

```json
{
  "ok": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Number must be greater than 0",
    "path": "total_building_sf"
  }
}
```

Recommended status codes:

- `400 INVALID_JSON`
- `400 VALIDATION_ERROR`
- `401 UNAUTHORIZED`
- `403 FORBIDDEN`
- `409 CONFLICT` only when a request conflicts with an active incompatible job
- `429 RATE_LIMITED`
- `503 UNAVAILABLE`

Validation requirements:

- `drive_folder_url` must be a Google Drive folder URL or a folder ID.
- Reject Google Drive file URLs when a folder is required.
- `m1_folder_id` must be accepted as a folder ID.
- `block_plan_file_id`, when present, must be accepted as a file ID.
- `total_building_sf`, when present, must be greater than zero.

## Security

DDR can send:

- `X-RayCon-Signature`: `sha256=<hex>` HMAC-SHA256 over the raw request body.
- `X-RayCon-API-Key`: legacy optional API key.

Target behavior:

1. If `X-RayCon-Signature` is present and a shared secret is configured, verify the signature against the exact raw bytes received.
2. Keep `X-RayCon-API-Key` during transition.
3. Use a least-privilege GitHub PAT or GitHub App token for the DDR workflow callback.
4. Do not log secrets, signed callback URLs, OAuth tokens, or raw source document contents.

## DDR Callback

After RayCon writes `raycon_scenario.json`, call the DDR GitHub workflow dispatch endpoint for `raycon-followup.yml`.

Workflow inputs:

```json
{
  "site_id": "rhodes-site-id-or-drive-folder-id",
  "run_id": "rc_20260601190000_abc123",
  "status": "succeeded"
}
```

Valid callback statuses:

- `succeeded`
- `failed`
- `partial`

Callback rules:

- Fire callback only after the JSON write is complete.
- Include the same `raycon_run_id` that appears in `raycon_scenario.json`.
- `site_id` may be Rhodes site ID or Drive folder ID. DDR can match both.
- Callback failure must not mark the RayCon job failed if the JSON was written. DDR cron will pick it up.

## Optional Chat API

RayCon may keep `POST /v1/chat` for advisory chat and frontend use, but DDR should treat it as optional.

If retained, `/v1/chat` should:

- Use the same deterministic scenario engine as `/v1/jobs`.
- Stream narrative as SSE if desired.
- Put authoritative numbers only in structured output.
- Never require DDR to scrape prose.
- Treat the final `done` event as authoritative when streaming.

## Observability

Each job should record:

- `job_id`
- `raycon_run_id`
- idempotency key
- request received time
- job started time
- job completed time
- selected Block Plan file ID
- selected source document file IDs
- output Drive file ID
- status
- validation errors
- calculation version
- input hash

Recommended log events:

- `job.accepted`
- `job.deduped`
- `drive.sources_loaded`
- `scenario.validation_failed`
- `scenario.completed`
- `drive.output_written`
- `callback.sent`
- `callback.failed`

## Acceptance Tests

RayCon is DDR-ready when these tests pass:

1. A valid Block Plan job returns `202 Accepted` with all accepted-response fields.
2. A duplicate Block Plan job for the same `block_plan_file_id` returns cached/no-op metadata and creates no duplicate Drive output.
3. A folder ping without `block_plan_file_id` is accepted and does not require `block_plan_url`.
4. A job with missing `drive_folder_url` returns `400 VALIDATION_ERROR` with `path: "drive_folder_url"`.
5. A job with a Google Drive file URL in `drive_folder_url` returns `400 VALIDATION_ERROR`.
6. A job with omitted `total_building_sf` is accepted.
7. A job with `total_building_sf: 0` is rejected or ignored consistently; DDR currently omits zero to avoid validator failure.
8. A successful job writes exactly one `raycon_scenario.json` to the supplied M1 folder.
9. A successful JSON includes `schema_version`, `raycon_run_id`, `status`, `analysis.fastest_open`, `analysis.max_capacity`, `provenance`, and `validation`.
10. Cost categories use only the closed DDR vocabulary.
11. Soft costs, GC fee, contingency, and grand total are scenario top-level fields, not category rows.
12. A failed job writes `raycon_scenario.json` with `status: "failed"` or `status: "validation_failed"`, `validation.passed: false`, and non-empty `validation.errors`.
13. A failed job does not emit successful-looking zero-dollar scenario totals.
14. Updating/recomputing a scenario updates Drive `modifiedTime`.
15. After JSON write, RayCon calls DDR callback with `site_id`, `run_id`, and `status`.
16. If callback fails, the job remains complete and the JSON remains available for DDR cron.
17. `GET status_url` returns terminal job metadata without requiring DDR to expose secrets in logs.

## Implementation Priorities

Phase 1: Async DDR contract

- Implement `POST /v1/jobs`.
- Implement idempotent job storage.
- Implement Drive read/write for M1.
- Emit `raycon_scenario.json` in the schema above.
- Implement job status endpoint.

Phase 2: Deterministic scenario engine

- Extract the cost/capacity/timeline calculations into a pure engine.
- Keep LLM narrative separate from numbers.
- Version the calculation engine.
- Add input hashing.

Phase 3: Callback and operations

- Trigger DDR workflow dispatch after successful JSON write.
- Add retry/backoff for callback.
- Add structured logs and run dashboard.

Phase 4: Optional frontend/chat

- Reconnect the RayCon UI and `/v1/chat` to the same deterministic engine.
- Expose structured results and downloadable docs, but do not fork the DDR calculation path.

## Open Questions For RayCon Team

1. Should `site_id` be treated as the Rhodes ID, the Drive folder ID, or both? DDR can currently match both, but RayCon should store both when available.
2. Should RayCon own Google Drive credentials, or should DDR provide signed file-access links? Current workflow assumes RayCon can read the Drive folder and write to M1.
3. Should `max_value` be computed in the initial rebuild, or emitted as `null` until the engine supports it?
4. What calculation versioning scheme should RayCon use: semantic version, commit SHA, or both?
5. Should failed scenario JSON replace the prior successful JSON, or should RayCon preserve last-success separately? DDR currently reads the canonical `raycon_scenario.json`, so replacing it with failure will correctly alert, but this is a product decision.
