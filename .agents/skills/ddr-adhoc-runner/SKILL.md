---
name: ddr-adhoc-runner
description: Run or diagnose the Due Diligence Reporter (DDR) ad hoc for a single site without relying on BrainTrust, Google Chat, or MCP Hive as the execution surface. Use when asked to run a manual/ad-hoc DDR, diagnose readiness, force-regenerate or republish a DDR, suppress notifications during a live test, inspect a run manifest, or choose the safe local/GitHub dispatch path in this due-diligence-reporter repo.
metadata:
  scorecard:
    themeId: site-due-diligence-opening
---

# DDR Ad-Hoc Runner

## Usage telemetry

At the start of this skill run, if telemetry is configured, create one stable
`runId` and log `started` with:

```bash
node ../../scripts/track-skill-usage.mjs --skill-id ddr-adhoc-runner --run-id "$RUN_ID" --outcome started
```

Before your final response, log `completed` with the same `runId`. If the
skill fails after starting, log `failed` instead. Telemetry is best-effort:
never interrupt the user if telemetry fails, and never send prompts, file
names, file contents, or outputs.

Use the repo-backed Python pipeline as the control plane. Do not use BrainTrust,
Google Chat, or MCP Hive as the ad-hoc execution surface unless the user
explicitly asks to test that external integration.

## First Checks

1. Read the closest `AGENTS.md` and the top of `HANDOFF.md`.
2. Inspect current work with `git status --short`.
3. Use Beads for tracking. If this is new project work, create or claim a
   scoped bead before editing or running mutating workflows.
4. If the run may touch Rhodes/LocationOS, prove live tool access before any
   write:

```powershell
codex mcp list
codex mcp get locationos --json
```

If `locationos` is missing, unauthorized, stale, or exposes no usable tools,
refresh auth before proceeding:

```powershell
codex mcp logout locationos
codex mcp login locationos
codex mcp list
```

Successful login is not enough. Prove the active execution surface can perform
a safe live read against the intended target, such as `getSite` for the exact
site ID. If this desktop/thread session still has no callable `mcp__locationos`
tools even though `codex mcp list` shows OAuth configured, use a fresh Codex
process/thread with LocationOS tools or an interactive OAuth-backed MCP helper
under `C:\tmp` that performs a safe read. Do not write from memory, prior chat,
or local manifests alone.

5. Confirm the operator mode:
   - `diagnose`: read-only readiness and blocker check.
   - `first-publish`: first DDR publish for a site with no existing DDR.
   - `force-regenerate`: operator-controlled rerun when an active DDR exists.
   - `source-sweep`: detect source-triggered republish candidates.
   - `source-republish`: force a source-event republish with explicit source metadata.

## Safety Rules

- Default to suppressed outbound Chat/email for ad-hoc runs. Use `--notify`
  only when the user explicitly wants normal DDR notifications.
- Rhodes due-diligence writes and Rhodes notes are not "notifications" for this
  skill; they are part of the system-of-record contract. Do not suppress or
  bypass them unless the user explicitly asks for read-only diagnosis.
- Preserve the SOR-first sequence: prepare normalized DD data, write Rhodes,
  then render or reuse the DDR/candidate document.
- Preserve candidate idempotency. Do not create manual Google Docs outside the
  pipeline to work around overwrite guards.
- For LocationOS/Rhodes writes, use fresh live reads, the narrowest correct
  tool, and post-write readback. For nested objects such as `dueDiligence`,
  preserve existing values unless the intended mutation explicitly changes
  them.
- Treat confirmation-gated writes as not applied until readback proves the
  value landed. If a write fails with `elicitation_unsupported`,
  `user cancelled MCP tool call`, or a generic server error, re-read the record
  before retrying so partial writes or duplicate notes are not missed.
- For report-event notes, pre-check `listNotes` for the exact body before
  retrying and post-check `listNotes` for the exact body, note ID, and mention.
- For DD report links, verify the latest active DDR in the Drive M1 folder
  before updating `dueDiligence.ddReportLink`; do not infer the link from a
  stale run summary when Drive can be read.
- Do not read or print `.env`, token files, OAuth credentials, API keys, or app
  passwords.

## Command

Use the package CLI from the repo root:

```powershell
uv run ddr run-site --help
```

The command sets Chat/email env vars to empty by default before loading repo
settings. It still uses the repo `.env` for Google, Anthropic, LocationOS, and
Drive configuration. The bundled `scripts/run_ddr_site.py` is only a
compatibility wrapper around `uv run ddr run-site`.

### Diagnose Readiness

Read-only diagnosis:

```powershell
uv run ddr run-site diagnose --site "Alpha Houston 777 W 23rd St"
```

Use this before any mutating mode. Report `partial_report_possible`,
`ready_for_full_report`, blockers, M1 status, RayCon status, and any Rhodes
Drive-folder issue.

### First Publish

Run only after diagnosis shows a SIR-backed first publish is appropriate:

```powershell
uv run ddr run-site first-publish --site "Alpha Example" --address "123 Main St, Austin, TX"
```

This calls `process_site_pipeline(..., force_regenerate=False)`.

### Force Regenerate

Use for operator-controlled reruns when a DDR already exists:

```powershell
uv run ddr run-site force-regenerate --site "Alpha Example"
```

This calls `process_site_pipeline(..., force_regenerate=True)`. If the active
DDR is protected by the overwrite guard, the pipeline should reuse or create an
automation-owned candidate according to current repo behavior.

### LocationOS MCP-Assisted SOR Write

Use this when the bearer-token LocationOS `updateDueDiligence` path fails with
`elicitation_unsupported` but the operator/user has the OAuth-backed
`locationos` MCP available:

```powershell
uv run ddr run-site force-regenerate --site "Alpha Example" --sor-write-mode mcp-assisted
```

If the run returns `status=locationos_mcp_write_required`, copy the exact
`locationos_mcp_write_request.arguments` into the OAuth-backed
`locationos.updateDueDiligence` MCP call. After the MCP write and Aerie approval
complete, rerun the emitted `mcp_resume_command`. That command uses
`uv run ddr run-site resume-mcp-write --run-id <source_run_id>` and is
manifest-bound: DDR verifies LocationOS readback against the exact saved
`updateDueDiligence` arguments and renders the DD Report from the saved
`render_input` without regenerating DD data.

#### Authenticated Completion Checklist

Use this checklist whenever `status=locationos_mcp_write_required` or a later
`rhodes.report_event` step fails on a confirmation gate.

1. Identify the exact target from a fresh live read:
   - use the site ID from the run manifest only as an identifier;
   - call live `getSite`;
   - confirm the returned name/address matches the intended site;
   - capture current `dueDiligence` values for any field that may be touched.
2. Validate the evidence:
   - use the manifest's exact `locationos_mcp_write_request.arguments` for the
     pending due-diligence field write;
   - use the Drive M1 folder listing to identify the latest active DDR before
     setting `ddReportLink`;
   - use the run manifest and `automation_event` renderer to reconstruct report
     event notes when the note body was not persisted in the manifest.
3. Run one exact OAuth-backed `updateDueDiligence` mutation for the pending
   fields and approve the Aerie/LocationOS confirmation if prompted.
4. Immediately call `getSite` and compare every intended field against the
   exact requested values. Do not resume rendering if readback mismatches.
5. Run the manifest-bound resume command:

```powershell
uv run ddr run-site resume-mcp-write --run-id "<source_run_id>"
```

6. Inspect the resumed run:

```powershell
uv run ddr status --run-id "<resume_run_id>"
uv run ddr trace --run-id "<resume_run_id>" --failed-only
```

7. If the report rendered but `rhodes.report_event` failed because `addNote`
   was confirmation-gated:
   - reconstruct the exact `AutomationEvent v1` body from the resume manifest
     using `build_dd_report_summary_event(...)` and
     `render_automation_event_note(...)`;
   - call `listNotes` first and skip posting if that exact body already exists;
   - call OAuth-backed `addNote` with `anchorType=site`, `siteId`, `anchorId`,
     the exact body, and the resolved owner/P1 mention when available;
   - approve the confirmation prompt;
   - call `listNotes` again and verify the exact body exists, a note ID is
     present, and the mention text/user is present when expected.
8. If the rendered DDR URL is not present in live `dueDiligence.ddReportLink`:
   - list the site M1 Drive folder;
   - choose the newest active `dd_report` document by modified time and name;
   - call OAuth-backed `updateDueDiligence` with only
     `{"siteId": "...", "ddReportLink": "<latest DDR URL>"}`;
   - approve the confirmation prompt;
   - call `getSite` and verify `dueDiligence.ddReportLink` equals the exact
     URL.

If the current Codex thread cannot call LocationOS tools directly, a temporary
Python helper under `C:\tmp` may be used with the repo `uv` environment and the
official MCP `streamablehttp_client` OAuth flow. Such helpers must:

- print the OAuth URL but never print tokens or secrets;
- perform a fresh safe read before writing;
- make at most one intended mutation per run;
- be duplicate-safe for notes;
- print structured JSON with before value, write result, after value, and
  readback verification;
- live outside the repo unless promoted into durable code through a reviewed
  implementation.

#### Completion Criteria

Do not call the DDR run complete until all applicable live readbacks are true:

- LocationOS due-diligence fields match the exact MCP-assisted write payload.
- The DD Report Google Doc exists in the site M1 folder.
- The report-event note either has a verified Rhodes note ID/body/mention or a
  separate blocker bead records why it could not be posted.
- `dueDiligence.ddReportLink` points to the latest active DDR Google Doc when a
  report URL exists.
- The run manifest/status and Beads handoff record any residual failed step,
  manual helper action, or durable code gap.

### Source Sweep

Detect changed source documents through the existing sweep path:

```powershell
uv run ddr run-site source-sweep --site "Alpha Example" --dry-run
```

Use `--apply` only when the user approves a mutating source-triggered run.

### Explicit Source Republish

Use only when the source type and fingerprint are known:

```powershell
uv run ddr run-site source-republish --site "Alpha Example" --source-type vendor_sir --fingerprint "drive-file-id:modifiedTime"
```

Supported `--source-type` values are the current core source types from
`open_questions.CORE_SOURCE_TYPES`.

## Output Contract

After a run, summarize:

- mode, site, status
- LocationOS auth/tool access proof used for any Rhodes write
- `run_id` and `manifest_path`
- `doc_url` or candidate URL, if present
- Rhodes due-diligence write status and exact readback fields verified
- Rhodes report-event status, note ID, exact-body readback, and mention
  verification when a note was expected
- `dueDiligence.ddReportLink` before/after value and readback verification when
  a report URL exists
- failed step and next operator action, if present
- whether notifications were suppressed or enabled
- any durable follow-up bead for a remaining code gap, such as a missing
  elicitation-capable default `addNote` client path

For failed or blocked runs, inspect the manifest with:

```powershell
uv run ddr status --run-id "<run_id>"
uv run ddr trace --run-id "<run_id>" --failed-only
```

Do not claim completion without manifest readback when a live mutating run was
performed.
