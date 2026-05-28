# Due Diligence Reporter Handoff

## 2026-05-28 - RayCon Explicit Rhodes Note Anchor

Confirmed PR #132 merged at `98d31ad` and retested on `main`.

Live test:

- Deleted only the two stale RayCon runtime caches from the failed PR #131
  retest runs:
  - `raycon-runtime-state-26581936544`
  - `raycon-runtime-state-26581936500`
- Santa Clara run:
  https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26583402921
- Tulsa run:
  https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26583402885
- Both runs failed closed with `missing_note_id`.
- Both runs included Devin Bates plus Greg Foote in `mentioned_user_ids`.
- Both runs posted the Google Chat fallback.
- Live Rhodes `listNotes` and audit-log readback still showed no new RayCon
  note on either site.
- The failure now survives site-ID write, readback recovery, and site-slug
  retry. That points to the hosted Rhodes MCP `addNote` write path rather than
  RayCon alert dedupe.

Branch: `codex/raycon-explicit-note-anchor`

Changed:

- `RhodesClient.add_site_note` now sends explicit site anchoring in the MCP
  payload:
  - `anchorType: "site"`
  - `anchorId: <siteId>` when a Rhodes site ID is available
- Added a client-level regression test for the exact `addNote` payload shape.

Verification:

```powershell
uv run pytest tests/test_rhodes.py tests/test_rhodes_events.py tests/test_raycon_followup.py --basetemp C:\tmp\pytest-raycon-explicit-note-anchor
uv run ruff check src\due_diligence_reporter\rhodes.py tests\test_rhodes.py tests\test_rhodes_events.py tests\test_raycon_followup.py scripts\raycon_followup.py src\due_diligence_reporter\rhodes_events.py
uv run mypy src/
uv run mypy scripts/raycon_followup.py
```

Results:

- Focused Rhodes/RayCon suite: 83 passed.
- Ruff on touched code/tests: passed.
- Source Mypy: no issues in 38 source files.
- Script Mypy: no issues.

Next:

- Open PR for `codex/raycon-explicit-note-anchor`.
- After merge, clear the fresh RayCon runtime caches from the failed PR #132
  retest runs if they would suppress the new test:
  - `raycon-runtime-state-26583402921`
  - `raycon-runtime-state-26583402885`
- Rerun RayCon Follow-up for Tulsa/Santa Clara.
- If explicit anchoring still produces `missing_note_id` and no Rhodes audit
  entry, the fix needs to move to the deployed Rhodes MCP/API write surface.

## 2026-05-28 - RayCon Rhodes Note Readback / Slug Fallback

Confirmed PR #131 merged at `c71de5e` and retested the merged fail-closed
behavior on `main`.

Live test:

- Cleared only the relevant `raycon-runtime-state-*` cache entries from the
  prior false-green test runs.
- Tulsa run:
  https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26581936544
- Santa Clara run:
  https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26581936500
- Both runs failed closed with `missing_note_id`.
- Both runs included Devin Bates plus Greg Foote in `mentioned_user_ids`.
- Both runs posted the Google Chat fallback.
- Live Rhodes `listNotes` and audit-log readback still showed no new RayCon
  note on either site.

Branch: `codex/raycon-rhodes-note-readback`

Changed:

- `RhodesClient` can now create/list notes by either `siteId` or `siteSlug`.
- `add_rhodes_site_note` now attempts to recover a note ID from `listNotes`
  after an `addNote` response with no ID.
- If the site-ID write path still returns no ID and a slug is available, the
  helper retries the note write with `siteSlug`, then reads back by slug.
- RayCon follow-up now carries Rhodes `site_slug` through site context and into
  the Rhodes note helper.
- Regression tests cover no-ID failure, readback recovery, slug retry, and the
  RayCon caller passing `site_slug`.

Verification:

```powershell
uv run pytest tests/test_rhodes.py tests/test_rhodes_events.py tests/test_raycon_followup.py --basetemp C:\tmp\pytest-raycon-note-readback
uv run ruff check src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\rhodes_events.py scripts\raycon_followup.py tests\test_rhodes.py tests\test_rhodes_events.py tests\test_raycon_followup.py
uv run mypy src/
uv run mypy scripts/raycon_followup.py
```

Results:

- Focused Rhodes/RayCon suite: 82 passed.
- Ruff on touched code/tests: passed.
- Source Mypy: no issues in 38 source files.
- Script Mypy: no issues.

Next:

- Open PR for `codex/raycon-rhodes-note-readback`.
- After merge, clear the fresh `raycon-runtime-state-*` cache entries for the
  failed PR #131 retest runs if they would suppress the new test.
- Rerun RayCon Follow-up for Tulsa/Santa Clara.
- Expected outcomes:
  - If `addNote` created a note but returned no ID, readback should recover the
    ID and the workflow should succeed.
  - If the `siteId` write path is the issue, the `siteSlug` retry may create the
    note.
  - If the remote MCP/API note-write path is truly no-op, confirmation-gated, or
    blocked, the workflow should still fail with `missing_note_id` and post Chat
    fallback; that would move the remaining fix to the Rhodes MCP/write surface.

## 2026-05-28 - RayCon Note ID Verification

Confirmed PR #130 was merged at `e7227be` and tested the merged RayCon
extra-mention path on `main`.

Live test:

- First rerun pair was dedup-suppressed by restored RayCon runtime alert state.
- Cleared only the four relevant `raycon-runtime-state-*` GitHub Actions cache
  entries from today's test runs.
- Second Tulsa run:
  https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26580325911
- Second Santa Clara run:
  https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26580325718
- Both second runs completed successfully and logged
  `owner_notification: mentioned` with Devin Bates plus Greg Foote in
  `mentioned_user_ids`.
- The log status also had an empty `rhodes_note_id`, and live Rhodes
  `listNotes` / audit-log checks showed no new RayCon note on either site.
  The workflow was therefore falsely treating a no-ID `addNote` response as a
  verified owner notification.

Branch: `codex/raycon-note-id-required`

Changed:

- `add_rhodes_site_note` now treats an `addNote` response without a concrete
  note ID as `failed` with reason `missing_note_id`.
- Shared Rhodes/Chat fallback logic now requires a created Rhodes note ID before
  considering an owner mention delivered.
- RayCon alert dedupe now advances only when the owner mention has a concrete
  Rhodes note ID, or when the no-owner Chat fallback posts.

Verification:

```powershell
uv run pytest tests/test_rhodes.py tests/test_rhodes_events.py tests/test_raycon_followup.py --basetemp C:\tmp\pytest-raycon-note-id
uv run ruff check src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\rhodes_events.py scripts\raycon_followup.py tests\test_rhodes.py tests\test_rhodes_events.py tests\test_raycon_followup.py
uv run mypy src/
uv run mypy scripts/raycon_followup.py
```

Results:

- Focused Rhodes/RayCon suite: 80 passed.
- Ruff on touched code/tests: passed.
- Source Mypy: no issues in 38 source files.
- Script Mypy: no issues.

Next:

- Open PR for `codex/raycon-note-id-required`.
- After merge, rerun RayCon Follow-up for Tulsa/Santa Clara again. Expected
  behavior if Rhodes still returns no note ID: workflow fails and posts Chat
  fallback instead of suppressing future retries.
- Keep `RAYCON_FOLLOWUP_EXTRA_MENTION_USER_IDS` set to Greg's user ID until a
  retest confirms the notification is actually delivered; then clear it.

## 2026-05-28 - RayCon Test Extra Mention

Confirmed PR #129 merged at `d54462d` and continued from updated `main`.

Goal:

- Add Greg as an additional Rhodes mention on RayCon follow-up alert notes so
  he can verify whether the `@` mention notification comes through during the
  Tulsa/Santa Clara test rerun.

Branch: `codex/raycon-test-extra-mention`

Draft PR: https://github.com/GFooteGK1/due-diligence-reporter/pull/130

Changed:

- Added `RAYCON_FOLLOWUP_EXTRA_MENTION_USER_IDS` as a configurable
  comma-separated Settings value.
- RayCon follow-up passes configured extra mention user IDs into the Rhodes note
  writer, while preserving the site owner's notification as the delivery
  success criterion.
- `add_rhodes_site_note` now supports extra mention user IDs and de-dupes them
  against the owner mention.
- The RayCon workflow writes the GitHub Actions variable into `.env`.
- Repository Actions variable set:
  `RAYCON_FOLLOWUP_EXTRA_MENTION_USER_IDS=kd7fnr0nm2tg1c8jq85wrc3gn9830jw5`
  for Greg Foote.

Verification:

```powershell
uv run pytest tests/test_raycon_followup.py tests/test_rhodes_events.py tests/test_rhodes.py --basetemp C:\tmp\pytest-raycon-extra-mention
uv run ruff check scripts/raycon_followup.py src/due_diligence_reporter/config.py src/due_diligence_reporter/rhodes.py src/due_diligence_reporter/rhodes_events.py tests/test_raycon_followup.py tests/test_rhodes_events.py tests/test_rhodes.py
uv run mypy src/
uv run mypy scripts/raycon_followup.py
uv run pytest tests/test_workflow_contracts.py --basetemp C:\tmp\pytest-raycon-extra-mention-workflow
```

Results:

- Focused RayCon/Rhodes suite: 79 passed.
- Workflow contract suite: 7 passed.
- Ruff on touched code/tests: passed.
- Source Mypy: no issues in 38 source files.
- Script Mypy: no issues.

Next:

- Wait for CI/review on PR #130.
- After merge, rerun RayCon Follow-up for `6940 S Utica` and
  `2340 Calle de Luna`; expected Rhodes note mentions Devin Bates plus Greg.
- After Greg confirms the notification behavior, remove or clear the extra
  mention variable so Greg is not permanently copied on RayCon alerts.

## 2026-05-28 - RayCon Alert Delivery Enforcement

Tested PR #128 after merge on `main` at
`164caac02d8c82582a4bd190fce105c32b3493ea`:

- Tulsa manual run:
  https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26577057390
- Santa Clara manual run:
  https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26577064119

Live findings:

- Both runs completed successfully.
- Both runs detected the failed `raycon_scenario.json` and resolved Devin Bates
  as the P1 owner.
- Rhodes readback showed no new site note and no `note.added` audit entry for
  either Tulsa or Santa Clara.
- Workflow logs showed `published=0 alerts=1 errors=0`; the alert row existed,
  but notification delivery was not visible in the logs and did not affect the
  workflow exit code.

Branch: `codex/raycon-alert-delivery-enforced`

Draft PR: https://github.com/GFooteGK1/due-diligence-reporter/pull/129

Changed:

- RayCon follow-up now logs sanitized Rhodes/Chat notification status for each
  fresh alert/error row.
- Owner-assigned rows only count as delivered when Rhodes creates the note and
  mentions the owner. A Google Chat post no longer advances alert dedupe for an
  owner-assigned row whose owner notification failed.
- Fresh alert/error rows with undelivered notifications now make the workflow
  exit non-zero instead of completing green.

Verification:

```powershell
uv run pytest tests/test_raycon_followup.py --basetemp C:\tmp\pytest-raycon-followup
uv run ruff check scripts/raycon_followup.py tests/test_raycon_followup.py
uv run mypy scripts/raycon_followup.py
```

Results:

- RayCon follow-up test file: 58 passed.
- Ruff on touched files: passed.
- Script Mypy: no issues.

Next:

- Wait for CI/review on PR #129.
- After merge, rerun RayCon Follow-up for `6940 S Utica` and
  `2340 Calle de Luna`. If Rhodes note creation still fails, the workflow
  should fail and log the exact `raycon_followup_event` status instead of
  silently reporting success.

## 2026-05-28 - RayCon Failed Alert Backfill

Confirmed PR #127 was merged at `cefda5c` and tested the production RayCon
follow-up workflow on `main`:

- Tulsa run: https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26576437853
- Santa Clara run: https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26576446208

Live findings:

- Both workflows completed successfully.
- Both sites have `raycon_scenario.json` files with `status: failed` and
  `validation.passed: false`.
- Tulsa's RayCon JSON points to a selected Block Plan in a nested
  `From Landlord / Floor Plan` folder, while `scripts/raycon_followup.py`
  required a direct M1 Block Plan before reading the scenario. That meant the
  failed JSON could be skipped as `no block plan in M1`.
- Santa Clara has a direct M1 Block Plan and a failed RayCon JSON, but no new
  Rhodes note appeared after the workflow. The likely cause is alert dedupe
  advancing before the owner/Chat notification result is known, so a prior
  failed notification can suppress later retries.

Branch: `codex/ddr-raycon-failed-alert-backfill`

Changed:

- RayCon follow-up now reads a present `raycon_scenario.json` before requiring a
  direct M1 Block Plan.
- Failed RayCon JSON now produces an alert row even when the Block Plan is not
  directly listed in M1; retry dispatch still requires a concrete Block Plan
  file.
- Failed-scenario alert dedupe keys now carry an `owner_note_v2` suffix so
  older alert-state entries do not suppress the new owner-note behavior.
- Runtime alert dedupe now advances only after the Rhodes owner mention or
  Google Chat fallback actually succeeds.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-raycon-alert-backfill2 tests\test_raycon_followup.py tests\test_rhodes_events.py -q
uv run ruff check scripts\raycon_followup.py tests\test_raycon_followup.py
uv run mypy scripts\raycon_followup.py
uv run python -m py_compile scripts\raycon_followup.py
```

Results:

- Focused RayCon/Rhodes event suite: 62 passed.
- Ruff on touched files: passed.
- Script Mypy: no issues.
- Script compile: passed.

Next:

- Open PR for `codex/ddr-raycon-failed-alert-backfill`.
- After merge, rerun RayCon Follow-up for `6940 S Utica` and
  `2340 Calle de Luna`; expected result is a Rhodes `raycon_followup_alert`
  owner mention for Devin Bates on both sites, with Chat fallback only if the
  owner mention cannot be delivered.

## 2026-05-28 - RayCon Failed Scenario State

Confirmed PR #126 was merged at `8dc8b16` and continued on a clean branch:

- Branch: `codex/ddr-raycon-failed-state`
- Draft PR: https://github.com/GFooteGK1/due-diligence-reporter/pull/127
- Implementation commit: `39f48d1` (`Handle failed RayCon scenario payloads`)
- Current state: branch pushed, draft PR open, GitHub reports merge state
  `CLEAN`. No checks were reported when checked.

Changed:

- Readiness now distinguishes a present `raycon_scenario.json` from a usable
  RayCon scenario. Payloads with `status: failed`, `status:
  validation_failed`, `status: error`, or `validation.passed: false` surface as
  `failed_validation` instead of satisfying the full-report RayCon slot.
- Failed RayCon report fields are carried into report generation as
  authoritative cached fields. If the agent supplies RayCon values anyway, the
  failed RayCon state overrides the RayCon-sourced cost/CAPEX/open-date tokens.
- Partial DDR completeness now has a separate `raycon_scenario_failed` reason,
  renders `RayCon validation failed` in the banner, preserves the RayCon
  failure reason, and treats generic `[Not found - RayCon scenario pending]`
  labels as pending instead of filled values.
- RayCon follow-up failed-scenario retry rows remain alert rows, so Rhodes
  owner-note / Google Chat fallback notification happens even when the workflow
  also dispatches an automatic recovery job.
- Updated `docs/process/HOW-IT-WORKS.md` with the failed-validation contract.

Verification:

```powershell
uv run python -m py_compile src\due_diligence_reporter\raycon_client.py src\due_diligence_reporter\completeness.py src\due_diligence_reporter\google_doc_builder.py src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py scripts\raycon_followup.py
uv run pytest --basetemp C:\tmp\ddr-raycon-failed-state-focused tests/test_completeness.py tests/test_dd_output_fixes.py tests/test_vendor_gate.py tests/test_report_pipeline.py::TestCheckSiteReadinessDirect tests/test_diagnose_site_readiness.py tests/test_raycon_followup.py::TestFailedScenarioAlerts -q
uv run pytest --basetemp C:\tmp\ddr-raycon-failed-state-final2 tests/test_raycon_client.py tests/test_completeness.py tests/test_dd_output_fixes.py tests/test_vendor_gate.py tests/test_report_pipeline.py tests/test_diagnose_site_readiness.py tests/test_raycon_followup.py tests/test_google_doc_builder.py tests/test_dd_republish.py tests/test_rhodes_events.py -q
uv run ruff check src\due_diligence_reporter\raycon_client.py src\due_diligence_reporter\completeness.py src\due_diligence_reporter\google_doc_builder.py src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py scripts\raycon_followup.py tests\test_raycon_client.py tests\test_completeness.py tests\test_dd_output_fixes.py tests\test_vendor_gate.py tests\test_report_pipeline.py tests\test_diagnose_site_readiness.py tests\test_raycon_followup.py
uv run mypy src/
git diff --check
```

Results:

- Focused RayCon failed-state suite: 126 passed.
- Affected RayCon/report/Rhodes suite: 388 passed.
- Ruff on touched code/tests: passed.
- Full source Mypy: no issues in 38 source files.
- Diff check: passed with expected Windows LF-to-CRLF warnings only.

Next:

- Wait for CI/review on PR #127.
- After merge, rerun RayCon follow-up or the DDR republish path against Tulsa
  6940 S Utica Ave and Santa Clara 2340 Calle de Luna so Rhodes gets the
  failed-validation owner note and the DDRs render explicit RayCon validation
  failure instead of pending/filled placeholders.

## 2026-05-28 - Inbox Manual Review Rhodes Events

Confirmed the previous shared-helper PR was merged:

- `due-diligence-reporter` PR #125 merged at `e96ab90`.

Continued Phase 2/3 record-completion work with a narrow inbox manual-review
slice.

Branch: `codex/ddr-inbox-manual-review-events`

Draft PR: https://github.com/GFooteGK1/due-diligence-reporter/pull/126

Implementation commit: `520a03a` (`Record inbox manual review events in Rhodes`)

Current state: branch pushed, draft PR open, GitHub reports merge state
`CLEAN`. No checks were reported when checked.

Changed:

- Added a DDR `inbox_manual_review_required` `AutomationEvent v1` builder.
- Matched-site inbox manual-review rows now write a Rhodes decision note before
  the email is labeled for manual review.
- The note mentions the P1 DRI when Rhodes can resolve a user ID.
- If the note cannot mention an owner, the same event body is posted to the
  configured Google Chat webhook.
- Existing `DD-Manual-Review` labels suppress duplicate Rhodes/Chat event
  creation on repeated scans.
- Google Chat scan summaries include the Rhodes decision-note reference when a
  manual-review event created one.
- Updated `docs/process/HOW-IT-WORKS.md`.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-inbox-manual-review-events-focused tests/test_automation_event.py tests/test_inbox_scanner.py::TestRhodesDocumentRegistration tests/test_inbox_scanner.py::test_scan_summary_includes_manual_review_reason -q
uv run ruff check src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\inbox_scanner.py tests\test_automation_event.py tests\test_inbox_scanner.py
uv run mypy src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\inbox_scanner.py
uv run pytest --basetemp C:\tmp\ddr-inbox-manual-review-events-broad tests/test_automation_event.py tests/test_inbox_scanner.py tests/test_scan_inbox_e2e.py tests/test_rhodes_events.py tests/test_rhodes.py -q
uv run mypy src/
git diff --check
git diff --cached --check
```

Results:

- Focused event/inbox suite: 17 passed.
- Ruff on touched code/tests: passed.
- Focused Mypy: no issues in 2 source files.
- Broader inbox/Rhodes suite: 110 passed.
- Full source Mypy: no issues in 38 source files.
- Diff checks: passed with expected Windows LF-to-CRLF warnings and the
  existing user-level ignore permission warning only.

Next:

- Wait for CI/review on PR #126.
- After merge, continue with the next notification-only/record-completion path,
  likely DDR republish failure events or broader manual-review task semantics.

## 2026-05-28 - DDR Shared Rhodes Event Helper

Confirmed the previous downstream helper PR was merged:

- `alpha-analysis-downstream-processing` PR #51 merged at `aae32a1`.

Continued Phase 3 adapter-efficiency work with a narrow DDR helper extraction.

Branch: `codex/ddr-shared-rhodes-events`

Draft PR: https://github.com/GFooteGK1/due-diligence-reporter/pull/125

Implementation commit: `7ed5b40` (`Share DDR Rhodes event helper`)

Current state: branch pushed, draft PR open, GitHub reports merge state
`CLEAN`. No checks were reported when checked.

Changed:

- Added `src/due_diligence_reporter/rhodes_events.py` as the shared DDR
  boundary for `AutomationEvent` Rhodes note creation, owner-notification Chat
  fallback decisions, and configured Google Chat posting.
- Reused the helper from inbox document-registration failure events, report
  pipeline source-review/vendor-gate/report-summary events, and RayCon
  follow-up events.
- Preserved existing event status shapes and test patch points while removing
  duplicated note/fallback logic from the call sites.
- Added direct helper regression coverage for owner context, missing site IDs,
  Chat fallback decisioning, and partial Chat send failures.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-shared-rhodes-events-focused tests/test_rhodes_events.py tests/test_automation_event.py tests/test_report_pipeline.py tests/test_inbox_scanner.py::TestRhodesDocumentRegistration tests/test_raycon_followup.py -q
uv run ruff check src\due_diligence_reporter\rhodes_events.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\inbox_scanner.py scripts\raycon_followup.py tests\test_rhodes_events.py tests\test_report_pipeline.py tests\test_inbox_scanner.py tests\test_raycon_followup.py
uv run mypy src\due_diligence_reporter\rhodes_events.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\inbox_scanner.py
uv run python -m py_compile scripts\raycon_followup.py
uv run pytest --basetemp C:\tmp\ddr-shared-rhodes-events-broad tests/test_rhodes_events.py tests/test_automation_event.py tests/test_report_pipeline.py tests/test_inbox_scanner.py tests/test_scan_inbox_e2e.py tests/test_raycon_followup.py tests/test_raycon_runtime_state_store.py tests/test_dd_republish.py tests/test_rhodes.py -q
uv run mypy src/
git diff --check
git diff --cached --check
```

Results:

- Focused helper/event/report/inbox/RayCon suite: 113 passed.
- Ruff on touched code/tests: passed.
- Focused Mypy: no issues in 3 source files.
- Script compile: passed.
- Broader affected DDR suite: 246 passed.
- Full source Mypy: no issues in 38 source files.
- Diff checks: passed with expected Windows LF-to-CRLF warnings and the
  existing user-level ignore permission warning only.

Next:

- Wait for CI/review on PR #125.
- After merge, continue with the next remaining adapter consolidation or
  notification-only path that still lacks a Rhodes-owned record.

## 2026-05-27 - Inbox Missing-Folder Cleanup: Linked Active Rhodes Folders, Suppressed Cancelled Torrance

Context:

- Inbox manual review showed `missing_drive_folder` for Port Chester, Torrance,
  Malibu, Los Angeles Beethoven, Tulsa 421 E 11th, and Santa Clara.
- Live LocationOS records resolved for all six sites, but the active five had no
  linked Rhodes Google Drive folder. Torrance is cancelled and its Drive folder
  is under `G:\Shared drives\Education Ops\All Locations\0.Archive`.

Actions completed:

- Renamed the synced shared-drive folder
  `Alpha Los Angeles 5400 Beethoven St` to
  `Alpha Los Angeles 5401 Beethoven St`.
- Linked existing Drive folder roots in LocationOS/Rhodes:
  - Port Chester: `1KhzTP0O2-oA0ZS5JIko0LRmB3RKarch2`
  - Malibu: `1YFji_KxEGOY38jXhxxRNNeLKn0OzqNUs`
  - Tulsa 421 E 11th: `1aECCszKKUydifS6nx23fEV5LZWEn3seh`
  - Santa Clara: `1RRF-_nxBMMvdcSXZsBj-qUAx_xYGKao1`
  - Los Angeles 5401 Beethoven: `1G8fc0sX3dP83A7uMF5Bhz2pXnhRpaRJz`
- Confirmed LocationOS `driveResolveSiteFolderPath(..., "M1 - Acquire Property")`
  now resolves for all five active sites.
- Updated `src/due_diligence_reporter/inbox_scanner.py` so a matched cancelled
  Rhodes site with no Drive folder URL is skipped instead of emitted as
  `missing_drive_folder` manual review. Active matched sites without a Drive
  folder still emit manual review.
- Added regression coverage in `tests/test_inbox_scanner.py` for the cancelled
  no-folder suppression path.

Verification:

- `uv run pytest tests/test_inbox_scanner.py -q` -> `72 passed`
- `uv run ruff check src/due_diligence_reporter/inbox_scanner.py tests/test_inbox_scanner.py`
  -> `All checks passed`

Notes:

- Google Drive search now shows the renamed
  `Alpha Los Angeles 5401 Beethoven St` folder for the original folder ID that
  contained the DD report, not the empty duplicate skeleton.
- Torrance was not linked because the Rhodes site is cancelled and its folder is
  archived.

## 2026-05-27 - DDR RayCon Follow-up Rhodes Events

Confirmed the previous vendor-gate PR was merged:

- `due-diligence-reporter` PR #122 merged at `7972ec3`.

Continued Phase 4 record-completion work with a narrow RayCon follow-up
alert slice.

Branch: `codex/ddr-raycon-alert-rhodes-events`

Draft PR: https://github.com/GFooteGK1/due-diligence-reporter/pull/123

Implementation commit: `a3ad6b0` (`Record RayCon follow-up alerts in Rhodes`)

Current state: branch pushed, draft PR open, GitHub reports merge state
`CLEAN`. No checks were reported when checked.

Changed:

- Added a DDR `raycon_followup_alert` `AutomationEvent v1` builder for
  RayCon stuck-site and error follow-up items.
- `scripts/raycon_followup.py` now enriches per-site alert/error rows with
  Rhodes site ID, Drive folder, and P1 DRI context from the Rhodes site
  inventory.
- Fresh RayCon follow-up alerts now write a Rhodes site note first and mention
  the P1 DRI when Rhodes can resolve a user ID.
- Google Chat remains the fallback when the site cannot be written to Rhodes,
  the Rhodes note write fails, or no P1 DRI can be mentioned.
- RayCon error rows now use the same notification path and a message-specific
  dedupe key so repeated cron failures do not spam owners or Chat every five
  minutes.
- Updated RayCon runtime-state docs, workflow comments, and regression
  coverage.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-raycon-alert-events-focused tests/test_automation_event.py tests/test_raycon_followup.py -q
uv run ruff check src\due_diligence_reporter\automation_event.py scripts\raycon_followup.py tests\test_automation_event.py tests\test_raycon_followup.py
uv run python -m py_compile scripts\raycon_followup.py
uv run pytest --basetemp C:\tmp\ddr-raycon-alert-events-broad tests/test_automation_event.py tests/test_raycon_followup.py tests/test_raycon_runtime_state_store.py tests/test_workflow_contracts.py tests/test_dd_republish.py tests/test_rhodes.py -q
uv run mypy src/
git diff --check
```

Results:

- Focused event/RayCon suite: 60 passed.
- Focused Ruff: passed.
- Script compile: passed.
- Broader affected suite: 124 passed.
- Full source Mypy: no issues in 37 source files.
- Diff check: passed with expected Windows LF-to-CRLF warnings only.

Next:

- Wait for CI/review on PR #123.
- After merge, continue with shared Rhodes adapter extraction or another
  remaining notification-only path.

## 2026-05-27 - DDR Vendor Gate Rhodes Events

Confirmed the previous source-review PR was merged:

- `due-diligence-reporter` PR #121 merged at `c17e670`.

Continued Phase 4 record-completion work with a narrow vendor-gate alert
slice.

Branch: `codex/ddr-vendor-gate-rhodes-events`

Draft PR: https://github.com/GFooteGK1/due-diligence-reporter/pull/122

Implementation commit: `aaaeec9` (`Record vendor gate alerts in Rhodes`)

Current state: branch pushed, draft PR open, GitHub reports merge state
`CLEAN`. No checks were reported when checked.

Changed:

- Added a DDR `vendor_gate_review_required` `AutomationEvent v1` builder for
  complete-input vendor-gate failures.
- When vendor SIR, vendor Building Inspection, and RayCon Scenario JSON are all
  present but generation still fails or the generated report remains
  incomplete, the pipeline writes a Rhodes site note before Chat fallback.
- The note mentions the P1 DRI when Rhodes can resolve the owner from context
  or email.
- Google Chat remains the fallback when the site is unknown, the Rhodes note
  write fails, or no owner can be mentioned.
- Added a `vendor_gate.alert` run step so manifests show the Rhodes/Chat event
  status.
- Updated `docs/process/HOW-IT-WORKS.md`.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-vendor-gate-events-focused tests/test_automation_event.py tests/test_report_pipeline.py -q
uv run ruff check src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\report_pipeline.py tests\test_automation_event.py tests\test_report_pipeline.py
uv run mypy src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\report_pipeline.py
uv run pytest --basetemp C:\tmp\ddr-vendor-gate-events-broad tests/test_automation_event.py tests/test_report_pipeline.py tests/test_dd_republish.py tests/test_rhodes.py -q
uv run mypy src/
git diff --check
git diff --cached --check
```

Results:

- Focused event/report pipeline suite: 47 passed.
- Focused Ruff: passed.
- Focused Mypy: no issues in 2 source files.
- Broader event/report/republish/Rhodes suite: 96 passed.
- Full source Mypy: no issues in 37 source files.
- Diff checks: passed with expected Windows LF-to-CRLF warnings and the
  existing user-level ignore permission warning only.

Next:

- Wait for CI/review on PR #122.
- After merge, continue with shared Rhodes adapter extraction or review the
  remaining alert-only/manual-review paths across the three repos.

## 2026-05-27 - DDR Source Review Rhodes Events

Confirmed the previous report-outcome PR was merged:

- `due-diligence-reporter` PR #120 merged at `d14acd3`.

Continued Phase 4 record-completion work with a narrow source-read alert
slice.

Branch: `codex/ddr-source-alert-rhodes-events`

Draft PR: https://github.com/GFooteGK1/due-diligence-reporter/pull/121

Implementation commit: `d2a3582` (`Record source review alerts in Rhodes`)

Changed:

- Added a DDR `source_review_required` `AutomationEvent v1` builder for
  unreadable SIR / Building Inspection traces.
- `source.alert` now writes the source-review event to a Rhodes site note when
  the pipeline knows the site ID.
- The note mentions the P1 DRI when Rhodes can resolve the owner from context
  or email.
- Google Chat remains the fallback when the site is unknown, the Rhodes note
  write fails, or no owner can be mentioned.
- The failed `source.alert` step now carries a Rhodes-event artifact so the run
  manifest points back to the system-of-record write attempt.
- Updated `docs/process/HOW-IT-WORKS.md`.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-source-alert-events-focused2 tests/test_automation_event.py tests/test_report_pipeline.py -q
uv run ruff check src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\report_pipeline.py tests\test_automation_event.py tests\test_report_pipeline.py
uv run mypy src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\report_pipeline.py
uv run pytest --basetemp C:\tmp\ddr-source-alert-events-broad2 tests/test_automation_event.py tests/test_report_pipeline.py tests/test_dd_republish.py tests/test_rhodes.py -q
uv run mypy src/
git diff --check
git diff --cached --check
```

Results:

- Focused event/report pipeline suite: 45 passed.
- Focused Ruff: passed.
- Focused Mypy: no issues in 2 source files.
- Broader event/report/republish/Rhodes suite: 94 passed.
- Full source Mypy: no issues in 37 source files.
- Diff checks: passed with expected Windows LF-to-CRLF warnings and the
  existing user-level ignore permission warning only.

Next:

- Wait for CI/review on PR #121.
- After merge, continue with the next alert-only/manual-review path, likely the
  vendor-gate extraction failure alert, or start the shared Rhodes adapter
  extraction now that the event patterns are repeated.

## 2026-05-27 - DDR Report Outcome Rhodes Events

Confirmed the previous Drive-to-Rhodes reconciliation PR was merged:

- `due-diligence-reporter` PR #119 merged at `4244aea`.

Continued the Phase 4 record-completion work with a narrow report-outcome
slice.

Branch: `codex/ddr-rhodes-report-summary-events`

Draft PR: https://github.com/GFooteGK1/due-diligence-reporter/pull/120

Implementation commit: `3f79154` (`Record DD report outcomes in Rhodes`)

Changed:

- Added a DDR report summary `AutomationEvent v1` builder for
  `dd_report_created` and `dd_report_updated`.
- `process_site_pipeline` now writes a Rhodes site note after a report reaches
  `report_created`.
- The Rhodes note records the DD report ID/URL, run ID, trigger source for
  updates, still-open verification items, and newly closed verification items.
- The note mentions the P1 DRI when a Rhodes user can be resolved from owner
  context. If open items require a decision and no owner mention is possible,
  the same event body is posted to the configured Google Chat webhook.
- The result is stored on `PipelineResult.rhodes_report_event` and in the run
  manifest as `rhodes_report_event`.
- Updated `docs/process/HOW-IT-WORKS.md`.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-rhodes-report-events-focused tests/test_automation_event.py tests/test_report_pipeline.py -q
uv run ruff check src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\pipeline_contracts.py src\due_diligence_reporter\report_pipeline.py tests\test_automation_event.py tests\test_report_pipeline.py
uv run mypy src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\pipeline_contracts.py src\due_diligence_reporter\report_pipeline.py
uv run pytest --basetemp C:\tmp\ddr-rhodes-report-events-broad tests/test_automation_event.py tests/test_report_pipeline.py tests/test_dd_republish.py tests/test_rhodes.py -q
uv run mypy src/
git diff --check
uv run pytest --basetemp C:\tmp\ddr-rhodes-report-events-affected tests/test_automation_event.py tests/test_report_pipeline.py tests/test_dd_republish.py tests/test_rhodes.py tests/test_inbox_scanner.py -q
```

Results:

- Focused event/report pipeline suite: 43 passed.
- Focused Ruff: passed.
- Focused Mypy: no issues in 3 source files.
- Broader event/report/republish/Rhodes suite: 92 passed.
- Full source Mypy: no issues in 37 source files.
- Diff check: passed; Git emitted expected Windows LF-to-CRLF warnings only.
- Affected event/report/republish/Rhodes/inbox suite: 163 passed.

Next:

- Wait for CI/review on PR #120.
- After merge, continue with the shared Rhodes adapter extraction design, or
  the next concrete record-completion path that still posts only to
  notification surfaces instead of Rhodes.

## 2026-05-27 - Drive-to-Rhodes Document Reconciliation

Started the remaining Phase 2 Drive-to-Rhodes reconciliation slice on branch
`codex/ddr-drive-rhodes-reconcile`.

Draft PR: https://github.com/GFooteGK1/due-diligence-reporter/pull/119

Implementation commit: `f7be81d` (`Reconcile Drive documents into Rhodes`)

This handoff entry is included on the same PR branch after the implementation
commit.

Current state: branch pushed, draft PR open and mergeable. No status checks were
reported yet when checked.

Changed:

- Added `src/due_diligence_reporter/drive_rhodes_reconciliation.py`.
  The sweep loads Rhodes-linked site records, resolves each site's canonical
  `M1 - Acquire Property` folder without creating folders, classifies recognized
  M1 files, and registers missing Rhodes document links by Drive file ID.
- Registration reuses `register_rhodes_document_for_upload`, preserving the
  existing DDR -> Rhodes doc type mapping and idempotent `listDocuments`
  pre-check.
- Generated or unmapped M1 files are reported as skipped rows rather than
  forced into unsafe Rhodes document types.
- Added `scripts/drive_rhodes_reconciliation.py` with `--dry-run` and `--site`
  controls.
- Added the weekday `Drive Rhodes Reconciliation` GitHub Actions workflow and
  included it in stale mutating run cancellation/timeout contract tests.
- Updated `docs/process/HOW-IT-WORKS.md`.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-drive-rhodes-focused tests/test_drive_rhodes_reconciliation.py -q
uv run pytest --basetemp C:\tmp\ddr-drive-rhodes-broad tests/test_drive_rhodes_reconciliation.py tests/test_rhodes.py tests/test_m1_lookup.py tests/test_workflow_contracts.py tests/test_vendor_doc_sweep.py -q
uv run pytest --basetemp C:\tmp\ddr-drive-rhodes-affected tests/test_drive_rhodes_reconciliation.py tests/test_rhodes.py tests/test_m1_lookup.py tests/test_workflow_contracts.py tests/test_vendor_doc_sweep.py tests/test_inbox_scanner.py -q
uv run ruff check src\due_diligence_reporter\drive_rhodes_reconciliation.py scripts\drive_rhodes_reconciliation.py tests\test_drive_rhodes_reconciliation.py tests\test_workflow_contracts.py
uv run mypy src\due_diligence_reporter\drive_rhodes_reconciliation.py
uv run mypy src/
uv run python -m py_compile scripts\drive_rhodes_reconciliation.py
git diff --check
git diff --cached --check
```

Results:

- Focused reconciliation tests: 4 passed.
- Broader Rhodes/M1/workflow/vendor sweep tests: 33 passed.
- Affected inbox/Rhodes/M1/workflow/vendor suite: 104 passed.
- Ruff on touched code/tests: passed.
- Focused Mypy: no issues in 1 source file.
- Full source Mypy: no issues in 37 source files.
- Script compile: passed.
- Diff checks: passed; Git emitted the existing user-level ignore permission
  warning and expected Windows LF-to-CRLF warnings only.

Next:

- Wait for CI/review on PR #119.
- After merge, continue with a shared Rhodes adapter skeleton or DDR generated
  report/open-item/closed-item Rhodes summary events.

## 2026-05-27 - Firestore RayCon Runtime State

Started the next Phase 2 durable-state item on branch
`codex/ddr-firestore-raycon-runtime-state`.

Draft PR: https://github.com/GFooteGK1/due-diligence-reporter/pull/118

Current state: branch pushed, draft PR open and mergeable. No status checks were
reported yet when checked.

Changed:

- Added `raycon_runtime_state_store.py` with:
  - existing local JSON stores for `.raycon_dispatch_state.json` and
    `.raycon_followup_alerts.json`
  - optional Firestore-backed write-through stores for both state maps
  - stale remote document deletion when local keys are removed
  - safe local JSON fallback when Firestore is unconfigured or unavailable
- `scripts/raycon_followup.py` now loads/saves RayCon dispatch dedupe and
  stuck-site alert suppression through the configured stores while preserving
  the existing in-memory dict contracts.
- Production can set:
  - `RAYCON_RUNTIME_STATE_STORE=firestore`
  - `RAYCON_RUNTIME_STATE_FIRESTORE_PROJECT_ID=<project>`
  - optional `RAYCON_RUNTIME_STATE_FIRESTORE_DATABASE`
  - optional `RAYCON_RUNTIME_STATE_DISPATCH_FIRESTORE_COLLECTION`
  - optional `RAYCON_RUNTIME_STATE_ALERT_FIRESTORE_COLLECTION`
- The RayCon follow-up workflow forwards those repository variables and can
  use the existing optional `GCP_FIRESTORE_SERVICE_ACCOUNT_JSON` secret.
  Successful Firestore saves refresh the local JSON files, so the GitHub
  Actions cache remains a current fallback.
- Updated `.env.example`, workflow contract tests, and
  `docs/process/HOW-IT-WORKS.md`.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-raycon-runtime-focused tests/test_raycon_runtime_state_store.py tests/test_workflow_contracts.py tests/test_raycon_followup.py -q
uv run ruff check src\due_diligence_reporter\raycon_runtime_state_store.py scripts\raycon_followup.py tests\test_raycon_runtime_state_store.py tests\test_workflow_contracts.py
uv run mypy src/
uv run python -m py_compile scripts\raycon_followup.py
uv run pytest --basetemp C:\tmp\ddr-raycon-runtime-broad tests/test_raycon_runtime_state_store.py tests/test_raycon_followup.py tests/test_dd_republish_state_store.py tests/test_dd_republish.py tests/test_rhodes_retry_state_store.py tests/test_workflow_contracts.py -q
git diff --check
```

Results:

- Focused RayCon runtime/workflow tests: 67 passed.
- Ruff on touched code/tests: passed.
- Full source Mypy: no issues in 36 source files.
- Script compile checks: passed.
- Broader affected DDR suite: 117 passed.
- Diff check: passed, with expected Windows LF-to-CRLF warnings only.

Next:

- After merge, continue with a shared Rhodes adapter skeleton or downstream
  AutomationEvent ledger alignment.

## 2026-05-27 - Firestore DD Republish State Store

Started the next Phase 2 durable-state item on branch
`codex/ddr-firestore-republish-state`.

Current behavior in progress:

- Added `dd_republish_state_store.py` with the existing local JSON republish
  dedupe store, optional Firestore-backed write-through storage, stale remote
  document deletion, and safe local JSON fallback.
- Added `firestore_state.py` to share Firestore REST field encode/decode and
  authenticated session helpers across automation state stores.
- Refactored `rhodes_retry_state_store.py` to use the shared Firestore helpers
  without changing its external behavior.
- `scan_inbox.py`, `raycon_followup.py`, and
  `vendor_doc_republish_sweep.py` now load/save DD republish dedupe state
  through the configured store while preserving the existing in-memory dict
  contract used by `maybe_republish_dd_report`.
- Production can set `DD_REPUBLISH_STATE_STORE=firestore` and
  `DD_REPUBLISH_STATE_FIRESTORE_PROJECT_ID=<project>` plus optional database
  and collection variables.
- The three scheduled workflows forward those repository variables and can use
  the existing optional `GCP_FIRESTORE_SERVICE_ACCOUNT_JSON` secret. Successful
  Firestore saves refresh `.dd_republish_state.json`, so the GitHub Actions
  cache remains a current fallback.
- Updated `.env.example`, workflow contract tests, and
  `docs/process/HOW-IT-WORKS.md`.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-republish-store-focused tests/test_dd_republish_state_store.py tests/test_rhodes_retry_state_store.py tests/test_workflow_contracts.py -q
uv run ruff check src\due_diligence_reporter\firestore_state.py src\due_diligence_reporter\dd_republish_state_store.py src\due_diligence_reporter\rhodes_retry_state_store.py scripts\scan_inbox.py scripts\raycon_followup.py scripts\vendor_doc_republish_sweep.py tests\test_dd_republish_state_store.py tests\test_rhodes_retry_state_store.py tests\test_workflow_contracts.py
uv run mypy src/
uv run python -m py_compile scripts\scan_inbox.py scripts\raycon_followup.py scripts\vendor_doc_republish_sweep.py
uv run pytest --basetemp C:\tmp\ddr-republish-store-broad tests/test_dd_republish_state_store.py tests/test_rhodes_retry_state_store.py tests/test_dd_republish.py tests/test_raycon_followup.py tests/test_inbox_scanner.py tests/test_vendor_doc_sweep.py tests/test_workflow_contracts.py -q
git diff --check
```

Results:

- Focused state/workflow tests: 20 passed.
- Ruff on touched code/tests: passed.
- Full source Mypy: no issues in 35 source files.
- Script compile checks: passed.
- Broader affected DDR suite: 183 passed.
- Diff check: passed, with expected Windows LF-to-CRLF warnings only.

Note:

- A targeted mypy invocation that included `scripts/*.py` directly hit the
  repo's duplicate-module import shape
  (`src.due_diligence_reporter.dd_republish` vs
  `due_diligence_reporter.dd_republish`). The repo-standard `uv run mypy src/`
  is clean, and the touched scripts compile.

Next:

- Commit, push, and open the DDR PR.
- Email-router PR #22 was still open/draft when checked from GitHub, despite
  the user saying it had merged. Do not treat it as merged until GitHub reports
  a merge commit.

## 2026-05-27 - DDR AutomationEvent Contract Module

Started the next Phase 2 event-contract slice on branch
`codex/ddr-automation-event-contract`.

Current behavior in progress:

- Added `src/due_diligence_reporter/automation_event.py` with a canonical
  `AutomationEvent` dataclass and `render_automation_event_note(...)`.
- Moved document-registration failure event construction out of
  `inbox_scanner.py` into `build_document_registration_failed_event(...)`.
- The rendered `AutomationEvent v1` note now carries shared contract fields:
  source system, source ID, event kind, site ID, decision-required status,
  requested decision, mutation status, retry state, artifact IDs, and
  created-at timestamp.
- DDR-specific details remain in the note body: owner, DDR/Rhodes doc types,
  milestone, reason, Drive file name, original filename, Gmail subject, Drive
  URL, and error.
- Existing Rhodes note and Google Chat fallback behavior is unchanged.

Verification in progress:

```powershell
uv run pytest --basetemp C:\tmp\ddr-automation-event-tests tests/test_automation_event.py tests/test_inbox_scanner.py::TestRhodesDocumentRegistration -q
uv run ruff check src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\inbox_scanner.py tests\test_automation_event.py tests\test_inbox_scanner.py
uv run mypy src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\inbox_scanner.py
uv run pytest --basetemp C:\tmp\ddr-automation-event-broad tests/test_automation_event.py tests/test_inbox_scanner.py tests/test_scan_inbox_e2e.py tests/test_rhodes.py -q
uv run mypy src/
```

Results:

- Focused event/scanner tests: 8 passed.
- Focused Ruff: passed.
- Focused Mypy: no issues in 2 source files.
- Broader inbox/Rhodes suite: 96 passed.
- Full source Mypy: no issues in 33 source files.

## 2026-05-27 - Firestore Rhodes Registration Retry State

Started the next Rhodes automation Phase 2 slice on branch
`codex/ddr-firestore-rhodes-retry-state`.

Current behavior in progress:

- Rhodes document-registration retry state now has a storage boundary:
  `JsonRhodesRetryStateStore` for the existing local file and
  `FirestoreRhodesRetryStateStore` for durable scheduled runs.
- `scripts/scan_inbox.py` loads/saves retry state through
  `build_rhodes_retry_state_store(...)`.
- JSON remains the default for local/dev and as the fallback when Firestore is
  not configured or unavailable.
- Firestore mode is opt-in with:
  - `RHODES_RETRY_STATE_STORE=firestore`
  - `RHODES_RETRY_STATE_FIRESTORE_PROJECT_ID=<project>`
  - optional `RHODES_RETRY_STATE_FIRESTORE_DATABASE`
  - optional `RHODES_RETRY_STATE_FIRESTORE_COLLECTION`
- Firestore documents include the retry key and the retry entry payload, so
  retry attempts, Rhodes note IDs, owner-notification metadata, and Google Chat
  fallback dedupe survive runner changes.
- Successful Firestore saves also refresh the local JSON fallback so the
  existing GitHub Actions cache never lags behind the durable store.
- `inbox-scan.yml` forwards the optional Firestore repository variables and
  writes `GCP_FIRESTORE_SERVICE_ACCOUNT_JSON` to
  `GOOGLE_APPLICATION_CREDENTIALS` when that secret is present. Without it, the
  workflow keeps the existing local JSON/cache fallback.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-retry-store-final tests/test_rhodes_retry_state_store.py tests/test_workflow_contracts.py -q
uv run ruff check src\due_diligence_reporter\rhodes_retry_state_store.py src\due_diligence_reporter\inbox_scanner.py scripts\scan_inbox.py tests\test_rhodes_retry_state_store.py tests\test_workflow_contracts.py
uv run mypy src/
uv run pytest --basetemp C:\tmp\ddr-retry-store-broad-final tests/test_rhodes_retry_state_store.py tests/test_inbox_scanner.py tests/test_scan_inbox_e2e.py tests/test_rhodes.py tests/test_workflow_contracts.py -q
git diff --check
```

Results:

- Focused store/workflow tests: 13 passed.
- Ruff on touched code/tests: passed.
- Full source Mypy: no issues in 32 source files.
- Broader inbox/Rhodes/workflow suite: 107 passed.
- Diff check: no whitespace errors; only expected Windows LF-to-CRLF warnings.
## 2026-05-27 - Lexington DDR Follow-up: Stale Automation and Stronger Summary Splitter

Follow-up after `Alpha Lexington 92 Hayden Ave DD Report - 05_27_2026.docx`
arrived with Miami-style dense executive-summary paragraphs after PR #112 had
merged.

Findings:

- The Lexington DOCX executive-summary fields were structurally single
  paragraphs: labels were bold, but there were no support bullet paragraphs for
  Education Regulatory Approval, Occupancy path, Permit Timeline, or
  Construction Timeline.
- Current `main` would split the exact Lexington payload into answer/support
  lines, so the report did not run through the merged renderer behavior.
- PR #112 was merged at `2026-05-27T15:36:05Z`; the MCP Hive publish workflow
  succeeded at `2026-05-27T15:36:21Z` on `6d59b14`.
- Two mutating automation runs were still in progress on old SHA `da3ce77`
  after the merge: `Inbox Scan` run `26516966816` and `Vendor Doc Republish
  Sweep` run `26518236682`. Both had started before PR #112 merged, which
  explains how a post-merge report could still be generated by old code.

Additional fixes in progress on branch
`codex/ddr-exec-summary-lexington-followup`:

- Strengthened `_summary_display_lines()` so any one-line multi-sentence
  executive-summary field splits into answer/support lines, not only long
  paragraphs.
- Protected inch/foot/square-foot abbreviations and common legal/address
  abbreviations from accidental sentence splitting.
- Normalized gap labels such as `[Not found - RayCon scenario pending]. ...`
  so the answer line does not keep a trailing period.
- Tightened `docs/prompts/prompt_v4.md` with `Never pack support facts into one
  paragraph.`
- Added workflow guardrails: MCP Hive publish now cancels in-progress mutating
  workflow runs on older SHAs, and Inbox Scan / Vendor Doc Republish Sweep have
  `timeout-minutes: 60`.

Verification completed:

```powershell
uv run pytest tests/test_google_doc_builder.py tests/test_prompt_contract.py tests/test_workflow_contracts.py
uv run ruff check src\due_diligence_reporter\google_doc_builder.py tests\test_google_doc_builder.py tests\test_prompt_contract.py tests\test_workflow_contracts.py
uv run mypy src/
```

Results:

- Focused builder/prompt/workflow tests: 79 passed.
- Focused Ruff: all checks passed.
- Full source Mypy: no issues in 31 source files.

## 2026-05-27 - DDR Rhodes Registration Failure Events

Started the next Rhodes automation Phase 2 slice on branch
`codex/ddr-rhodes-registration-events`.

Draft PR: https://github.com/GFooteGK1/due-diligence-reporter/pull/114

Current behavior:

- When inbox-filed document registration fails, DDR still preserves the Drive
  filing and records retry state as before.
- After the original attempt plus two retries, retry exhaustion now writes an
  `AutomationEvent v1` note to the matched Rhodes site.
- The Rhodes note mentions the P1 DRI when `p1_assignee_user_id` is present, or
  resolves a Rhodes user ID from the P1 DRI email before adding the note.
- If no owner can be notified in Rhodes, or the note write fails, DDR posts the
  same event body to the configured Google Chat webhook.
- Retry state stores `rhodes_failure_note_id` and Chat-notification metadata so
  repeated scans do not duplicate notes or Chat alerts.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-rhodes-events-tests tests/test_inbox_scanner.py::TestRhodesDocumentRegistration tests/test_rhodes.py -q
uv run ruff check src\due_diligence_reporter\inbox_scanner.py src\due_diligence_reporter\rhodes.py tests\test_inbox_scanner.py tests\test_rhodes.py
uv run mypy src\due_diligence_reporter\inbox_scanner.py src\due_diligence_reporter\rhodes.py
uv run pytest --basetemp C:\tmp\ddr-rhodes-events-broad tests/test_inbox_scanner.py tests/test_rhodes.py tests/test_scan_inbox_e2e.py -q
git diff --check
```

Results:

- Focused Rhodes/inbox tests: 19 passed.
- Focused Ruff: all checks passed.
- Focused Mypy: no issues in 2 source files.
- Broader inbox/Rhodes/e2e scanner suite: 94 passed.
- Diff check: no whitespace errors; only expected Windows LF-to-CRLF warnings.

## 2026-05-27 - DDR Executive Summary Answer-First Rendering

Implemented the Boston-style executive summary guardrail on `main`.

Current behavior:

- `docs/prompts/prompt_v4.md` now explicitly requires every executive-summary
  field to use one answer line followed by plain-line support facts. The prompt
  metadata was updated to `Last Updated: 2026-05-27`.
- `src/due_diligence_reporter/google_doc_builder.py` now normalizes long
  one-paragraph executive-summary fields into answer/support lines before
  rendering. This preserves the Boston pattern even when the agent sends a
  Miami-style multi-sentence paragraph.
- Gap labels such as `[Not found - RayCon scenario pending]` stay on the answer
  line; the explanatory sentences render as support bullets below.
- Added builder tests for one-paragraph support bulleting and gap-label answer
  preservation, plus updated the prompt contract test.

Verification:

```powershell
uv run pytest tests/test_google_doc_builder.py tests/test_prompt_contract.py
uv run ruff check src\due_diligence_reporter\google_doc_builder.py tests\test_google_doc_builder.py tests\test_prompt_contract.py
uv run mypy src/
```

Results:

- Focused builder/prompt tests: 72 passed.
- Focused Ruff on touched files: all checks passed.
- Full source Mypy: no issues in 31 source files.
- Raw `uv run pytest` is blocked on current checkout by inaccessible pytest
  cache/temp directories. A broad rerun with cache folders ignored collected
  963 tests and showed unrelated existing failures in assignment and
  sender-filter tests plus temp-permission setup errors.
- Repo-level `uv run ruff check .` still reports unrelated pre-existing lint in
  `scripts/reprocess_mislabeled.py`, `tests/test_cds_verification.py`,
  `tests/test_opening_plan.py`, and `tests/test_sender_filter.py`.

## 2026-05-27 - Rhodes Roster Performance: Hydration and Callback Fast Path

Started the deferred performance slice from the DDR remediation plan on branch
`codex/rhodes-performance-roster`.

Current behavior:

- `list_rhodes_site_records()` now skips the per-site `getSite` hydration call
  when a `listSites` summary already includes the fields full-roster callers
  need: site ID, name, address, Drive folder URL, and P1 owner context.
- `list_rhodes_site_records(site_ids=[...])` can load specific Rhodes site IDs
  directly with `getSite`, bypassing the full `listSites` inventory.
- `scripts/raycon_followup.py` uses the direct site-ID path for callback runs
  (`--site-id`) before falling back to the full inventory. The fallback remains
  in place so legacy callback values that are Drive folder IDs can still match
  through the existing full-roster identity check.
- RayCon per-site processing now lists the M1 folder once and reuses that file
  list for Block Plan detection, `raycon_scenario.json` lookup, and published
  RayCon Scenario Doc freshness checks.
- Full-roster behavior remains unchanged for daily DD, vendor sweeps, inbox
  scans, and RayCon cron sweeps when no callback site ID is provided.

Verification completed so far:

```powershell
uv run pytest --basetemp C:\tmp\ddr-performance-roster tests/test_rhodes.py tests/test_raycon_followup.py tests/test_daily_dd_check.py tests/test_vendor_doc_sweep.py -q
uv run pytest --basetemp C:\tmp\ddr-performance-raycon tests/test_raycon_client.py tests/test_raycon_followup.py tests/test_rhodes.py -q
uv run pytest --basetemp C:\tmp\ddr-performance-affected tests/test_rhodes.py tests/test_raycon_followup.py tests/test_raycon_client.py tests/test_daily_dd_check.py tests/test_vendor_doc_sweep.py tests/test_scan_inbox_e2e.py tests/test_inbox_scanner.py -q
uv run ruff check src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\raycon_client.py scripts\raycon_followup.py tests\test_rhodes.py tests\test_raycon_followup.py tests\test_raycon_client.py tests\test_daily_dd_check.py tests\test_vendor_doc_sweep.py
uv run mypy src/
git diff --check
```

Results:

- Focused roster/RayCon/daily/vendor tests: 68 passed.
- Focused RayCon client/follow-up/Rhodes tests: 122 passed.
- Broader affected inbox/Rhodes/RayCon suite: 207 passed.
- Focused Ruff: all checks passed.
- Full source Mypy: no issues in 31 source files.
- `git diff --check`: no whitespace errors; only Windows LF-to-CRLF warnings.

## 2026-05-27 - DDR Remediation: Workflow Safety, Rhodes Retry, and Source-of-Truth Fixes

Implemented the first remediation pass from the `$check` / `$qplan` review on
branch `codex/inbox-rhodes-auto-resolve`.

Current behavior:

- Workflow dispatch inputs are passed through environment variables and Bash
  argv arrays in `daily-dd-check.yml`, `vendor-doc-republish-sweep.yml`, and
  `reprocess-mislabeled.yml`; run blocks no longer interpolate manual inputs
  directly into shell command strings.
- MCP Hive publish now verifies `ANTHROPIC_API_KEY` and `RHODES_API_KEY`,
  advertises the runtime env names in the custom payload, and refuses to package
  generated secret/state files such as `.env`, `.gcp-saved-tokens.json`,
  `credentials/`, `.dd_republish_state.json`,
  `.rhodes_registration_retry_state.json`, and RayCon runtime state.
- Inbox Drive filing remains primary. Rhodes document registration failures are
  recorded in `.rhodes_registration_retry_state.json`, retried on later scans,
  and only become manual review after the original attempt plus two retries.
- Inbox scan workflow restores/saves the Rhodes registration retry state via
  Actions cache so scheduled runs can retry failed Rhodes links without
  re-uploading the Drive file.
- `scripts/daily_dd_check.py` now uses active Rhodes site records as the daily
  roster, passes `site_address`, Rhodes site ID, P1 owner context, and created
  date into `process_site_pipeline`, and fails closed when the Rhodes roster is
  unavailable.
- Open-question closures require the triggering source event type to match the
  question's expected source type.
- Vendor provenance errors surface as site error rows instead of being reported
  as `no_core_sources_found`.
- DD republish dedup state is written only after `report_created`; failed or
  incomplete reruns retry on a later scan instead of being suppressed.
- RayCon scenario publishing now requires Rhodes site identity and address, and
  compares parsed Drive modified timestamps instead of raw strings.
- `.env.example` and `docs/process/HOW-IT-WORKS.md` now list current
  Rhodes/Anthropic/inbox-label requirements and remove stale Pricing/template
  requirements.

Verification completed:

```powershell
uv run pytest --basetemp C:\tmp\ddr-remediation-focused tests/test_workflow_contracts.py tests/test_docs_env_contract.py tests/test_daily_dd_check.py tests/test_inbox_scanner.py::TestRhodesDocumentRegistration tests/test_open_questions.py tests/test_vendor_doc_sweep.py tests/test_dd_republish.py::TestVendorSIRArrival tests/test_raycon_followup.py::TestSafetyNetDispatch -q
uv run pytest --basetemp C:\tmp\ddr-remediation-affected tests/test_rhodes.py tests/test_inbox_scanner.py tests/test_scan_inbox_e2e.py tests/test_daily_dd_check.py tests/test_workflow_contracts.py tests/test_docs_env_contract.py tests/test_open_questions.py tests/test_vendor_doc_sweep.py tests/test_dd_republish.py tests/test_raycon_client.py tests/test_raycon_followup.py -q
uv run ruff check src\due_diligence_reporter\inbox_scanner.py src\due_diligence_reporter\open_questions.py src\due_diligence_reporter\vendor_doc_sweep.py src\due_diligence_reporter\dd_republish.py scripts\scan_inbox.py scripts\daily_dd_check.py scripts\raycon_followup.py tests\test_workflow_contracts.py tests\test_docs_env_contract.py tests\test_daily_dd_check.py tests\test_inbox_scanner.py tests\test_open_questions.py tests\test_vendor_doc_sweep.py tests\test_dd_republish.py tests\test_raycon_followup.py
uv run mypy src/
git diff --check
```

Results:

- Focused remediation tests: 35 passed.
- Affected suite: 246 passed.
- Focused Ruff: all checks passed.
- Mypy: no issues in 31 source files.
- `git diff --check`: no whitespace errors; only Windows LF-to-CRLF warnings.

Deferred:

- Portfolio/Rhodes N+1 and full-roster performance cleanup is intentionally
  out of scope for this branch per Greg's direction.

## 2026-05-27 - RayCon Follow-up Rhodes Site Address Source

Updated PR 107 after merging `origin/main` into
`codex/rhodes-drive-folder-context`; the only merge conflict was in
`HANDOFF.md` and was resolved by keeping the newer Rhodes document-registration
validation details from `main`.

Current behavior:

- `scripts/raycon_followup.py` now loads active Rhodes site records with
  `list_rhodes_site_records()` and uses those records as the primary RayCon
  follow-up site inventory.
- RayCon dispatch summaries now carry Rhodes site ID, linked Drive folder ID,
  Drive folder URL, and site address, so failed-scenario retries can pass the
  required `site_address` into `post_raycon_job`.
- Drive root folder scanning remains as a fallback when Rhodes inventory is
  unavailable or returns no Drive-linked sites, but fallback folder-derived
  records still have no address and therefore fail closed before dispatch.
- RayCon callback scoping now matches either the Rhodes site ID or the linked
  Drive folder ID.
- `.github/workflows/raycon-followup.yml` now fails fast when
  `RHODES_API_KEY` is absent, matching the new Rhodes-backed address contract.

Verification completed:

```powershell
uv run pytest --basetemp C:\tmp\ddr-pytest-raycon-address tests/test_raycon_followup.py -q
uv run ruff check scripts/raycon_followup.py tests/test_raycon_followup.py
uv run pytest --basetemp C:\tmp\ddr-pytest-raycon-rhodes tests/test_raycon_followup.py tests/test_rhodes.py tests/test_vendor_doc_sweep.py -q
git diff --check
```

Results:

- RayCon follow-up tests: 48 passed.
- Focused RayCon/Rhodes/vendor sweep tests: 60 passed.
- Focused Ruff: all checks passed.
- `git diff --check`: no whitespace errors; only Windows LF-to-CRLF warnings.

## 2026-05-26 - Rhodes Document Registration on Inbox Upload

Implemented the arrival-time Rhodes document linking path for inbox-filed DD
source documents.

Current behavior:

- `RhodesClient` now wraps `listDocuments`, `registerDocument`, and
  Drive-file dedup lookup through the existing Rhodes MCP JSON-RPC transport.
- `register_rhodes_document_for_upload` maps DDR inbox doc types to Rhodes:
  `sir -> siteInvestigationReport`, `building_inspection ->
  propertyConditionAssessment`, `block_plan -> floorPlan`, and `isp -> other`.
  All four are associated with the `acquireProperty` milestone.
- Inbox uploads now attach `uploads[].rhodes_registration` after a successful
  Drive upload. This is non-blocking: Rhodes failures are recorded on the
  upload row but do not undo the Drive filing or email processing.
- `build_scan_summary` includes Rhodes registration counts and shows failed
  Rhodes registration detail under the affected upload.
- Inbox manual-review rows now carry explicit reason codes so high-confidence
  items are reviewable by cause, not just by classifier confidence.
- `docs/process/HOW-IT-WORKS.md` documents Rhodes document links as part of the
  system-of-record contract and records the DDR-to-Rhodes document map.

Verification completed:

```powershell
uv run pytest tests/test_rhodes.py tests/test_inbox_scanner.py tests/test_scan_inbox_e2e.py -q
uv run ruff check src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\inbox_scanner.py tests\test_rhodes.py tests\test_inbox_scanner.py
uv run mypy src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\inbox_scanner.py
uv run mypy src/
```

Results:

- Focused Rhodes/inbox scanner/e2e tests: 84 passed.
- Focused Ruff: all checks passed.
- Targeted mypy for touched source files: no issues.
- Full source mypy: no issues in 31 source files.

Broader validation notes:

- `uv run pytest` still fails during collection on inaccessible Windows pytest
  cache/temp directories (`pytest-cache-files-o55d_4rl`,
  `tests/_tmp/pytest-cache-files-lhmtb2lz`).
- `uv run pytest tests --ignore=tests/_tmp --basetemp .pytest-tmp -q` runs the
  suite but shows unrelated existing failures: 11 assignment API/signature
  failures in `tests/test_assignment.py` and 2 `test_sender_filter.py` failures
  patching a non-existent `inbox_scanner.build_site_summary` alias.
- `uv run ruff check .` still reports unrelated baseline lint in
  `scripts/reprocess_mislabeled.py`, `tests/test_cds_verification.py`,
  `tests/test_opening_plan.py`, and `tests/test_sender_filter.py`.

## 2026-05-26 - Active DDR Open-Question Closure Workflow

Implemented the partial-first, republish-in-place workflow for first-round DDRs.

Current behavior:

- `verification.open_items` is now converted into structured open-question
  state with stable IDs, affected DDR field, expected source type, created run,
  and closure metadata.
- Pipeline manifests now carry `source_event`, `open_questions`,
  `closed_open_questions`, and `republish_summary` outside the DDR body.
- DDR body rendering remains unchanged: only `Open Items to Verify` is visible;
  internal IDs, fingerprints, closure metadata, and source-event state do not
  render into the report.
- `dd_republish` now supports all five core source reasons:
  `vendor_sir`, `building_inspection`, `raycon_scenario`,
  `e_occupancy_report`, and `school_approval_report`.
- A closure is recorded only after a validated `report_created` rerun and only
  when a prior open item is absent from the updated report data.
- `RepublishOutcome` now returns run ID, manifest path, trigger source event,
  still-open items, and closed items.
- `vendor_doc_republish_sweep.py` is the new scheduled/script entrypoint for
  active source sweeps. It reads active Rhodes site records, scans linked Drive
  roots/M1 folders for the five core source docs, fingerprints each source by
  Drive file ID plus modified time, and calls the existing shared republish
  path with `force_regenerate=True`.
- Inbox and RayCon workflows now write Rhodes and Anthropic env vars needed for
  in-place DDR updates.
- `list_rhodes_site_records` now returns active Rhodes site records shaped for
  inbox matching and source sweeps, including Drive folder and P1 DRI context.
- Prompt/process docs now document Rhodes as source of truth, first-round
  readiness as `SIR found AND no existing DDR`, structured open-question state,
  and the active source sweep.

Verification completed:

```powershell
uv run pytest --basetemp C:\tmp\ddr-pytest-active-closure-2 tests/test_dd_republish.py tests/test_inbox_scanner.py tests/test_report_pipeline.py tests/test_completeness.py tests/test_google_doc_builder.py tests/test_open_questions.py tests/test_vendor_doc_sweep.py tests/test_rhodes.py tests/test_pipeline_contracts.py
uv run pytest --basetemp C:\tmp\ddr-pytest-docs-active tests/test_prompt_contract.py tests/test_dd_output_fixes.py tests/test_report_schema.py
uv run ruff check src\due_diligence_reporter\open_questions.py src\due_diligence_reporter\vendor_doc_sweep.py src\due_diligence_reporter\dd_republish.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\m1_lookup.py scripts\vendor_doc_republish_sweep.py scripts\scan_inbox.py tests\test_open_questions.py tests\test_vendor_doc_sweep.py tests\test_dd_republish.py tests\test_rhodes.py
uv run mypy src/
```

Results:

- Focused pipeline/inbox/builder/open-question sweep: 246 passed.
- Prompt/schema/output regression suite: 145 passed.
- Ruff: all checks passed.
- Mypy: no issues in 31 source files.
- A first pytest attempt without `--basetemp` hit the known Windows temp-folder
  permission issue at `C:\Users\foote\AppData\Local\Temp\pytest-of-foote`; the
  rerun with `C:\tmp` basetemp passed.

## 2026-05-26 - Vendor SIR M1 Acquire Property Folder Routing

Fixed the shared M1 Drive-folder resolver so vendor SIR uploads prefer the
site-specific `M1 - Acquire Property` milestone folder instead of an arbitrary
generic `M1` folder.

Current behavior:

- `scripts/scan_inbox.py` now loads active Rhodes site records before scanning
  Gmail and passes those records into `scan_inbox`, so site matching uses
  Rhodes site identity and the linked Rhodes Google Drive root folder URL.
- `_resolve_m1_folder` now prefers folder names that represent M1 Acquire
  Property, including the supplied `M1-Aquiring Property` spelling variant,
  before falling back to legacy generic `M1` folders.
- Inbox uploads pass `allow_legacy_fallback=False`, so vendor SIR / BI / ISP /
  Block Plan filing creates or uses the Acquire Property milestone folder
  instead of continuing to write to a legacy plain `M1` folder.
- When the M1 folder is missing and the caller is allowed to create it, the
  resolver now creates `M1 - Acquire Property` instead of plain `M1`.
- The server-side skill-report publisher now uses the same folder selection
  helper, so Drive-published support docs do not recreate the old plain-M1
  behavior.
- Read-only callers still pass `create_if_missing=False` and do not create any
  Drive folders.

Verification completed:

```powershell
uv run pytest --basetemp C:\tmp\ddr-pytest-m1-lookup tests/test_m1_lookup.py
uv run pytest --basetemp C:\tmp\ddr-pytest-m1-vendor-sir-full tests/test_m1_lookup.py tests/test_inbox_scanner.py
uv run pytest --basetemp C:\tmp\ddr-pytest-rhodes-inbox tests/test_rhodes.py tests/test_scan_inbox_e2e.py tests/test_m1_lookup.py tests/test_inbox_scanner.py
uv run ruff check src/due_diligence_reporter/m1_lookup.py src/due_diligence_reporter/inbox_scanner.py src/due_diligence_reporter/server.py tests/test_m1_lookup.py tests/test_inbox_scanner.py
uv run ruff check scripts/scan_inbox.py src/due_diligence_reporter/rhodes.py src/due_diligence_reporter/m1_lookup.py src/due_diligence_reporter/inbox_scanner.py src/due_diligence_reporter/server.py tests/test_rhodes.py tests/test_scan_inbox_e2e.py tests/test_m1_lookup.py tests/test_inbox_scanner.py
uv run mypy src/
```

Results:

- Resolver tests: 5 passed.
- Full inbox scanner path plus resolver tests: 67 passed.
- Rhodes/inbox scanner path plus resolver tests: 82 passed.
- Ruff: all checks passed.
- Mypy: no issues in 30 source files.

## 2026-05-26 - Rhodes Drive Folder and REBL Context for DDR Runs

Fixed the DDR live-run path so Drive folder and address context can come from
Rhodes instead of only from the user's prompt, and so the required REBL Site ID
is resolved deterministically from the site address.

Current behavior:

- `lookup_rhodes_site_owner` still returns P1 DRI fields, and now also resolves
  the linked Rhodes Google Drive root folder when present.
- The lookup returns `drive_folder_id`, `drive_folder_url`,
  `drive_folder_status`, and `meta.drive_folder_url` /
  `site.drive_folder_url` report-data fields.
- The lookup also returns Rhodes address fields into `site.address` /
  `site.site_address` so downstream report creation has a deterministic address
  source.
- `run_dd_report_agent` now tells the agent to use Rhodes when the request omits
  a Drive folder URL. Once Rhodes returns `drive_folder_url`, later
  `list_drive_documents`, skill publishing, and `create_dd_report` tool calls
  are canonicalized to that URL.
- `create_dd_report` now accepts `site_address`, and normalization uses that
  address to resolve `meta.rebl_site_id` / `sources.rebl_link` even if the agent
  omits REBL fields from `report_data`.
- `process_site_pipeline` now tries Rhodes before readiness when
  `drive_folder_url` is missing. If Rhodes does not return a linked folder, the
  pipeline blocks with a clear setup message instead of letting the agent search
  or fail ambiguously.
- Prompt V4 now says a missing user-supplied Drive URL should be resolved from
  Rhodes, that DDR publishing should stop when the Rhodes site folder is not
  linked/provisioned, and that `site_address` should be passed to
  `create_dd_report` so REBL Site ID is builder-owned.

Live data finding:

- `Alpha Los Angeles 5400 Beethoven St` resolves in Rhodes to
  `k9798fdj3vmy08sce06nhe167n874mvh`.
- The site has P1 DRI `Devin Bates <devin.bates@trilogy.com>`.
- LocationOS Drive resolution currently returns:
  `Site "Alpha Los Angeles 5400 Beethoven St" has no Google Drive folder. Use driveProvisionSiteFolders to create one first.`
- To unblock the live test, link the existing Beethoven Drive folder to this
  Rhodes site or provision site folders from Rhodes, then rerun.

Verification completed:

```powershell
uv run pytest --basetemp C:\tmp\ddr-pytest-rhodes-rebl tests/test_rhodes.py tests/test_report_pipeline.py tests/test_prompt_contract.py tests/test_dd_output_fixes.py tests/test_report_schema.py
uv run ruff check src/due_diligence_reporter/rhodes.py src/due_diligence_reporter/report_pipeline.py src/due_diligence_reporter/server.py docs/prompts/prompt_v4.md tests/test_rhodes.py tests/test_report_pipeline.py tests/test_prompt_contract.py tests/test_dd_output_fixes.py
uv run mypy src/
```

Results:

- Focused pytest: 187 passed.
- Ruff: all checks passed.
- Mypy: no issues in 29 source files.

## 2026-05-26 - Greg Edits DDR V4 Formatting Contract

Adopted `Alpha Los Angeles 5400 Beethoven St DD Report - Greg Edits.docx` as
the first-round V4 DDR formatting contract and moved enforcement into the
builder plus regression tests.

Current behavior:

- Executive summary now renders Greg's structure: question line, direct answer,
  then labeled fields for `Zoning`, `Education Regulatory Approval`,
  `Occupancy path`, `Permit Timeline`, and `Construction Timeline`.
- Executive-summary labels are bolded separately from values. Continuation
  lines inside a field render as support bullets under that field.
- First-round `Supporting Notes` renders only `Open Items to Verify`.
  Source-quality, lease-condition, and trade-off sections are still accepted as
  compatibility/internal values but do not render in the first-round body.
- `Source Notes` now renders after the `Referenced Reports` table as small,
  one-line notes with no bullets.
- Default school type is now `K-8 Private (Alpha School model)` unless a sourced
  value overrides it.
- Missing source-link gaps now use Greg's wording for building inspection and
  E-Occupancy report gaps.
- `Site Name / Address` uses the full canonical site name supplied by the
  pipeline/request instead of trusting a shortened agent-provided `meta.site_name`.
- Live source/docs grep is clean for removed first-round labels, `Report Trace`,
  `sources.trace_link`, dashboard publishing text, and Wrike references.

Verification completed:

```powershell
uv run ruff check src/due_diligence_reporter/google_doc_builder.py docs/prompts/prompt_v4.md tests/test_google_doc_builder.py tests/test_prompt_contract.py
uv run mypy src/
uv run pytest --basetemp C:\tmp\ddr-pytest-greg-format tests/test_google_doc_builder.py tests/test_report_schema.py tests/test_prompt_contract.py tests/test_dd_output_fixes.py
git diff --check
rg -n "Source Quality Notes|Lease Conditions|Trade-Offs and Deficiencies|Report Trace|sources\.trace_link|dashboard publishing|Wrike|wrike" src/due_diligence_reporter docs/prompts/prompt_v4.md docs/templates/Site_DD_Report_Template_V4.md
```

Results:

- Ruff: all checks passed.
- Mypy: no issues in 29 source files.
- Focused pytest: 211 passed.
- Diff whitespace check: clean, with only existing CRLF conversion warnings.
- Final live source/docs grep: no matches.

## 2026-05-26 - Dashboard and Drive Trace Artifact Removal

Removed the live dashboard publishing path and the extra Drive-published
diagnostic artifacts from DDR generation.

Current behavior:

- `process_site_pipeline` no longer runs `publish.dashboard`.
- `create_dd_report` no longer writes dashboard JSON or a Report Trace JSON.
- Pipeline manifests are saved only to local `.ddr-runs`; Drive manifest upload
  was removed.
- Pipeline trace data remains available as local run diagnostics only; it is not
  uploaded as a separate Drive file.
- `sources.trace_link` and the `Report Trace` source-link row were removed from
  the report schema, prompt, builder, and templates.
- Inbox scan no longer updates dashboard readiness state.
- Legacy dashboard modules, scripts, workflows, and tests were removed from the
  repo.
- Legacy `Report Trace` JSON filenames are ignored by the classifier and are not
  exposed to the report agent as usable site-folder artifacts.

Verification completed:

```powershell
uv run mypy src/
uv run ruff check src/due_diligence_reporter/classifier.py src/due_diligence_reporter/config.py src/due_diligence_reporter/google_doc_builder.py src/due_diligence_reporter/inbox_scanner.py src/due_diligence_reporter/pipeline_quality.py src/due_diligence_reporter/provenance.py src/due_diligence_reporter/raycon_client.py src/due_diligence_reporter/rebl.py src/due_diligence_reporter/report_pipeline.py src/due_diligence_reporter/report_schema.py src/due_diligence_reporter/retry.py src/due_diligence_reporter/risk_flags.py src/due_diligence_reporter/server.py tests/test_report_pipeline.py tests/test_dd_output_fixes.py tests/test_google_doc_builder.py tests/test_report_schema.py tests/test_prompt_contract.py tests/test_permit_history.py tests/test_rebl.py tests/test_pipeline_contracts.py tests/test_inbox_scanner.py tests/test_classifier_keywords.py tests/test_provenance.py tests/test_risk_flags.py
uv run pytest --basetemp C:\tmp\ddr-pytest-dashboard-trace-removal tests/test_classifier_keywords.py tests/test_dd_output_fixes.py tests/test_report_pipeline.py tests/test_google_doc_builder.py tests/test_report_schema.py tests/test_prompt_contract.py tests/test_inbox_scanner.py tests/test_provenance.py tests/test_risk_flags.py
git diff --check
rg -n "publish_to_dashboard|dashboard_|DASHBOARD_PUBLISH|dd-dashboard|sources\.trace_link|trace\.save|manifest\.upload|publish\.dashboard|_build_report_trace_data|_save_pipeline_trace|Report Trace|report_trace" src scripts .github docs .env.example
rg -n "Wrike|wrike" src scripts docs .github .env.example
```

Results:

- Mypy: no issues in 29 source files.
- Targeted ruff: all checks passed.
- Focused pytest: 406 passed.
- Diff whitespace check: clean.
- Final live-path grep: no matches in `src`, `scripts`, `.github`, `docs`, or
  `.env.example`.
- Full `uv run ruff check .` was attempted and still reports unrelated existing
  lint findings in broad test/script files; those were not part of this cleanup.

## 2026-05-26 - Beethoven V4 Live Rerun and Source-Selection Guards

Re-ran the Beethoven first-round DDR flow against:

- Site: `Alpha Los Angeles 5400 Beethoven St`
- Address: `5400 Beethoven St, Los Angeles, CA 90066`
- Drive folder: `https://drive.google.com/drive/folders/1G8fc0sX3dP83A7uMF5Bhz2pXnhRpaRJz?usp=drive_link`

During the rerun, two source-selection issues surfaced and were fixed before
accepting the final live artifact:

- The agent shortened `site_name` to `Alpha Los Angeles`, which let shared
  source matching consider the unrelated Whitley Avenue Los Angeles files.
  `run_dd_report_agent` now canonicalizes site-scoped tool calls back to the
  pipeline's full `site_title`, `drive_folder_url`, and `site_address`.
- The server-side shared-folder match threshold now rejects city-only matches.
  The Whitley Avenue Building Inspection continued to appear as an LLM
  candidate but was rejected at score `30`, below the required score.
- The source-alert parser was treating a successful
  `Successfully read ...` message as a source-read issue. It now only flags
  explicit errors, zero-length reads, or OCR/no-text warnings.
- `list_drive_documents` no longer exposes generated `dd_report` artifacts to
  the agent as source inputs, preventing reruns from reading a prior DDR as
  evidence. The prompt doc-type table now says generated DDRs are not source
  evidence.

Final accepted live run:

```text
run_id: 20260526152812-alpha-los-angeles-5400-beethoven-st-9fa94736
status: report_created
quality: green / 95
failed_step: notify.email
doc: https://docs.google.com/document/d/19NYJPmyhF7OHMBh6hjAMZ5SMXNELDpvAd2OE3U-9F2A/edit?usp=drivesdk
trace: https://drive.google.com/file/d/17zLiXAvkIgJj06whkNwLSAAKnVk_CIGD/view?usp=drivesdk
manifest: https://drive.google.com/file/d/1C9OcSM7xBRqrS_YChrtSljanwayC5uVI/view?usp=drivesdk
local manifest: C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.ddr-runs\20260526152812-alpha-los-angeles-5400-beethoven-st-9fa94736.json
```

Final run step status:

- `readiness.check`: succeeded
- `sir.learning_review`: skipped, `AI SIR present; CDS/vendor SIR not found yet`
- `rhodes.owner_lookup`: succeeded
- `report.generate`: succeeded
- `trace.save`: succeeded
- `source.alert`: succeeded
- `report.validate`: succeeded
- `notify.email`: failed with Gmail SMTP `535 Username and Password not accepted`
- `publish.dashboard`: succeeded
- `manifest.save`: succeeded
- `manifest.upload`: succeeded

Final artifact check:

- Exported report text length: `11980` chars.
- Report contains `Beethoven` and `5400`.
- Report does not contain `Whitley` or `1726`.
- Final report trace read only:
  - `5400-beethoven-st-los-angeles-ca_2026-05-21_SIR.docx`
  - `5400-beethoven-st-los-angeles-ca_2026-05-21_school-approval.docx`

Verification completed:

```powershell
uv run pytest --basetemp C:\tmp\ddr-pytest-beethoven-final-guards tests/test_report_pipeline.py tests/test_find_site_docs_m1_first.py tests/test_dd_output_fixes.py tests/test_prompt_contract.py
uv run ruff check src/due_diligence_reporter/report_pipeline.py src/due_diligence_reporter/server.py tests/test_report_pipeline.py tests/test_find_site_docs_m1_first.py tests/test_dd_output_fixes.py tests/test_prompt_contract.py
uv run mypy src/due_diligence_reporter/report_pipeline.py src/due_diligence_reporter/server.py
```

Results:

- Focused pytest: 96 passed.
- Ruff: all checks passed.
- Mypy: no issues in the two touched source files.

## 2026-05-26 - Prompt V4 Contract Rewrite

Rewrote `docs/prompts/prompt_v4.md` from a long accumulated workflow prompt
into a compact first-round DDR contract.

Behavior / prompt intent changed:

- Renamed the active prompt file from the previous prompt path to
  `docs/prompts/prompt_v4.md`.
- Renamed the markdown template file to
  `docs/templates/Site_DD_Report_Template_V4.md`.
- Updated scheduled loaders in `daily_dd_check.py`, `scan_inbox.py`, and
  `raycon_followup.py` to read `prompt_v4.md`.
- Updated prompt metadata to version `4.0.0` and `Last Updated: 2026-05-26`.
- Updated the report trace prompt version to `4`.
- Removed current-flow old-version labels from the prompt, docs, source comments,
  tool descriptions, and focused tests. Remaining lowercase version hits are Google
  Drive API version strings, not DDR prompt/report version labels.
- Removed the deprecated template-ID readiness gate from `scan_inbox.py`;
  generated reports are built programmatically and no longer require a template
  environment variable to start the pipeline phase.
- Centered the normal path on first-round DDR publishing from an AI SIR /
  research baseline instead of full vendor-doc readiness.
- Kept Rhodes / LocationOS P1 DRI lookup as a hard pre-report step.
- Replaced duplicated formatting and citation guidance with a single JC-style
  writing section plus one consolidated `Source Notes` contract through
  `exec.citations_block`.
- Removed stale normal-workflow instructions to generate an Opening Plan, call
  email from inside the agent loop, wait on full vendor docs, or call RayCon
  directly.
- Preserved the current report data contract, first-round open-item requirements,
  sourced gap-label rules, and exact executive-summary enum values.
- Added `tests/test_prompt_contract.py` to prevent the stale workflow strings,
  inline footnote style, encoding artifacts, retired-system references, and
  excessive prompt length from coming back silently.

Verification completed:

```powershell
uv run pytest --basetemp C:\tmp\ddr-pytest-prompt-contract-3 tests/test_prompt_contract.py tests/test_report_schema.py tests/test_google_doc_builder.py tests/test_dd_output_fixes.py
uv run ruff check tests/test_prompt_contract.py
uv run pytest --basetemp C:\tmp\ddr-pytest-prompt-v4-rename tests/test_prompt_contract.py tests/test_report_pipeline.py tests/test_report_trace.py tests/test_scan_inbox_e2e.py tests/test_raycon_followup.py
uv run ruff check tests/test_prompt_contract.py scripts/daily_dd_check.py scripts/scan_inbox.py scripts/raycon_followup.py src/due_diligence_reporter/report_pipeline.py src/due_diligence_reporter/server.py
uv run pytest --basetemp C:\tmp\ddr-pytest-v4-no-old-version-final-2 tests/test_prompt_contract.py tests/test_report_schema.py tests/test_google_doc_builder.py tests/test_report_pipeline.py tests/test_report_trace.py tests/test_scan_inbox_e2e.py tests/test_raycon_followup.py
uv run ruff check tests/test_prompt_contract.py tests/test_google_doc_builder.py tests/test_report_schema.py tests/test_raycon_followup.py scripts/daily_dd_check.py scripts/scan_inbox.py scripts/raycon_followup.py src/due_diligence_reporter/report_pipeline.py src/due_diligence_reporter/server.py src/due_diligence_reporter/report_schema.py src/due_diligence_reporter/completeness.py src/due_diligence_reporter/google_doc_builder.py src/due_diligence_reporter/config.py
rg -n "prompt_v[0-3]\.md|prompt_v[0-3]|Prompt V[0-3]|prompt v[0-3]" scripts src tests docs HANDOFF.md
rg -n "\bV[0-3]\b|\bv[0-3]\b|prompt_v[0-3]|Prompt V[0-3]|prompt v[0-3]" docs\prompts\prompt_v4.md docs\process\HOW-IT-WORKS.md HANDOFF.md src\due_diligence_reporter tests\test_google_doc_builder.py tests\test_report_schema.py scripts\daily_dd_check.py scripts\scan_inbox.py scripts\raycon_followup.py .env.example
rg --files | rg "V[0-3]|v[0-3]|prompt_v[0-3]|Template_V[0-3]|template_v[0-3]"
rg -n "apply_opening_plan_skill|Always.*send_dd_report_email|Every DD Report answers four questions|How to Use Me|\[1\]|â|Ã|Wrike|wrike|RayCon API|calls the RayCon" docs\prompts\prompt_v4.md
```

Results:

- Focused pytest: 210 passed.
- Ruff: all checks passed for the new prompt contract test.
- Rename-path pytest: 111 passed.
- Rename-path ruff: all checks passed.
- Final focused pytest: 270 passed.
- Final focused ruff: all checks passed.
- Old prompt-path reference scan: no active matches.
- Current-flow old-version label scan: no DDR prompt/report-version matches; only
  Google Drive API version strings remain.
- Old-version filename scan: no matches.
- Stale-string scan: no matches.

## 2026-05-26 - JC-Style Report Cleanup and Rhodes P1 DRI Lookup

Changed the DDR generation path so first-round and full DDRs use cleaner
executive formatting and resolve the site owner from Rhodes.

Behavior changed:

- Added a read-only Rhodes / LocationOS MCP client in `rhodes.py` using
  `RHODES_API_KEY` and optional `RHODES_MCP_URL`.
- Added `lookup_rhodes_site_owner` as a server/tool-loop tool. It resolves the
  site, hydrates the Rhodes site record, and returns `p1Dri.name` /
  `p1Dri.email` plus `report_data_fields`.
- `process_site_pipeline` now performs a best-effort Rhodes owner lookup before
  report generation when a P1 name/email was not already supplied. The result
  seeds `meta.prepared_by`, P1 email recipients, dashboard owner, and
  `site_created_at` when Rhodes provides it. Missing/unconfigured Rhodes does
  not block report generation.
- `run_dd_report_agent` now carries initial report fields into
  `create_dd_report` and includes the resolved Rhodes owner in the agent request
  context.
- `exec.citations_block` now survives server normalization and renders once as
  `Source Notes`; when the block is present, inline `[1]` markers are stripped
  from displayed Lease Conditions / Trade-Offs bullets.
- Prompt/process docs now require JC-style answer-first formatting, clean
  source notes instead of inline citations, and Rhodes P1 DRI lookup before DDR
  creation.

Live Rhodes check:

- `Alpha Los Angeles 5400 Beethoven St` resolved in LocationOS to
  `k9798fdj3vmy08sce06nhe167n874mvh`.
- Rhodes `p1Dri` is Devin Bates (`devin.bates@trilogy.com`).

Verification completed:

```powershell
uv run ruff check src/due_diligence_reporter/rhodes.py src/due_diligence_reporter/server.py src/due_diligence_reporter/report_pipeline.py src/due_diligence_reporter/google_doc_builder.py tests/test_rhodes.py tests/test_report_pipeline.py tests/test_google_doc_builder.py tests/test_dd_output_fixes.py tests/test_report_schema.py
uv run mypy src/due_diligence_reporter/rhodes.py src/due_diligence_reporter/server.py src/due_diligence_reporter/report_pipeline.py src/due_diligence_reporter/google_doc_builder.py
uv run pytest --basetemp C:\tmp\ddr-pytest-rhodes-style-2 tests/test_rhodes.py tests/test_report_pipeline.py tests/test_google_doc_builder.py tests/test_dd_output_fixes.py tests/test_report_schema.py
rg -n "Wrike|wrike" src docs tests scripts
rg -n "retired work-management|project_notes|work-management|source citations|Wrike|wrike|Citations" docs\prompts\prompt_v4.md docs\process\HOW-IT-WORKS.md src\due_diligence_reporter tests
```

Results:

- Targeted ruff: all checks passed.
- Targeted mypy: no issues in 4 source files.
- Focused pytest: 248 passed.
- Tracked grep for Wrike is clean.
- Prompt/process grep for stale retired-system wording is clean.

## 2026-05-26 - Retired Work-Management Integration Removal

Removed the retired work-management integration from the active DDR codebase.
The operating contract is now: supply the site name, site address, and Google
Drive folder URL directly, then generate/read evidence from Drive and the report
data.

Behavior changed:

- Removed the retired API client module, fuzzy site-record matching wrapper,
  and MCP tools for external site-record/comment lookup.
- Removed old one-off maintenance scripts, GitHub Actions workflows, and tests
  whose only purpose was syncing/backfilling/reconciling against the retired
  system.
- `daily_dd_check`, `scan_inbox`, `raycon_followup`, and publish workflows no
  longer require or write the retired access-token secret.
- Report metadata now uses `site_created_at` instead of the old
  source-specific created-at field.
- Missing P1 fallback copy is now `[Not found - P1 DRI not assigned]`.
- Active prompts/process docs now describe Drive + supplied site context.

Verification completed:

```powershell
uv run ruff check src/due_diligence_reporter/server.py src/due_diligence_reporter/report_pipeline.py src/due_diligence_reporter/report_schema.py src/due_diligence_reporter/assignment.py src/due_diligence_reporter/inbox_scanner.py src/due_diligence_reporter/dd_republish.py src/due_diligence_reporter/dashboard_publisher.py src/due_diligence_reporter/site_record.py src/due_diligence_reporter/raycon_client.py scripts/daily_dd_check.py scripts/scan_inbox.py scripts/raycon_followup.py tests/test_report_pipeline.py tests/test_dd_output_fixes.py tests/test_diagnose_site_readiness.py tests/test_dashboard_publisher.py tests/test_raycon_client.py tests/test_raycon_followup.py tests/test_scan_inbox_e2e.py tests/test_inbox_scanner.py tests/test_retry.py tests/test_report_trace.py tests/test_google_doc_builder.py
uv run mypy src/due_diligence_reporter/server.py src/due_diligence_reporter/report_pipeline.py src/due_diligence_reporter/assignment.py src/due_diligence_reporter/inbox_scanner.py src/due_diligence_reporter/dd_republish.py src/due_diligence_reporter/dashboard_publisher.py src/due_diligence_reporter/site_record.py src/due_diligence_reporter/raycon_client.py
uv run pytest --basetemp C:\tmp\ddr-pytest-retired-integration-3 tests/test_report_pipeline.py tests/test_dd_output_fixes.py tests/test_diagnose_site_readiness.py tests/test_dashboard_publisher.py tests/test_raycon_client.py tests/test_raycon_followup.py tests/test_scan_inbox_e2e.py tests/test_inbox_scanner.py tests/test_retry.py tests/test_report_trace.py tests/test_google_doc_builder.py tests/test_report_schema.py tests/test_classifier_keywords.py tests/test_completeness.py tests/test_vendor_gate.py
```

Results:

- Targeted ruff: all checks passed.
- Targeted mypy: no issues in 8 source files.
- Focused pytest: 640 passed.
- Tracked grep for the retired system name and old lookup tool names is clean.

## 2026-05-26 - First-Round DDR Publishing from AI SIR

Changed the DDR pipeline so the first publish can proceed from any SIR/AI SIR
research baseline instead of waiting for the full vendor document package.
Vendor SIR, Building Inspection, and RayCon scenario readiness still matter for
full-report completeness and republish, but they no longer block the initial
DDR slice.

Behavior changed:

- Readiness now treats `sir_found AND NOT report_exists` as enough for
  first-round report generation.
- Pipeline readiness now blocks only when no SIR/AI SIR is present; the old
  vendor/full-doc gate remains available for diagnostics and full-report status.
- Incomplete first-round DDRs carry a partial-completeness reason of
  `vendor_verification_pending` when `verification.open_items` is populated.
- The Google Doc builder renders `Open Items to Verify` in Supporting Notes and
  keeps the partial banner generic for vendor-verification items while preserving
  RayCon-specific timestamp language only for RayCon-pending items.
- `_normalize_report_replacements` accepts `verification.open_items`,
  `open_items.verification`, `verification_open_items`, or the internal open
  items token and maps them into the rendered DDR.

Prompt/template/process docs updated:

- `docs/prompts/prompt_v4.md` now defines the first-round scope as site
  metadata plus executive-summary fields for current-school-year open,
  zoning, education regulatory approval, occupancy path, permit timelines, and
  construction timelines.
- The prompt now instructs the agent to log concrete verification items from
  AI SIR/research output, especially B/C confidence items affecting those
  first-round fields.
- `docs/templates/Site_DD_Report_Template_V4.md` names the 8/12 or 9/8
  opening question and keeps permit/construction timelines in the executive
  summary row.
- `docs/process/HOW-IT-WORKS.md` now describes the first-round publish and later
  vendor/RayCon republish flow.

Verification completed:

```powershell
$stamp = Get-Date -Format 'yyyyMMddHHmmss'
$envTmp = "C:\tmp\ddr-env-tmp-$stamp"
$baseTmp = "C:\tmp\ddr-pytest-$stamp"
New-Item -ItemType Directory -Force -Path $envTmp | Out-Null
$env:TMP=$envTmp
$env:TEMP=$envTmp
uv run pytest --basetemp $baseTmp tests/test_completeness.py tests/test_google_doc_builder.py tests/test_report_pipeline.py tests/test_vendor_gate.py tests/test_diagnose_site_readiness.py tests/test_dd_output_fixes.py
uv run ruff check src/due_diligence_reporter/completeness.py src/due_diligence_reporter/google_doc_builder.py src/due_diligence_reporter/server.py src/due_diligence_reporter/report_pipeline.py tests/test_completeness.py tests/test_google_doc_builder.py tests/test_report_pipeline.py tests/test_vendor_gate.py tests/test_diagnose_site_readiness.py tests/test_dd_output_fixes.py
uv run mypy src/due_diligence_reporter/completeness.py src/due_diligence_reporter/google_doc_builder.py src/due_diligence_reporter/server.py src/due_diligence_reporter/report_pipeline.py
uv run pytest --basetemp $baseTmp --ignore-glob "*pytest-cache-files*"
```

Results:

- Focused pytest: 215 passed.
- Targeted ruff: all checks passed.
- Targeted mypy: no issues in the four touched source files.
- Full pytest: 1224 passed when ignoring stale `pytest-cache-files-*` folders
  already present in the repo tree.
- Repo-wide `uv run ruff check .` is still blocked by 44 pre-existing issues in
  unrelated scripts/tests.
- Repo-wide `uv run mypy src/` is still blocked by 11 pre-existing errors in 7
  unrelated modules.

No commit was created.

### Beethoven Live Test - 2026-05-26

Tested the first-round flow against:

- Site: `Alpha Los Angeles 5400 Beethoven St`
- Address: `5400 Beethoven St, Los Angeles, CA 90066`
- Drive folder: `https://drive.google.com/drive/folders/1G8fc0sX3dP83A7uMF5Bhz2pXnhRpaRJz?usp=drive_link`

Important findings from the live run:

- The site-record lookup did not find a matching operating record for Beethoven.
  The closest same-market records were ignored; this run proceeded from the
  direct Drive folder URL and supplied site/address context.
- The Drive folder contains the AI SIR and school-approval report in M1. The
  vendor Building Inspection, vendor SIR/CDS SIR, and RayCon scenario were not
  present, so the report is a first-round/partial DDR.
- Shared-source matching initially risked pulling Whitley Avenue Los Angeles
  docs because city-only evidence scored too high. The matching floor and city
  token handling now reject those weak matches.
- Filename classification now treats `_SIR.docx` as `sir` and
  `school-approval.docx` as `school_approval_report`.
- Direct-folder `list_drive_documents` now succeeds when no site record exists
  and skips shared-folder source matching in that path.
- The prompt/agent path now receives `drive_folder_url` and `site_address`
  directly, so it can continue from supplied context.
- Rendering/validation fixes from the live test:
  - `meta.prepared_by` falls back to
    `[Not found - P1 DRI not assigned]` when no P1 DRI is supplied.
  - Source-quality prose is sanitized so internal template keys such as
    `meta.prepared_by` do not appear in the report body.
  - Report completeness accepts the renderer's canonical display phrases
    `Yes, if:` / `No, because:` while still rejecting malformed uppercase
    answers like `YES`.

Final live pipeline run:

```text
run_id: 20260526135816-alpha-los-angeles-5400-beethoven-st-f9e9c240
status: report_created
report.validate: succeeded
source.alert: failed with source_read_issue
quality: orange / 69
doc: https://docs.google.com/document/d/1FtfkczUXerAvM1dQ6aFKLDq7pU6iUMnbHgwBfyYi22c/edit?usp=drivesdk
trace: https://drive.google.com/file/d/1DaPpFUW8mGVFlXvD6zI_pDEjAtWCv4vy/view?usp=drivesdk
manifest: C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.ddr-runs\20260526135816-alpha-los-angeles-5400-beethoven-st-f9e9c240.json
```

`source.alert` is still failing because the first-round report has a required
source-read issue / missing vendor source state. That does not block
`report_created` after the validation fixes, but it is still the operator
follow-up if this needs to be green instead of orange.

Additional verification after the Beethoven fixes:

```powershell
uv run pytest --basetemp C:\tmp\ddr-pytest-beethoven-1 tests/test_google_doc_builder.py tests/test_dd_output_fixes.py tests/test_classifier_keywords.py tests/test_report_pipeline.py tests/test_report_schema.py
uv run ruff check src/due_diligence_reporter/google_doc_builder.py src/due_diligence_reporter/server.py src/due_diligence_reporter/report_schema.py tests/test_google_doc_builder.py tests/test_dd_output_fixes.py
uv run mypy src/due_diligence_reporter/google_doc_builder.py src/due_diligence_reporter/server.py src/due_diligence_reporter/report_schema.py
```

Results:

- Focused pytest after all Beethoven fixes: 215 passed for
  `test_dd_output_fixes.py`, `test_google_doc_builder.py`, and
  `test_report_schema.py`; earlier expanded focused pass was 297 passed.
- Targeted ruff: all checks passed.
- Targeted mypy: no issues in the touched validation/rendering modules.

## 2026-05-21 - AI SIR vs. CDS SIR First-Pass Deep Dive

Objective: compare AI-generated Site Investigation Report packets against completed CDS SIRs and the supporting evidence in the reports. This pass used the model `AI SIR vs. CDS SIR vs. underlying evidence`, not a simple AI/CDS scorecard.

Source access notes:

- Google Drive connector required reauthorization, so source files were pulled from the usable `greg.foote@trilogy.com` Gmail searches and downloaded locally.
- Synced shared drive path `G:\Shared drives\Education Ops\All Locations` returned access denied in this session.
- Source attachments are local under `C:\Users\foote\.google_workspace_mcp\attachments\edu_ops`.
- Extracted text files are under `C:\tmp\sir_review_text`.

Reviewed first-pass cohort:

- `5601 Stone Rd, Centreville, VA`
- `1726 Whitley Ave, Los Angeles, CA`
- `421 E 11th St, Tulsa, OK`
- `6940 S Utica Ave, Tulsa, OK`
- `2409 S Macdill Ave, Tampa, FL`

Structured outcomes recorded with:

```powershell
uv run ddr sir-review add ...
```

Outcome store:

```text
.ddr-runs/sir-review-outcomes.jsonl
```

Recorded issue IDs:

- `112df21001e94d3d9fbd9cdc30ca2a22` - Centreville zoning: AI inferred likely SUP from wrong zoning assumption; CDS found C-6 by-right.
- `118e9058b29f4a378a687a7d93f953f4` - Centreville health: AI treated health inspection as required; CDS found no health permit if students bring lunches.
- `a21510d52c434fc4b446faff31e6c571` - 421 E 11th zoning: AI inferred SUP/public hearing; CDS found CBD by-right and no discretionary review.
- `b219f5f86a614f07949a8b0ed046a7d0` - 421 E 11th parking: AI estimated parking ratio; CDS found no parking required in CBD.
- `0b3f6457849f47dfb66d2f9e3e8487ad` - 421 E 11th health: AI overgeneralized school health inspection; CDS narrowed to food-service trigger.
- `0b5d63ed484547e9ab703b10588ad974` - 6940 S Utica zoning: AI inferred SUP; CDS found PUD-287/base OM by-right.
- `8178575aa7e64e889eede109148e8a47` - 6940 S Utica parking: AI estimated 10-15 spaces; CDS found 4 spaces.
- `2988d70f78504c5d8f92f3805424abe4` - 6940 S Utica health: AI overgeneralized health inspection; CDS narrowed to food-service trigger.
- `6084d624e7744e2f99a417c85cd304a7` - 1726 Whitley zoning: AI undercalled entitlement as CUP; CDS found zoning variance plus CPIO and Historic Design Review.
- `44899df9b1a7436a8d0d2177679d8b05` - 1726 Whitley historic/entitlements: AI flagged generic historic likelihood but missed named designations and sequencing.
- `e43e285d26b1414faff923d1bd63a7c6` - Tampa parking: AI used generic parking basis; CDS found jurisdiction-specific classroom ratio.

Trend command run:

```powershell
uv run ddr sir-trends --since 30d
```

Trend output:

```text
Issues: 11
Sites reviewed: 5
SIR pairs reviewed: 5
AI missed items/SIR: 0.4
AI unsupported claims/SIR: 1.2
CDS missed items/SIR: 0.0
DDR-impacting findings: 11
Blocking/material findings: 10
Top categories:
  AI unsupported claim: 6
  Better wording needed: 3
  AI missed item: 2
Top sections:
  Zoning: 4
  Health: 3
  Parking: 3
  Historic / Entitlements: 1
Repeat issues:
  Health | Better wording needed: 3
  Parking | AI unsupported claim: 3
  Zoning | AI unsupported claim: 3
```

Process changes suggested by this batch:

1. Retrieval rule: verify exact zoning district, PUD/base zoning, and use table before generating SUP/CUP/variance conclusions.
2. Retrieval rule: compute parking from the actual jurisdiction/district/use table before using generic student or square-foot ratios.
3. Prompt/template rule: make health findings conditional on the operating food model. Own-lunch/no cooking should not be treated the same as catering, serving, or cold-holding food.

Do not treat these as source-code changes yet. The next step should be deciding whether these repeated issues are enough to update retrieval/prompt/template guidance, then summarizing accepted learning outcomes back into the relevant operating workflow or system of record.

## 2026-05-21 - Continuous SIR Improvement Loop Implementation

Added repo support for making SIR review a recurring operating process:

- `ddr sir-review queue`
  - Scans local `.ddr-runs/*.json` pipeline manifests for `sir_learning_review` metadata.
  - Defaults to unreviewed `ready_for_review` pairs.
  - Deduplicates repeated manifests for the same site/SIR pair.
  - Marks pairs as reviewed when matching rows already exist in `.ddr-runs/sir-review-outcomes.jsonl`.
  - Useful options: `--status all`, `--include-reviewed`, `--limit 25`, `--manifest-dir`, `--store`.
- `ddr sir-monthly-summary --since 30d`
  - Reads the existing SIR review outcome store.
  - Prints a markdown operating memo with review volume, reliability signals, repeat patterns, accepted learning actions, and monthly decision prompts.

Files changed:

- `src/due_diligence_reporter/sir_review_queue.py`
- `src/due_diligence_reporter/ddr_cli.py`
- `src/due_diligence_reporter/sir_trends.py`
- `tests/test_ddr_cli.py`
- `docs/process/sir-learning-loop.md`

Verification:

```powershell
$env:TMP = 'C:\tmp\pytest-ddr'
$env:TEMP = 'C:\tmp\pytest-ddr'
uv run pytest tests/test_ddr_cli.py tests/test_sir_trends.py tests/test_sir_learning.py
uv run ruff check src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\sir_trends.py src\due_diligence_reporter\sir_review_queue.py tests\test_ddr_cli.py
uv run mypy src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\sir_trends.py src\due_diligence_reporter\sir_review_queue.py
uv run ddr sir-review queue --limit 5 --include-reviewed
uv run ddr sir-monthly-summary --since 30d
```

Results:

- Focused pytest: 12 passed.
- Ruff: all checks passed.
- Mypy: success for touched modules. It still prints the pre-existing unused `pyproject.toml` mypy override note.
- Live queue smoke test found one local ready candidate from existing manifests: `Alpha Keller`.
- Live monthly summary smoke test consumed the 11 current SIR review outcomes and reproduced the expected repeat patterns.

## 2026-05-21 - Google Workspace MCP Auth Repair

Google Workspace MCP auth was repaired so Drive-backed SIR evidence retrieval can resume after a Codex restart.

What changed outside this repo:

- `C:\Users\foote\.codex\bin\google-workspace-mcp-stdio.ps1` now uses the `greg_trilogy` credential profile.
- The wrapper launches `workspace-mcp==1.15.0` with read-only Drive, Docs, and Sheets permissions.
- Token/cache path is `C:\Users\foote\.google_workspace_mcp\credentials\greg_trilogy`.
- OAuth credentials are stored in Windows Credential Manager target `Codex:google_workspace_mcp_oauth`; do not print or inspect the secret.

Verification completed:

- OAuth callback log showed successful authorization-code exchange for `greg.foote@trilogy.com`.
- Token file appeared at `C:\Users\foote\.google_workspace_mcp\credentials\greg_trilogy\greg.foote@trilogy.com.json`.
- A direct local MCP Drive read succeeded with the new token.
- Temporary auth-helper processes were stopped afterward so port `8000` was clear.

Important operational lesson:

- If the local callback page shows only `Authentication Error`, check the MCP log before changing credentials. In this case the log said `No authorization code received from Google`, which meant the callback endpoint was opened directly or the in-app browser lost the `?code=...&state=...` query. The fix was to open the generated Google authorization URL in external Chrome/Edge.

Global lesson note added:

- `C:\Users\foote\.codex\memories\extensions\ad_hoc\notes\20260521-203015-google-workspace-mcp-auth-loopback.md`

## 2026-05-27 - Inbox Scanner Rhodes Auto-Resolve Follow-Up

Greg reviewed a manual-review list where most `unmatched_site` rows had real Rhodes
sites. Summer-camp SIRs should not create/manual-review Rhodes work because summer
camps do not have Rhodes entries.

What changed:

- `scripts/scan_inbox.py` now loads Rhodes site records with `status=None`, so
  cancelled/historical Rhodes sites are eligible for inbox matching. This is
  needed for rows like `Alpha Torrance 22600 Crenshaw Blvd`, which exists in
  Rhodes but is not active.
- `process_email()` skips recognized summer-camp documents when no site matches
  instead of adding them to `manual_review`.
- Unmatched supported PDFs now get one fallback site-match pass using extracted
  PDF text. This lets address text inside inspections/permit forms disambiguate
  otherwise generic subjects such as `May 8 Alpha Miami Beach Building Inspection`.
- Shared site-match terms now include the street-address line and street tokens,
  so PDF text like `1021 Biarritz Dr` or `4260 El Camino Real` contributes to the
  deterministic score.

Files changed:

- `scripts/scan_inbox.py`
- `src/due_diligence_reporter/inbox_scanner.py`
- `src/due_diligence_reporter/rhodes.py`
- `src/due_diligence_reporter/utils.py`
- `tests/test_inbox_scanner.py`
- `tests/test_scan_inbox_e2e.py`

Verification:

```powershell
uv run pytest tests/test_inbox_scanner.py tests/test_rhodes.py tests/test_scan_inbox_e2e.py -q
uv run ruff check src\due_diligence_reporter\inbox_scanner.py src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\utils.py scripts\scan_inbox.py tests\test_inbox_scanner.py tests\test_scan_inbox_e2e.py
uv run mypy src\due_diligence_reporter\inbox_scanner.py src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\utils.py
```

Results:

- Focused pytest: 86 passed.
- Ruff: all checks passed.
- Mypy: success for 3 touched source files.
