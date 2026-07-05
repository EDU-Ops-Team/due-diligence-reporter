# Due Diligence Reporter Handoff

## 2026-07-02 - Verified DD handoff notes now complete M2 automation locally

- Branch/worktree: current checkout at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Publication:
  - Pushed `2c66cf76fe731d50abb06a1fe6fab4d145a21783` to `origin/main`
    with the verified-handoff terminal path.
- Scope:
  - Implemented the accepted add-note/human-after-the-loop pattern for
    due-diligence field writes inside `m2_executor`.
  - When `updateDueDiligence` returns `pending_user_action` /
    `handoff_note_created` and the Rhodes/Aerie handoff note is created with
    verified readback and a note ID, M2 now finishes the automation as
    `m2_state=complete` with
    `completion_mode=due_diligence_update_handoff`.
  - The completed row/state preserves `human_followup_required=true`,
    `human_followup_type=due_diligence_update`,
    `manual_handoff_note_id`, and handoff field metadata so the process does
    not misrepresent the manual LocationOS update as already read back.
  - Failed, missing, or unverified handoff notes still block on the existing
    capacity/packet readback blockers.
  - The site-owner note body now explicitly says to copy/paste the listed field
    names and values into the LocationOS due diligence record.
- Beads:
  - `ddr-w88`: closed after local implementation and focused validation.
  - `ddr-d2l`: closed after scoped live canary proof on the published code.
  - `ddr-1q6`: still open and now unblocked; schedule variables remain
    disabled until the broad backlog behavior is accepted and first enabled
    runs are inspected.
- Validation:

```powershell
uv run pytest tests\test_m2_executor.py tests\test_rhodes.py -q --basetemp C:\tmp\ddr-handoff-terminal
uv run pytest tests\test_ddr_cli.py tests\test_workflow_contracts.py tests\test_vendor_doc_sweep.py tests\test_m2_pipeline.py tests\test_m2_executor.py tests\test_rhodes.py -q --basetemp C:\tmp\ddr-incoming-handoff-enable
uv run ruff check src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\vendor_doc_sweep.py src\due_diligence_reporter\m2_pipeline.py src\due_diligence_reporter\m2_executor.py src\due_diligence_reporter\rhodes.py tests\test_ddr_cli.py tests\test_workflow_contracts.py tests\test_vendor_doc_sweep.py tests\test_m2_pipeline.py tests\test_m2_executor.py tests\test_rhodes.py
uv run mypy src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\vendor_doc_sweep.py src\due_diligence_reporter\m2_pipeline.py src\due_diligence_reporter\m2_executor.py src\due_diligence_reporter\rhodes.py
git diff --check
```

Results: focused executor/Rhodes pytest passed (`56 passed`); broader focused
pytest passed (`127 passed`); Ruff passed; mypy passed for 5 source files;
`git diff --check` passed with only normal Windows LF/CRLF warnings.

- Post-push no-write workflow proof:
  - M2 Direct DD Events run `28613677867` completed success on head SHA
    `2c66cf76fe731d50abb06a1fe6fab4d145a21783`.
  - `m2-poll-events.json`: `status=success`, `apply=false`,
    `events_found=0`, `blocked=0`, filtered to the Miami Beach canary.
  - `m2-source-watch.json`: `status=success`, `open_states_checked=1`,
    source-event queue clear (`events_pending=0`, `events_blocked=0`), and
    the targeted state remains `capacity_ready` with
    `next_actions=["write_capacity_fields"]`.
  - `m2-execute-ready.json`: `status=success`, `apply=false`, `blocked=0`,
    `completed=0`, `executed=0`; preview row would execute
    `write_capacity_fields` for Alpha Miami Beach 300 71st 3rd.
- Scoped live apply proof:
  - User approved the owner-facing canary run on 2026-07-02.
  - M2 Direct DD Events apply run `28618140999` completed success on head SHA
    `de35c4422871bfadbab34d8181d99c1f35e5d590`.
  - `m2-poll-events.json`: `status=success`, `apply=true`,
    `events_found=0`, `blocked=0`, filtered to the Miami Beach canary.
  - `m2-source-watch.json`: `status=success`, `apply=true`,
    `open_states_checked=1`, source-event queue clear, and the target state
    remained ready for `write_capacity_fields` before execution.
  - `m2-execute-ready.json`: `status=success`, `apply=true`, `executed=1`,
    `completed=1`, `blocked=0`, `m2_state=complete`,
    `completion_mode=due_diligence_update_handoff`,
    `human_followup_required=true`,
    `manual_handoff_note_id=hx7hbxbngam4dck780tztb34bx89rcwk`, and
    `manual_handoff_note_readback_status=verified`.
  - The step was `write_capacity_fields` with `status=pending_user_action`,
    `reason=handoff_note_created`, and updated fields
    `foCapacity` / `maxCapCapacity`.
- Post-apply no-write proof:
  - M2 Direct DD Events no-write run `28618235348` completed success on head
    SHA `de35c4422871bfadbab34d8181d99c1f35e5d590`.
  - Artifacts show the targeted canary is no longer open/ready:
    `m2-source-watch.json` has `open_states_checked=0`, and
    `m2-execute-ready.json` has `states_checked=0`, `rows=[]`, `blocked=0`.

Remaining operational sequence for `ddr-1q6`:

1. Decide whether to enable broad scheduled apply now that `ddr-d2l` is green.
   Vendor dry-run still has a broad source backlog (`252` source events in the
   last dry-run), so turning on the vendor schedule means accepting that backlog
   behavior.
2. If accepted, set `VENDOR_DOC_REPUBLISH_SWEEP_ENABLED=true` and
   `M2_DIRECT_DD_EVENTS_ENABLED=true`, then inspect the first scheduled
   Vendor/M2 runs before closing `ddr-1q6`.

## 2026-07-02 - Incoming-source enablement verified, schedules still gated

- Branch/worktree: current checkout at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Code publication:
  - `origin/main` is at `fe9ade8a21d6f2c3d88c28057dcdd405732ee2ad`.
  - This includes the source-sweep related local commit `08733ea`, the
    console-script import fix `ad06870`, and workflow preflight hardening for
    `AERIE_API_KEY`.
- Beads:
  - `ddr-20h`: closed; Daily DD console-script import fix is pushed.
  - `ddr-7to`: closed; `AERIE_API_KEY` was provisioned and live note readback
    was verified.
  - `ddr-d2l`: still open; scoped M2 canary remains blocked on
    LocationOS/browser approval and DD field readback.
  - `ddr-1q6`: still open; scheduled incoming-source workflows remain gated.
- GitHub configuration:
  - Repo secret list now includes `AERIE_API_KEY`, updated
    `2026-07-02T17:05:29Z`.
  - Repo variables still do not include
    `VENDOR_DOC_REPUBLISH_SWEEP_ENABLED` or `M2_DIRECT_DD_EVENTS_ENABLED`.
    This is intentional; do not enable schedules until `ddr-d2l` is green.
- Current no-write workflow proof on `fe9ade8`:
  - Vendor Doc Republish Sweep dry-run `28608109863` completed success in
    `36m59s`. Required setup/secrets steps passed. Source-sweep log summary:
    `sites=61 events=252 republished=0 skipped=262 errors=0`. Filtered log
    review found no `ModuleNotFoundError`, traceback, validation failure, or
    fatal GitHub error marker; only Node deprecation warnings and the summary
    line matched the broad failure search.
  - M2 Direct DD Events no-write run `28608113491` completed success and
    uploaded `m2-poll-events.json`, `m2-source-watch.json`, and
    `m2-execute-ready.json`. Artifacts report `status=success` throughout.
    Source-event queue is clear:
    `events_found=0`, `events_pending=0`, `events_blocked=0`,
    `events_invalid=0`.
- Scoped canary proof after Aerie provisioning:
  - M2 canary apply `28607871909` ran on `fe9ade8` with filters
    `site_id=k174yvghy8yzb638b6rt5wdh3s88c6pq` and
    `event_id=m2-canary-20260701-miami-beach-300-71st-3rd`.
  - Workflow completed success, but `m2-execute-ready.json` still shows the
    targeted row blocked:
    `capacity_write_readback_pending` / `pending_user_action` /
    `handoff_note_created` for `write_capacity_fields`.
  - Live Aerie/Rhodes note readback verified note
    `hx7wkfrad5jxzrwn6208zq4kj189sfss` on site
    `k174yvghy8yzb638b6rt5wdh3s88c6pq`; the note includes
    `foCapacity: 114`, `maxCapCapacity: 199`, and an owner mention.
  - M2 no-write preview `28608113491` confirms the only ready state is this
    same Miami Beach canary and it would execute `write_capacity_fields`.

Current decision:

- Do not set `VENDOR_DOC_REPUBLISH_SWEEP_ENABLED=true` yet. The vendor dry-run
  sees a broad source backlog (`252` source events) and would emit/process it
  when scheduled apply is enabled.
- Do not set `M2_DIRECT_DD_EVENTS_ENABLED=true` yet. The only ready state still
  requires DD write approval and LocationOS readback proof.
- Next required proof is to complete or approve the Miami Beach canary DD field
  write, then rerun scoped M2 apply and verify LocationOS readback shows
  `foCapacity=114` and `maxCapCapacity=199`. Only after that should the broad
  schedule variables be enabled and the first scheduled runs inspected.

## 2026-07-02 - Incoming-source enablement attempted, Aerie secret blocked

- Branch/worktree: current checkout at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issues:
  - `ddr-20h`: closed after the console-script import fix was pushed.
  - `ddr-1q6`: still open/in-progress; scheduled enablement is blocked.
  - `ddr-d2l`: still open/in-progress; scoped M2 canary is not green.
  - `ddr-7to`: new blocker for provisioning `AERIE_API_KEY` in GitHub.
- Code publication:
  - Pushed local ahead commit `08733ea` and import-fix commit `ad06870` to
    `origin/main`.
  - `origin/main` and local `HEAD` both reached
    `ad068706878703c332f46bf3e1f77e2a2798addf`.
- Local validation before the push:

```powershell
uv run pytest tests\test_ddr_cli.py tests\test_workflow_contracts.py tests\test_vendor_doc_sweep.py tests\test_m2_pipeline.py tests\test_m2_executor.py -q --basetemp C:\tmp\ddr-incoming-process-enable
uv run ruff check src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\vendor_doc_sweep.py src\due_diligence_reporter\m2_pipeline.py src\due_diligence_reporter\m2_executor.py tests\test_ddr_cli.py tests\test_workflow_contracts.py tests\test_vendor_doc_sweep.py tests\test_m2_pipeline.py tests\test_m2_executor.py
uv run mypy src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\vendor_doc_sweep.py src\due_diligence_reporter\m2_pipeline.py src\due_diligence_reporter\m2_executor.py
git diff --check
```

Results: focused pytest passed (`80 passed`); focused Ruff passed; focused
mypy passed for 4 source files; `git diff --check` passed with normal Windows
LF-to-CRLF warnings.

- GitHub no-write verification:
  - Vendor Doc Republish Sweep dry-run `28604615477` succeeded on
    `origin/main` commit `ad06870`. Required setup/secrets steps passed and
    source sweep summary was
    `sites=61 events=250 republished=0 skipped=260 errors=0`. Extracted logs
    had no `ModuleNotFoundError`, traceback, or GitHub error lines.
  - M2 Direct DD Events no-write run `28604622239` succeeded and uploaded
    `m2-poll-events.json`, `m2-source-watch.json`, and
    `m2-execute-ready.json`. The source-event queue reported
    `status=success`, `events_pending=0`, and `events_blocked=0`; the ready
    executor preview still showed the known Miami Beach canary would execute
    `write_capacity_fields`.
- Scoped canary:
  - M2 canary apply `28605628703` was correctly filtered to site
    `k174yvghy8yzb638b6rt5wdh3s88c6pq` and event
    `m2-canary-20260701-miami-beach-300-71st-3rd`.
  - Workflow completed successfully, but `m2-execute-ready.json` is not green:
    `executed=1`, `blocked=1`, blocker `capacity_write_readback_pending`,
    step `write_capacity_fields`, error `awaiting_browser_approval`, reason
    `due_diligence_handoff_failed`.
- Live configuration blocker:
  - `gh secret list --repo EDU-Ops-Team/due-diligence-reporter` does not show
    `AERIE_API_KEY`.
  - The incoming-source workflows already write `AERIE_API_KEY` to `.env`, but
    they did not fail fast when it was absent.
  - Added repo-side workflow preflight hardening so
    `vendor-doc-republish-sweep.yml` and `m2-direct-dd-events.yml` require
    `AERIE_API_KEY`, with coverage in `tests/test_workflow_contracts.py`.
  - Focused validation for this hardening passed:
    `uv run pytest tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-aerie-workflow-contracts`,
    `uv run ruff check tests\test_workflow_contracts.py`, and
    `git diff --check`.
- Schedule variables were intentionally not enabled:
  - `VENDOR_DOC_REPUBLISH_SWEEP_ENABLED` remains unset.
  - `M2_DIRECT_DD_EVENTS_ENABLED` remains unset.
  - Queued scheduled Vendor run `28604734897` skipped because the vendor gate
    was still unset.

Next operational sequence:

1. Provision `AERIE_API_KEY` as a GitHub secret for
   `EDU-Ops-Team/due-diligence-reporter`.
2. Rerun a scoped M2 canary with the same site/event selector and require
   headless handoff note/readback proof, or a completed DD write/readback.
3. Rerun Vendor dry-run and M2 no-write after the secret is present.
4. Enable schedule variables only after the above is green, then verify the next
   scheduled Vendor/M2 runs execute steps instead of skipping.

## 2026-07-02 - Incoming-source DDR readiness review

- Branch/worktree: current checkout at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issues:
  - `ddr-0q2`: readiness review, closed after verification.
  - `ddr-20h`: still open; local CLI import fix is not pushed.
  - `ddr-1q6`: follow-up to enable scheduled incoming-source workflows.
- Request: review and verify whether the DDR process has the pieces needed to
  run as new source information arrives.
- Verdict:
  - The code/workflow pieces are present locally: Inbox Scan is scheduled;
    Vendor Doc Republish Sweep calls `uv run ddr source-sweep`; source sweep
    collects core M1/root source docs, emits canonical source events when the
    M2 queue is configured, and runs the compatibility republish path; M2
    Direct DD Events runs `poll-events`, `source-watch`, and `execute-ready`
    against Firestore-backed event/state stores.
  - The process is not operationally green on `origin/main` yet.
- Live blockers:
  - Latest Daily DD Check run `28599202771` failed on `origin/main` commit
    `f85a02e` with `ModuleNotFoundError: No module named 'scripts'` from
    `ddr_cli._run_daily_check`.
  - `origin/main` still imports `scripts.daily_dd_check` and
    `scripts.vendor_doc_republish_sweep` from the installed `ddr` console
    script path. Local working-tree changes fix both by loading repo scripts
    by file path, but they are uncommitted/unpushed.
  - Latest scheduled Vendor Doc Republish Sweep run `28601122621` was skipped
    with no steps. Latest scheduled M2 Direct DD Events run `28600025022` was
    skipped with no steps. GitHub variables include Firestore event/state
    settings, but do not include `VENDOR_DOC_REPUBLISH_SWEEP_ENABLED=true` or
    `M2_DIRECT_DD_EVENTS_ENABLED=true`.
  - Inbox Scan is active: latest checked scheduled run `28598827516` succeeded
    on `origin/main` commit `f85a02e`.
- Validation:

```powershell
uv run pytest tests\test_ddr_cli.py tests\test_workflow_contracts.py tests\test_vendor_doc_sweep.py tests\test_m2_pipeline.py tests\test_m2_executor.py -q --basetemp C:\tmp\ddr-incoming-process-readiness
uv run ruff check src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\vendor_doc_sweep.py src\due_diligence_reporter\m2_pipeline.py src\due_diligence_reporter\m2_executor.py tests\test_ddr_cli.py tests\test_workflow_contracts.py tests\test_vendor_doc_sweep.py tests\test_m2_pipeline.py tests\test_m2_executor.py
uv run mypy src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\vendor_doc_sweep.py src\due_diligence_reporter\m2_pipeline.py src\due_diligence_reporter\m2_executor.py
```

Results: focused pytest passed (`80 passed`); focused Ruff passed; focused
mypy passed for 4 source files.

Next operational steps:

1. Commit and push the local `ddr_cli.py` / `tests/test_ddr_cli.py` import fix
   plus any intended prior local commit(s).
2. Enable `VENDOR_DOC_REPUBLISH_SWEEP_ENABLED=true` and
   `M2_DIRECT_DD_EVENTS_ENABLED=true` intentionally in GitHub variables.
3. Run a manual no-write verification:
   `vendor-doc-republish-sweep.yml` with `dry_run=true`, then
   `m2-direct-dd-events.yml` with `apply=false`.
4. Verify the next scheduled Vendor/M2 runs execute steps instead of skipping,
   and inspect artifacts/logs before calling the incoming-source process green.

## 2026-07-02 - Ad-hoc source sweep no-prior-DDR fallback

- Branch/worktree: current checkout at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issue: `ddr-fxc`.
- Request: the DDR ad-hoc runner should not stop only because no existing DDR
  is found. It should inspect current site/source inputs and run applicable
  source-backed DDR work when inputs exist; if nothing is runnable, it should
  report that clearly.
- Changes:
  - Added `run_without_existing_report` to
    `maybe_republish_dd_report(...)`. Scheduled/default callers still return
    `skip_no_prior_report` when no prior DD Report exists.
  - `uv run ddr run-site source-sweep ...` now opts into that fallback, so a
    manual ad-hoc source sweep can run the pipeline from available source
    inputs even when `find_existing_dd_report(...)` returns nothing.
  - Source-sweep rows with no discovered core sources now report
    `reason=no_runnable_skill_inputs` with a user-facing message that no DDR
    source skills have the required inputs.
  - Republish outcomes now include `prior_report_status` so operators can see
    whether the pipeline ran against an existing DDR or as a no-prior-DDR input
    pass.
- Validation:

```powershell
uv run pytest tests\test_dd_republish.py tests\test_vendor_doc_sweep.py tests\test_adhoc_runner.py -q --basetemp C:\tmp\ddr-adhoc-runner-regression
uv run ruff check src\due_diligence_reporter\dd_republish.py src\due_diligence_reporter\vendor_doc_sweep.py src\due_diligence_reporter\adhoc_runner.py tests\test_dd_republish.py tests\test_vendor_doc_sweep.py tests\test_adhoc_runner.py
uv run mypy src\due_diligence_reporter\dd_republish.py src\due_diligence_reporter\vendor_doc_sweep.py src\due_diligence_reporter\adhoc_runner.py
uv run ruff check .
uv run mypy src/
uv run pytest -q --basetemp C:\tmp\ddr-adhoc-runner-full
```

Results: focused pytest passed (`64 passed`); scoped Ruff passed; scoped mypy
passed; full Ruff passed; full mypy passed for 52 source files; full pytest
passed (`1346 passed, 13 skipped`).

## 2026-07-01 - DD-field approval handoff note

- Branch/worktree: current checkout at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issue: `ddr-e3w`.
- Request: make due-diligence field writes behave like document-registration
  handoffs when LocationOS blocks on browser/OAuth/elicitation approval.
- Changes:
  - `update_rhodes_due_diligence(...)` now detects approval-required
    `updateDueDiligence` responses such as `awaiting_browser_approval` and
    elicitation/confirmation-style errors before readback.
  - Instead of surfacing those as generic field mismatches, DDR writes one
    concise Rhodes site note with:
    `Site Name`, `Site Address`, and `Due Diligence Fields to update`.
  - The result status is `pending_user_action` with
    `human_followup_type=due_diligence_update`; it is not treated as a
    successful SOR write.
  - Structured result metadata preserves safe approval response fields such as
    `pendingMutationId`, `approvalSessionId`, `reviewUrl`, and
    `rejectionReason`; the note body does not include those technical IDs.
- Validation:

```powershell
uv run pytest tests\test_rhodes.py -q --basetemp C:\tmp\ddr-dd-handoff-rhodes
uv run ruff check src\due_diligence_reporter\rhodes.py tests\test_rhodes.py
uv run mypy src\due_diligence_reporter\rhodes.py
uv run pytest tests\test_m2_executor.py tests\test_report_pipeline.py -q --basetemp C:\tmp\ddr-dd-handoff-pipeline
```

Results: Rhodes tests passed (`44 passed`); scoped Ruff passed; scoped mypy
passed; adjacent M2/report pipeline tests passed (`96 passed`).

## 2026-07-01 - Scoped M2 live canary blocked at DD approval gate

- Branch/worktree: current checkout at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issue: `ddr-d2l` remains open/in-progress. Follow-up bugs filed:
  `ddr-53o` and `ddr-e3w`.
- Request: run the scoped M2 Direct DD canary end to end after the prior
  no-write smoke only proved workflow infrastructure.
- Site/event: Alpha Miami Beach 300 71st 3rd,
  `m2-canary-20260701-miami-beach-300-71st-3rd`.
- Setup performed:
  - Registered and read back the existing School Approval document as
    `regulatoryApproval`.
  - Registered and read back the existing Alpha Capacity Analysis JSON as
    `capacityCalculation`.
  - Confirmed required SIR and Block Plan registrations were already present.
  - Set M2-specific GitHub variables so the event queue and state store use
    Firestore project `ap-automation-464623`, database
    `edu-ops-email-router`, collections `m2DirectDdEvents` and
    `ddrM2DirectDdState`.
  - Seeded the Firestore canary event for the scoped site/event selector.
- Workflow evidence:
  - Dry-run `28541483946` succeeded and selected exactly the intended event.
    Artifact: `C:\tmp\ddr-m2-run-28541483946\m2-direct-dd-events`.
  - First apply `28541527111` reached `execute-ready` but blocked at
    `run_alpha_capacity_analysis` because GitHub Actions could not load
    Ops-Skills:
    `Could not load Ops-Skills alpha-capacity-analysis skill and rulesets. Set OPS_SKILLS_REPO_PATH to the Ops-Skills repo root or install the Ops Skills Codex plugin cache.`
  - Recovery apply `28541634944` reused the registered Alpha Capacity report
    fields (`foCapacity=114`, `maxCapCapacity=199`) and executed the capacity
    write step. Artifact:
    `C:\tmp\ddr-m2-run-28541634944\m2-direct-dd-events`.
- Final live readback:
  - LocationOS returned `status=awaiting_browser_approval` for the
    `updateDueDiligence` capacity write.
  - The site still reads back `foCapacity=95` and `maxCapCapacity=114`; the
    requested canary values `114` and `199` were not applied.
  - The persisted M2 state remains `status=blocked`,
    `m2_state=capacity_ready`, with blocker
    `capacity_write_readback_pending` and reason
    `LocationOS readback mismatch for foCapacity, maxCapCapacity`.
- Follow-up work:
  - `ddr-53o`: provision or preflight Ops-Skills in the GitHub Actions runner
    so Alpha Capacity generation works without local plugin cache.
  - `ddr-e3w`: classify `awaiting_browser_approval` / pending LocationOS
    approval responses explicitly instead of surfacing them as generic
    readback mismatches.
- Validation/readback commands used:

```powershell
gh run watch 28541483946 --repo EDU-Ops-Team/due-diligence-reporter --exit-status
gh run watch 28541527111 --repo EDU-Ops-Team/due-diligence-reporter --exit-status
gh run watch 28541634944 --repo EDU-Ops-Team/due-diligence-reporter --exit-status
gh run download 28541634944 --repo EDU-Ops-Team/due-diligence-reporter --dir C:\tmp\ddr-m2-run-28541634944
Get-Content -Raw -LiteralPath C:\tmp\ddr-m2-run-28541634944\m2-direct-dd-events\m2-execute-ready.json
```

Current status: the full scoped canary was run, but it is not green. Do not run
broad M2 `apply=true`; fix or explicitly approve the DD write boundary, then
rerun the same scoped selector and require live LocationOS readback before
closing `ddr-d2l`.

## 2026-07-01 - Opening Plan and Phase 1 Phase 2 workbook artifact language

- Branch/worktree: current checkout at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issue: `ddr-y7g` (closed after validation).
- Request: standardize how DDR talks about aligned DD evidence artifacts:
  the evidence artifacts are the Opening Plan and the Phase 1 Phase 2 workbook.
- Changes:
  - Updated process docs, source-packet labels, report source labels, Google Doc
    report headings, workbook metadata/title row, tool descriptions, blocked
    messages, and uploaded workbook filename to use `Phase 1 Phase 2 workbook`.
  - Kept the internal `alpha_phasing_plan_report` source type and
    `alpha_phasing_*` report-data keys for compatibility with the existing M2
    packet schema.
  - Left legacy filename/source aliases in place so older `Alpha Phasing Plan`
    or `Phase Scope Register` files can still be recognized and normalized.
- Validation:

```powershell
uv run pytest tests/test_source_packet.py tests/test_m2_executor.py tests/test_classifier_keywords.py tests/test_alpha_phasing_plan.py tests/test_google_doc_builder.py tests/test_report_schema.py
uv run mypy src/
uv run ruff check .
uv run pytest
git diff --check
```

Results: focused pytest passed (`256 passed`); full mypy passed for `52 source
files`; full Ruff passed; full pytest passed (`1332 passed, 13 skipped`);
`git diff --check` passed with normal Windows LF-to-CRLF warnings only.

## 2026-07-01 - Grouped document-registration handoff for multi-artifact skills

- Branch/worktree: current checkout at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issue: `ddr-5ow`.
- Request: avoid one Rhodes handoff note per artifact when a skill produces
  multiple documents and direct document registration is approval-gated.
- Changes:
  - Added `create_document_registration_handoff_for_uploads(...)` as a shared
    batch finalizer in `src/due_diligence_reporter/rhodes.py`.
  - The single-document helper still owns the default policy. Batch callers can
    set `handoff_on_registration_failure=False`, collect attempted
    registrations, and then write one grouped note for all eligible
    `pending_user_action` documents.
  - `apply_outdoor_play_space_skill` now registers each generated artifact with
    per-artifact handoff disabled, then finalizes one grouped handoff for the
    Markdown, JSON, PNG, and HTML artifacts if registration is approval-gated.
  - Updated `docs/process/HOW-IT-WORKS.md` to state that multi-artifact skills
    group eligible document-registration failures into one note per run.
- Validation:

```powershell
uv run pytest tests/test_outdoor_play_space_tool.py tests/test_rhodes.py -k "document_registration_handoff or register_rhodes_document_for_upload or outdoor_play_space_skill"
uv run ruff check src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\server.py tests\test_rhodes.py tests\test_outdoor_play_space_tool.py
uv run mypy src/
uv run ruff check .
uv run pytest
```

Results: focused grouped-handoff tests passed (`10 passed`); scoped Ruff
passed; full mypy passed for `52 source files`; full Ruff passed; full pytest
passed (`1332 passed, 13 skipped`).

## 2026-07-01 - Phase 1 Phase 2 workbook source aliases

- Branch/worktree: current checkout at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issue: `ddr-uka` (closed).
- Request: evaluate PR 142's Ops-Skills changes against the DDR due
  diligence process, explain the real-world impact, and add the agreed
  alignment fixes.
- Changes:
  - Added shared DDR source-type canonicalization in
    `src/due_diligence_reporter/source_types.py`.
  - Normalized `phase-1-phase-2`, `Phase 1 Phase 2 workbook`, legacy
    `Phase Scope Register`, and Rhodes `phasing` aliases to canonical
    `alpha_phasing_plan_report` in source-packet, M2 executor, and M2 pipeline
    paths.
  - Updated classifier keyword routing so files named `Phase 1 Phase 2
    Workbook` are recognized as phasing source artifacts.
  - Documented the process boundary: SIR candidate traces are first-round
    handoff evidence, Opening Plan owns final opening dates and aligned
    opening-scope decisions, the Phase 1 Phase 2 workbook validates aligned
    phase scope / CapEx / building-field data points, Cost/Timeline remains
    required supporting evidence, source-packet completion does not replace
    human/legal validation, and skills must return machine-readable
    `supporting_documents[]` entries for DDR field writes.
- Validation:

```powershell
uv run pytest tests/test_source_packet.py tests/test_m2_executor.py tests/test_classifier_keywords.py
uv run ruff check src/due_diligence_reporter/source_types.py src/due_diligence_reporter/source_packet.py src/due_diligence_reporter/m2_executor.py src/due_diligence_reporter/m2_pipeline.py src/due_diligence_reporter/classifier.py tests/test_source_packet.py tests/test_m2_executor.py tests/test_classifier_keywords.py
uv run mypy src/
uv run pytest
uv run ruff check .
git diff --check
```

Results: focused pytest passed (`72 passed`); scoped Ruff passed; full mypy
passed for `52 source files`; full pytest passed (`1330 passed, 13 skipped`);
full Ruff passed; `git diff --check` passed with normal Windows LF-to-CRLF
warnings only.

## 2026-07-01 - Document registration handoff pattern adopted

- Branch/worktree: current checkout at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issue: `ddr-t3o`.
- Request: adopt the AADP document-registration handoff pattern for DDR:
  automation should finish artifact/Drive-owned work, then write a Rhodes note
  so a human can complete Aerie/Rhodes document registration when direct
  registration is approval-gated or unavailable.
- Changes:
  - `register_rhodes_document_for_upload(...)` still returns `registered` /
    `already_registered` for successful direct registration.
  - Eligible approval/OAuth/unavailable `registerDocument` errors with a
    human-openable Drive URL now create and read back a grouped Rhodes site
    note before returning `status=pending_user_action`.
  - The handoff note follows the copy/paste contract:
    `Site`, `Address`, `Documents to register`, then document title and Drive
    URL only. File IDs, doc types, milestones, Gmail IDs, task/source keys, and
    raw blockers remain in the structured receipt.
  - Owner routing uses the site P1/site owner first and resolves Greg Foote as
    fallback when no owner route is available. Missing owner/fallback, missing
    URL, missing site name/address, note-write failure, or note-readback failure
    remain failed/blocking.
  - Updated `docs/process/HOW-IT-WORKS.md` with the new operating contract.
- Validation:

```powershell
uv run pytest tests/test_rhodes.py -k "register_rhodes_document_for_upload"
uv run pytest tests/test_rhodes.py
uv run ruff check src\due_diligence_reporter\rhodes.py tests\test_rhodes.py
uv run mypy src/
uv run ruff check .
uv run pytest
```

Results: focused registration tests passed (`6 passed`); full Rhodes tests
passed (`41 passed`); scoped Ruff passed; full mypy passed for `51 source
files`; full Ruff passed; full pytest passed (`1327 passed, 13 skipped`).

## 2026-07-01 - M2 Direct DD canary controls and smoke verification

- Branch/worktree: `codex/ddr-adhoc-locationos-runner` at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issue: `ddr-kdr`.
- Request: reviewer/verifier pass to determine whether the repo-owned M2 Direct
  DD process works and what should happen next.
- Live smoke findings:
  - Initial no-write GitHub Actions smoke failed in `Verify required secrets`
    because the workflow interpolated multiline Firestore JSON directly into a
    shell `[ -n ... ]` test.
  - Fixed and pushed that workflow secret check, then re-ran the no-write M2
    workflow successfully. Artifacts reported `status=success`, but with
    `events_found=0`, `open_states_checked=0`, and `states_checked=0`, so the
    workflow infrastructure is proven but no live M2 state was exercised.
  - A read-only Drive/Rhodes reconciliation smoke for
    `Alpha Miami Beach 300 71st 3rd` succeeded and found M1 source files, but
    reported `would_register=13`, `already_registered=0`, and
    `registered_verified=0`. That site is not ready for an M2 canary until the
    required docs are registered/read back in Rhodes.
- Changes:
  - Added optional `--site-id` and `--event-id` canary selectors to
    `uv run ddr m2 poll-events`, `source-watch`, and `execute-ready`.
  - Firestore event polling applies filters before `limit`, so a targeted
    event cannot be skipped because another pending event sorts first.
  - Source watch and executor filtering now share the same state selector and
    leave scheduled all-site behavior unchanged when selectors are omitted.
  - Added `target_site_id` and `target_event_id` manual workflow inputs and
    passed them through all three M2 workflow stages.
  - M2 poll/source-watch/execute artifacts now include a top-level `filters`
    object so no-write and apply runs show the site/event selector used.
- Validation:

```powershell
uv run pytest tests/test_m2_pipeline.py tests/test_m2_executor.py tests/test_ddr_cli.py tests/test_workflow_contracts.py
uv run ruff check src/due_diligence_reporter/m2_pipeline.py src/due_diligence_reporter/m2_executor.py src/due_diligence_reporter/ddr_cli.py tests/test_m2_pipeline.py tests/test_m2_executor.py tests/test_ddr_cli.py tests/test_workflow_contracts.py
uv run ruff check .
uv run mypy src/
git diff --check
uv run pytest
```

Results: focused M2/CLI/workflow pytest passed (`63 passed`); scoped Ruff
passed; full Ruff passed; full mypy passed for `51 source files`;
`git diff --check` passed with normal Windows LF-to-CRLF warnings only; full
pytest passed (`1324 passed, 13 skipped`).

Operational next step:

- Do not run broad `apply=true` for M2 yet. First prepare one canary site by
  registering and verifying the minimum AADP handoff documents in Rhodes,
  enqueue or seed one `aadp.site_ready_for_ddr.v1` event, run the M2 workflow
  with `apply=false` plus `target_site_id`/`target_event_id`, inspect artifacts,
  then run `apply=true` only for that same selector if the dry-run names the
  intended site/event.

## 2026-07-01 - Security Due Diligence M2 source gate

- Branch/worktree: current checkout at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issue: `ddr-pl6`.
- Request: add the new `ops-skills:security-due-diligence` memo as a source
  DDR wants to run once a Block Plan or Floor Plan exists and Alpha Capacity
  Analysis is done.
- Changes:
  - Added `security_due_diligence_report` to DDR source classification,
    M1-recognized documents, source sweep events, open-question source mapping,
    and Rhodes document registration mapping (`other` / `acquireProperty`).
  - Updated the M2 executor downstream chain so Security Due Diligence becomes
    due only when a block/floor plan source and registered Alpha Capacity
    Analysis are present. If no registered memo exists, the live adapter blocks
    with next action `run_security_due_diligence` and resume source type
    `security_due_diligence_report`.
  - Updated M2 source-watch follow-up handling so a later registered security
    memo moves the state to `source_packet_ready` rather than incorrectly
    marking the M2 state complete.
  - Documented the memo as evidence-only source-packet support for now; it does
    not write LocationOS DD fields until a field/schema owner exists.
- Validation:

```powershell
uv run pytest tests/test_classifier_keywords.py tests/test_rhodes.py tests/test_open_questions.py tests/test_vendor_doc_sweep.py tests/test_m2_pipeline.py tests/test_m2_executor.py
uv run ruff check .
uv run mypy src/
uv run pytest
```

Results: focused tests passed (`119 passed`); full Ruff passed; full mypy
passed for `51 source files`; full pytest passed (`1320 passed, 13 skipped`).

Operational notes:

- No live M2 `--apply` run was executed in this session.
- No commit, push, or Beads remote sync was performed.

## 2026-06-30 - Retire active RayCon interactions for cost/timeline

- Branch/worktree: `codex/ddr-adhoc-locationos-runner` at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issue: `ddr-7xk`.
- Request: stop interacting with RayCon for M2 cost/time estimation. DDR should
  let the repo-owned Cost/Timeline Estimate run once the required capacity data
  is in hand.
- Changes:
  - M2 source-watch no longer treats `raycon_scenario` /
    `raycon_scenario_json` as phasing/build context. `cost_timeline_estimate`
    remains the registered M1 source mapped to Rhodes `initialCostEstimate`.
  - The vendor/full-report gate now requires vendor SIR, vendor Building
    Inspection, and a usable Cost/Timeline Estimate. Readiness reads the
    registered Cost/Timeline JSON from M1 and requires FO/Max open-date and
    capex report fields before it can open the gate.
  - Inbox RayCon folder pings and Block Plan dispatches are default-disabled.
    They return explicit skipped markers unless `RAYCON_INTERACTIONS_ENABLED`
    is set to `1`, `true`, or `yes`. Duplicate Block Plan handling now skips
    only when a Cost/Timeline Estimate already exists.
  - `raycon-followup.yml` is retained only as historical reference and its job
    is guarded with `if: ${{ false }}`. Scheduled/manual runs will not interact
    with RayCon.
  - Public readiness/diagnostic and automation-event text now reports
    Cost/Timeline Estimate as the third input instead of RayCon. Legacy RayCon
    parsing/helpers/tests remain for historical artifacts and explicit manual
    fallback only.
- Validation:

```powershell
uv run pytest tests/test_inbox_scanner.py tests/test_report_pipeline.py tests/test_vendor_gate.py tests/test_m2_pipeline.py tests/test_vendor_doc_sweep.py tests/test_workflow_contracts.py tests/test_diagnose_site_readiness.py tests/test_automation_event.py
uv run ruff check src/due_diligence_reporter/inbox_scanner.py src/due_diligence_reporter/report_pipeline.py src/due_diligence_reporter/server.py src/due_diligence_reporter/automation_event.py tests/test_inbox_scanner.py tests/test_report_pipeline.py tests/test_vendor_gate.py tests/test_diagnose_site_readiness.py tests/test_automation_event.py tests/test_workflow_contracts.py
uv run mypy -m due_diligence_reporter.inbox_scanner -m due_diligence_reporter.report_pipeline -m due_diligence_reporter.server -m due_diligence_reporter.automation_event
uv run ruff check .
uv run mypy src/
uv run pytest
```

Results: focused pytest passed (`235 passed, 13 skipped`); focused Ruff and
focused mypy passed; full Ruff passed; full mypy passed for `51 source files`;
full pytest passed (`1316 passed, 13 skipped`).

## 2026-06-30 - DDR M2 executor after capacity_ready

- Branch/worktree: `codex/ddr-adhoc-locationos-runner` at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issue: `ddr-t9c`.
- Request: automate DDR M2 states after `capacity_ready`, including capacity
  analysis, cost/timeline, downstream M2 sources, source-packet DD writes,
  readback verification, Rhodes source note, and final `complete` state.
- Changes:
  - Added `src/due_diligence_reporter/m2_executor.py`, a dry-run-by-default
    executor for open M2 states. `--apply` runs the deterministic chain:
    Alpha Capacity -> capacity write/readback -> cost/timeline ->
    Outdoor Play -> Opening Plan -> Phase 1/2 -> source packet ->
    packet-approved LocationOS writes/readbacks -> Rhodes source note ->
    `complete`.
  - The executor reuses registered M1/Rhodes source artifacts when present,
    persists step history, JSON-safe adapter artifacts, blockers,
    `source_packet`, `capacity_write`, `dd_write`, and `source_note`, and
    skips blocked states whose `next_action` is not a known DDR executor
    action.
  - Added `uv run ddr m2 execute-ready --limit N [--apply] [--state-store PATH]`
    and wired `.github/workflows/m2-direct-dd-events.yml` to run
    `poll-events -> source-watch -> execute-ready`, uploading
    `m2-execute-ready.json`.
  - Promoted `cost_timeline_estimate` to a first-class registered M1 source:
    classifier/doc-type vocabulary, M1 sweep recognition, Rhodes mapping to
    `initialCostEstimate`, M2 pipeline canonicalization, source-packet source
    requirements, and the M2 diligence field-source matrix.
  - Added focused tests for executor state transitions, source-packet
    cost/timeline requirements, source sweep recognition, CLI dispatch, Rhodes
    doc-type mapping, and workflow contract coverage.
- Validation:

```powershell
uv run pytest tests/test_m2_executor.py tests/test_source_packet.py
uv run pytest tests/test_m2_executor.py tests/test_m2_pipeline.py tests/test_source_packet.py tests/test_ddr_cli.py tests/test_vendor_doc_sweep.py tests/test_rhodes.py::test_ddr_doc_type_mapping_covers_inbox_supported_docs tests/test_workflow_contracts.py
uv run ruff check .
uv run mypy src/
uv run pytest
git diff --check
```

Results: executor/source-packet focused tests passed (`16 passed`); broader M2
focused suite passed (`70 passed`); full Ruff passed; full mypy passed for `51
source files`; full pytest passed (`1325 passed`); `git diff --check` passed
with only normal LF-to-CRLF warnings.

Operational notes:

- No live `--apply` workflow run was executed in this session. Live execution
  depends on the existing OAuth, LocationOS/Rhodes, OpenAI/Anthropic, and
  Firestore secrets already used by the M2 workflow.
- No commit or push was performed.

## 2026-06-30 - DDR M2 event consumer and watcher entrypoints

- Branch/worktree: `codex/ddr-adhoc-locationos-runner` at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issue: `ddr-rae`.
- Request: implement the repo-owned AADP -> DDR M2 closed-loop plan from the
  pasted brief. This session implemented the DDR-owned event intake, durable
  state, source-watch, and workflow wiring in the current DDR checkout. The AADP
  event producer/skill-execution slice was not edited in this workspace.
- Changes:
  - Added `src/due_diligence_reporter/m2_pipeline.py` with validation for
    `aadp.site_ready_for_ddr.v1`, required SIR + School Approval
    registration/readback gates, local JSON M2 state, optional Firestore-backed
    M2 state, Firestore `m2DirectDdEvents` polling, and open-state source
    resume rules.
  - Added `uv run ddr m2 consume-event --input <json>`,
    `uv run ddr m2 poll-events --apply`, and
    `uv run ddr m2 source-watch --apply`. Live Rhodes document readback is the
    default for event consumption/polling; `--skip-rhodes-readback` exists for
    schema/local-state canaries.
  - Added `.github/workflows/m2-direct-dd-events.yml`, scheduled/manual and
    gated by `M2_DIRECT_DD_EVENTS_ENABLED`. It uses Firestore event queue envs,
    writes OAuth/Firestore credentials from secrets, polls pending events, then
    watches only sites with open M2 state.
  - Updated MCP Hive packaging and stale mutating workflow cancellation so
    `.m2_direct_dd_state.json` is excluded and stale `M2 Direct DD Events` runs
    are canceled during publish.
  - Added focused tests in `tests/test_m2_pipeline.py`, CLI coverage in
    `tests/test_ddr_cli.py`, and workflow contract assertions in
    `tests/test_workflow_contracts.py`.
- Validation:

```powershell
uv run pytest tests/test_m2_pipeline.py tests/test_ddr_cli.py tests/test_workflow_contracts.py
uv run ruff check src/due_diligence_reporter/m2_pipeline.py src/due_diligence_reporter/ddr_cli.py tests/test_m2_pipeline.py tests/test_ddr_cli.py tests/test_workflow_contracts.py
uv run mypy -m due_diligence_reporter.m2_pipeline -m due_diligence_reporter.ddr_cli
uv run ruff check .
uv run pytest
uv run mypy src/
```

Results: focused pytest `47 passed`; focused Ruff passed; focused mypy passed;
full Ruff passed; full pytest `1316 passed`; full mypy passed for `50 source
files`.

Remaining cross-repo work:

- AADP still needs the producer side: execute-mode EOC + School Approval
  generation/registration/readback, `aadp.site_ready_for_ddr.v1` event
  emission to `m2DirectDdEvents`, and its workflow contract tests. This should
  be done in the `alpha-analysis-downstream-processing` checkout with its own
  Beads issue and validation.
- The new DDR event consumer currently initializes state and resumes blockers
  when matching source inputs land. The deeper skill-chain execution after
  resume (capacity analysis -> cost/timeline -> outdoor play/opening/phasing ->
  packet-approved DD writes -> Rhodes note) remains the next DDR automation
  slice.

## 2026-06-30 - Rhodes-backed cost-and-timeline estimate skill

- Branch/worktree: `codex/ddr-adhoc-locationos-runner` at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issue: `ddr-wdu`. Earlier `ddr-jux` was the superseded RayCon-dependent
  interpretation; follow-up Beads issue `ddr-lk2` renamed and aligned the skill
  with Ops-Skills rules.
- Publish follow-up: Beads issue `ddr-t8v` opened ready PR
  `https://github.com/EDU-Ops-Team/Ops-Skills/pull/137` from clean temp
  checkout `C:\tmp\ops-skills-cost-timeline-pr`, branch
  `codex/cost-and-timeline-estimate-skill`, commit `1eec15e`.
- Request: build a standalone skill, similar in output shape to RayCon, that
  reads accepted Fastest Open / Max Capacity counts from Rhodes and estimates
  cost and timeline without judging or deriving capacity.
- Changes:
  - Reviewed Ops-Skills `origin/main` reference files from the local
    `EDU-Ops-Team/Ops-Skills.git` checkout: `README.md`, `CONTRIBUTING.md`,
    `.github/CODEOWNERS`, `.claude/skill-authoring-guidelines.md`,
    `scripts/skill-lint.mjs`, `skills/rhodes-site-sync/SKILL.md`, and
    `skills/cost-benchmarking/SKILL.md`.
  - Added `docs/skills/cost-and-timeline-estimate/SKILL.md` as a standalone
    Codex skill. It follows the Ops-Skills shape with scorecard metadata,
    best-effort telemetry, Rhodes-first input rules, and explicit prohibitions
    on RayCon dispatch, `raycon_scenario.json`, Block Plan capacity derivation,
    or capacity-quality judgment.
  - Added
    `docs/skills/cost-and-timeline-estimate/scripts/estimate.py`, a
    standard-library deterministic estimator that reads capacity from a
    `rhodes_site`/`getSite` payload (`dueDiligence.foCapacity` and
    `dueDiligence.maxCapCapacity`) plus optional gross SF, start date,
    market/city multiplier, and scenario overrides.
  - Added
    `docs/skills/cost-and-timeline-estimate/references/assumptions.md` with
    Rhodes capacity field mapping, ROM unit costs, schedule assumptions,
    category vocabulary, downstream payload shape, and override semantics.
  - Added `tests/test_cost_and_timeline_estimate_skill.py` covering
    Rhodes-sourced capacity, category/timeline overrides, CLI output, missing
    Rhodes capacity, and downstream handoff fields.
  - Fixed the generated `agents/openai.yaml` default prompt to reference
    `$cost-and-timeline-estimate`.
  - The estimator returns `source_system=cost_and_timeline_estimate`,
    `estimate_version=cost_and_timeline_estimate.v1`, `rhodes_capacity_read`,
    `scenarios`, `warnings`, `assumptions`, DDR-ready `report_data_fields`, and
    `downstream_inputs` for subsequent skills.
- Validation:

```powershell
uv run pytest tests\test_cost_and_timeline_estimate_skill.py -q
uv run ruff check docs\skills\cost-and-timeline-estimate\scripts\estimate.py tests\test_cost_and_timeline_estimate_skill.py
uv run mypy docs\skills\cost-and-timeline-estimate\scripts\estimate.py tests\test_cost_and_timeline_estimate_skill.py
```

Results: pytest passed (`4 passed`); scoped Ruff passed; scoped mypy passed
with only the existing pyproject unused-override note.

Skill validation note: attempted
`quick_validate.py docs\skills\cost-and-timeline-estimate`; it failed before
validating because `PyYAML`/`yaml` is not installed in the repo `uv`
environment.

## 2026-06-29 - Ad-hoc runner aligned to M2 source-packet closure

- Branch/worktree: `codex/ddr-adhoc-locationos-runner` at
  `C:\Users\foote\.claude\Work\repos\due-diligence-reporter`.
- Beads issue: `ddr-zzy`.
- Feedback addressed: Greg approved the review findings that the ad-hoc runner
  and `ddr-adhoc-runner` skill still let operators treat DD Report / SOR link
  success as closure even when the M2 source packet was blocked.
- DDR runtime changes:
  - `_due_diligence_result_is_final_ready(...)` now requires a present source
    packet to be explicitly complete before writing `status=complete`,
    `dateCompleted`, or `ddReportLink`.
  - `locationos_fields_allowed_by_source_packet(...)` now withholds
    `dateCompleted` / `ddReportLink` until the packet is complete and coerces a
    stale `status=complete` to `data-gathering` for blocked or non-explicit
    packets. Packet completion requires `m2_source_packet_complete: true`,
    `status: complete`, and no open items.
  - `uv run ddr run-site ...` payloads now include `source_packet` when the
    pipeline result has one, so operators can see `supporting_documents`,
    `dd_field_updates`, source note lines, and open items without opening the
    manifest.
- Ops-Skills guidance changes are staged in a clean temp worktree:
  `C:\tmp\ops-skills-ddr-source-packet-runner-head`, branch
  `codex/ddr-source-packet-runner-skill-head`. The main Ops-Skills checkout was
  dirty on `codex/skill-telemetry-audit-hardening`, so it was intentionally not
  edited in place. The temp skill patch updates
  `skills/ddr-adhoc-runner/SKILL.md` and
  `skills/ddr-adhoc-runner/references/self-service-runtime.md` to make M2
  closure source-packet based, require Outdoor Play / source-doc readback, use
  `uv run ddr source-sweep` as the current sweep surface, and remove the active
  BrainTrust reference.
- Validation:

```powershell
uv run pytest tests\test_source_packet.py tests\test_report_pipeline.py::test_blocked_source_packet_prevents_completion_status_and_report_link tests\test_report_pipeline.py::test_source_packet_without_completion_flag_prevents_final_status tests\test_report_pipeline.py::test_record_due_diligence_update_respects_source_packet tests\test_adhoc_runner.py::test_result_payload_surfaces_source_packet tests\test_automation_event.py::test_dd_report_summary_event_renders_m2_source_packet_lines tests\test_workflow_contracts.py::test_scheduled_dd_workflows_use_repo_cli_surfaces -q
uv run pytest
uv run ruff check .
uv run mypy src/
git diff --check
node scripts/skill-lint.mjs
node scripts/skill-lint.test.mjs
```

Results: DDR focused tests passed (`14 passed`); full DDR suite passed
(`1302 passed`); Ruff passed; mypy passed; `git diff --check` passed with only
normal LF-to-CRLF warnings; Ops-Skills skill lint and skill-lint tests passed
from the temp worktree. Beads issue `ddr-zzy` is closed.

## 2026-06-23 - DDR Doc/SOR/event-note sequencing

- Branch/worktree: `codex/ddr-document-first-on-readback` at
  `C:\tmp\ddr-document-first-on-readback`.
- Feedback addressed: Jarvis Brandon's cloud agent runs could prepare DDR data
  but usefulness was blocked when SOR readback or Rhodes event note paths
  failed. Greg asked that ad-hoc DDR runs always produce the Google Doc when
  render succeeds, always attempt LocationOS/Rhodes `dueDiligence`, and then
  try the Rhodes report-event note as a warning-only side effect if it fails.
- Prepared-data runs now render/validate the DD Report before the default
  Rhodes due-diligence write. That write now includes `ddReportLink` because
  the Doc URL exists before `rhodes.due_diligence_update` runs.
- `--document-first-on-sor-blocker` is now the default for ad-hoc runs via
  `argparse.BooleanOptionalAction`; `--no-document-first-on-sor-blocker` keeps
  the strict pre-render SOR-first path for debugging.
- Failed due-diligence writes/readbacks after Doc creation keep
  `status=report_created`, `doc_url`, and
  `failed_step=rhodes.due_diligence_update`, plus a top-level warning in the
  ad-hoc runner payload.
- Failed Rhodes report-event note writes now record `rhodes.report_event` as a
  skipped warning with `rhodes_report_event.severity=warning` and manual-check
  metadata. They no longer set `failed_step` when the Doc and dueDiligence
  steps succeeded.
- Post-Doc `locationos_mcp_write_request` payloads no longer get an automatic
  `mcp_resume_command`; the request tells the operator to run
  OAuth-backed `updateDueDiligence` and verify `getSite`. Strict pre-render
  MCP handoffs still emit the manifest-bound resume command.

Validation:

```powershell
uv run pytest tests\test_report_pipeline.py::TestProcessSitePipeline::test_prepared_data_renders_ddr_before_updating_sor tests\test_report_pipeline.py::TestProcessSitePipeline::test_prepared_data_sor_failure_still_renders_ddr_and_warns tests\test_report_pipeline.py::TestProcessSitePipeline::test_prepared_data_document_first_on_readback_blocker_creates_ddr tests\test_report_pipeline.py::TestProcessSitePipeline::test_prepared_data_mcp_assisted_sor_failure_creates_doc_and_emits_write_request tests\test_report_pipeline.py::TestProcessSitePipeline::test_prepared_data_mcp_completed_verifies_readback_after_rendering tests\test_report_pipeline.py::TestProcessSitePipeline::test_report_event_note_failure_is_warning_after_doc_and_sor tests\test_report_pipeline.py::TestProcessSitePipeline::test_report_created_still_attempts_event_note_when_prior_warning_exists tests\test_adhoc_runner.py::test_force_regenerate_suppresses_notifications_and_calls_pipeline tests\test_adhoc_runner.py::test_force_regenerate_mcp_assisted_surfaces_write_request_and_resume_command tests\test_adhoc_runner.py::test_result_payload_surfaces_manual_check_warnings -q --basetemp C:\tmp\ddr-doc-sor-note-focused
uv run pytest tests\test_ddr_cli.py tests\test_adhoc_runner.py -q --basetemp C:\tmp\ddr-doc-sor-note-cli
uv run pytest tests\test_pipeline_contracts.py tests\test_report_pipeline.py -q --basetemp C:\tmp\ddr-doc-sor-note-pipeline
uv run ruff check src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\adhoc_runner.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\pipeline_contracts.py tests\test_ddr_cli.py tests\test_adhoc_runner.py tests\test_report_pipeline.py
uv run mypy --explicit-package-bases src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\adhoc_runner.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\pipeline_contracts.py
uv run ddr run-site first-publish --help
git diff --check
```

Results: focused sequencing pytest passed (`10 passed`), CLI/runner pytest
passed (`25 passed`), pipeline/contract pytest passed (`89 passed`), Ruff
passed, mypy passed for the touched source files, help shows
`--document-first-on-sor-blocker | --no-document-first-on-sor-blocker`, and
`git diff --check` passed with only normal LF-to-CRLF warnings.

## 2026-06-23 - Actionable report.generate 529 recovery

- Branch/worktree: `codex/ddr-document-first-on-readback` at
  `C:\tmp\ddr-document-first-on-readback`.
- Feedback addressed: Alpha Boca Raton 5000 T-Rex Ave first-publish reached
  partial-report readiness, then three suppressed runs failed at
  `report.generate` with Anthropic `529 overloaded_error`; the emitted
  `ddr rerun --run-id ... --step report.generate` action only printed a
  command and did not execute recovery.
- `run-site` now persists a secret-free `launch_context` in the run manifest:
  mode, site/address/site ID/slug/Drive folder URL, notification preference,
  SOR write mode, MCP-completed flag, document-first flag, force-regenerate
  flag, and source-republish metadata when applicable.
- `ddr rerun --run-id <run_id> --step report.generate` now executes the saved
  `ddr run-site ...` launch context. It supports bounded retries with
  `--max-attempts` and `--backoff-seconds`, and only repeats attempts for
  retryable generation-provider failures such as Anthropic 529/overload/rate
  limit/timeouts.
- Legacy manifests without `launch_context` get a best-effort inferred
  first-publish retry from `site_title` and `site_id`. If the manifest lacks
  enough site context, the command exits with an actionable error instead of
  printing a no-op.
- This does not change the document-first SOR fallback. The 529 failure occurs
  before DD facts are prepared or any Rhodes/SOR write/render step starts, so
  the fix is an executable generation retry path.

Validation:

```powershell
uv run pytest tests\test_ddr_cli.py tests\test_adhoc_runner.py -q --basetemp C:\tmp\ddr-529-rerun-tests
uv run pytest tests\test_pipeline_contracts.py tests\test_report_pipeline.py -q --basetemp C:\tmp\ddr-529-pipeline-tests
uv run ruff check src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\adhoc_runner.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\pipeline_contracts.py tests\test_ddr_cli.py tests\test_adhoc_runner.py
uv run mypy --explicit-package-bases src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\adhoc_runner.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\pipeline_contracts.py
```

Results: focused CLI/runner pytest passed (`24 passed`), pipeline contract and
report pipeline pytest passed (`88 passed`), Ruff passed, and mypy passed for
the touched source files. Plain `uv run mypy src/` still hits the existing
duplicate-module mapping issue in `vendor_doc_sweep.py`.

## 2026-06-23 - Document-first DDR fallback for Rhodes readback blockers

- Branch/worktree: `codex/ddr-document-first-on-readback` at
  `C:\tmp\ddr-document-first-on-readback`, based on
  `origin/codex/ddr-adhoc-locationos-runner`.
- Added `uv run ddr run-site ... --document-first-on-sor-blocker`.
- The flag is opt-in. Default behavior remains SOR-first: prepared DD data
  stops before render when `rhodes.due_diligence_update` fails.
- With the flag, eligible Rhodes/LocationOS SOR blockers can continue to
  `report.render`, return `status=report_created` and `doc_url`, preserve
  `failed_step=rhodes.due_diligence_update`, and keep any
  `locationos_mcp_write_request` / resume metadata in the run payload.
- Field mismatches, invalid/rejected writes, missing source data, missing Drive
  folder, Google OAuth failures, and render failures still block the run.
- Ops-Skills companion docs in
  `C:\tmp\ops-skills-ddr-runner-self-service` now instruct agents to report
  this as "DD Report Google Doc created; Rhodes/SOR readback pending" until
  live Rhodes readback verifies fields, report-event note, and
  `dueDiligence.ddReportLink`.

Validation:

```powershell
uv run pytest tests\test_report_pipeline.py::TestProcessSitePipeline::test_prepared_data_sor_failure_stops_before_rendering_ddr tests\test_report_pipeline.py::TestProcessSitePipeline::test_prepared_data_document_first_on_readback_blocker_creates_ddr tests\test_report_pipeline.py::TestProcessSitePipeline::test_document_first_sor_blocker_rejects_field_mismatch tests\test_report_pipeline.py::TestProcessSitePipeline::test_prepared_data_mcp_assisted_sor_failure_emits_write_request tests\test_adhoc_runner.py::test_force_regenerate_suppresses_notifications_and_calls_pipeline tests\test_adhoc_runner.py::test_force_regenerate_mcp_assisted_surfaces_write_request_and_resume_command -q --basetemp C:\tmp\ddr-document-first-on-readback-tests
uv run ruff check src\due_diligence_reporter\adhoc_runner.py src\due_diligence_reporter\report_pipeline.py tests\test_adhoc_runner.py tests\test_report_pipeline.py
uv run mypy -m due_diligence_reporter.adhoc_runner -m due_diligence_reporter.report_pipeline
git diff --check
```

Results: focused pytest passed (`6 passed`), Ruff passed, mypy passed for the
touched modules, and `git diff --check` passed with only normal LF-to-CRLF
warnings for edited files.

## 2026-06-19 - DDR Ad-Hoc Runner Skill Source Corrected

- The durable `ddr-adhoc-runner` skill update belongs in
  `EDU-Ops-Team/Ops-Skills`, not this DDR application repo's project-local
  `.agents` folder.
- Removed the project-local `.agents/skills/ddr-adhoc-runner` files from this
  DDR PR branch after opening the corrected Ops-Skills PR:
  `https://github.com/EDU-Ops-Team/Ops-Skills/pull/110`.
- This DDR branch remains scoped to the application/runtime support for the
  ad-hoc runner, LocationOS MCP-assisted write/resume path, workflow dispatch,
  and tests.

## 2026-06-18 - 35 E 62nd St OAuth LocationOS Write Completed, Report Rendered

- User approved the live LocationOS/Rhodes write for source run
  `20260618222025-35-e-62nd-st-new-york-ny-10065-2490016f`.
- Direct OAuth MCP helper `C:\tmp\rhodes_dd_update_35e62_oauth_mcp.py`
  called `updateDueDiligence` once with the exact 9-field payload from that
  source manifest. LocationOS returned `status=approved`; REBL3 returned
  `httpStatus=200`, `ok=true`, slug `35-e-62nd-st-new-york-ny`; Rhodes returned
  `ok=true`, `outcome=updated`.
- Post-write `getSite` readback verified all intended fields:
  `buildingComment`, `buildingScore`, `playAreaComment`, `playAreaScore`,
  `regulatoryComment`, `regulatoryScore`, `schoolOperationsComment`,
  `schoolOperationsScore`, and `status`.
- Manifest-bound resume then succeeded for the DDR render:
  `uv run ddr run-site resume-mcp-write --run-id 20260618222025-35-e-62nd-st-new-york-ny-10065-2490016f`.
  Resume run:
  `20260618224737-35-e-62nd-st-new-york-ny-10065-78406154`.
  DD Report:
  `https://docs.google.com/document/d/143DobftGC9WASR5XDCCjZOHcNsnPfuZosiWfWEJFIg0/edit?usp=drivesdk`.
  Manifest:
  `.ddr-runs\20260618224737-35-e-62nd-st-new-york-ny-10065-78406154.json`.
  Status is `report_created`; quality is `90` / `green`; notifications were
  suppressed; missing docs still include `Building Inspection`; open asks are
  `14`.
- The resume run still has failed step `rhodes.report_event`: the DD report
  AutomationEvent note could not be verified because `addNote` returned
  `elicitation_unsupported` through the repo's normal Rhodes client. This is a
  narrower report-event note blocker, not the original due-diligence SOR write
  blocker.
- Attempted the same report-event note through
  `C:\tmp\rhodes_dd_report_event_note_35e62_oauth_mcp.py`; the OAuth callback
  did not complete within 300 seconds, so no `addNote` call occurred.
- Attempted a duplicate-safe lower-level MCP note helper
  `C:\tmp\rhodes_dd_report_event_note_35e62_api_mcp.py`. It proved API-key MCP
  reads work with elicitation capability, but `addNote` returned LocationOS
  server error request IDs `d0911650c7a637cb` /
  `ddc14628-2158-448f-9f60-7659948a004c`; `listNotes` did not find the exact
  body afterward.
- Retried the interactive OAuth-backed report-event note on 2026-06-19 with
  Greg back at the computer. Updated helper
  `C:\tmp\rhodes_dd_report_event_note_35e62_oauth_mcp.py` to precheck
  `listNotes` for the exact rendered body before posting. `addNote` returned
  `status=approved` with note ID `hx7q07enmt4nvyn7ypbw8j894x88y5dr`, author
  Greg Foote, and a Brandon Gee mention. Post-write `listNotes` readback
  verified the exact 2,521-character AutomationEvent body.
- Added the latest DDR Google Doc URL to `dueDiligence.ddReportLink` on
  2026-06-19 using
  `C:\tmp\rhodes_dd_report_link_35e62_oauth_mcp.py`. Evidence source was the
  M1 Drive folder listing: `35 E 62ND ST, New York, NY 10065 DD Report -
  06/18/2026`, document ID `143DobftGC9WASR5XDCCjZOHcNsnPfuZosiWfWEJFIg0`,
  modified `2026-06-18T22:48:00.284Z`. Before value was `null`; LocationOS
  `updateDueDiligence` returned `status=approved`, REBL3 `ok=true`, Rhodes
  `outcome=updated`; post-write `getSite` readback verified:
  `https://docs.google.com/document/d/143DobftGC9WASR5XDCCjZOHcNsnPfuZosiWfWEJFIg0/edit?usp=drivesdk`.
- Current next action, if a fully clean run is required: complete the
  site-specific 35 E 62nd DDR work is now complete. Durable follow-up
  `ddr-uwg` remains open because DDR's normal Rhodes note client still does not
  support confirmation/elicitation for future `addNote` report events.

## 2026-06-18 - Manifest-Bound LocationOS MCP Resume for 35 E 62nd St

- Beads issue `ddr-76b` tracks the fix for the MCP-assisted DDR resume
  blocker discovered on the 35 E 62nd St run.
- Root cause: the old `--mcp-write-completed` resume command reran the DDR
  agent, so the readback check could compare LocationOS against a regenerated
  payload instead of the exact payload approved through the OAuth-backed
  LocationOS MCP.
- Implemented `locationos_mcp_resume.v1` on pipeline manifests. When
  `status=locationos_mcp_write_required`, DDR now persists the exact
  `locationos.updateDueDiligence` arguments plus the saved `render_input`,
  prepared report data, owner context, open questions, and missing-doc state.
- Added `uv run ddr run-site resume-mcp-write --run-id <source_run_id>`.
  This path loads the source manifest, verifies LocationOS readback against the
  saved MCP arguments, and renders from saved `render_input` without calling
  the DDR agent or regenerating DD data.
- Updated the ad-hoc runner output, project-local `ddr-adhoc-runner` skill, and
  GitHub `Ad-Hoc DDR Run` workflow dispatch to use the manifest-bound resume
  mode.
- Fresh 35 E 62nd mcp-assisted first-publish run:
  `20260618222025-35-e-62nd-st-new-york-ny-10065-2490016f`.
  Manifest:
  `.ddr-runs\20260618222025-35-e-62nd-st-new-york-ny-10065-2490016f.json`.
  Status remains `locationos_mcp_write_required`; notifications suppressed; no
  DD Report doc was rendered. The emitted resume command is:
  `uv run ddr run-site resume-mcp-write --run-id 20260618222025-35-e-62nd-st-new-york-ny-10065-2490016f`.
- Pre-write resume test run:
  `20260618222342-35-e-62nd-st-new-york-ny-10065-a9a26d2c`.
  It failed only the LocationOS readback step, produced no `doc_id`/`doc_url`,
  and did not render because LocationOS still does not match the saved MCP
  payload. This confirms the guard works before the OAuth write is completed.

Validation:

```powershell
uv run pytest tests\test_adhoc_runner.py tests\test_ddr_cli.py tests\test_workflow_contracts.py tests\test_report_pipeline.py -q --basetemp C:\tmp\ddr-manifest-resume-suite-2
uv run ruff check src\due_diligence_reporter\adhoc_runner.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\pipeline_contracts.py tests\test_adhoc_runner.py tests\test_report_pipeline.py tests\test_workflow_contracts.py
uv run mypy -m due_diligence_reporter.adhoc_runner -m due_diligence_reporter.report_pipeline -m due_diligence_reporter.pipeline_contracts
uv run ddr status --run-id 20260618222025-35-e-62nd-st-new-york-ny-10065-2490016f
uv run ddr trace --run-id 20260618222025-35-e-62nd-st-new-york-ny-10065-2490016f --failed-only
uv run ddr status --run-id 20260618222342-35-e-62nd-st-new-york-ny-10065-a9a26d2c
uv run ddr trace --run-id 20260618222342-35-e-62nd-st-new-york-ny-10065-a9a26d2c --failed-only
```

Results: affected tests passed (`113 passed`), Ruff passed, mypy passed for
the touched modules, the fresh source manifest has `locationos_mcp_resume.v1`
with saved `render_input` and 9 LocationOS fields, and the pre-write resume
test stopped at `locationos_mcp_readback_failed` without rendering.

Remaining operator action:

1. Use an OAuth-backed `locationos` MCP surface to run
   `locationos.updateDueDiligence` with the exact
   `locationos_mcp_write_request.arguments` from source run
   `20260618222025-35-e-62nd-st-new-york-ny-10065-2490016f`.
2. Approve the Aerie card if prompted.
3. Run
   `uv run ddr run-site resume-mcp-write --run-id 20260618222025-35-e-62nd-st-new-york-ny-10065-2490016f`.
   If readback matches, DDR will render the DD Report from the saved manifest
   payload.

## 2026-06-18 - Portfolio Gaps Emits Phase 1 Site Identity Contract

- Implemented DDR/Portfolio Gaps source-side work for WTC bead `wtc-bmg.2.5`.
- `portfolio_automation_gaps.py` now treats verified Rhodes `site_id` as a
  prerequisite before routing `missing_p1_dri` or `missing_drive_folder`
  findings to AADP.
- If `site_id` is present, those findings remain AADP-owned automatic
  candidates and include Phase 1 closed-loop fields:
  `idempotency_key`, `autonomy_mode`, SOR system/write/readback status,
  operating-note status, P1 DRI route status, failure route, and next step.
- If `site_id` is missing, Portfolio Gaps emits a source-context blocker owned
  by `portfolio-gaps`; it does not route the finding to AADP as executable
  work.
- `run_aadp_portfolio_gap_remediation.py` now follows the same rule when the
  AADP remediation runner is unavailable: no-site rows become Portfolio Gaps
  blockers and are not counted as AADP attempts.
- Missing required/current-milestone documents remain excluded from Portfolio
  Gaps action records.

Verification:

```powershell
uv run pytest tests\test_portfolio_automation_gaps.py tests\test_aadp_portfolio_gap_remediation_trigger.py tests\test_portfolio_gap_telemetry.py tests\test_portfolio_gap_notifications.py tests\test_pipeline_contracts.py -q --basetemp C:\tmp\wtc-phase1-ddr
uv run ruff check src\due_diligence_reporter\portfolio_automation_gaps.py scripts\run_aadp_portfolio_gap_remediation.py tests\test_portfolio_automation_gaps.py tests\test_aadp_portfolio_gap_remediation_trigger.py
git diff --check -- src/due_diligence_reporter/portfolio_automation_gaps.py scripts/run_aadp_portfolio_gap_remediation.py tests/test_portfolio_automation_gaps.py tests/test_aadp_portfolio_gap_remediation_trigger.py
```

Results:

- Focused DDR tests passed: `27 passed`.
- Ruff passed.
- Scoped `git diff --check` passed with expected LF-to-CRLF warnings only.

Remaining:

- AADP must consume executable Portfolio Gaps records and emit SOR/Drive
  write/readback, operating-note, P1 DRI route, and final action status
  telemetry.
- WTC must publish the preserved closed-loop fields in the business-readable
  Phase 1 dashboard view.

## 2026-06-18 - 35 E 62nd St Ad-Hoc DDR Run Blocked at LocationOS MCP Approval

- Beads issue `ddr-1ef` tracks the operator-requested DDR run for
  `35 E 62ND ST, New York, NY 10065`.
- Important safety note: the first diagnosis using the broad site text
  `Alpha New York 35 E 62nd St` resolved to the wrong Rhodes site
  (`Alpha New York City 787 11th Ave`). Use the exact site ID
  `k17fsrj9m5y8843d04x5nmf0ch88daws` for this run until the resolver is
  hardened. Follow-up bug: `ddr-0bu`.
- Correct-site diagnosis:
  `uv run ddr run-site diagnose --site "35 E 62ND ST, New York, NY 10065" --site-id "k17fsrj9m5y8843d04x5nmf0ch88daws" --address "35 E 62nd St, New York, NY 10065"`
  resolved the M1 folder
  `https://drive.google.com/drive/folders/1eYYvDFoXpHrcTBEHEakLE0waRuIHc-YA`,
  P1 owner Brandon Gee, `partial_report_possible=true`, and
  `ready_for_full_report=false` because Building Inspection and RayCon scenario
  remain unavailable.
- A first-publish run initially failed LocationOS validation because
  `foCapEx` was sent as a string. Fixed the report-pipeline SOR field mapper to
  parse single currency values for `foCapEx` / `maxCapCapEx` and suppress
  range or pending/gap labels rather than sending invalid strings.
- Latest first-publish run:
  `20260618204045-35-e-62nd-st-new-york-ny-10065-4ef0f302`.
  Manifest:
  `.ddr-runs\20260618204045-35-e-62nd-st-new-york-ny-10065-4ef0f302.json`.
  Status is `locationos_mcp_write_required`; no DD Report doc was rendered yet.
- The manifest contains the exact
  `locationos_mcp_write_request.arguments` for
  `locationos.updateDueDiligence`, plus the resume command. This Codex thread
  did not expose a callable `mcp__locationos` tool even though
  `codex mcp list` shows `locationos` enabled with OAuth, so the write still
  needs to be completed through an OAuth-backed LocationOS MCP surface and then
  resumed with:
  `uv run ddr run-site first-publish --site "35 E 62ND ST, New York, NY 10065" --sor-write-mode mcp-assisted --mcp-write-completed --address "35 E 62ND ST, New York, NY 10065" --site-id "k17fsrj9m5y8843d04x5nmf0ch88daws" --drive-folder-url "https://drive.google.com/drive/folders/1eYYvDFoXpHrcTBEHEakLE0waRuIHc-YA"`.
- Notifications remained suppressed. Rhodes report event was not attempted on
  the latest run because the pipeline stopped before DD Report rendering.

Validation so far:

```powershell
uv run pytest tests\test_report_pipeline.py::test_due_diligence_numeric_fields_parse_currency_and_skip_gaps tests\test_report_pipeline.py::test_due_diligence_score_fields_normalize_to_locationos_enum_values tests\test_report_pipeline.py::test_invalid_due_diligence_score_fields_are_not_sent_to_locationos -q --basetemp C:\tmp\ddr-fo-capex-focused
uv run pytest tests\test_report_pipeline.py::TestProcessSitePipeline::test_report_created_updates_rhodes_due_diligence_before_notifying_p1 -q --basetemp C:\tmp\ddr-fo-capex-process
uv run ruff check src\due_diligence_reporter\report_pipeline.py tests\test_report_pipeline.py
uv run mypy -m due_diligence_reporter.report_pipeline
```

Results: focused mapper tests passed (`3 passed`) and process payload test
passed (`1 passed`), Ruff passed, and module mypy passed.

## 2026-06-18 - LocationOS MCP-Assisted SOR Write Path

- Beads issue `ddr-8gs` tracks the MCP-assisted LocationOS SOR write path for
  ad-hoc DDR.
- Added a read-only Rhodes helper
  `verify_rhodes_due_diligence_fields(...)` that reuses LocationOS `getSite`
  readback verification without calling `updateDueDiligence`.
- `process_site_pipeline(...)` now accepts
  `due_diligence_write_mode="api"|"mcp_assisted"` and
  `locationos_mcp_write_completed`. Default `api` behavior is unchanged.
- In `mcp_assisted` mode, if the bearer-token `updateDueDiligence` path fails
  with `elicitation_unsupported`, the pipeline stops before DD Report rendering
  with `status=locationos_mcp_write_required` and embeds an exact
  `locationos_mcp_write_request` containing the `locationos.updateDueDiligence`
  arguments and `getSite` readback fields. It does not write the P1 report note
  for that pending handoff.
- After the user/operator completes the OAuth-backed `locationos` MCP
  `updateDueDiligence` call and approves Aerie, rerun the emitted
  `mcp_resume_command`. That passes `--mcp-write-completed`; DDR rebuilds the
  normalized DD fields, verifies LocationOS readback, records the SOR step as
  `reason=locationos_mcp_readback_verified`, and then renders the DD Report.
- `uv run ddr run-site ...` now exposes `--sor-write-mode mcp-assisted` and
  `--mcp-write-completed`, promotes the MCP write request to top-level JSON,
  and emits a command-array `mcp_resume_command`.
- `.github/workflows/ad-hoc-ddr-run.yml` exposes matching dispatch inputs
  (`sor_write_mode`, `mcp_write_completed`) using the same env-var shell
  assembly pattern as the rest of the workflow.
- `.agents/skills/ddr-adhoc-runner/SKILL.md` documents the new MCP-assisted
  SOR write and resume flow.

Validation:

```powershell
uv run pytest tests\test_rhodes.py::test_verify_rhodes_due_diligence_fields_uses_readback_without_write tests\test_report_pipeline.py::TestProcessSitePipeline::test_prepared_data_mcp_assisted_sor_failure_emits_write_request tests\test_report_pipeline.py::TestProcessSitePipeline::test_prepared_data_mcp_completed_verifies_readback_before_rendering tests\test_adhoc_runner.py::test_run_site_mcp_write_completed_requires_mcp_assisted_mode tests\test_adhoc_runner.py::test_force_regenerate_mcp_assisted_surfaces_write_request_and_resume_command tests\test_workflow_contracts.py::test_ad_hoc_ddr_workflow_dispatch_uses_runner_and_opt_in_notifications -q --basetemp C:\tmp\ddr-mcp-assisted-focused-2
uv run pytest tests\test_adhoc_runner.py tests\test_report_pipeline.py tests\test_rhodes.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-mcp-assisted-suite
uv run ruff check src\due_diligence_reporter\adhoc_runner.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\rhodes.py tests\test_adhoc_runner.py tests\test_report_pipeline.py tests\test_rhodes.py tests\test_workflow_contracts.py
uv run mypy -m due_diligence_reporter.adhoc_runner -m due_diligence_reporter.report_pipeline -m due_diligence_reporter.rhodes
git diff --check
```

Results: focused tests passed (`6 passed`), affected suite passed
(`130 passed`), Ruff passed, module-name mypy passed, and `git diff --check`
passed with expected Windows LF-to-CRLF warnings only. Direct path mypy on
`src\...` still hits the repo's known duplicate-module import issue.

## 2026-06-18 - Ad-Hoc DDR Runner Design Review

- Beads issue `ddr-9fm` tracks the operator-safe ad-hoc DDR runner. The core
  method is now implemented as package CLI plus workflow dispatch; the
  project-local skill remains the invocation contract for agents/operators.
- Added `src/due_diligence_reporter/adhoc_runner.py` and wired
  `uv run ddr run-site ...` into `ddr_cli.py`. Supported modes:
  `diagnose`, `first-publish`, `force-regenerate`, `source-sweep`, and explicit
  `source-republish`.
- The runner suppresses outbound Chat/email env vars by default before loading
  repo settings; `--notify` re-enables normal notifications. Rhodes
  due-diligence writes and Rhodes report-event notes remain part of mutating
  pipeline behavior because they are SOR writes, not external notifications.
- Added `.github/workflows/ad-hoc-ddr-run.yml` as the manual operator surface.
  Workflow inputs are passed through env vars before shell assembly, not
  interpolated directly in `run: |` blocks. The workflow defaults
  `source-sweep` to `--dry-run`, requires explicit `apply_source_sweep=true`
  for `--apply`, requires explicit `notify=true` for `--notify`, preserves DD
  republish state, and uploads `ad-hoc-ddr-run.json` plus run manifests.
- Updated `.agents/skills/ddr-adhoc-runner` so the skill points to
  `uv run ddr run-site ...`. The bundled script
  `.agents/skills/ddr-adhoc-runner/scripts/run_ddr_site.py` is now a thin
  compatibility wrapper around the package CLI.
- Focused validation passed:
  `uv run pytest tests\test_adhoc_runner.py tests\test_ddr_cli.py
  tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-adhoc-runner-final-2`
  -> `34 passed`; `uv run ruff check src\due_diligence_reporter\adhoc_runner.py
  src\due_diligence_reporter\ddr_cli.py tests\test_adhoc_runner.py
  tests\test_ddr_cli.py tests\test_workflow_contracts.py
  .agents\skills\ddr-adhoc-runner\scripts\run_ddr_site.py` passed;
  `uv run mypy -m due_diligence_reporter.adhoc_runner -m
  due_diligence_reporter.ddr_cli` passed; `uv run python -m py_compile
  .agents\skills\ddr-adhoc-runner\scripts\run_ddr_site.py` passed.
- Validation limitation: the repo-standard `uv run mypy src/` currently exits
  before checking code with `Source file found twice under different module
  names: "src.due_diligence_reporter.server" and
  "due_diligence_reporter.server"`. The skill-creator `quick_validate.py`
  still cannot run on this host because `PyYAML` is not installed
  (`ModuleNotFoundError: No module named 'yaml'`).
- Greg asked for a repo review because BrainTrust / Google Chat remains an
  unreliable ad-hoc launch surface for DDR runs.
- Current repo review found that BrainTrust is not a first-class source-code
  dependency in this repo. The repeated failures are captured in prior
  handoff/changelog notes as MCP Hive / Google Chat runtime and tool-list
  issues, while the durable DDR execution path is the Python
  `process_site_pipeline(...)` flow.
- Current ad-hoc surfaces are fragmented: `ddr diagnose` only prints the
  `daily_dd_check.py --site` command, `daily_dd_check.py --site` runs the cron
  first-publish path, and `vendor_doc_republish_sweep.py --site` runs the
  source-triggered republish path. None is a single operator command with
  explicit mode, preflight, force, notification, and manifest-readback controls.
- Recommended method is now implemented: use `uv run ddr run-site ...` locally
  or the `Ad-Hoc DDR Run` GitHub workflow dispatch, both backed by
  `process_site_pipeline(...)` and not BrainTrust.
- Important safety constraints for implementation: keep the existing SOR-first
  sequence, preserve candidate idempotency, do not bypass missing-drive-folder
  or vendor-gate semantics unless the selected mode explicitly says it is a
  partial first publish, and make notification suppression a runner-level
  setting rather than an environment hack.

## 2026-06-18 - RayCon Overhaul Planning Review

- Greg asked for a repo review and overhaul plan because RayCon is not reliably
  producing the needed Fastest Open and Max Capacity cost/timeline estimates.
- Current deployed RayCon API health/version checks passed on 2026-06-18:
  `/health` returned `status=ok`; `/version` returned
  `git_commit=e385f6328dd58399d6051ed7f735c72a11c007a7` and
  `calculation_version=raycon-engine-2.0.0`.
- Focused local DDR RayCon contract tests passed:
  `uv run pytest tests\test_raycon_client.py tests\test_raycon_followup.py -q
  --basetemp C:\tmp\ddr-raycon-plan-review` -> `154 passed`.
- Key finding: the repo already has the right target contract in
  `docs/reference/RayCon-DDR-Rebuild-Package.md`: RayCon should be a
  deterministic async scenario engine that writes one canonical
  `raycon_scenario.json` into M1 and keeps narrative/chat separate from
  authoritative numbers.
- Key finding: the current DDR integration is a recovery/polling shell around
  external RayCon behavior. It dispatches `/v1/jobs`, requires complete Alpha
  Capacity Analysis before automated dispatch, reads `raycon_scenario.json`,
  maps only Alpha-sourced capacity, and blanks failed scenario fields. That is
  protective, but it does not by itself prove RayCon can hit 95%+ estimate
  success across prior site inputs.
- Recommended overhaul plan: first build a golden-corpus eval harness from prior
  DDR/RayCon inputs and expected FO/MaxCap outputs; then make a shared
  deterministic scenario engine the product core; then wrap it with async jobs,
  synchronous analysis/proof endpoints, callback/Drive writeback, and DDR
  ingestion. Preserve useful RayCon pieces only when they serve that contract:
  cost category vocabulary, timeline and city-multiplier tables, Drive/M1
  writeback, status/idempotency, and structured provenance.

## 2026-06-18 - SOR-First DD Data Preparation Before DDR Rendering

- Beads issue `ddr-8cu` tracks Greg's approval to split normalized
  due-diligence data extraction from DDR rendering.
- ADR `docs/decisions/0001-sor-first-dd-data-before-ddr-render.md` records the
  accepted boundary: structured DD data is prepared and published to Rhodes
  before a DDR Google Doc is created or updated.
- Added `prepare_due_diligence_data(...)` as the non-rendering handoff tool. It
  performs deterministic report-data normalization, REBL/default injection, and
  completeness metadata generation without creating a Google Doc.
- Updated the agent loop so the preferred path stops after
  `prepare_due_diligence_data` succeeds. The shared pipeline then records
  `due_diligence.prepare`, calls `rhodes.due_diligence_update`, and only then
  renders the DDR through the existing `create_dd_report` tool.
- If the pre-render Rhodes due-diligence write fails, the pipeline writes/tags
  the P1 DRI with the failed SOR write and stops before rendering the DDR. If
  the SOR write is skipped for a non-failed reason such as missing site ID, the
  existing non-blocking behavior is preserved.
- The old direct `create_dd_report` path remains as a compatibility fallback
  for callers that have not yet moved to the prepared-data handoff.
- Updated `docs/prompts/prompt_v4.md`, `docs/process/HOW-IT-WORKS.md`, and
  `docs/process/dd-end-to-end-flow.mmd` to describe the SOR-first sequence:
  prepare normalized data, write Rhodes, then render the DDR as a supporting
  view.

Validation:

```powershell
uv run pytest tests\test_report_pipeline.py::TestAgentToolMerging::test_run_dd_report_agent_stops_after_preparing_due_diligence_data tests\test_report_pipeline.py::TestProcessSitePipeline::test_prepared_data_updates_sor_before_rendering_ddr tests\test_report_pipeline.py::TestProcessSitePipeline::test_prepared_data_sor_failure_stops_before_rendering_ddr tests\test_prompt_contract.py::test_prompt_v4_keeps_first_round_contract -q --basetemp C:\tmp\ddr-sor-first-prepare-focused-3
uv run pytest tests\test_report_pipeline.py::TestProcessSitePipeline tests\test_report_pipeline.py::TestAgentToolMerging tests\test_prompt_contract.py tests\test_automation_event.py tests\test_dd_output_fixes.py::TestAsyncOffloading -q --basetemp C:\tmp\ddr-sor-first-prepare-affected-4
uv run pytest tests\test_report_pipeline.py -q --basetemp C:\tmp\ddr-sor-first-report-pipeline-full
uv run pytest tests\test_pipeline_contracts.py -q --basetemp C:\tmp\ddr-sor-first-pipeline-contracts-final
uv run ruff check docs src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\automation_event.py tests\test_report_pipeline.py tests\test_prompt_contract.py tests\test_automation_event.py tests\test_dd_output_fixes.py
uv run mypy src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py
git diff --check
```

Results: focused SOR-first prepare tests passed (`4 passed`), the broad
affected suite passed (`62 passed`), full `test_report_pipeline.py` passed
(`69 passed`), pipeline contracts passed (`10 passed`), Ruff passed, mypy
passed for `server.py` and `report_pipeline.py`, and `git diff --check` passed
with expected Windows LF-to-CRLF warnings only.

## 2026-06-18 - DDR Candidate Idempotency and SOR-First Publish Behavior

- Beads issue `ddr-fs5` tracks Greg's request to prevent forced/manual DDR
  reruns from creating many same-day DD Report Candidate Google Docs.
- Beads issue `ddr-zna` tracks Greg's follow-up direction that candidate
  publishes must still update the system of record first, then log what
  changed or failed and tag the P1 DRI.
- `create_dd_report` now makes candidate creation idempotent when the active
  same-day DDR is protected by the overwrite guard. It searches the target M1
  folder for one automation-owned candidate with the same active source doc,
  report date, and guard reason. If found, it clears and rebuilds that
  candidate instead of creating another Google Doc.
- Candidate docs are now marked with automation appProperties immediately after
  create/reuse and again after the builder succeeds. That means a builder
  failure no longer leaves the next run unable to identify the candidate it just
  created.
- If more than one matching automation-owned candidate exists for the same
  source/date/guard reason, the run fails closed and tells the operator to
  review or clean up the candidates before rerunning.
- SOR behavior was updated: active/final DD report publishes and protected
  candidate publishes both go through `rhodes.due_diligence_update` as soon as
  normalized report data exists. The follow-up `rhodes.report_event` note then
  includes the Rhodes write result or failure and tags the P1 DRI when a Rhodes
  owner can be resolved. Candidate notes still tell the operator to review the
  candidate before replacing the protected active DDR, but the structured DD
  data is no longer held back from Rhodes.
- `docs/process/HOW-IT-WORKS.md` now states the SOR-first contract: structured
  DD data goes to Rhodes before the DDR/candidate note, and the DDR can serve
  as a supporting view rather than the first place the data becomes durable.

Validation:

```powershell
uv run ruff check src\due_diligence_reporter\server.py tests\test_dd_output_fixes.py tests\test_report_pipeline.py
uv run mypy src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py
uv run pytest tests\test_dd_output_fixes.py::TestAsyncOffloading -q --basetemp C:\tmp\ddr-candidate-offloading-final
uv run pytest tests\test_report_pipeline.py::TestProcessSitePipeline -q --basetemp C:\tmp\ddr-candidate-process-pipeline-final
uv run pytest tests\test_report_pipeline.py::TestProcessSitePipeline::test_candidate_publish_updates_sor_before_review_event tests\test_report_pipeline.py::TestProcessSitePipeline::test_report_created_updates_rhodes_due_diligence_before_notifying_p1 -q --basetemp C:\tmp\ddr-candidate-sor-first-focused
uv run pytest tests\test_automation_event.py::test_dd_report_candidate_event_renders_due_diligence_write tests\test_automation_event.py::test_dd_report_summary_event_renders_failed_due_diligence_write -q --basetemp C:\tmp\ddr-candidate-event-focused
uv run ruff check src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\report_pipeline.py tests\test_automation_event.py tests\test_report_pipeline.py
uv run mypy src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\report_pipeline.py
uv run pytest tests\test_automation_event.py tests\test_report_pipeline.py::TestProcessSitePipeline -q --basetemp C:\tmp\ddr-sor-first-candidate-suite
uv run pytest tests\test_dd_output_fixes.py::TestAsyncOffloading tests\test_automation_event.py tests\test_report_pipeline.py::TestProcessSitePipeline tests\test_pipeline_contracts.py -q --basetemp C:\tmp\ddr-sor-first-affected-final
git diff --check
```

Results: Ruff passed, mypy passed for `server.py` and `report_pipeline.py`,
`TestAsyncOffloading` passed (`9 passed`), `TestProcessSitePipeline` passed
(`29 passed` before the SOR-first follow-up), candidate SOR-first focused
pipeline tests passed (`2 passed`), candidate event tests passed (`2 passed`),
Ruff and mypy passed for `automation_event.py` and `report_pipeline.py`, the
event/process pipeline suite passed (`40 passed`), the broad affected suite
passed (`59 passed`), and `git diff --check` passed with expected Windows
LF-to-CRLF warnings only.

## 2026-06-18 - DDR Missing-Folder Site Identity Hardening

- Beads issue `ddr-xon` tracks the WTC/AADP source identity slice.
- Read-only live proof on 2026-06-18 showed
  `lookup_rhodes_site_owner(site_name="Alpha Los Angeles 5400 Beethoven St",
  site_address="5400 Beethoven St, Los Angeles, CA 90066")` now resolves to
  Rhodes site ID `k179e1zh0jg4h3ty1q9knptt8h88ddxz` and linked Drive folder
  `https://drive.google.com/drive/folders/1G8fc0sX3dP83A7uMF5Bhz2pXnhRpaRJz`.
- The helper `_site_id()` now accepts the snake_case `site_id` key in addition
  to `siteId`, `_id`, and `id`, so alternate LocationOS/Rhodes payload shapes
  cannot silently drop verified site identity.
- The missing Drive-folder pipeline test now asserts the saved run manifest and
  emitted `ActionRecord` carry top-level `site_id`, flat action `site_id`, and
  nested `site.site_id` whenever Rhodes lookup supplied a verified site ID.
- No live mutation was performed in this slice.

Validation:

```powershell
uv run pytest tests\test_rhodes.py::test_lookup_rhodes_site_owner_accepts_snake_case_site_id tests\test_report_pipeline.py::TestProcessSitePipeline::test_missing_drive_folder_blocks_with_rhodes_setup_message -q --basetemp C:\tmp\ddr-site-id-focused
uv run pytest tests\test_rhodes.py tests\test_report_pipeline.py tests\test_pipeline_contracts.py -q --basetemp C:\tmp\ddr-site-id-suite
uv run ruff check src\due_diligence_reporter\rhodes.py tests\test_rhodes.py tests\test_report_pipeline.py
uv run mypy src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\report_pipeline.py
uv run python -m py_compile src\due_diligence_reporter\rhodes.py tests\test_rhodes.py tests\test_report_pipeline.py
git diff --check
```

Results:

- Focused tests passed: `2 passed`.
- Affected suite passed: `107 passed`.
- Ruff passed.
- Mypy passed for `rhodes.py` and `report_pipeline.py`.
- `py_compile` passed.
- `git diff --check` passed with expected LF-to-CRLF warnings only.

## 2026-06-18 - WTC Source-Context Blocker for Missing Drive Folder

- Bead `ddr-3kd` was created and closed for the WTC/AADP source-context gap.
- DDR failed-step `ActionRecord` payloads now include flat `site_id`,
  `site_name`, and `current_milestone` fields in addition to the nested `site`
  payload, so WTC/AADP can consume the same site identity consistently when it
  exists.
- When DDR records `missing_drive_folder_url` but `PipelineRun.site_id` is
  empty, the action record now says the Rhodes site ID or site record URL must
  be resolved before AADP can safely create or link the Drive folder. It keeps
  `owning_workflow=ddr` / `workflow_owner=ddr` instead of implying AADP can act
  from a display name alone.
- This pairs with the WTC change that only force-routes
  `missing_drive_folder_url` to AADP when `site_id` is present. No-site-ID
  actions remain source-context blocked on the dashboard.
- Live evidence: WTC inspected the selected Alpha LA DDR manifest
  `20260610174320-alpha-los-angeles-5400-beethoven-st-101c1d16`; it had
  `site_id=null` and nested `site.site_id=""`. Newer Alpha LA missing-folder
  manifests on 2026-06-18 also lacked `site_id`, so the dashboard should not
  keep sending those records to AADP until DDR/Rhodes emits verified identity.

Validation:

```powershell
uv run pytest tests\test_pipeline_contracts.py::test_pipeline_run_emits_action_record_for_failed_step tests\test_pipeline_contracts.py::test_pipeline_run_marks_missing_drive_folder_without_site_id_as_source_context_blocked tests\test_report_pipeline.py::TestProcessSitePipeline::test_missing_drive_folder_blocks_with_rhodes_setup_message -q --basetemp C:\tmp\ddr-source-context-focused
uv run pytest tests\test_pipeline_contracts.py tests\test_report_pipeline.py -q --basetemp C:\tmp\ddr-source-context-suite
uv run ruff check src\due_diligence_reporter\pipeline_contracts.py tests\test_pipeline_contracts.py
uv run mypy src\due_diligence_reporter\pipeline_contracts.py
uv run python -m py_compile src\due_diligence_reporter\pipeline_contracts.py tests\test_pipeline_contracts.py
git diff --check
```

Results:

- Focused source-context tests passed: `3 passed`.
- Pipeline contract/report pipeline suite passed: `75 passed`.
- Ruff passed.
- Mypy passed for `pipeline_contracts.py`.
- `py_compile` passed.
- `git diff --check` passed with expected LF-to-CRLF warnings only.

## 2026-06-18 - LocationOS MCP Readback Guard for DDR SOR and Note Writes

- Beads issue `ddr-gmg` tracks the broader migration away from stale
  `RHODES_API_KEY` blocker language and older write assumptions. This slice
  keeps the existing hosted LocationOS MCP JSON-RPC transport, but now treats
  the bearer token as LocationOS MCP auth: `LOCATIONOS_MCP_API_KEY` is preferred
  and `RHODES_API_KEY` remains a legacy/GitHub-workflow alias.
- A fresh read-only `codex exec --ephemeral` LocationOS probe resolved Chapel
  Hill and confirmed current read shapes for `getSite` due-diligence data and
  `listNotes`. No live mutation was run in this slice because production
  Rhodes/LocationOS writes require exact target/body preview and explicit
  approval.
- `update_rhodes_due_diligence` now readbacks `getSite` after
  `updateDueDiligence` and fails closed with `reason=readback_failed` if any
  attempted field is missing or mismatched. Successful results include
  `readback.status=verified` and the verified field list.
- `add_rhodes_site_note` now readbacks `listNotes` after note creation. It
  verifies either the returned note ID or exact body match, returns the
  canonical readback note ID, and fails with `reason=note_readback_failed` if
  the note cannot be found or the body mismatches.
- Operator-facing missing-folder/auth messages now distinguish "LocationOS MCP
  auth is not configured" from "Rhodes did not return a linked Drive folder,"
  so the workflow no longer tells operators that an API key is the blocker when
  the issue is auth context or missing folder linkage.
- GitHub workflows that need LocationOS/Rhodes auth now accept either
  `LOCATIONOS_MCP_API_KEY` or legacy `RHODES_API_KEY`, fail fast with
  `LOCATIONOS_MCP_API_KEY or RHODES_API_KEY missing`, and write
  `LOCATIONOS_MCP_API_KEY` into `.env` for runtime use while preserving the
  legacy alias.
- `docs/process/HOW-IT-WORKS.md` and `.env.example` document the preferred
  `LOCATIONOS_MCP_API_KEY` name, legacy `RHODES_API_KEY` alias, and the
  readback responsibilities in `rhodes.py`.

Validation:

```powershell
uv run pytest tests\test_rhodes.py tests\test_report_pipeline.py tests\test_rhodes_events.py tests\test_diagnose_site_readiness.py tests\test_dd_output_fixes.py tests\test_docs_env_contract.py -q --basetemp C:\tmp\ddr-locationos-readback-affected
uv run ruff check src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\server.py tests\test_rhodes.py tests\test_report_pipeline.py tests\test_diagnose_site_readiness.py tests\test_docs_env_contract.py
uv run mypy src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\server.py
uv run pytest tests\test_workflow_contracts.py tests\test_docs_env_contract.py -q --basetemp C:\tmp\ddr-locationos-env-contract
uv run pytest tests\test_workflow_contracts.py tests\test_docs_env_contract.py -q --basetemp C:\tmp\ddr-locationos-workflow-contract
uv run ruff check .
uv run mypy src\
uv run pytest tests --ignore=tests\_tmp --basetemp C:\tmp\ddr-locationos-full-final
git diff --check
```

Results: affected suite passed (`186 passed`), env/workflow contracts passed
(`18 passed`, then `19 passed` after workflow secret-contract coverage), full
Ruff passed, full mypy passed (`47 source files`), and full tracked pytest
passed (`1225 passed`). Raw `uv run pytest` still collects
existing unreadable local temp/cache directories
`pytest-cache-files-o55d_4rl` and `tests/_tmp/pytest-cache-files-lhmtb2lz`;
running against `tests` with `--ignore=tests\_tmp` avoids that unrelated
Windows scratch-directory issue.

## 2026-06-18 - P1 DRI Note Required for SOR Write Success or Failure

- Beads issue `ddr-gmg` now includes the additional requirement that after a
  DDR due-diligence SOR write is attempted, the workflow must add a Rhodes
  report-event note and tag the P1 DRI with what was written or what failed.
- The prior flow failed closed immediately after `updateDueDiligence` returned
  `status=failed`, so the P1 DRI could miss the failed SOR mutation. The new
  flow still treats the SOR write failure as a failed pipeline step and still
  suppresses the success email, but it proceeds into `rhodes.report_event` so
  Rhodes/Chat notification can carry the failure details.
- `AutomationEvent` rendering now treats failed due-diligence writes as
  decision-required. The note action line says to review the failed Rhodes
  due-diligence write and DD report, and the detail line names the attempted
  fields plus the failure reason when available.
- `process_site_pipeline` now records `rhodes.report_event` after both
  successful and failed due-diligence write attempts. It only sends the DD
  success/update email when the SOR write did not fail.
- `docs/process/HOW-IT-WORKS.md` was updated so the durable process contract
  matches the new behavior: P1 DRI gets a note/fallback alert for write success
  or write failure.

## 2026-06-18 - Chapel Hill Fresh DDR via LocationOS Folder Lookup

- The earlier operator-facing blocker text saying Rhodes was unavailable
  because `RHODES_API_KEY` was missing was wrong for current operations.
  `codex mcp list` / `codex mcp get locationos --json` showed the hosted
  LocationOS MCP is configured and enabled on this machine. The current desktop
  thread did not expose `mcp__locationos__...` tools directly, so a fresh
  `codex exec --ephemeral` probe used LocationOS MCP to resolve the site.
- LocationOS resolved `Alpha Chapel Hill 605 Eastowne Dr` /
  `605 Eastowne Dr, Chapel Hill, NC` to the linked Drive folder:
  `https://drive.google.com/drive/folders/1wRDbYbc57xjXMJtaOmy9_fPa9N2n0_Mr`.
- A strict read-only readiness diagnostic found a Vendor SIR and no Building
  Inspection, so the workflow could create a partial report but not a full
  final-ready report.
- Forced Chapel Hill regeneration initially exposed two additional Google Docs
  builder defects beyond the prior `ddr-2w8` cell-index fix:
  - Phase 5 reused one `_DocBuilder` after an `insert_table`, leaving `_idx=-1`
    and causing a second append request at invalid body index `0`.
  - Both cost tables were then populated in one forward batch, so inserts into
    the first table shifted cached indexes for the second table.
- Updated `src/due_diligence_reporter/google_doc_builder.py` so each cost table
  is created in its own readback/append phase, cost-table text population runs
  from later table to earlier table, and `_batch_update` now validates outgoing
  Google Docs body indexes/ranges before sending invalid requests.
- Added regression coverage in `tests/test_google_doc_builder.py` for invalid
  index validation, separate cost-table batches, and descending cost-table
  population order.
- A fresh forced Chapel Hill run succeeded in report generation. Because the
  active same-day document lacked the automation revision watermark, the system
  protected it and created a candidate instead of overwriting:
  `https://docs.google.com/document/d/11kFej7RiTPHIN707rDCUii0tXaWd5oHIuXyaKkyaBh8/edit?usp=drivesdk`.
  The active protected doc was
  `https://docs.google.com/document/d/1wEwkYD9-VG4HkfCLLaeCyqjKKVcvrXRk7IhSG8nYJ4g/edit?usp=drivesdk`.
- Manifest:
  `.ddr-runs\20260618130744-alpha-chapel-hill-605-eastowne-dr-380c2fed.json`.
  Status was `republish_candidate_created`; missing docs included
  `Building Inspection`.
- Google export readback of the candidate showed the title, partial-report
  banner, referenced-reports section, and no unresolved `{{...}}` braces.
- Rhodes `report_event` still failed through the repo's older note-write path
  with `rejectionReason=elicitation_unsupported`; Google Chat fallback was
  skipped because `DDR_GOOGLE_CHAT_WEBHOOK_URL` was not configured. Track the
  durable fix separately: DDR should route report-event/note writes through the
  current LocationOS MCP-compatible write surface, with readback verification.
- Failed blank/partial candidate docs from earlier forced attempts were left in
  place. Do not delete them without explicit approval.

Validation:

```powershell
uv run pytest tests\test_google_doc_builder.py::TestBatchUpdateValidation tests\test_google_doc_builder.py::TestBuildDdReportDoc::test_cost_tables_are_created_in_separate_valid_batches tests\test_google_doc_builder.py::TestCostBreakdownTableInsertOrder tests\test_google_doc_builder.py::TestReferencedReportsTableInsertOrder -q --basetemp C:\tmp\ddr-doc-builder-cost-order
uv run ruff check src\due_diligence_reporter\google_doc_builder.py tests\test_google_doc_builder.py
uv run pytest tests\test_google_doc_builder.py -q --basetemp C:\tmp\ddr-doc-builder-full-cost-order
uv run mypy src\due_diligence_reporter\google_doc_builder.py
uv run pytest tests\test_dd_output_fixes.py::TestAsyncOffloading::test_create_dd_report_uses_to_thread tests\test_dd_output_fixes.py::TestAsyncOffloading::test_create_dd_report_rebuilds_existing_same_day_doc tests\test_dd_output_fixes.py::TestAsyncOffloading::test_create_dd_report_moves_legacy_root_report_to_m1 tests\test_dd_output_fixes.py::TestAsyncOffloading::test_create_dd_report_creates_candidate_when_existing_doc_has_no_watermark tests\test_dd_output_fixes.py::TestAsyncOffloading::test_create_dd_report_creates_candidate_when_active_revision_changed -q --basetemp C:\tmp\ddr-create-report-candidate-cost-order
```

Results: focused cost-order/index tests passed (`5 passed`), full Google Doc
builder tests passed (`84 passed`), Ruff passed, mypy passed for the touched
source file, and focused create-dd-report candidate tests passed (`5 passed`).

## 2026-06-17 - Google Doc Builder Guard for Invalid Table Cell Indexes

- Beads issue `ddr-2w8` tracks the manual Chapel Hill `create_dd_report`
  failure where Google Docs returned
  `Invalid requests[6].insertText: Index must be greater than or equal to 0`.
- Root cause was not a DD template copy/permissions problem. The current
  `create_dd_report` path creates a blank Google Doc and the programmatic
  builder writes the structure. The builder could read back a table cell with
  missing paragraph element indexes and fall back to insertion/range index `0`,
  which is invalid for Docs body inserts.
- Updated `src/due_diligence_reporter/google_doc_builder.py` so table-cell
  insertion and style ranges use valid paragraph/text-run indexes, then valid
  table-cell metadata. If Google returns a cell with no usable index metadata,
  the builder now raises a clear local error instead of sending a corrupting
  Docs request at index `0` or the document start.
- Added regression coverage in `tests/test_google_doc_builder.py` for empty
  table-cell content, missing paragraph elements, empty-cell ranges, and
  missing metadata fail-closed behavior.

Validation:

```powershell
uv run pytest tests\test_google_doc_builder.py::TestCellIndex -q --basetemp C:\tmp\ddr-doc-builder-cell-index-tight
uv run pytest tests\test_google_doc_builder.py -q --basetemp C:\tmp\ddr-doc-builder-full-tight
uv run pytest tests\test_dd_output_fixes.py::TestAsyncOffloading::test_create_dd_report_uses_to_thread tests\test_dd_output_fixes.py::TestAsyncOffloading::test_create_dd_report_rebuilds_existing_same_day_doc tests\test_dd_output_fixes.py::TestAsyncOffloading::test_create_dd_report_moves_legacy_root_report_to_m1 -q --basetemp C:\tmp\ddr-create-report-wrapper-tight
uv run ruff check src\due_diligence_reporter\google_doc_builder.py tests\test_google_doc_builder.py
uv run mypy src\due_diligence_reporter\google_doc_builder.py
```

Results: focused cell-index tests passed (`6 passed`), full Google Doc builder
tests passed (`80 passed`), create-dd-report wrapper tests passed (`3 passed`),
Ruff passed, and mypy passed for the touched source file.

## 2026-06-17 - WTC Action Records for DDR SOR Updates and Missing P1 Route

- Beads issue `ddr-9ku` tracks Greg's follow-up decision to start missing P1
  DRI handling with the existing WTC/AADP action-record route rather than a
  direct AADP assignment attempt from DDR.
- DDR due-diligence field writes now follow the agreed lifecycle:
  - interim writes use `status=data-gathering`;
  - source-triggered updates with the full vendor set present but open
    verification items remaining use `status=follow-up`;
  - final-ready writes use `status=complete`;
  - `dateCompleted` and `ddReportLink` are written only for final-ready runs.
- `PipelineRun.action_records` now emits WTC-compatible success facts for:
  - `ddr_sor_updated` when `updateDueDiligence` returns `status=updated`;
  - `ddr_p1_note_created` / `ddr_rhodes_note_created` when the Rhodes report
    event note is created.
- If Rhodes owner lookup returns `owner_missing`, the run manifest now carries
  `p1_dri_missing=true` and emits a queued `missing_p1_dri` ActionRecord with
  `source_workflow=ddr`, `owning_workflow=aadp`, and `workflow_owner=aadp`.
  DDR does not attempt to assign the P1 DRI directly in this route; AADP owns
  the remediation/readback.
- Existing failed/blocked step and open-question ActionRecords remain in place.
- Updated `docs/process/HOW-IT-WORKS.md` to describe the interim/final/follow-up
  status behavior, final-only DD Report link, and WTC/AADP missing-P1 route.

Validation:

```powershell
uv run pytest tests\test_pipeline_contracts.py tests\test_report_pipeline.py -q --basetemp C:\tmp\ddr-wtc-action-records-focused-2
uv run ruff check src\due_diligence_reporter\pipeline_contracts.py src\due_diligence_reporter\report_pipeline.py tests\test_pipeline_contracts.py tests\test_report_pipeline.py
uv run mypy src\due_diligence_reporter\pipeline_contracts.py src\due_diligence_reporter\report_pipeline.py
uv run ruff check .
uv run mypy src\
uv run pytest tests --ignore=tests\_tmp --basetemp C:\tmp\ddr-wtc-action-records-full-2
```

Results:

- Focused pipeline/contract tests passed: `74 passed`.
- Focused Ruff and mypy passed.
- Full Ruff passed.
- Full mypy passed: no issues in 47 source files.
- Full pytest against `tests/` passed: `1210 passed`.

## 2026-06-17 - DDR Due-Diligence Rhodes Field Write Before P1 Review Notice

- Beads issue `ddr-691` tracks Greg's request for the workflow to write the
  DD result to the system of record, then notify the P1 DRI so they can review
  the action.
- Added a LocationOS/Rhodes writer wrapper for `updateDueDiligence` in
  `src/due_diligence_reporter/rhodes.py`.
- `process_site_pipeline` now records a `rhodes.due_diligence_update` step
  after a DD report validates and before the P1 review note/email path.
- The workflow maps normalized DDR report fields to Rhodes due-diligence
  fields:
  `status`, `dateCompleted`, `ddReportLink`, Fastest Open capacity/capex/date,
  Max Capacity capacity/capex/date, regulatory/building/play-area/school-ops
  score/comment, and explicit `recommendation` when the report data already
  supplies `go` or `no-go`.
- Status is `complete` only when no open verification items or outstanding
  full-report vendor docs remain; otherwise it writes `follow-up`.
- Placeholder values such as `[Not found - ...]` and raw `{{token}}`
  placeholders are not written into Rhodes due-diligence fields.
- A real `updateDueDiligence` failure now fails closed for the P1 success/review
  notification: `rhodes.report_event` and `notify.email` are not sent after a
  failed SOR write. Skipped cases such as missing site identity remain visible
  in the manifest and preserve prior notification behavior.
- Successful SOR writes are included in the `dd_report_created` /
  `dd_report_updated` AutomationEvent note, which now asks the P1 DRI to review
  the Rhodes due-diligence fields and DD report. A successful SOR write bypasses
  the old open-ask frequency cap so the P1 DRI is notified about the mutation.
- Updated `docs/process/HOW-IT-WORKS.md` to reflect the write-then-notify
  contract and fail-closed behavior.

Validation:

```powershell
uv run pytest tests\test_rhodes.py tests\test_report_pipeline.py -q --basetemp C:\tmp\ddr-due-diligence-update-tests
uv run pytest tests\test_automation_event.py tests\test_pipeline_contracts.py -q --basetemp C:\tmp\ddr-due-diligence-contract-tests
uv run pytest tests\test_rhodes.py tests\test_report_pipeline.py tests\test_automation_event.py tests\test_pipeline_contracts.py -q --basetemp C:\tmp\ddr-due-diligence-update-all-focused
uv run ruff check src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\pipeline_contracts.py tests\test_rhodes.py tests\test_report_pipeline.py
uv run mypy src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\pipeline_contracts.py
uv run ruff check .
uv run mypy src\
uv run pytest tests --ignore=tests\_tmp --basetemp C:\tmp\ddr-due-diligence-full-tests
```

Results:

- Focused Rhodes/report-pipeline tests: `89 passed`.
- Adjacent AutomationEvent/pipeline-contract tests: `16 passed`.
- Combined focused suite after final typing edit: `105 passed`.
- Focused ruff and mypy passed.
- Full ruff passed.
- Full mypy passed: no issues in 47 source files.
- Full pytest against `tests/` passed: `1204 passed`.
- A raw `uv run pytest --basetemp C:\tmp\ddr-due-diligence-full-pytest`
  still hits existing unreadable temp cache directories
  `pytest-cache-files-o55d_4rl` and `tests/_tmp/pytest-cache-files-lhmtb2lz`
  during collection; rerunning against `tests` with `--ignore=tests\_tmp`
  avoids that unrelated Windows cache collection issue.

## 2026-06-16 - Missing Drive Folder Review-Execution Handler

- Beads issue `ddr-0dj` tracked the source-repo slice.
- Added a specific `missing_drive_folder_url` handler in
  `src/due_diligence_reporter/review_execution.py`.
- Approved missing-drive-folder review requests now emit `needs_review`
  source-owned readback instead of the generic "no source-specific execution
  handler" blocker.
- The handler is intentionally non-mutating:
  - it does not create or link Drive folders;
  - it does not write Rhodes;
  - it does not send Chat;
  - it tells operators that AADP/Rhodes folder provisioning must create or link
    the site Drive folder before DDR readiness can rerun.
- Review-execution result attachment now overwrites request-level
  `action_taken`, `review_reason`, `error_summary`, `execution_status`, and
  `execution_summary` with the latest source readback so stale dashboard input
  text is not echoed back into WTC.
- Existing routing-instruction carry-through is preserved and sanitized.
- Production WTC proof deployed at
  `https://site-nu-seven-29.vercel.app` via Vercel deployment
  `dpl_umCVw3yAuVbDx8fHqnWUSadtPAbd`.

Verification:

```powershell
uv run pytest tests/test_review_execution.py tests/test_ddr_cli.py -q --basetemp C:\tmp\ddr-missing-folder-review-execution-2
uv run ruff check src\due_diligence_reporter\review_execution.py tests\test_review_execution.py tests\test_ddr_cli.py
uv run mypy --explicit-package-bases src\due_diligence_reporter\review_execution.py src\due_diligence_reporter\ddr_cli.py
uv run python -B -c "import ast, pathlib; paths=['src/due_diligence_reporter/review_execution.py','tests/test_review_execution.py','tests/test_ddr_cli.py']; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8'), filename=p) for p in paths]; print('ddr ast ok')"
uv run ddr review-execution --review-requests C:\Users\foote\.claude\Work\repos\workflow-telemetry-center\data\review-execution-requests-ddr-result.json --output C:\tmp\ddr-missing-folder-review-execution-result-v2.json
```

Results:

- Focused pytest passed: `15 passed`.
- Ruff passed.
- Mypy passed for `review_execution.py` and `ddr_cli.py`.
- AST parse passed.
- CLI smoke produced `status=needs_review`, `attempted=1`,
  `needs_review=1`, `blocked=0`, `errors=0`.
- WTC live readback shows:
  `DDR handled the approved request as a missing Drive folder prerequisite.
  DDR did not create or link the folder; AADP/Rhodes folder provisioning must
  create or link the site Drive folder before DDR readiness can rerun.`

Next:

- Commit and push the verified source/test changes when Greg approves making
  the source repo durable.
- Broader DDR review-execution automation still needs separate handlers for
  report generation, source-read repair, and reconciliation-specific reruns.

## 2026-06-15 - Manual Chat Houston Rhodes Resolver Fix

- Beads issue `ddr-eer` tracks the repeated manual Google Chat / BrainTrust DDR
  failure where the card still said Rhodes was unavailable after the earlier
  public-tool folder fallback had shipped.
- Live LocationOS reads confirmed Rhodes was available. The current
  `listSites(location="Houston", status="active")` result now has two active
  matches: `Alpha Houston 777 W 23rd St` and
  `Alpha The Woodlands 2000 Woodlands Pkwy`.
- Live `resolveSite(name="Houston")` still resolves to the old cancelled
  `Alpha School Houston 5625`, so the broad city-only fallback became unsafe
  once a second active Houston-metro site appeared.
- Updated `RhodesClient.resolve_site` so broad name-only active-location
  lookups still avoid inactive/cancelled fallback, but can choose a unique
  central-city active match over metro/suburb matches. True ties remain
  ambiguous and fall through to the existing resolver behavior.
- Added focused coverage for the current Houston shape: Woodlands appears as a
  Houston metro match, but `Alpha Houston 777 W 23rd St` wins because its name
  and Rhodes region/market metadata match the requested city.
- Live non-mutating smoke check from this checkout:
  `lookup_rhodes_site_owner(site_name="Houston")` returned
  `Alpha Houston 777 W 23rd St`, `777 W 23rd St, Houston, TX`, linked Drive
  folder status `found`, and P1 Brandon Gee.

Verification:

```powershell
uv run pytest tests\test_rhodes.py tests\test_diagnose_site_readiness.py::test_missing_drive_url_resolves_rhodes_site_folder tests\test_diagnose_site_readiness.py::test_check_site_readiness_missing_drive_url_resolves_rhodes_site_folder tests\test_dd_output_fixes.py::TestListDriveDocumentsFiltering::test_missing_drive_url_resolves_rhodes_site_folder -q --basetemp C:\tmp\ddr-rhodes-chat-manual
uv run ruff check src\due_diligence_reporter\rhodes.py tests\test_rhodes.py
uv run mypy src\due_diligence_reporter\rhodes.py
uv run python -c "from due_diligence_reporter.rhodes import lookup_rhodes_site_owner; import json; r=lookup_rhodes_site_owner(site_name='Houston'); print(json.dumps({k:r.get(k) for k in ['status','site_name','site_address','drive_folder_status','drive_folder_url','p1_assignee_name','message']}, indent=2, sort_keys=True))"
```

Results: focused pytest passed (`29 passed`); Ruff passed; mypy passed; live
smoke resolved Houston to the active 777 W 23rd site with the linked Drive
folder and Brandon Gee as P1.

## 2026-06-15 - DDR Aerie-Style Formatting Alignment

- Beads issue `ddr-8a6` tracks the DDR formatting alignment slice.
- Updated the Google Docs renderer to keep the site metadata table first, then
  render an Aerie-style Due Diligence table while omitting Completed Date and
  DD Report.
- Reordered the body so Fastest Open renders first, Max Capacity renders
  second, Direct Answer follows those path narratives, path-specific cost
  breakdown tables follow, and detailed score explanations render after costs.
- Added answer-first support-bullet rendering for Fastest Open, Max Capacity,
  Direct Answer, and score comments, preserving the tight answer line followed
  by supporting facts.
- Added score normalization for Aerie numeric inputs: `1 - Green`,
  `2 - Yellow`, and `3 - Red`; four scored categories total to 4 best and 12
  worst.
- Added report tokens and aliases for `exec.fastest_open_summary`,
  `exec.max_capacity_summary`, and the `regulatory`, `building`, `play_area`,
  and `school_ops` score/comment pairs from Rhodes/Aerie-style data.
- Updated the V4 markdown template, prompt contract, process docs, schema, and
  Google Docs builder tests.
- Validation on 2026-06-15:
  - `uv run pytest tests/test_prompt_contract.py` passed (`3 passed`).
  - `uv run pytest --basetemp C:\tmp\ddr-pytest-basetemp --ignore=pytest-cache-files-o55d_4rl --ignore=tests/_tmp/pytest-cache-files-lhmtb2lz` passed (`1196 passed` after score-normalization coverage).
  - `uv run ruff check .` passed.
  - `uv run mypy src/` passed.

## 2026-06-11 - Boca Raton 1515 Scorecard v2 Re-run

- Beads issue `ddr-e7n` tracks the Boca Raton 1515 updated-DDR scorecard
  rerun.
- Reviewed the current DDR, Rhodes due-diligence fields, registered documents,
  Drive root/M1 contents, Jun 11 RayCon 8,500 SF max-buildout summary, Jun 11
  Matterport-only RayCon summary, older Jun 7 RayCon summary, raw
  `raycon_scenario.json`, Jun 10 building inspection PDF, Jun 09 SIR PDF, the
  opening plan, Block Plan V2, and the Florida school-approval report.
- Published corrected scorecard markdown to the synced M1 folder:
  `G:\Shared drives\Education Ops\All Locations\Alpha Boca Raton 1515 N Federal Hwy\M1 - Acquire Property\Future-State Ops Scorecard - Alpha Boca Raton 1515 N Federal Hwy - 2026-06-11 v2.md`.
- Drive indexed the v2 file as
  `https://drive.google.com/file/d/1WQry2xJF4UHD0rwKA2HQAGRv9tfsb9S_/view?usp=drivesdk`.
- Updated the existing Rhodes `other` / `acquireProperty` scorecard document
  row to the v2 Drive file, instead of registering a duplicate document.
- Corrected controlling values: Fastest Open remains 29 / ~$460k from the
  updated DDR; Max Capacity is now 93 / $875,387 from the Jun 11 8,500 SF
  RayCon run; all four dimensions remain YELLOW.
- Important source-rank note: the prior 40/51/63-student RayCon artifacts are
  superseded for MaxCap. The raw `raycon_scenario.json` failed validation and
  carried wrong-source risks, while the Jun 11 8,500 SF summary was created to
  correct the Matterport-only run.
- Follow-up note: live Rhodes DD fields still have older target dates
  (`2026-12-01` FO and `2026-12-17` MaxCap) and the school-ops comment still
  says "MaxCap is only 40 students." The scorecard recommends replacing that
  language with the 93-student MaxCap posture, but REBL3/Aerie Portfolio owns
  DD field writes.

## 2026-06-11 - Boston 3815 Washington Scorecard v2 Re-run

- Beads issue `ddr-536` tracks the Boston / Croft 3815 Washington scorecard
  evidence review.
- Reviewed the current DDR, Rhodes due-diligence fields, registered documents,
  M1 Drive contents, audited capacity analysis, audited block plan, opening
  plan, OPS package, RayCon JSON, Jun 11 building inspection report, Jun 8 SIR,
  and older April AI SIR / school-approval docs.
- Published corrected scorecard markdown to the synced M1 folder:
  `G:\Shared drives\Education Ops\All Locations\Alpha Boston 3815 Washington St\M1 - Acquire Property\Future-State Ops Scorecard - Alpha Boston 3815 Washington St - 2026-06-11 v2.md`.
- Drive indexed the v2 file as
  `https://drive.google.com/file/d/1g0J7Bw8ew61T-g25qgvy2Mp_zTxbAf-Y/view?usp=drivesdk`.
- Updated the existing Rhodes `other` / `acquireProperty` scorecard document
  row to the v2 Drive file, instead of registering a duplicate document.
- Corrected controlling values: Fastest Open 322 / $1.64M / 2026-09-09; Max
  Capacity 367 / $3.09M / 2027-08-10; scores Regulatory YELLOW, Building
  YELLOW, Play Area YELLOW, School Ops YELLOW.
- Important source-rank note: the later raw `raycon_scenario.json` showing
  51/52 capacity was excluded because it resolved the physical model to 300
  Cambridge St / 4,444 SF rather than the 3815 Washington Croft building.

## 2026-06-11 - Google Chat Rhodes Folder Fallback Hardened

- Beads issue `ddr-0n3` tracks the incident where Google Chat / BrainTrust kept
  returning a card that said Rhodes was unavailable and asked Brandon for the
  Houston Drive folder URL.
- Live LocationOS reads confirmed Rhodes itself was available for Houston:
  `listSites(location="Houston", status="active")` returned the active
  `Alpha Houston 777 W 23rd St` site, and `getSite` returned its linked Drive
  folder ID.
- First fix commit `3c572d1` made broad name-only Rhodes lookups prefer the
  single active `listSites(location=..., status="active")` match and hydrate
  sparse rows before returning owner / Drive-folder context.
- The repeated Chat failure showed another public-tool path still existed:
  `list_drive_documents`, `check_site_readiness`, and
  `diagnose_site_readiness` rejected missing `drive_folder_url` before trying
  Rhodes. Commit `a0f6864` added a shared public MCP boundary resolver so
  broad site-name calls like `Houston` resolve the linked Rhodes Drive folder
  before touching Drive or readiness checks.
- `report_pipeline.TOOL_DEFINITIONS` no longer marks `list_drive_documents`
  `drive_folder_url` as required; the description now instructs callers to pass
  `site_name` / `site_address` so the tool can resolve the folder from Rhodes
  instead of asking the user for a folder.
- Pushed `a0f6864` to GitHub `main`. Publish run `27362106589` checked out
  `a0f68644e1a40fdee15659e2900e72526b5c277d`, verified required secrets,
  published to MCP Hive, and returned `healthStatus=HEALTHY`,
  `toolsCount=14`, `updatedAt=2026-06-11T16:33:34.931Z`.
- Note: the Google Chat card title still says `Due Diligence Reporter Two`,
  while the published MCP Hive is named `Alpha DD Report Generator`. If Chat
  still returns the same fallback after this publish, the remaining issue is
  likely Chat/BrainTrust app wiring to a different runtime or stale hive rather
  than Rhodes or this DDR package.

Verification:

```powershell
uv run pytest tests\test_diagnose_site_readiness.py tests\test_dd_output_fixes.py::TestListDriveDocumentsFiltering tests\test_rhodes.py -q --basetemp C:\tmp\ddr-chat-rhodes-public-tools
uv run ruff check src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py tests\test_diagnose_site_readiness.py tests\test_dd_output_fixes.py
uv run mypy src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py
git diff --check -- src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py tests\test_diagnose_site_readiness.py tests\test_dd_output_fixes.py
gh run view 27362106589 --json status,conclusion,createdAt,updatedAt,url,headSha,name
```

Results: focused pytest passed (`48 passed`); Ruff passed; mypy passed; diff
check reported only expected Windows LF-to-CRLF warnings; MCP Hive publish
completed successfully.

## 2026-06-10 - Dashboard Review Execution Consumer Added

- Beads issue `ddr-e4t` tracks this slice.
- Added the source-owned DDR review-execution command:
  ```powershell
  uv run ddr review-execution `
    --review-requests <path> `
    --output <path> `
    [--max-actions N] `
    [--dry-run]
  ```
- The command consumes dashboard-approved `review_execution_requests.v1`
  payloads for DDR-owned requests only:
  - `owning_workflow=ddr`;
  - known DDR owner keys;
  - or source `action_id` values prefixed with `ddr:`.
- The command emits a sanitized `ddr_review_execution_result.v1` artifact with:
  - `runs[]` using `workflow_id=ddr`,
    `source_type=review_execution_result`, and
    `subworkflow_id=ddr-review-execution`;
  - source-owned `action_records`;
  - execution summaries keyed back to the original source `action_id`;
  - echoed request context sanitized for emails, URLs, local paths, request
    IDs, and secret/config names.
- The first implementation is conservative and non-mutating. It does not rerun
  report generation, publish docs, write Drive/Rhodes, or send Chat. Unsafe
  report-generation, source-read, inbox-scan, RayCon follow-up, vendor-doc, and
  reconciliation requests return `needs_review` or `blocked` until the source
  run/document context is safe. `markNotApplicable` returns
  `skipped_already_corrected`.
- Workflow Telemetry Center invokes this command through
  `scripts/execute-ddr-review-requests.ps1` and deploys its status to
  `https://site-nu-seven-29.vercel.app`.
- Pushed commit `8b447d7` (`Add DDR dashboard review execution`) to
  `GFooteGK1/due-diligence-reporter` `main`.
- Scheduler proof from WTC after registration:
  - task: `Workflow Telemetry Dashboard Auto Deploy`;
  - DDR repo path:
    `C:\Users\foote\Documents\Codex\2026-06-04\research-analyst-i-need-to-be\work\ddr-review-execution-main`;
  - last run time: `6/10/2026 1:01:00 PM` CT;
  - last result: `0`;
  - live dashboard `monitor_last_checked_at`:
    `2026-06-10T13:00:34.397803-05:00`;
  - DDR bridge exit code: `0`.

Verification:

```powershell
uv run pytest tests/test_review_execution.py tests/test_ddr_cli.py -q --basetemp C:\tmp\ddr-review-execution-origin-tests
uv run ruff check src\due_diligence_reporter\review_execution.py src\due_diligence_reporter\ddr_cli.py tests\test_review_execution.py tests\test_ddr_cli.py
uv run mypy --explicit-package-bases src\due_diligence_reporter\review_execution.py src\due_diligence_reporter\ddr_cli.py
uv run python -B -c "import ast, pathlib; paths=['src/due_diligence_reporter/review_execution.py','src/due_diligence_reporter/ddr_cli.py','tests/test_review_execution.py','tests/test_ddr_cli.py']; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8'), filename=p) for p in paths]; print('ddr ast ok')"
```

Results: focused pytest passed with 13 tests; focused Ruff passed; focused mypy
passed; AST parse passed.

Known repo-wide gate caveats from the original local validation worktree:

- `uv run pytest tests --ignore=tests/_tmp --basetemp C:\tmp\ddr-review-execution-tests-all`
  still has 13 unrelated pre-existing failures:
  - 11 in `tests/test_assignment.py` around `assign_p1()` /
    `build_site_counts()` signature drift;
  - 2 in `tests/test_sender_filter.py` around the missing
    `due_diligence_reporter.inbox_scanner.build_site_summary` patch target.
- `uv run ruff check .` still has unrelated pre-existing issues in
  `scripts\reprocess_mislabeled.py`, `tests\test_cds_verification.py`, and
  `tests\test_sender_filter.py`.

## 2026-06-09 - RayCon Alpha Capacity Live Proof Completed

- RayCon commit `e385f6328dd58399d6051ed7f735c72a11c007a7` was pushed to
  `b-randongee/RayCon.git`, built as
  `gcr.io/brandon-gee/raycon-api:e385f6328dd58399d6051ed7f735c72a11c007a7`,
  and deployed to Cloud Run revision `raycon-api-00201-99g`.
- Both RayCon service URLs reported `/version.git_commit` as
  `e385f6328dd58399d6051ed7f735c72a11c007a7` before DDR dispatch.
- DDR release commit `bae01f4a0fc107c77bafc2397faa4fb6304989b3` was merged
  with current `origin/main` as
  `e6b685b79adf967dff089f1b9833b32dee8c079a` and pushed to GitHub `main`.
  The local validation checkout's `origin` still points at the dirty canonical
  local repo, so it may show ahead of local origin even though GitHub is current.
- Scoped Miami Beach proof command ran non-dry-run with notifications and DD
  republish suppressed:
  `uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --redispatch-after-minutes 0 --skip-dd-republish --suppress-notifications --require-raycon-git-commit e385f6328dd58399d6051ed7f735c72a11c007a7`.
- DDR generated and uploaded the Alpha Capacity artifact
  `Alpha Capacity Analysis - Alpha Miami Beach 300 71st 3rd - 10dPoeXlUcuY.json`
  with Drive file ID `151mE4BLJO9fl4nbSlrzEy44iC70xmzoQ`.
- Dispatch attached Alpha Capacity with `capacity_analysis_status=generated`,
  `capacity_analysis_attached=true`, and `capacity_analysis_signature=114-199`.
  RayCon returned a non-cached job
  `2679119b11f664bed8b6a63b6d6e36c5` with idempotency segment
  `capacity:alpha:114-199`.
- RayCon wrote/updated M1 `raycon_scenario.json` Drive file
  `1YGmrdxtWXfiTA0hp8JuXMtoIrudYu1Wy`, modified
  `2026-06-10T01:43:08.718Z`, with run
  `rc_20260610013800_1c6ea7eff8`, `status=completed`,
  `validation.passed=true`, and zero validation errors.
- JSON readback verified:
  - `analysis.site_context.capacity_analysis.source_system=alpha_capacity_analysis`
  - `provenance.capacity_analysis.source_system=alpha_capacity_analysis`
  - Fastest Path `capacity_students=114`, `grand_total=1353726`,
    `timeline_weeks=10`
  - Max Capacity `capacity_students=199`, `grand_total=1852736`,
    `timeline_weeks=14`
  - Both scenario `capacity_trace.source_system` values are
    `alpha_capacity_analysis`.
- RayCon emitted two non-blocking Alpha Capacity caveats about restroom, egress,
  fire/life-safety, HVAC, and plan-review proof gaps, but validation still
  passed and the published capacity follows Alpha Capacity Analysis.
- The optional MatterBot scout follow-up was still `scout_running` at final
  status check. This did not block the DDR proof because the construction
  estimate artifact was already written, validation-passed, and readable from
  M1.

Verification:

```powershell
npm.cmd test
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_client.py tests\test_raycon_followup.py -q --basetemp C:\tmp\ddr-raycon-alpha-release
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_portfolio_gap_telemetry.py tests\test_workflow_contracts.py tests\test_config.py -q --basetemp C:\tmp\ddr-raycon-alpha-merge-release-2
uv run ruff check src\due_diligence_reporter\alpha_capacity_analysis.py src\due_diligence_reporter\raycon_client.py src\due_diligence_reporter\portfolio_gap_telemetry.py src\due_diligence_reporter\config.py scripts\raycon_followup.py scripts\build_portfolio_gap_telemetry.py scripts\post_portfolio_gap_summary.py tests\test_alpha_capacity_analysis.py tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_portfolio_gap_telemetry.py tests\test_workflow_contracts.py tests\test_config.py
uv run mypy src\due_diligence_reporter\alpha_capacity_analysis.py src\due_diligence_reporter\raycon_client.py src\due_diligence_reporter\portfolio_gap_telemetry.py src\due_diligence_reporter\config.py
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content
```

Results: RayCon root tests passed (`13 passed` frontend/root, `198 passed`;
API `20 passed`, `275 passed`); DDR final focused release pytest passed
(`167 passed`); merged DDR release/portfolio pytest passed (`186 passed`);
Ruff passed; mypy passed; RayCon `/version` matched the deployed SHA; live
Miami Beach proof produced Alpha-sourced Fastest Path and Max Capacity priced
estimates.

## 2026-06-09 - Portfolio Gaps Emits WorkflowRun Telemetry

- The scheduled `portfolio-automation-gaps` workflow now captures start/finish
  timing, preserves the Chat notification result, and uploads
  `reports/telemetry/portfolio-automation-gaps-telemetry.json`.
- Added `scripts/build_portfolio_gap_telemetry.py` and
  `portfolio_gap_telemetry.py` to convert the Portfolio Gaps snapshot plus
  notification result into a sanitized `workflow_run.v1` artifact with
  source-owned `action_records` and site gap rows.
- The emitted telemetry keeps missing-doc coverage out of operator gap rows and
  does not publish raw Drive URLs, P1 emails, required-doc lists, local paths,
  or dependency internals.
- `scripts/post_portfolio_gap_summary.py` now supports `--result-output` so
  the notification outcome can be carried into the workflow telemetry artifact.

Verification:

```powershell
uv run pytest tests/test_portfolio_gap_telemetry.py tests/test_portfolio_gap_notifications.py tests/test_workflow_contracts.py tests/test_config.py -q --basetemp C:\tmp\ddr-portfolio-telemetry-script
uv run ruff check src/due_diligence_reporter/portfolio_gap_telemetry.py scripts/build_portfolio_gap_telemetry.py scripts/post_portfolio_gap_summary.py tests/test_portfolio_gap_telemetry.py tests/test_workflow_contracts.py tests/test_config.py src/due_diligence_reporter/config.py
git diff --check
```

Results: 21 tests passed; Ruff passed; `git diff --check` reported only
expected Windows LF-to-CRLF warnings.

## 2026-06-09 - DDR Chat Route Is Process-Specific

- DDR automation notifications now read `DDR_GOOGLE_CHAT_WEBHOOK_URL` through
  `Settings.google_chat_webhook_url`; the generic `GOOGLE_CHAT_WEBHOOK_URL`
  no longer populates the setting.
- Updated DDR GitHub Actions workflows (`daily-dd-check`, `inbox-scan`,
  `vendor-doc-republish-sweep`, `raycon-followup`, `publish-to-mcp-hive`, and
  `portfolio-automation-gaps`) to use `secrets.DDR_GOOGLE_CHAT_WEBHOOK_URL`.
- `DDR_GOOGLE_CHAT_WEBHOOK_URL` is intentionally optional in workflow
  preflights. Missing process-specific Chat config must not route DDR events to
  the generic Ops Skill Announcement webhook or block non-Chat workflow work.
- Added config and workflow-contract coverage proving the DDR-specific webhook
  is used and `secrets.GOOGLE_CHAT_WEBHOOK_URL` is not referenced by workflows.

Verification:

```powershell
uv run pytest tests/test_config.py tests/test_workflow_contracts.py tests/test_portfolio_gap_notifications.py -q --basetemp C:\tmp\ddr-chat-routing-tests
uv run ruff check src/due_diligence_reporter/config.py tests/test_config.py tests/test_workflow_contracts.py scripts/daily_dd_check.py scripts/scan_inbox.py scripts/raycon_followup.py
git diff --check
```

Results: 18 tests passed; Ruff passed; `git diff --check` reported only
expected Windows LF-to-CRLF warnings.

## 2026-06-09 - Completion Audit Against Original Goal

Goal: make the DDR/RayCon Block Plan flow reliably produce Fast Path and Max
Capacity construction estimates, with capacity numbers sourced from
`ops-skills:alpha-capacity-analysis` and RayCon consuming those numbers for
pricing rather than independently owning published capacity.

Audit result: **not complete yet**. Local DDR and RayCon implementations are
aligned and validated, but the required live proof is still missing because
production RayCon remains on `git_commit=7cba48d`.

Requirement-by-requirement status:

| Requirement | Current evidence | Status |
| --- | --- | --- |
| DDR gets capacity numbers from `ops-skills:alpha-capacity-analysis` | `src/due_diligence_reporter/alpha_capacity_analysis.py` loads `alpha-capacity-analysis` plus referenced rulesets through `load_ops_skill_file(...)`, normalizes outputs with `source_system=alpha_capacity_analysis`, and can fall back to explicit Block Plan capacity schedules only when printed in the same Block Plan. | Implemented locally |
| DDR creates and attaches a reusable Alpha Capacity artifact | `generate_alpha_capacity_analysis_artifact(...)` writes the normalized JSON artifact to M1 and returns `capacity_analysis`, `capacity_analysis_file_id`, and `capacity_analysis_url`; tests cover artifact upload and filename generation. | Implemented locally |
| DDR does not auto-dispatch RayCon when complete Alpha counts are unavailable | Inbox and RayCon follow-up paths return `dispatch_skipped=capacity_analysis_not_available` / `blocked_capacity_analysis_not_available` when no complete Strict/Fast Path and Max payload exists. Focused tests cover this guard. | Implemented locally |
| DDR can recover old failed/no-capacity RayCon scenarios once Alpha Capacity exists | RayCon follow-up stores `capacity_analysis_signature`, detects old failed/completed/no-capacity states, and redispatches when complete Alpha capacity becomes available. Focused tests cover Miami Beach `114-199` signatures and recovery cases. | Implemented locally |
| RayCon uses Alpha counts for Fast Path and Max Capacity pricing | `api/src/rayconJobs.js` normalizes Alpha Capacity payloads, applies external capacity overrides, passes `capacitySource=alpha_capacity_analysis`, `mvpCapacity`, and `idealCapacity` into `estimate_costs`, and emits Alpha provenance/capacity traces. Focused RayCon tests cover Miami Beach `114/199`. | Implemented locally |
| RayCon does not let generic/manual capacity inputs own published capacity | RayCon requires Alpha identity (`source_system`, `capacity_source`, `source_label`, or `skill_name`) before trusting external counts. `api/src/rayTools.js` ignores `mvpCapacity`/`idealCapacity` unless `capacitySource=alpha_capacity_analysis`; prompt/schema no longer tell Ray to pass arbitrary capacities. Tests cover generic `114/199` payloads being ignored. | Implemented locally |
| RayCon idempotency avoids reusing old no-capacity jobs after Alpha Capacity attaches | `api/src/index.js` adds `capacity:alpha:<strict>-<max>` only for complete Alpha-identified payloads and separates incomplete Alpha artifacts. Tests cover no-capacity, partial, complete, comma-formatted, and generic payload keys. | Implemented locally |
| Miami Beach proof site can produce Alpha Capacity `114-199` before dispatch | Dry-run preview against `Alpha Miami Beach 300 71st 3rd` attaches preview Alpha Capacity with `capacity_analysis_signature=114-199` and `dispatch_skipped=dry_run`. | Verified dry-run |
| Live production proof produces `raycon_scenario.json` with Alpha Capacity provenance and priced Fast Path/Max Capacity estimates | Production RayCon `/version` still reports `git_commit=7cba48d`; local RayCon changes are not deployed. The guarded proof must wait for commit/deploy and then run with `--require-raycon-git-commit <new-sha>`. | Missing live proof |

Most recent focused validation supporting this audit:

```powershell
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_client.py tests\test_raycon_followup.py -q --basetemp C:\tmp\ddr-raycon-alpha-contract-ready
uv run ruff check src\due_diligence_reporter\alpha_capacity_analysis.py src\due_diligence_reporter\raycon_client.py scripts\raycon_followup.py tests\test_alpha_capacity_analysis.py tests\test_raycon_client.py tests\test_raycon_followup.py
uv run mypy src\due_diligence_reporter\alpha_capacity_analysis.py src\due_diligence_reporter\raycon_client.py
npx.cmd vitest run src\rayconJobs.test.js src\jobsRoute.test.js src\rayTools.test.js src\openApiSpec.test.js src\deployManifest.test.js -t "Alpha Capacity|capacity|source-selection|deploy|OpenAPI|capacity guesses|generic capacity"
npm.cmd test
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content
```

Most recent results:

- DDR focused pytest: `167 passed`.
- DDR focused Ruff: passed.
- DDR focused mypy: success in 2 source files.
- RayCon focused Vitest: `5 passed`, `34 passed`, `104 skipped`.
- RayCon root `npm.cmd test`: frontend/root `13 passed`, `198 passed`; API
  `20 passed`, `275 passed`.
- Production `/version`: `git_commit=7cba48d`.

Do not mark the goal complete until the live proof verifies:

1. RayCon `/version.git_commit` equals the new committed/deployed SHA.
2. Miami Beach non-dry-run follow-up dispatches with Alpha Capacity signature
   `114-199`.
3. M1 receives/updates `raycon_scenario.json`.
4. The scenario JSON has Alpha Capacity provenance and publishes/prices Fast
   Path `114` plus Max Capacity `199`.

## 2026-06-09 - RayCon Version Guard Verified Against Live Production

- Confirmed the RayCon checkout is on `main...origin/main` with dirty
  validated changes and remote `origin=https://github.com/b-randongee/RayCon.git`.
- Confirmed local RayCon `HEAD` is still
  `7cba48d2ed315bf3028983edfa4cbb2cd3a3322f`; production `/version` reports
  short `git_commit=7cba48d`, so the local validated changes are not deployed.
- Ran the DDR RayCon follow-up proof command with
  `--require-raycon-git-commit deadbeef` against production. It exited nonzero
  before Google Client initialization and before any Drive/Rhodes work, with:
  `RayCon /version git_commit mismatch: expected deadbeef, got 7cba48d`.
- This verifies the intended live-proof guard: after RayCon is committed and
  deployed, use the actual new SHA in `--require-raycon-git-commit` so DDR
  cannot dispatch the Miami Beach proof to the old deployed service.

Verification:

```powershell
git status --branch --short
git remote -v
git rev-parse HEAD
uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --dry-run --skip-dd-republish --suppress-notifications --require-raycon-git-commit deadbeef
```

Result:

- RayCon branch: `main...origin/main`.
- RayCon remote: `origin https://github.com/b-randongee/RayCon.git`.
- RayCon local HEAD: `7cba48d2ed315bf3028983edfa4cbb2cd3a3322f`.
- Guard proof exited `1` before Google Client startup with the expected
  `/version` mismatch message.

## 2026-06-09 - Deploy-Readiness Audit Refreshed

- Re-audited the current RayCon and DDR diffs for rollout safety. The added
  secret scan found only fake test status URLs and documentation/test
  placeholders; no real API keys or private keys were found in added lines.
- Confirmed the only untracked RayCon file is
  `scripts/deploy-raycon-cloud-run.mjs`, and the only untracked DDR files are
  `src/due_diligence_reporter/alpha_capacity_analysis.py` and
  `tests/test_alpha_capacity_analysis.py`.
- Re-ran focused cross-repo contract gates:
  DDR Alpha Capacity/RayCon handoff tests passed, RayCon focused
  capacity/source/deploy tests passed, syntax checks passed, and both repos'
  diff checks passed with expected Windows LF/CRLF warnings only.
- Re-ran full RayCon `npm.cmd test` on the current tree. Frontend/root and API
  tests passed.
- Production RayCon still reports `/version git_commit=7cba48d`; do not run
  the non-dry-run Miami Beach proof until RayCon is committed and deployed.

Validation:

```powershell
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_client.py tests\test_raycon_followup.py -q --basetemp C:\tmp\ddr-raycon-alpha-contract-ready
uv run ruff check src\due_diligence_reporter\alpha_capacity_analysis.py src\due_diligence_reporter\raycon_client.py scripts\raycon_followup.py tests\test_alpha_capacity_analysis.py tests\test_raycon_client.py tests\test_raycon_followup.py
uv run mypy src\due_diligence_reporter\alpha_capacity_analysis.py src\due_diligence_reporter\raycon_client.py
npx.cmd vitest run src\rayconJobs.test.js src\jobsRoute.test.js src\rayTools.test.js src\openApiSpec.test.js src\deployManifest.test.js -t "Alpha Capacity|capacity|source-selection|deploy|OpenAPI|capacity guesses|generic capacity"
node -c api\src\index.js
node -c api\src\rayconJobs.js
node -c scripts\deploy-raycon-cloud-run.mjs
git diff --check
npm.cmd test
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content
```

Results:

- DDR focused pytest: `167 passed`.
- DDR focused Ruff: passed.
- DDR focused mypy: success in 2 source files.
- RayCon focused Vitest: `5 passed`, `34 passed`, `104 skipped`.
- RayCon syntax checks passed for `api\src\index.js`,
  `api\src\rayconJobs.js`, and `scripts\deploy-raycon-cloud-run.mjs`.
- RayCon root `npm.cmd test`: frontend/root `13 passed`, `198 passed`; API
  `20 passed`, `275 passed`.
- RayCon and DDR `git diff --check`: passed with expected Windows LF/CRLF
  warnings only.
- Production `/version`: `git_commit=7cba48d`.

Exact remaining rollout/proof sequence after approval:

```powershell
# RayCon repo
git status --short
git add CLAUDE.md api/package.json api/src/deployManifest.test.js api/src/index.js api/src/jobsRoute.test.js api/src/openApiSpec.js api/src/openApiSpec.test.js api/src/rayTools.js api/src/rayTools.test.js api/src/rayconJobs.js api/src/rayconJobs.test.js docs/api-reference.md docs/decisions.md docs/patterns.md package.json scripts/deploy-raycon-cloud-run.mjs src/engine/estimateToolSchema.js src/engine/plannerSystemPrompt.js
git commit -m "Route DDR Alpha Capacity into RayCon job estimates"
$RAYCON_SHA = git rev-parse HEAD
npm.cmd run deploy:cloud-run:execute
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content

# DDR repo, after production /version reports $RAYCON_SHA
uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --redispatch-after-minutes 0 --skip-dd-republish --suppress-notifications --require-raycon-git-commit $RAYCON_SHA
```

Expected proof evidence:

- RayCon `/version.git_commit` equals the committed RayCon SHA.
- Miami Beach RayCon follow-up dispatch attaches Alpha Capacity with signature
  `114-199`.
- M1 receives/updates `raycon_scenario.json`.
- The generated scenario carries Alpha Capacity provenance and uses Fast Path
  `114` plus Max Capacity `199` for student-scaled pricing.

## 2026-06-09 - RayCon Requires Alpha Identity Before Trusting Capacity Counts

- Hardened the RayCon capacity-input trust boundary so a complete-looking
  `capacity_analysis` object is not enough to override RayCon capacity math.
  RayCon now treats external capacity as authoritative only when the payload
  identifies Alpha Capacity Analysis through `source_system`,
  `capacity_source`, `source_label`, or `skill_name`.
- Added RayCon regression coverage for a generic `capacity_analysis` payload
  with `114/199` counts. It is ignored as authoritative: published scenario
  capacity stays on RayCon's internal audit values, no Alpha provenance is
  emitted, and `estimate_costs` is not called with
  `capacitySource=alpha_capacity_analysis`.
- Added route/idempotency coverage so generic capacity-shaped payloads do not
  create `capacity:alpha:*` keys. Positive Alpha payloads still include
  `capacity:alpha:<strict>-<max>`, partial Alpha artifacts still use
  `capacity:alpha_incomplete:<artifact>`, and comma-formatted Alpha counts are
  normalized.
- Updated RayCon OpenAPI/API reference wording to tell callers that the
  payload must be Alpha-identified before RayCon uses it as the authoritative
  Fast Path / Max Capacity source for student-scaled pricing.
- Confirmed DDR-generated Alpha Capacity artifacts include both
  `source_system=alpha_capacity_analysis` and
  `source_label=Alpha Capacity Analysis`, so the stricter RayCon guard remains
  compatible with the DDR Block Plan path.
- Re-ran the Miami Beach DDR dry-run preview. It still attaches preview Alpha
  Capacity with `capacity_analysis_signature=114-199` and skips only because
  `--dry-run` was supplied.
- Production RayCon is still not updated: `/version` reports
  `git_commit=7cba48d`. Live non-dry-run proof remains gated on committing and
  deploying RayCon, then rerunning Miami Beach with
  `--require-raycon-git-commit <new-sha>`.

Verification:

```powershell
node -c api\src\index.js
node -c api\src\rayconJobs.js
node -c api\src\openApiSpec.js
npx.cmd vitest run src\rayconJobs.test.js src\jobsRoute.test.js src\openApiSpec.test.js -t "Alpha Capacity|capacity|idempotency|OpenAPI|generic capacity"
npx.cmd vitest run
npm.cmd test
git diff --check -- api\src\index.js api\src\rayconJobs.js api\src\rayconJobs.test.js api\src\jobsRoute.test.js api\src\openApiSpec.js api\src\openApiSpec.test.js docs\api-reference.md
npm.cmd run deploy:cloud-run -- --out-dir ..\raycon-deploy-preview-trust-boundary-current
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content
uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --dry-run --preview-capacity-analysis --skip-dd-republish --suppress-notifications --redispatch-after-minutes 0
```

Results:

- RayCon syntax checks passed for `api\src\index.js`,
  `api\src\rayconJobs.js`, and `api\src\openApiSpec.js`.
- Focused RayCon capacity/source/idempotency/OpenAPI Vitest:
  `3 passed`, `18 passed`, `100 skipped`.
- Full RayCon API Vitest: `20 passed`, `275 passed`.
- Root RayCon `npm.cmd test`: frontend/root `13 passed`, `198 passed`; API
  `20 passed`, `275 passed`.
- RayCon diff check passed with expected Windows LF/CRLF warnings only.
- RayCon deploy helper dry run printed Cloud Build/Cloud Run commands only and
  did not execute `gcloud`; it still warns that the tree is dirty and should
  not be executed until committed.
- Production `/version`: `git_commit=7cba48d`.
- Miami Beach dry-run preview:
  `capacity_analysis_status=preview_success`,
  `capacity_analysis_attached=true`,
  `capacity_analysis_signature=114-199`,
  `capacity_analysis_preview=true`, and `dispatch_skipped=dry_run`.

## 2026-06-09 - DDR Capacity Mapping Prefers Alpha Artifact Counts

- Hardened `src/due_diligence_reporter/raycon_client.py` so
  `raycon_scenario_to_report_fields` treats the Alpha Capacity Analysis
  artifact as the actual source of capacity counts, not just a provenance gate.
- When a RayCon payload carries Alpha Capacity Analysis with parseable
  Strict/Fast Path and Max counts, DDR now maps those artifact counts into
  `exec.fastest_open_capacity` and `exec.max_capacity_capacity` even if the
  mirrored scenario `capacity_students` values are stale or mismatched.
- Scenario-level `capacity_trace.source_system=alpha_capacity_analysis` still
  allows capacity mapping when no inline Alpha artifact counts are present.
- Completed RayCon scenarios without Alpha provenance continue to map
  capex/open-date/cost buckets while leaving DDR capacity tokens blank.
- Added regression coverage in `tests/test_raycon_client.py` for stale scenario
  mirrors: Alpha artifact `114/199` wins over scenario `126/211`.

Verification:

```powershell
uv run pytest tests\test_raycon_client.py -q --basetemp C:\tmp\ddr-raycon-client-alpha-artifact-precedence-2
uv run ruff check src\due_diligence_reporter\raycon_client.py tests\test_raycon_client.py
uv run mypy src\due_diligence_reporter\raycon_client.py
git diff --check -- src\due_diligence_reporter\raycon_client.py tests\test_raycon_client.py
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_inbox_scanner.py tests\test_raycon_client.py tests\test_raycon_followup.py -q --color=no --basetemp C:\tmp\ddr-raycon-alpha-artifact-precedence-a
uv run pytest tests\test_report_pipeline.py tests\test_report_schema.py tests\test_prompt_contract.py tests\test_completeness.py tests\test_classifier_keywords.py tests\test_docs_env_contract.py tests\test_workflow_contracts.py -q --color=no --basetemp C:\tmp\ddr-raycon-alpha-artifact-precedence-b
uv run ruff check .
uv run mypy src/
```

Results:

- Focused RayCon client pytest: `72 passed`.
- Split affected DDR suites: `245 passed` and `253 passed`.
- Focused Ruff and full Ruff: passed.
- Focused mypy and full `mypy src/`: passed.
- Diff check passed with expected Windows LF/CRLF warnings only.

## 2026-06-09 - RayCon npm Test Command Fixed for Windows

- Patched RayCon `api/package.json` so `npm --prefix api test` uses
  `vitest run` directly instead of the POSIX-only `NODE_ENV=test vitest run`.
  Vitest sets test-mode behavior for the suite, and this makes the root
  `npm.cmd test` command work on Windows.
- Re-ran the root RayCon `npm.cmd test`; it now runs both frontend/root Vitest
  and API Vitest successfully.
- Re-ran the scoped Miami Beach DDR dry-run preview after the DDR report
  mapping provenance gate. It still produces the expected Alpha Capacity
  signature `114-199` and skips only because `--dry-run` was supplied.
- Re-ran the RayCon Cloud Run deploy helper in dry-run mode. It still prints
  commands only and refuses execute-mode while the RayCon tree is dirty. No
  `gcloud` commands were executed.
- Production RayCon still reports `/version git_commit=7cba48d`; the live
  proof remains gated on committing/deploying RayCon and requiring the new
  commit in DDR.

Verification:

```powershell
npm.cmd test
node -c api\src\index.js
git diff --check -- api\package.json package.json api\src\rayconJobs.js api\src\rayconJobs.test.js
uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --dry-run --preview-capacity-analysis --skip-dd-republish --suppress-notifications --redispatch-after-minutes 0
npm.cmd run deploy:cloud-run -- --out-dir ..\raycon-deploy-preview-test-script-current
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content
```

Results:

- RayCon root `npm.cmd test`: frontend/root `13 passed`, `198 passed`; API
  `20 passed`, `273 passed`.
- RayCon syntax check for `api\src\index.js`: passed.
- Miami Beach DDR dry-run preview:
  `capacity_analysis_status=preview_success`,
  `capacity_analysis_attached=true`,
  `capacity_analysis_signature=114-199`,
  `capacity_analysis_preview=true`, and `dispatch_skipped=dry_run`.
- RayCon deploy helper dry run printed commands only; no `gcloud` execution.
- Production `/version`: `git_commit=7cba48d`.

## 2026-06-09 - Deploy Readiness Refreshed After Provenance Gate

- Re-ran RayCon frontend and API tests with Windows-compatible commands after
  the DDR report-capacity provenance gate and RayCon authoritative-capacity
  review fix.
- `npm.cmd test` at the RayCon root is still not a reliable Windows command:
  before root dependencies were installed it failed because `vitest` was not
  found, and the API package script uses POSIX `NODE_ENV=test`. The equivalent
  direct Windows commands passed.
- Ran the RayCon Cloud Run deploy helper in dry-run mode only. It generated
  the Cloud Build config and Cloud Run env file and printed the exact `gcloud`
  commands, but did not execute `gcloud`.
- Production RayCon still reports `/version git_commit=7cba48d`; live proof
  remains gated on committing/deploying the local RayCon changes and then
  running Miami Beach through DDR with `--require-raycon-git-commit <new-sha>`.
- `npm.cmd install` was used to hydrate root test dependencies. It reported
  3 audit findings (`1 moderate`, `2 high`) and caused transient Windows
  optional-package lockfile metadata churn; `package-lock.json` was restored to
  its prior clean state.

Verification:

```powershell
npm.cmd install
npx.cmd vitest run
npx.cmd vitest run  # from RayCon\api
npm.cmd run deploy:cloud-run -- --out-dir ..\raycon-deploy-preview-provenance-current
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content
git diff --check -- package.json api\src\rayconJobs.js api\src\rayconJobs.test.js
```

Results:

- RayCon frontend/root Vitest: `13 passed`, `198 passed`.
- RayCon API Vitest: `20 passed`, `273 passed`.
- Deploy helper dry run printed commands only; no `gcloud` commands executed.
- Production `/version`: `git_commit=7cba48d`.
- RayCon diff check passed with expected Windows LF/CRLF warnings only.

## 2026-06-09 - DDR Report Mapping Now Requires Alpha Capacity Provenance

- Tightened `src/due_diligence_reporter/raycon_client.py` so
  `raycon_scenario_to_report_fields` only fills
  `exec.fastest_open_capacity` and `exec.max_capacity_capacity` when the
  RayCon payload or per-scenario `capacity_trace` carries Alpha Capacity
  Analysis provenance.
- Completed RayCon scenarios without Alpha Capacity provenance still map cost
  and schedule fields, but the DDR capacity tokens stay blank. This prevents
  legacy/manual RayCon internal capacity math from satisfying the automated
  DDR Alpha-sourced capacity requirement.
- Added regression coverage in `tests/test_raycon_client.py`:
  non-Alpha completed payloads preserve capex/open-date fields while blanking
  capacity, and Alpha-provenance payloads still publish the Miami Beach
  `114/199` counts.
- A single combined broad pytest command hit a Windows terminal
  `OSError: [Errno 22] Invalid argument` during timeout handling, so the
  affected suite was split into two smaller commands and passed cleanly.

Verification:

```powershell
uv run pytest tests\test_raycon_client.py -q --basetemp C:\tmp\ddr-raycon-client-alpha-provenance
uv run ruff check src\due_diligence_reporter\raycon_client.py tests\test_raycon_client.py
uv run mypy src\due_diligence_reporter\raycon_client.py
uv run pytest tests\test_raycon_client.py tests\test_report_schema.py tests\test_completeness.py tests\test_prompt_contract.py tests\test_docs_env_contract.py -q --basetemp C:\tmp\ddr-raycon-capacity-provenance-suite
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_inbox_scanner.py tests\test_raycon_client.py tests\test_raycon_followup.py -q --color=no --basetemp C:\tmp\ddr-raycon-alpha-capacity-provenance-a
uv run pytest tests\test_report_pipeline.py tests\test_report_schema.py tests\test_prompt_contract.py tests\test_completeness.py tests\test_classifier_keywords.py tests\test_docs_env_contract.py tests\test_workflow_contracts.py -q --color=no --basetemp C:\tmp\ddr-raycon-alpha-capacity-provenance-b
uv run ruff check .
uv run mypy src/
git diff --check -- src\due_diligence_reporter\raycon_client.py tests\test_raycon_client.py HANDOFF.md
```

Results:

- Focused RayCon client pytest: `71 passed`.
- Focused RayCon client/report contract pytest: `204 passed`.
- Split broad affected suite: `244 passed` and `253 passed`.
- Focused Ruff and full Ruff: passed.
- Focused mypy and full `mypy src/`: passed.
- Diff check passed with expected Windows LF/CRLF warnings only.

## 2026-06-09 - RayCon Keeps Alpha-Backed Capacity Defensible on Review Disagreement

- Patched RayCon `api/src/rayconJobs.js` so `applyRayReviewToScenario` receives
  the `authoritativeCapacity` flag. When complete Alpha Capacity Analysis is
  attached, Ray review disagreement is now preserved as a caveat in the
  rationale and validation warnings, but the Alpha-backed `capacity_trace`
  remains `defensible=true`.
- Non-Alpha jobs still fail closed when Ray rejects a scenario capacity as
  indefensible. The same focused test run covered both the Alpha-backed
  non-blocking path and the ordinary fail-closed path.
- Updated the Miami Beach Alpha Capacity test in `api/src/rayconJobs.test.js`
  to assert Fast Path `114` and Max Capacity `199` remain authoritative,
  priced, Alpha-sourced, and defensible even when RayCon's internal audit
  counts differ.
- Full RayCon API validation remains green. Production RayCon is still not
  updated until these RayCon changes are committed and deployed.

Verification:

```powershell
node -c api\src\rayconJobs.js
npx.cmd vitest run src\rayconJobs.test.js -t "Alpha Capacity|fails validation when Ray rejects"
npx.cmd vitest run src\rayconJobs.test.js src\jobsRoute.test.js src\rayTools.test.js src\openApiSpec.test.js src\deployManifest.test.js -t "Alpha Capacity|capacity|source-selection|deploy|OpenAPI|capacity guesses"
npx.cmd vitest run
git diff --check -- api\src\rayconJobs.js api\src\rayconJobs.test.js
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content
```

Results:

- RayCon syntax check passed.
- Focused Alpha Capacity plus non-Alpha Ray rejection test: `4 passed`,
  `49 skipped`.
- Broader RayCon capacity/source-selection suite: `5 passed`, `32 passed`,
  `104 skipped`.
- Full RayCon API suite: `20 passed`, `273 passed`.
- RayCon diff check passed with expected Windows LF/CRLF warnings only.
- Production `/version`: `git_commit=7cba48d`.

## 2026-06-09 - Test Site Refresh Reconfirmed Miami Beach

- Refreshed the RayCon Block Plan candidate inventory. The only first-time
  missing-scenario sites are still Plano and Tampa, but neither Block Plan has
  parseable strict/max student pairs and the Alpha Capacity model returns
  `insufficient_evidence` for both.
- Re-ran the all-Block-Plan text evidence probe. Miami Beach remains the only
  current proof candidate with explicit capacity pairs:
  `40/70`, `24/42`, and `50/87`, totaling Strict/Fast Path `114` and Max
  Capacity `199`.
- Re-ran the scoped Miami Beach dry-run preview with no production mutations.
  It reached the failed-scenario recovery path, attached preview Alpha Capacity,
  and skipped only because `--dry-run` was supplied:
  `capacity_analysis_status=preview_success`,
  `capacity_analysis_attached=true`,
  `capacity_analysis_signature=114-199`,
  `capacity_analysis_preview=true`, and `dispatch_skipped=dry_run`.
- Production RayCon still reports `/version git_commit=7cba48d`, so the
  non-dry-run proof remains gated on committing/deploying RayCon and running
  the DDR proof with `--require-raycon-git-commit <new-sha>`.

Recommended test plan:

- Success proof: `Alpha Miami Beach 300 71st 3rd`
  (`site_id=k972ay4w964539mq0naqyde5ws85fr3r`) after RayCon deploy.
- Negative guard proof: Plano or Tampa if we want to show DDR blocks automated
  RayCon dispatch when Alpha Capacity cannot produce both counts.

Verification:

```powershell
uv run python ..\find_raycon_test_sites.py
uv run python ..\probe_all_block_plan_capacity_text.py
uv run python ..\probe_alpha_capacity_model_candidates.py
uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --dry-run --preview-capacity-analysis --skip-dd-republish --suppress-notifications --redispatch-after-minutes 0
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content
```

Results:

- Inventory refresh: Plano and Tampa are first-time missing-scenario candidates,
  but both lack complete capacity evidence.
- Capacity text/model probes: Plano and Tampa `insufficient_evidence`; Miami
  Beach `114-199`.
- Miami Beach dry-run preview: `capacity_analysis_signature=114-199` and
  `dispatch_skipped=dry_run`.
- Production `/version`: `git_commit=7cba48d`.

## 2026-06-09 - Alpha Capacity Ownership Contract Audit

- Re-ran a stale-contract scan after tightening the automated no-capacity
  dispatch gate. No docs or source comments still say DDR should continue
  automated RayCon dispatch when Alpha Capacity cannot produce both counts.
- Clarified `scripts/raycon_followup.py` so `_capacity_analysis_for_dispatch`
  is described as non-throwing rather than fail-soft dispatch permission. The
  caller owns the dispatch decision, and the automated DDR Block Plan path
  requires complete Strict/Fast Path and Max Capacity counts.
- Tightened `docs/reference/RayCon-DDR-Rebuild-Package.md` scenario-field
  language: automated DDR Block Plan capacity is Alpha Capacity or a sourced
  gap; legacy/manual RayCon fallback capacity may appear only as caveated
  RayCon calculator output and should not satisfy the Alpha-sourced capacity
  requirement.
- Production RayCon still reports `/version git_commit=7cba48d`. Local DDR and
  RayCon remain ready for a guarded post-deploy proof, but the goal is not
  live-proven until RayCon is committed/deployed and Miami Beach is run with
  `--require-raycon-git-commit <new-sha>`.

Verification:

```powershell
rg -n "still dispatch|dispatches? RayCon without|without external capacity|RayCon can continue|fallback basis|should not prevent RayCon|no-capacity" docs src scripts tests .github
uv run pytest tests\test_docs_env_contract.py tests\test_raycon_followup.py::TestSafetyNetDispatch -q --basetemp C:\tmp\ddr-raycon-capacity-contract-doc-comment
uv run ruff check scripts\raycon_followup.py docs\reference\RayCon-DDR-Rebuild-Package.md tests\test_docs_env_contract.py
uv run mypy scripts\raycon_followup.py
git diff --check -- scripts\raycon_followup.py docs\reference\RayCon-DDR-Rebuild-Package.md tests\test_docs_env_contract.py
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content
```

Results:

- Stale-contract scan now returns only intentional no-capacity blocking
  language plus the expected inbox warning string.
- Focused docs plus `TestSafetyNetDispatch` pytest: `25 passed`.
- Focused Ruff: passed.
- Focused mypy for `scripts\raycon_followup.py`: passed.
- Diff check passed with expected Windows LF/CRLF warnings only.
- Production `/version`: `git_commit=7cba48d`.

## 2026-06-09 - Automated Block Plan Dispatch Now Requires Alpha Capacity

- Tightened the DDR side of the RayCon/Alpha Capacity contract. Automatic
  Block Plan dispatch now requires a complete Alpha Capacity payload with both
  Strict/Fast Path and Max Capacity counts before calling RayCon.
- `src/due_diligence_reporter/inbox_scanner.py` now returns a
  `raycon_scenario_request` row with
  `dispatch_skipped=capacity_analysis_not_available` and status
  `blocked_capacity_analysis_not_available` when the inbox Block Plan path
  cannot find or generate complete Alpha Capacity. It does not POST a
  no-capacity RayCon job in that branch.
- `scripts/raycon_followup.py` now applies the same requirement to the normal
  missing-scenario safety-net dispatch path. Failed-scenario, terminal-status,
  and completed-no-capacity recovery branches already required complete Alpha
  Capacity; the first-time safety-net branch now matches that policy.
- Updated tests and docs so the durable contract says DDR skips no-capacity
  automated dispatch instead of letting RayCon own published capacity from its
  internal fallback calculator.
- Miami Beach remains the proof site. A fresh dry-run preview after this
  change still reaches the failed-scenario recovery path with
  `capacity_analysis_signature=114-199` and skips only because `--dry-run` was
  supplied.
- Production RayCon still reports `/version git_commit=7cba48d`; the
  non-dry-run proof remains gated on committing/deploying RayCon and using the
  guarded `--require-raycon-git-commit` proof flag.

Verification:

```powershell
uv run pytest tests\test_inbox_scanner.py::TestBlockPlanDownstream tests\test_raycon_followup.py -q --basetemp C:\tmp\ddr-raycon-capacity-required-focused-4
uv run ruff check scripts\raycon_followup.py src\due_diligence_reporter\inbox_scanner.py tests\test_raycon_followup.py tests\test_inbox_scanner.py tests\test_docs_env_contract.py
uv run mypy scripts\raycon_followup.py
uv run mypy src\due_diligence_reporter\inbox_scanner.py
git diff --check -- scripts\raycon_followup.py src\due_diligence_reporter\inbox_scanner.py tests\test_raycon_followup.py tests\test_inbox_scanner.py tests\test_docs_env_contract.py docs\reference\RayCon-DDR-Rebuild-Package.md docs\process\HOW-IT-WORKS.md
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_inbox_scanner.py tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_report_pipeline.py tests\test_report_schema.py tests\test_prompt_contract.py tests\test_completeness.py tests\test_classifier_keywords.py tests\test_docs_env_contract.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-alpha-capacity-required-gate
uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --dry-run --preview-capacity-analysis --skip-dd-republish --suppress-notifications --redispatch-after-minutes 0
npx.cmd vitest run src\rayconJobs.test.js src\jobsRoute.test.js src\rayTools.test.js src\openApiSpec.test.js src\deployManifest.test.js -t "Alpha Capacity|capacity|source-selection|deploy|OpenAPI|capacity guesses"
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content
```

Results:

- Focused DDR inbox/follow-up suite: `89 passed`.
- Focused DDR Ruff: passed.
- DDR mypy passed when checking `scripts\raycon_followup.py` and
  `src\due_diligence_reporter\inbox_scanner.py` separately. The combined
  script+src invocation still hits the known duplicate-module import pattern.
- DDR diff check passed with expected Windows LF/CRLF warnings only.
- Broad affected DDR Alpha Capacity/RayCon suite: `496 passed`.
- Miami Beach dry-run preview:
  `capacity_analysis_status=preview_success`,
  `capacity_analysis_attached=true`,
  `capacity_analysis_signature=114-199`,
  `capacity_analysis_preview=true`, and `dispatch_skipped=dry_run`.
- RayCon focused capacity/source-selection suite: `5 passed`, `32 passed`,
  `104 skipped`.
- Production `/version`: `git_commit=7cba48d`.

## 2026-06-09 - Proof Site Refresh Confirms Miami Beach

- Re-read the current RayCon proof inventory after context compaction. The only
  first-time missing-scenario Block Plan candidates are still:
  - `Alpha Plano 5509 Pleasant Valley Dr`
    (`site_id=k978wq2je97vw8aftnz0j7rv0d85emyj`)
  - `Alpha Tampa 2409 S MacDill Ave`
    (`site_id=k971m94ck04aqyhnr8jcs17zyn83dq4h`)
- Re-ran the Block Plan text evidence scan. Plano extracted 712 characters and
  Tampa extracted 211 characters; neither had parseable strict/max student
  pairs. The Alpha Capacity model probe returned `insufficient_evidence` for
  both, so they remain poor tests for the capacity-backed RayCon path.
- Miami Beach remains the best proof site because its Block Plan has explicit
  student-count pairs `40/70`, `24/42`, and `50/87`, totaling Strict/Fast Path
  `114` and Max Capacity `199`.
- Re-ran the scoped Miami Beach dry-run preview from the DDR validation
  checkout. It reached the failed-scenario recovery path, attached previewed
  Alpha Capacity, and skipped dispatch only because `--dry-run` was supplied:
  `capacity_analysis_status=preview_success`,
  `capacity_analysis_attached=true`,
  `capacity_analysis_signature=114-199`,
  `capacity_analysis_preview=true`, and `dispatch_skipped=dry_run`.
- Production RayCon still reports `/version git_commit=7cba48d`, so the
  non-dry-run proof should not run until RayCon is committed/deployed and the
  guarded DDR proof can require the new deployed commit.

Use this site for the first post-deploy production proof:

```text
site: Alpha Miami Beach 300 71st 3rd
site_id: k972ay4w964539mq0naqyde5ws85fr3r
drive_folder_id: 1qjyrtHSFkPOQjTHPo8VSORCGh9h7KqOt
m1_folder_id: 1DuceE9iu0y45G6wncl4cRZyTkgP7IiYL
block_plan_id: 10dPoeXlUcuYwvEGflf0r9zo4RQMCfErM
expected_alpha_capacity_signature: 114-199
```

Verification:

```powershell
git -C C:\Users\foote\Documents\Codex\2026-06-09\relative-to-the-ddr-process-what\work\due-diligence-reporter status --short
git -C C:\Users\foote\Documents\Codex\2026-06-09\relative-to-the-ddr-process-what\work\RayCon status --short
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing
uv run python ..\find_raycon_test_sites.py
uv run python ..\probe_all_block_plan_capacity_text.py
uv run python ..\probe_alpha_capacity_model_candidates.py
uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --dry-run --preview-capacity-analysis --skip-dd-republish --suppress-notifications --redispatch-after-minutes 0
```

Results:

- DDR and RayCon remain dirty with the expected local Alpha Capacity/RayCon
  integration changes.
- Production RayCon `/version`: `git_commit=7cba48d`.
- Inventory scan: Plano and Tampa are still the only first-time
  missing-scenario Block Plan candidates, but both lack complete Alpha Capacity
  evidence.
- Miami Beach dry-run preview: `capacity_analysis_signature=114-199` and
  `dispatch_skipped=dry_run`.

## 2026-06-09 - DDR Guarded Proof Contract Documented and Reverified

- Added the guarded post-deploy proof path to
  `docs/reference/RayCon-DDR-Rebuild-Package.md`. The durable rebuild package
  now says `raycon-followup.yml` accepts `require_raycon_git_commit`, passes it
  through as `--require-raycon-git-commit`, and checks RayCon `/version` before
  Drive, Rhodes, Alpha Capacity artifact, or RayCon job mutations.
- Added a docs contract assertion in `tests/test_docs_env_contract.py` so the
  rebuild package cannot drop the workflow-dispatch guard language without a
  test failure.
- Re-ran the broad DDR Alpha Capacity/RayCon affected suite from the current
  worktree after the workflow guard and doc-contract updates. This suite covers
  Alpha Capacity generation, inbox dispatch, RayCon client payload mapping,
  RayCon follow-up retry/preview/guard behavior, report-pipeline exposure,
  report schema mapping, prompt/completeness contracts, classifier keywords,
  docs/env contracts, and workflow contracts.
- Production proof remains gated on RayCon commit/deploy and `/version`
  matching the new RayCon commit.

Verification:

```powershell
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_inbox_scanner.py tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_report_pipeline.py tests\test_report_schema.py tests\test_prompt_contract.py tests\test_completeness.py tests\test_classifier_keywords.py tests\test_docs_env_contract.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-alpha-capacity-post-workflow-guard
uv run pytest tests\test_docs_env_contract.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-proof-doc-contract
uv run ruff check tests\test_docs_env_contract.py tests\test_workflow_contracts.py
git diff --check -- docs\reference\RayCon-DDR-Rebuild-Package.md tests\test_docs_env_contract.py HANDOFF.md
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_inbox_scanner.py tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_report_pipeline.py tests\test_report_schema.py tests\test_prompt_contract.py tests\test_completeness.py tests\test_classifier_keywords.py tests\test_docs_env_contract.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-alpha-capacity-proof-doc-current
```

Results:

- Broad DDR Alpha Capacity/RayCon suite before the docs patch: `493 passed`.
- Focused docs/workflow contract tests: `16 passed`.
- Focused Ruff over docs/workflow contract tests: passed.
- Diff check passed with expected Windows LF/CRLF warnings only.
- Broad DDR Alpha Capacity/RayCon suite after the docs patch: `494 passed`.

## 2026-06-09 - RayCon Capacity Deploy Readiness Rechecked

- Re-read the RayCon capacity-ingestion path from the current dirty worktree.
  The local `/v1/jobs` contract still accepts `capacity_analysis`,
  `alpha_capacity_analysis`, `capacity_analysis_file_id`, and
  `capacity_analysis_url`; complete Alpha Capacity payloads get a separate
  `capacity:alpha:<strict>-<max>` idempotency segment so a fixed capacity-backed
  submit does not reuse an old no-capacity job.
- Rechecked the trust boundary in `api/src/rayTools.js`: `estimate_costs` only
  honors caller-supplied `mvpCapacity` / `idealCapacity` when
  `capacitySource` or `capacity_source` is exactly `alpha_capacity_analysis`.
  Otherwise it falls back to RayCon's deterministic capacity math. This prevents
  Ray/model guesses from silently becoming the student-scaled pricing basis.
- Rechecked `api/src/rayconJobs.js`: complete Alpha Capacity overrides published
  Fastest Path and Max Capacity counts, RayCon internal capacity is retained as
  audit evidence, and Ray review disagreement/failure stays non-blocking when
  Alpha Capacity is authoritative.
- Re-ran the deploy helper dry run. It still prints the Cloud Build and Cloud
  Run commands only, refuses execute-mode while the RayCon tree is dirty, and
  writes generated deploy files with the current base `GIT_COMMIT` because the
  capacity changes are not committed yet.
- Production RayCon `/version` still reports the old deployed commit
  `7cba48d`. The live Miami Beach proof remains gated on committing/deploying
  RayCon and then using DDR's guarded `require_raycon_git_commit` workflow input
  or local `--require-raycon-git-commit` flag.

Verification:

```powershell
node -c api\src\index.js
node -c api\src\rayconJobs.js
node -c api\src\rayTools.js
node -c scripts\deploy-raycon-cloud-run.mjs
npx.cmd vitest run src\rayconJobs.test.js src\jobsRoute.test.js src\rayTools.test.js src\openApiSpec.test.js src\deployManifest.test.js -t "Alpha Capacity|capacity|source-selection|deploy|OpenAPI|capacity guesses"
git diff --check
npx.cmd vitest run
npm.cmd run deploy:cloud-run -- --out-dir ..\raycon-deploy-preview-current
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content
```

Results:

- Syntax checks passed for `api/src/index.js`, `api/src/rayconJobs.js`,
  `api/src/rayTools.js`, and `scripts/deploy-raycon-cloud-run.mjs`.
- Focused RayCon capacity/deploy/OpenAPI suite: `5 passed`, `32 passed`,
  `104 skipped`.
- Full RayCon API Vitest: `20 passed`, `273 passed`.
- RayCon `git diff --check` passed with expected Windows LF/CRLF warnings only.
- Deploy helper dry run generated the Cloud Build/env files and printed the
  `gcloud` commands; no `gcloud` commands were executed.
- Production `/version`: `git_commit=7cba48d`.

## 2026-06-09 - RayCon Follow-up Workflow Can Enforce Deploy Commit Guard

- Added an optional `workflow_dispatch` input to
  `.github/workflows/raycon-followup.yml`:
  `require_raycon_git_commit`. It is intended for the controlled post-deploy
  proof run after RayCon is committed and deployed.
- The workflow passes the input through
  `INPUT_REQUIRE_RAYCON_GIT_COMMIT` and appends
  `--require-raycon-git-commit "$INPUT_REQUIRE_RAYCON_GIT_COMMIT"` only when
  the env var is non-empty. The shell block does not directly interpolate the
  raw `${{ inputs.require_raycon_git_commit }}` expression.
- This exposes the existing `scripts/raycon_followup.py` `/version` preflight
  from the normal GitHub Actions surface. A guarded Miami Beach proof should
  now fail before Google Drive/Rhodes/RayCon mutations if production RayCon is
  still on the old commit.
- Production proof is still gated on committing/deploying RayCon and seeing
  `/version` report the deployed commit. Do not run the non-dry-run Miami Beach
  proof without passing the expected new RayCon commit.

Verification:

```powershell
uv run pytest tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-workflow-version-guard
uv run ruff check tests\test_workflow_contracts.py
git diff --check -- .github\workflows\raycon-followup.yml tests\test_workflow_contracts.py
uv run pytest tests\test_raycon_followup.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-workflow-version-guard-full
```

Results:

- Workflow contract tests: `13 passed`.
- Ruff on `tests/test_workflow_contracts.py`: passed.
- Diff check passed with expected Windows LF/CRLF warnings only.
- Adjacent RayCon follow-up plus workflow contract suite: `94 passed`.

## 2026-06-09 - RayCon OpenAPI Alpha Capacity Contract Tightened

- Audited the DDR-to-RayCon payload contract after adding the guarded live
  proof. Runtime code already accepts Alpha Capacity aliases such as `strict`,
  `fastest_open`, `fast_path`, `max`, and `max_capacity`, plus student-count
  fields such as `capacity_students`, `student_count`, and `students`.
- RayCon's OpenAPI descriptor still documented `capacity_analysis` as a generic
  object, which was weaker than the runtime contract and easier for a future
  caller to misread.
- Tightened `api/src/openApiSpec.js` in RayCon so `capacity_analysis` and
  `alpha_capacity_analysis` document the complete Alpha Capacity payload shape:
  one Strict/Fast Path scenario, one Max Capacity scenario, accepted alias keys,
  accepted student-count fields, optional scenario containers, and the fact
  that complete counts override RayCon capacity math for published capacity and
  student-scaled pricing.
- Added `api/src/openApiSpec.test.js` coverage that pins the Alpha Capacity
  schema aliases and accepted student-count fields.
- Production RayCon is still not updated. `/version` continues to report
  `git_commit=7cba48d`, so the local contract is stronger but the live proof
  remains gated on deploy.

Verification:

```powershell
node -c api\src\openApiSpec.js
npx.cmd vitest run src/openApiSpec.test.js src/index.test.js
npx.cmd vitest run src/jobsRoute.test.js src/rayconJobs.test.js src/deployManifest.test.js -t "Alpha Capacity"
npx.cmd vitest run
git -C .\work\RayCon diff --check -- api\src\openApiSpec.js api\src\openApiSpec.test.js
git -C .\work\due-diligence-reporter diff --check -- HANDOFF.md
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content
```

Results:

- OpenAPI syntax check passed.
- OpenAPI/index focused tests: `2 passed`, `33 passed`.
- Focused Alpha Capacity RayCon route/job/deploy slice: `2 passed`,
  `6 passed`, `112 skipped`.
- Full RayCon API Vitest: `20 passed`, `273 passed`.
- Diff checks passed with LF/CRLF warnings only.
- Production `/version`: `git_commit=7cba48d`.

## 2026-06-09 - RayCon Version Guard Added for Live Proof

- Audited the DDR/RayCon API hostname split before the post-deploy proof. Both
  known RayCon hostnames currently resolve to the same deployed service and
  return `/version` with `git_commit=7cba48d`.
- Added an opt-in RayCon deploy-proof guard to `scripts/raycon_followup.py`:
  `--require-raycon-git-commit <sha>`. When supplied, the script derives
  RayCon `/version` from the configured `RAYCON_JOBS_URL` origin and exits
  non-zero unless the reported `git_commit` matches the expected full or short
  SHA.
- The guard runs immediately after settings load and before Google Drive,
  Rhodes, Alpha Capacity artifact upload, or RayCon dispatch. This prevents the
  Miami Beach production proof from accidentally posting a capacity-backed job
  to the old deployed RayCon revision.
- Updated the script usage notes with the guarded proof mode. After RayCon is
  committed and deployed, the production proof command should include the new
  commit:

```powershell
uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --redispatch-after-minutes 0 --skip-dd-republish --suppress-notifications --require-raycon-git-commit <new-raycon-commit>
```

Verification:

```powershell
uv run pytest tests\test_raycon_followup.py::test_raycon_version_url_from_jobs_url_uses_configured_origin tests\test_raycon_followup.py::test_git_commit_matches_full_or_short_sha tests\test_raycon_followup.py::test_verify_raycon_git_commit_accepts_matching_version tests\test_raycon_followup.py::test_verify_raycon_git_commit_rejects_mismatch tests\test_raycon_followup.py::test_main_require_raycon_git_commit_stops_before_google_client -q --basetemp C:\tmp\ddr-raycon-version-guard
uv run ruff check scripts\raycon_followup.py tests\test_raycon_followup.py
uv run mypy scripts\raycon_followup.py
uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --dry-run --suppress-notifications --require-raycon-git-commit abcdef1
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_followup.py tests\test_raycon_client.py tests\test_inbox_scanner.py tests\test_workflow_contracts.py tests\test_docs_env_contract.py -q --basetemp C:\tmp\ddr-alpha-capacity-version-guard
npx.cmd vitest run src/jobsRoute.test.js src/rayconJobs.test.js src/deployManifest.test.js -t "Alpha Capacity|capacity|dirty working trees|runtime git commit|source-selection"
```

Results:

- Focused RayCon version-guard pytest: `5 passed`.
- Focused Ruff and mypy: passed.
- Deliberate live mismatch check exited before Drive/RayCon work with
  `RayCon /version git_commit mismatch: expected abcdef1, got 7cba48d`.
- Affected DDR Alpha Capacity/RayCon suite: `256 passed`.
- Focused RayCon capacity/deploy suite: `3 passed`, `17 passed`, `101 skipped`.

## 2026-06-09 - Alpha Capacity Workflow Secret Preflight Tightened

- Audited DDR workflow runtime wiring for the Alpha Capacity/RayCon Block Plan
  path. The workflows were already writing `OPENAI_API_KEY` and
  `OPENAI_CAPACITY_MODEL` into `.env`, but `inbox-scan`,
  `raycon-followup`, and `vendor-doc-republish-sweep` did not fail early when
  `OPENAI_API_KEY` was missing.
- Tightened those workflow preflights so missing OpenAI credentials fail before
  the job reaches Block Plan processing. This protects the operating goal from
  silent no-capacity RayCon dispatches caused by an unset secret.
- Added a workflow contract test covering all Alpha-capacity-aware workflow
  entrypoints: `inbox-scan`, `raycon-followup`,
  `vendor-doc-republish-sweep`, `daily-dd-check`, and
  `publish-to-mcp-hive`. The test asserts they carry the OpenAI key, carry the
  optional `OPENAI_CAPACITY_MODEL` override, and include an
  `OPENAI_API_KEY missing` preflight.
- This does not remove fail-soft behavior inside the Python path. If a specific
  Block Plan lacks enough evidence, DDR still records the capacity status and
  may dispatch RayCon without Alpha Capacity. The workflow change only makes
  missing platform credentials explicit instead of blending them with
  document-evidence failures.

Verification:

```powershell
uv run pytest tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-workflow-alpha-capacity-secret
uv run ruff check tests\test_workflow_contracts.py
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_followup.py tests\test_raycon_client.py tests\test_inbox_scanner.py tests\test_workflow_contracts.py tests\test_docs_env_contract.py -q --basetemp C:\tmp\ddr-alpha-capacity-workflow-fastfail
uv run ruff check .github\workflows tests\test_workflow_contracts.py scripts\raycon_followup.py src\due_diligence_reporter\alpha_capacity_analysis.py src\due_diligence_reporter\inbox_scanner.py src\due_diligence_reporter\raycon_client.py
```

Results:

- Workflow contract tests: `12 passed`.
- Focused workflow Ruff: all checks passed.
- Affected Alpha Capacity/RayCon DDR suite: `251 passed`.
- Focused Ruff over workflow/test/touched capacity path files: all checks
  passed.

## 2026-06-09 - RayCon Deploy Command Surface Pinned

- Refreshed current production state before touching the deploy path.
  Production RayCon `/version` still reports `git_commit=7cba48d`, so the live
  Miami Beach proof remains gated on committing and deploying the RayCon
  capacity-ingestion changes.
- Audited the RayCon Cloud Run deploy helper and found one readiness gap: the
  helper was present and tested, but not exposed through the repo's normal
  command surface. Added root package scripts:
  - `npm run deploy:cloud-run` for the dry-run deploy plan.
  - `npm run deploy:cloud-run:execute` for the guarded production deploy.
- The execute command still uses the deploy helper's dirty-tree hard stop. It
  will refuse to deploy while the local RayCon tree has modified or untracked
  files, which is intentional: `/version`, the image tag, and deployed source
  must all match a committed revision before the DDR proof is meaningful.
- Updated RayCon `CLAUDE.md` command docs and pinned the package scripts in
  `api/src/deployManifest.test.js`.
- Re-ran the dry-run through the new npm script. It printed the Cloud Build and
  Cloud Run commands only; no `gcloud` commands were executed. Because the tree
  is intentionally dirty, the planned image/env commit remains the current
  base commit `7cba48d2ed315bf3028983edfa4cbb2cd3a3322f`.

Verification:

```powershell
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content
npx.cmd vitest run src/deployManifest.test.js
node -c scripts\deploy-raycon-cloud-run.mjs
npm.cmd run deploy:cloud-run -- --out-dir ..\raycon-deploy-preview-current
npx.cmd vitest run
git -C .\work\RayCon diff --check
git -C .\work\due-diligence-reporter diff --check -- HANDOFF.md
```

Results:

- Production `/version`: `git_commit=7cba48d`.
- Focused deploy-manifest suite: `1 passed`, `6 passed`.
- Deploy helper syntax check passed.
- New npm dry-run script printed `gcloud builds submit` and `gcloud run deploy`
  commands only; dry-run did not execute them.
- Full RayCon API Vitest: `20 passed`, `272 passed`.
- Diff checks passed with LF/CRLF warnings only.

## 2026-06-09 - RayCon Proof Site Selection Refreshed

- Re-ran the live RayCon test-site inventory after the repo validation work.
  The only active Block Plan sites with missing RayCon scenarios remain:
  - `Alpha Plano 5509 Pleasant Valley Dr`
    (`site_id=k978wq2je97vw8aftnz0j7rv0d85emyj`)
  - `Alpha Tampa 2409 S MacDill Ave`
    (`site_id=k971m94ck04aqyhnr8jcs17zyn83dq4h`)
- Re-ran text extraction across current Block Plans. Plano extracted only 712
  characters and Tampa only 211 characters; neither had parseable student-count
  pairs or capacity snippets.
- Re-ran no-upload Alpha Capacity probes for Plano and Tampa. Both returned
  `insufficient_evidence`, so they are still poor proof sites for the
  capacity-backed RayCon path even though their RayCon scenario is missing.
- Re-ran the Miami Beach dry-run preview. It still attaches Alpha Capacity with
  signature `114-199` from the Block Plan pairs `40/70`, `24/42`, and `50/87`.
  It is therefore the best current proof site, despite being a failed-scenario
  recovery test rather than a first-time missing-scenario test.
- Use Miami Beach for the first post-deploy production proof:
  `Alpha Miami Beach 300 71st 3rd`,
  `site_id=k972ay4w964539mq0naqyde5ws85fr3r`, Block Plan file
  `10dPoeXlUcuYwvEGflf0r9zo4RQMCfErM`, expected Fast Path/Strict capacity
  `114`, expected Max Capacity `199`.
- Do not run the non-dry-run proof until RayCon is committed/deployed and
  `/version` reports the new commit. The current dry-run evidence proves DDR can
  attach Alpha Capacity, but production RayCon is still expected to ignore the
  new capacity fields until deployed.

Verification:

```powershell
uv run python ..\find_raycon_test_sites.py
uv run python ..\probe_all_block_plan_capacity_text.py
uv run python ..\probe_alpha_capacity_model_candidates.py
uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --dry-run --preview-capacity-analysis --skip-dd-republish --suppress-notifications --redispatch-after-minutes 0
```

Results:

- Inventory scan: Plano and Tampa are the only missing-scenario Block Plan
  candidates; both have no Alpha Capacity signature.
- Block Plan text scan: Plano/Tampa have no student pairs; Miami Beach has
  `40/70`, `24/42`, and `50/87`, totaling `114/199`.
- Alpha Capacity model candidate probe: Plano and Tampa both returned
  `insufficient_evidence`.
- Miami Beach dry-run preview: `capacity_analysis_status=preview_success`,
  `capacity_analysis_attached=true`, `capacity_analysis_signature=114-199`,
  `capacity_analysis_preview=true`, and `dispatch_skipped=dry_run`.

## 2026-06-09 - Repo-Level Gates Cleaned Before RayCon Deploy Proof

- Ran repo-level DDR validation after the Alpha Capacity/RayCon changes. The
  first full run exposed stale compatibility failures outside the capacity path:
  old tests still called `assign_p1(..., gc)` and `build_site_counts(records,
  cfg)`, sender-filter tests patched `build_site_summary` while current code
  called `_build_site_summary`, and Ruff found import/date cleanup in older
  files.
- Added narrow backwards-compatible surfaces:
  - `assignment.assign_p1(..., gc=None)` accepts the legacy unused positional
    Google client argument.
  - `assignment.build_site_counts(records, cfg=None)` accepts legacy config and
    routes through `extract_p1_from_record`.
  - `inbox_scanner.build_site_summary` is restored as a public alias, and
    inbox processing uses a small resolver so tests/callers patching either
    `build_site_summary` or `_build_site_summary` continue to work.
  - Cleaned Ruff-only issues in `scripts/reprocess_mislabeled.py`,
    `tests/test_cds_verification.py`, and `tests/test_sender_filter.py`.
- This was deliberately scoped as validation cleanup, not a new assignment or
  inbox behavior change. The RayCon/Alpha Capacity path remains the same.
- RayCon full API validation was rerun and passed. The deploy helper dry-run was
  also rerun; it still refuses to execute while the RayCon tree is dirty and
  still plans image/env commit `7cba48d2ed315bf3028983edfa4cbb2cd3a3322f`.

Verification:

```powershell
bd ready
uv run pytest tests\test_assignment.py::TestAssignP1 tests\test_assignment.py::TestBuildSiteCounts tests\test_sender_filter.py::TestProcessEmailInternalSenderSkip::test_vendor_sender_proceeds_normally tests\test_sender_filter.py::TestScanInboxInternalCounter::test_mixed_internal_and_vendor_emails -q --basetemp C:\tmp\ddr-raycon-compat-failures-2
uv run ruff check scripts\reprocess_mislabeled.py tests\test_cds_verification.py tests\test_sender_filter.py src\due_diligence_reporter\assignment.py src\due_diligence_reporter\inbox_scanner.py
uv run mypy src\due_diligence_reporter\assignment.py src\due_diligence_reporter\inbox_scanner.py
uv run pytest -q --basetemp C:\tmp\ddr-raycon-full-current-2
uv run ruff check .
uv run mypy src/
npx.cmd vitest run
node scripts\deploy-raycon-cloud-run.mjs --out-dir ..\raycon-deploy-preview-current
git -C .\work\due-diligence-reporter diff --check
git -C .\work\RayCon diff --check
```

Results:

- `bd ready`: no open issues.
- Focused compatibility regression slice: `13 passed`.
- Focused Ruff and mypy slices: passed.
- Full DDR pytest: `1165 passed`.
- Full DDR Ruff: all checks passed.
- Full DDR mypy over `src/`: no issues in 45 source files.
- Full RayCon API Vitest: `20 passed`, `271 passed`.
- RayCon deploy helper dry-run printed `gcloud` commands only; no deploy
  commands executed.
- DDR and RayCon `git diff --check` passed with LF/CRLF warnings only.

## 2026-06-09 - Capacity Source Boundary Tightened in DDR Prompt/Docs

- Audited the current DDR prompt/process contracts after selecting Miami Beach
  as the proof site. Runtime code and tests already route Block Plan capacity
  through Alpha Capacity Analysis, but `prompt_v4.md` still allowed
  `Block Plan, RayCon Scenario, team note` as broad capacity sources.
- Tightened `docs/prompts/prompt_v4.md` so published capacity must come from
  Alpha Capacity Analysis, a RayCon Scenario that explicitly carries Alpha
  Capacity Analysis provenance, or a sourced gap. It now says not to use team
  notes, RayCon narrative prose, or RayCon internal capacity fallbacks as the
  published capacity source when Alpha Capacity Analysis is available.
- Tightened `docs/process/HOW-IT-WORKS.md` so sourced team notes may override
  cost or schedule only, not published capacity. This keeps the written DDR
  process aligned with Greg's requirement that capacity numbers come from the
  `alpha-capacity-analysis` skill while RayCon consumes those counts for
  pricing.
- Production RayCon was rechecked at `2026-06-09T22:51:44.088Z`; `/version`
  still reports `git_commit=7cba48d`, so the Miami Beach non-dry-run proof
  remains gated until RayCon is committed and deployed.

Verification:

```powershell
uv run pytest tests\test_prompt_contract.py tests\test_docs_env_contract.py -q --basetemp C:\tmp\ddr-raycon-capacity-doc-contract
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_followup.py tests\test_raycon_client.py tests\test_inbox_scanner.py tests\test_workflow_contracts.py tests\test_prompt_contract.py tests\test_docs_env_contract.py -q --basetemp C:\tmp\ddr-raycon-capacity-contract-current
npx.cmd vitest run src/rayTools.test.js src/rayconJobs.test.js -t "capacity guesses|Alpha Capacity source|Miami Beach Alpha Capacity|does not fail Alpha-capacity-backed"
npx.cmd vitest run src/jobsRoute.test.js src/deployManifest.test.js -t "Alpha Capacity|capacity|dirty working trees|runtime git commit|source-selection"
node -c api\src\index.js
node -c api\src\rayconJobs.js
node -c api\src\rayTools.js
git -C .\work\due-diligence-reporter diff --check
git -C .\work\RayCon diff --check
```

Results:

- DDR prompt/docs contract tests: `5 passed`.
- DDR affected Alpha Capacity/RayCon suite plus prompt/docs tests:
  `253 passed`.
- RayCon trust-boundary focused slice: `4 passed`.
- RayCon route/deploy focused slice: `9 passed`.
- RayCon syntax checks passed for `api\src\index.js`,
  `api\src\rayconJobs.js`, and `api\src\rayTools.js`.
- DDR and RayCon `git diff --check` passed with LF/CRLF warnings only.

## 2026-06-09 - Miami Beach Proof Site Reconfirmed and DDR Report Mapping Pinned

- Re-ran live site discovery for the RayCon/DDR capacity proof. The only clean
  active Block Plan sites with missing RayCon scenarios are still Plano
  (`site_id=k978wq2je97vw8aftnz0j7rv0d85emyj`) and Tampa
  (`site_id=k971m94ck04aqyhnr8jcs17zyn83dq4h`), but both current Alpha Capacity
  probes returned `insufficient_evidence` and the extracted Block Plan text has
  no explicit `X / Y students` evidence.
- Use `Alpha Miami Beach 300 71st 3rd`
  (`site_id=k972ay4w964539mq0naqyde5ws85fr3r`) as the first live proof site.
  Its Block Plan file is `10dPoeXlUcuYwvEGflf0r9zo4RQMCfErM`; extracted
  evidence still contains `40 / 70 STUDENTS`, `24 / 42 STUDENTS`, and
  `50 / 87 STUDENTS`, summing to Fast Path/Strict `114` and Max `199`.
- Scoped dry-run preview against Miami Beach again proved the intended
  pre-dispatch state: `capacity_analysis_status=preview_success`,
  `capacity_analysis_attached=true`, `capacity_analysis_signature=114-199`,
  `capacity_analysis_preview=true`, and `dispatch_skipped=dry_run`.
- Added a DDR report-mapping regression for a Miami-Beach-shaped completed
  RayCon envelope with Alpha Capacity provenance. It asserts the generated
  report fields render `exec.fastest_open_capacity=114` and
  `exec.max_capacity_capacity=199` and keep the RayCon run status completed.
- Production proof is still gated: do not run the Miami Beach non-dry-run until
  RayCon is committed/deployed and `/version` reports the new commit. The last
  checked production API was still on commit `7cba48d`.

Verification:

```powershell
uv run python ..\find_raycon_test_sites.py
uv run python ..\probe_alpha_capacity_model_candidates.py
uv run python ..\probe_all_block_plan_capacity_text.py
uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --dry-run --preview-capacity-analysis --skip-dd-republish --suppress-notifications --redispatch-after-minutes 0
uv run pytest tests\test_raycon_client.py::TestRayConPayloadEnvelope tests\test_raycon_client.py::TestRayConScenarioToReportFields -q --basetemp C:\tmp\ddr-raycon-report-map-alpha
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_followup.py tests\test_raycon_client.py tests\test_inbox_scanner.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-capacity-report-map
git -C .\work\due-diligence-reporter diff --check
```

Results:

- Plano and Tampa probes: `insufficient_evidence`.
- Miami Beach capacity text scan: Fast Path/Strict `114`, Max `199`.
- Miami Beach dry-run preview: one failed-scenario alert row, no dispatch/upload,
  Alpha Capacity signature `114-199`.
- Focused DDR report-mapping pytest: `19 passed`.
- Broader affected DDR suite: `248 passed`.
- DDR `git diff --check` passed with LF/CRLF warnings only.

## 2026-06-09 - RayCon Deploy Gate Dry-Run Verified

- Audited the untracked RayCon deploy helper
  `scripts/deploy-raycon-cloud-run.mjs`. The helper builds a Cloud Build config
  and Cloud Run env file from `deploy-manifest.yaml`, tags the image with
  `git rev-parse HEAD`, and refuses `--execute` when `git status --porcelain`
  is non-empty.
- Added a deploy-manifest regression proving untracked files also hard-stop
  execution, not only modified tracked files. This matters because the deploy
  helper itself is currently untracked and must be committed before it can be a
  trustworthy production deploy path.
- Ran a dry-run deploy plan with:

```powershell
node scripts\deploy-raycon-cloud-run.mjs --out-dir ..\raycon-deploy-preview-current
```

- The dry run wrote generated config files under
  `work\raycon-deploy-preview-current`, printed the `gcloud builds submit` and
  `gcloud run deploy` commands, and executed no `gcloud` commands.
- The dry-run image/env still used
  `GIT_COMMIT=7cba48d2ed315bf3028983edfa4cbb2cd3a3322f`, proving the current
  local fixes are not deployable/provable until RayCon is committed.
- Next production proof remains:
  1. commit the intended RayCon changes, including `scripts/deploy-raycon-cloud-run.mjs`;
  2. run the deploy helper with `--execute` after approval;
  3. verify `/version` returns the new commit;
  4. run the Miami Beach non-dry-run DDR follow-up proof.

Verification:

```powershell
npx.cmd vitest run src/deployManifest.test.js -t "dirty working trees|runtime git commit|source-selection"
node -c scripts\deploy-raycon-cloud-run.mjs
node scripts\deploy-raycon-cloud-run.mjs --out-dir ..\raycon-deploy-preview-current
npx.cmd vitest run
git -C .\work\RayCon diff --check
git -C .\work\due-diligence-reporter diff --check
```

Results:

- Focused deploy-manifest slice: `3 passed`.
- Deploy helper syntax check passed.
- Dry-run deploy printed commands only; no `gcloud` execution.
- Full RayCon API Vitest: `271 passed`.
- Diff checks passed for both repos, with LF/CRLF warnings only.

## 2026-06-09 - Incomplete Alpha Capacity Retry Dedupe Guard Pinned

- Audited the current DDR/RayCon Block Plan path end to end against the active
  goal:
  - DDR inbox and RayCon follow-up prefer complete Alpha Capacity Analysis
    artifacts already in M1.
  - If none exists, DDR runs hosted `alpha-capacity-analysis` from extracted
    Block Plan text plus PDF bytes and only attaches generated output when both
    Strict/Fast Path and Max Capacity counts are present.
  - RayCon normalizes complete Alpha Capacity payloads, applies those counts to
    published Fastest Path and Maximum Capacity scenarios, and passes
    `capacitySource: "alpha_capacity_analysis"` into `estimate_costs` so
    student-scaled pricing follows Alpha capacity while internal RayCon capacity
    remains audit evidence.
- Added a RayCon route regression proving incomplete Alpha Capacity artifacts do
  not suppress later recovery:
  - no-capacity Block Plan job key remains unchanged;
  - incomplete Alpha artifact gets `capacity:alpha_incomplete:<artifact>`;
  - later complete Alpha payload for the same Block Plan/artifact gets
    `capacity:alpha:<strict>-<max>` and enqueues a fresh job.
- This protects the 95%+ operating target from a common failure mode: a partial
  or early capacity artifact can no longer poison durable job dedupe and block a
  later complete capacity-backed run.

Verification:

```powershell
npx.cmd vitest run src/jobsRoute.test.js -t "Alpha Capacity|capacity"
npx.cmd vitest run
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_followup.py tests\test_raycon_client.py tests\test_inbox_scanner.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-capacity-current-2
git -C .\work\RayCon diff --check
git -C .\work\due-diligence-reporter diff --check
```

Results:

- Focused RayCon route/idempotency slice: `4 passed`.
- Full RayCon API Vitest: `271 passed`.
- DDR affected Alpha Capacity/RayCon suite: `247 passed`.
- Diff checks passed for both repos, with LF/CRLF warnings only.

## 2026-06-09 - RayCon Planner Prompt Aligned With Alpha Capacity Trust Boundary

- Patched RayCon `src/engine/plannerSystemPrompt.js` so the model-facing
  `estimate_costs` instructions no longer tell Ray to pass arbitrary
  `mvpCapacity` / `idealCapacity` values.
- The planner prompt now states the source boundary explicitly: Ray may pass
  grade/classroom/NLA scenario inputs for deterministic capacity, but only the
  system-owned DDR job runner may send Alpha Capacity counts with
  `capacitySource="alpha_capacity_analysis"`.
- Removed the legacy workflow example that called
  `estimate_costs(... scope="ideal", mvpCapacity=38, idealCapacity=52)`. The
  example now calls `scope="ideal"` without manual capacity counts.
- A follow-up search shows remaining `mvpCapacity` / `idealCapacity` references
  are backend/tool-schema handling or tests, not prompt instructions for Ray to
  invent student counts.
- Production RayCon is still not updated. `/version` last checked at
  `2026-06-09T22:34:52.866Z` still reported `git_commit=7cba48d`; do not run
  the Miami Beach non-dry-run proof until the local RayCon changes are
  committed/deployed and `/version` confirms the new commit.

Verification:

```powershell
node -c src\engine\plannerSystemPrompt.js
node -c src\engine\estimateToolSchema.js
git -C .\work\RayCon diff --check
npx.cmd vitest run src/rayTools.test.js src/rayconJobs.test.js -t "capacity guesses|Alpha Capacity source|Miami Beach Alpha Capacity|does not fail Alpha-capacity-backed"
npx.cmd vitest run
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_followup.py tests\test_raycon_client.py tests\test_inbox_scanner.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-capacity-current
```

Results:

- RayCon prompt/schema syntax checks passed.
- RayCon `git diff --check` passed, with LF/CRLF warnings only.
- Focused RayCon trust-boundary slice: `4 passed`.
- Full RayCon API Vitest: `270 passed`.
- DDR affected Alpha Capacity/RayCon suite: `247 passed`.

## 2026-06-09 - RayCon Estimator Trust Boundary Fixed

- Full RayCon API Vitest exposed an important regression risk: the legacy
  `rayTools` guardrail still expected `estimate_costs` to ignore arbitrary
  model-supplied `mvpCapacity` / `idealCapacity` guesses for the same canonical
  site, but the Alpha Capacity integration had made those input fields affect
  Maximum Capacity pricing unconditionally.
- Fixed RayCon so supplied student-count inputs are trusted only when paired
  with `capacitySource: "alpha_capacity_analysis"` (or snake-case equivalent).
  Ordinary Ray/model capacity guesses are ignored again; the unified internal
  capacity calculator remains the fallback/audit path.
- Updated `runRayconJob` to pass `capacitySource: "alpha_capacity_analysis"`
  only after it normalizes a complete Alpha Capacity payload. This preserves the
  goal: DDR/Alpha supplies authoritative Fast Path and Max counts for pricing,
  while RayCon does not let untrusted model guesses own capacity.
- Updated the model-facing `estimate_costs` tool schema text so it no longer
  tells Ray to pass arbitrary capacity counts for ideal/Maximum Capacity.
- Production RayCon `/version` was rechecked after this local fix:
  `git_commit=7cba48d`, timestamp `2026-06-09T22:24:24.462Z`. The production
  gate remains open until the local RayCon changes are committed and deployed.

Verification:

```powershell
npx.cmd vitest run src/rayTools.test.js src/rayconJobs.test.js -t "capacity guesses|Alpha Capacity source|Miami Beach Alpha Capacity|does not fail Alpha-capacity-backed"
npx.cmd vitest run
node -c api\src\rayTools.js
node -c api\src\rayconJobs.js
node -c src\engine\estimateToolSchema.js
git -C .\work\RayCon diff --check
Invoke-WebRequest -Uri https://raycon-api-dkxp2hji2q-uc.a.run.app/version -UseBasicParsing | Select-Object -ExpandProperty Content
```

Results:

- Focused RayCon trust-boundary slice: `4 passed`.
- Full RayCon API Vitest: `270 passed`.
- Syntax checks passed for `api\src\rayTools.js`,
  `api\src\rayconJobs.js`, and `src\engine\estimateToolSchema.js`.
- `git diff --check` passed for RayCon, with LF/CRLF warnings only.
- Root/frontend `npm test` is still not a useful verifier in this checkout
  because frontend dependencies are not installed (`vitest`, `react`,
  `firebase`, `@supabase/supabase-js`, `jsdom` missing). The API suite is the
  relevant verifier for this backend capacity path.

## 2026-06-09 - Partial Alpha Capacity Artifacts Guarded

- Tightened the DDR attachment gate for generated/previewed Alpha Capacity
  artifacts in both RayCon follow-up and inbox Block Plan downstream dispatch.
  DDR now requires `alpha_capacity_counts_signature(...)` to return a complete
  `strict-max` signature before it attaches a generated payload to RayCon.
- If the capacity run returns `status=success` but only one scenario count, DDR
  still dispatches RayCon with the Block Plan, but marks the capacity result
  `generation_incomplete` or `preview_incomplete` and does not send
  `capacity_analysis_file_id` / `capacity_analysis`. This prevents a partial
  Alpha output from becoming an authoritative capacity source.
- Existing complete artifacts already came through `read_alpha_capacity_analysis_from_m1`,
  which skips partial payloads. The new guard covers generated artifacts and
  dry-run previews as a second line of defense.
- Added focused regressions for the follow-up safety-net path and the inbox
  Block Plan path to prove partial generated artifacts are not attached.

Verification:

```powershell
uv run pytest tests\test_raycon_followup.py::TestSafetyNetDispatch::test_dispatch_generates_capacity_artifact_when_missing tests\test_raycon_followup.py::TestSafetyNetDispatch::test_dispatch_does_not_attach_generated_partial_capacity_artifact tests\test_raycon_followup.py::TestSafetyNetDispatch::test_dispatch_continues_when_capacity_generation_fails tests\test_inbox_scanner.py::TestBlockPlanDownstream::test_generates_alpha_capacity_analysis_from_pdf_when_text_is_empty_before_raycon tests\test_inbox_scanner.py::TestBlockPlanDownstream::test_does_not_attach_generated_partial_alpha_capacity_analysis tests\test_inbox_scanner.py::TestBlockPlanDownstream::test_pings_raycon_with_alpha_capacity_analysis_when_available tests\test_raycon_client.py::TestPostRayConJob::test_capacity_analysis_payload_sent_when_available tests\test_raycon_client.py::test_alpha_capacity_counts_signature_accepts_aliases_and_student_strings -q --basetemp C:\tmp\ddr-raycon-capacity-guard
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_followup.py tests\test_raycon_client.py tests\test_inbox_scanner.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-capacity-suite-guard
uv run ruff check scripts\raycon_followup.py src\due_diligence_reporter\alpha_capacity_analysis.py src\due_diligence_reporter\inbox_scanner.py tests\test_raycon_followup.py tests\test_inbox_scanner.py tests\test_alpha_capacity_analysis.py
uv run mypy scripts\raycon_followup.py
uv run mypy src\due_diligence_reporter\alpha_capacity_analysis.py
uv run mypy src\due_diligence_reporter\inbox_scanner.py
git -C .\work\due-diligence-reporter diff --check
git -C .\work\RayCon diff --check
```

Results:

- Focused DDR guard pytest: `8 passed`.
- Broader affected DDR pytest: `247 passed`.
- Ruff: all checks passed.
- Mypy: passed file-by-file for `scripts\raycon_followup.py`,
  `src\due_diligence_reporter\alpha_capacity_analysis.py`, and
  `src\due_diligence_reporter\inbox_scanner.py`. The combined multi-file mypy
  invocation still hits the repo's known duplicate-module mapping issue.
- Diff checks passed for DDR and RayCon, with LF/CRLF warnings only.

## 2026-06-09 - Miami Beach Selected for RayCon Capacity Proof

- Refreshed live RayCon test-site inventory. The only active sites currently
  showing a Block Plan with no `raycon_scenario.json` remain:
  `Alpha Plano 5509 Pleasant Valley Dr`
  (`site_id=k978wq2je97vw8aftnz0j7rv0d85emyj`) and
  `Alpha Tampa 2409 S MacDill Ave`
  (`site_id=k971m94ck04aqyhnr8jcs17zyn83dq4h`).
- Do not use Plano or Tampa as first proof sites. A current no-upload Alpha
  Capacity probe returned `insufficient_evidence` for both, with no Strict/Fast
  Path or Max Capacity counts. Their extracted PDF text also contains no
  `X / Y students` pairs.
- Use `Alpha Miami Beach 300 71st 3rd`
  (`site_id=k972ay4w964539mq0naqyde5ws85fr3r`) as the proof site. Its Block
  Plan (`10dPoeXlUcuYwvEGflf0r9zo4RQMCfErM`) still exposes `40/70`, `24/42`,
  and `50/87` student pairs, which sum to Alpha Capacity `114-199`.
- Current Miami Beach dry-run preview proved the intended recovery path without
  writing Drive artifacts or posting to RayCon:
  `capacity_analysis_status=preview_success`,
  `capacity_analysis_attached=true`,
  `capacity_analysis_signature=114-199`,
  `capacity_analysis_preview=true`, and `dispatch_skipped=dry_run`.
- The existing Miami Beach `raycon_scenario.json` is still the failed RayCon
  scenario from run `rc_20260609202847_ef340148f3`, modified
  `2026-06-09T20:34:44.269Z`, rejected because RayCon owned a mismatched
  126-student Fastest Open count. That makes Miami Beach the direct proof of the
  fix: DDR attaches Alpha Capacity `114/199`, and RayCon prices from those
  Alpha-sourced counts.
- RayCon's latest Alpha-capacity-backed deterministic-pricing regression slice
  passed, including the case where Ray review throws `No JSON object found in
  model response`. The broader affected RayCon test slice also passed.
- Remaining production gate: RayCon capacity-ingestion code is still local and
  dirty, not committed/deployed. Do not run the Miami Beach non-dry-run proof
  until RayCon is committed, deployed, and `/version` confirms the new commit.

Verification:

```powershell
uv run python ..\find_raycon_test_sites.py
uv run python ..\probe_block_plan_capacity_text.py
uv run python ..\probe_alpha_capacity_model_candidates.py
uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --dry-run --preview-capacity-analysis --skip-dd-republish --suppress-notifications --redispatch-after-minutes 0
npx.cmd vitest run src/rayconJobs.test.js -t "Alpha-capacity-backed deterministic pricing|uses Miami Beach Alpha Capacity|ignores partial Alpha|normalizes comma-formatted Alpha Capacity|does not fail Alpha-capacity-backed deterministic pricing when Ray review fails|fails validation when Ray review returns empty rationale"
npx.cmd vitest run src/rayconJobs.test.js src/jobsRoute.test.js src/deployManifest.test.js
node -c api\src\rayconJobs.js
node -c scripts\deploy-raycon-cloud-run.mjs
git -C .\work\RayCon diff --check
git -C .\work\due-diligence-reporter diff --check
```

Results:

- Live inventory refreshed at `2026-06-09T22:16:17Z`.
- Plano/Tampa Alpha Capacity probes: `insufficient_evidence`.
- Miami Beach dry-run preview: one failed-scenario alert row, no dispatch/upload,
  Alpha Capacity signature `114-199`.
- Focused RayCon regression slice: `5 passed`.
- Broader RayCon affected slice: `116 passed`.
- `node -c` checks passed.
- `git diff --check` passed for DDR and RayCon, with LF/CRLF warnings only.

## 2026-06-09 - RayCon Capacity Preview Dry-Run Added

- Added `scripts/raycon_followup.py --preview-capacity-analysis` for scoped
  validation runs. The flag only has an effect with `--dry-run`: it downloads
  the Block Plan, runs Alpha Capacity Analysis without uploading an artifact,
  and carries `capacity_analysis_status`, `capacity_analysis_attached`,
  `capacity_analysis_signature`, and `capacity_analysis_preview` into the
  per-site log row.
- Default `--dry-run` remains cheap and does not call Alpha Capacity, upload to
  Drive, or post to RayCon. Preview mode also does not upload or post; it only
  proves whether a real run would have complete Alpha Capacity counts available.
- Fixed preview summary propagation for both missing-scenario and failed-scenario
  retry paths. This matters for the Miami Beach proof because that site already
  has a failed `raycon_scenario.json`, so the retry path is
  `_handle_failed_scenario`, not the missing-scenario safety net.
- Hardened the Alpha Capacity prompt to explicitly treat attached Block Plan PDF
  pages as evidence, not only extracted PDF text, while preserving the
  no-invention rule. Plano and Tampa still returned `insufficient_evidence` in
  live no-upload probes after this prompt fix, so they remain poor first proof
  sites.
- Updated the insufficient-evidence message to say "Block Plan evidence" rather
  than "Block Plan text" because DDR may send attached PDF evidence.
- Scoped Miami Beach preview command now proves the pre-deploy handoff:
  `capacity_analysis_status=preview_success`,
  `capacity_analysis_attached=true`,
  `capacity_analysis_signature=114-199`,
  `capacity_analysis_preview=true`, while `dispatch_skipped=dry_run`.

Verification:

```powershell
uv run pytest tests\test_raycon_followup.py::TestFailedScenarioAlerts::test_failed_status_dry_run_previews_capacity_retry tests\test_raycon_followup.py::TestSafetyNetDispatch::test_dispatch_dry_run_can_preview_capacity_without_upload_or_post tests\test_raycon_followup.py::TestSafetyNetDispatch::test_dispatch_dry_run_does_not_call_post tests\test_alpha_capacity_analysis.py -q --basetemp C:\tmp\ddr-raycon-capacity-preview-4
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_followup.py tests\test_raycon_client.py tests\test_inbox_scanner.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-capacity-preview-suite
uv run ruff check scripts\raycon_followup.py src\due_diligence_reporter\alpha_capacity_analysis.py tests\test_raycon_followup.py tests\test_alpha_capacity_analysis.py
uv run mypy scripts\raycon_followup.py
uv run mypy src\due_diligence_reporter\alpha_capacity_analysis.py
uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --dry-run --preview-capacity-analysis --skip-dd-republish --suppress-notifications --redispatch-after-minutes 0
```

Results:

- Focused preview regression pytest: `16 passed`.
- Broader affected DDR pytest: `245 passed`.
- Ruff: all checks passed.
- Mypy: passed separately for `scripts\raycon_followup.py` and
  `src\due_diligence_reporter\alpha_capacity_analysis.py`; the combined
  script/src invocation still hits the repo's known duplicate-module pattern.
- Miami Beach preview dry-run returned one failed-scenario alert row with
  capacity preview `114-199` and no dispatch/upload because dry-run was set.

## 2026-06-09 - RayCon Capacity Test Site Selection

- Recommended proof site remains `Alpha Miami Beach 300 71st 3rd`
  (`site_id=k972ay4w964539mq0naqyde5ws85fr3r`,
  Drive folder `1qjyrtHSFkPOQjTHPo8VSORCGh9h7KqOt`, M1 folder
  `1DuceE9iu0y45G6wncl4cRZyTkgP7IiYL`).
- Its Block Plan is
  `2026.05.19 - Alpha Miami Beach 300 71st 3rd Block Plan.pdf`
  (`10dPoeXlUcuYwvEGflf0r9zo4RQMCfErM`). PDF text extraction still exposes
  explicit capacity pairs `40/70`, `24/42`, and `50/87`, so the deterministic
  Alpha Capacity expectation is Fast Path/Strict `114` and Max `199`.
- Scoped RayCon follow-up dry-run against Miami Beach shows the current
  `raycon_scenario.json` is a failed RayCon output:
  run `rc_20260609202847_ef340148f3`, modified
  `2026-06-09T20:34:44.269Z`, rejected Fast Path count `126` because RayCon
  relied on mismatched room-schedule/capacity evidence. This makes Miami Beach
  a direct proof of the desired fix: DDR should attach Alpha Capacity `114/199`
  and RayCon should price from those counts rather than owning capacity.
- Live inventory found two clean active sites with Block Plans and no
  `raycon_scenario.json`: `Alpha Plano 5509 Pleasant Valley Dr`
  (`k978wq2je97vw8aftnz0j7rv0d85emyj`, Block Plan
  `1pp9uGPsBJnJ5Y-5gwBo2nZJYbfGa_zyC`) and
  `Alpha Tampa 2409 S MacDill Ave` (`k971m94ck04aqyhnr8jcs17zyn83dq4h`,
  Block Plan `1cRz03h7c3186Iq8iguqwzmbhhDr_uO9W`). Do not use either as the
  first proof site: no-upload Alpha Capacity model probes returned
  `insufficient_evidence` for both, even with the Block Plan PDF attached.

Verification:

```powershell
uv run python ..\find_raycon_test_sites.py
uv run python ..\probe_block_plan_capacity_text.py
uv run python ..\probe_alpha_capacity_model_candidates.py
uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --dry-run --skip-dd-republish --suppress-notifications --redispatch-after-minutes 0
```

Results:

- Live inventory read 60 active Rhodes records and listed Block Plan/RayCon
  scenario state.
- Plano/Tampa Alpha Capacity probes returned `insufficient_evidence`; neither
  produced Strict/Fast Path or Max counts.
- Miami Beach scoped dry-run returned one failed RayCon scenario alert and no
  writes because `--dry-run` and `--suppress-notifications` were set.

## 2026-06-09 - RayCon Alpha Capacity Proof Site and Env Fallback Hardened

- Active proof site remains `Alpha Miami Beach 300 71st 3rd`
  (`site_id=k972ay4w964539mq0naqyde5ws85fr3r`), because its Block Plan has
  explicit student-count pairs `40/70`, `24/42`, and `50/87`, which sum to
  Fast Path/Strict `114` and Max Capacity `199`.
- This is the right full-flow validation site after RayCon deployment: DDR can
  generate the Alpha Capacity JSON artifact from the Block Plan, attach it to
  the RayCon job, and RayCon should publish Fast Path and Max Capacity pricing
  using the Alpha-sourced counts rather than its internal fallback/audit count.
- Hardened the runtime model selection so blank GitHub `OPENAI_CAPACITY_MODEL`
  variables do not write an empty model into production `.env`. The inbox scan,
  RayCon follow-up, daily DD check, vendor republish sweep, and MCP publish
  workflows now default the Actions env expression to `gpt-4o`; the Alpha
  Capacity runner also falls back to `gpt-4o` when `model` or
  `openai_capacity_model` is blank.
- Added a regression test that forces a blank capacity model and verifies the
  OpenAI request still uses `gpt-4o`.
- Remaining production gate is unchanged: do not use the Miami Beach non-dry-run
  proof as final evidence until the RayCon capacity-ingestion changes are
  deployed. Current production `/version` was last checked as pre-change commit
  `7cba48d`; rechecked on 2026-06-09 at `2026-06-09T21:43:02.579Z` from the
  endpoint response.
- Additional RayCon recovery hardening was added after this note: Block Plan
  async idempotency now includes `capacity:alpha:<strict>-<max>` when a complete
  Alpha Capacity payload is attached. That prevents a later Miami Beach
  `114/199` capacity-backed submit from reusing an earlier no-capacity job state
  for the same Block Plan under the same source-selection contract, while
  repeated capacity-backed submits still dedupe.
- Additional DDR recovery hardening was added after the RayCon idempotency fix:
  `scripts/raycon_followup.py` now makes its own dispatch dedupe capacity-aware.
  A recent no-capacity dispatch can be superseded as soon as DDR can attach a
  complete Alpha Capacity artifact; recent capacity-backed dispatches still
  dedupe when the strict/max signature is unchanged. Dispatch state now records
  `capacity_analysis_signature` (`strict-max`, for example `114-199`) so a
  corrected capacity artifact can trigger a new RayCon job even if the Drive
  artifact file ID is unchanged.
- DDR's Alpha Capacity signature/readback helper now recognizes the same
  top-level containers RayCon accepts for capacity scenarios, including
  `result`. This keeps existing-artifact reuse and dispatch-state signatures in
  lockstep with RayCon's `capacity:alpha:<strict>-<max>` idempotency segment
  when an artifact wraps `fast_path` / `maximum_capacity` under `result`.
- DDR's RayCon follow-up terminal-status path is also capacity-aware now. If a
  prior known no-capacity job has become terminal `failed` / `validation_failed`
  before `raycon_scenario.json` appears, and DDR can now attach complete Alpha
  Capacity counts, it dispatches a new capacity-backed job instead of stopping
  at a terminal-status alert. Terminal capacity-backed jobs still alert normally.
- DDR's completed-status path is capacity-aware now as well. If the RayCon
  status endpoint says a prior known no-capacity job is `completed` but
  `raycon_scenario.json` is still not visible in M1, and DDR can now attach
  complete Alpha Capacity counts, it dispatches a fresh capacity-backed job
  instead of stopping at a missing-scenario alert. Completed capacity-backed
  jobs still alert normally when the scenario file is missing.
- RayCon deploy dry-run was rechecked with the deploy planner. It generated
  the expected Cloud Build and Cloud Run commands without executing `gcloud`,
  but it also proved the current RayCon checkout must be committed before
  deploy: the image tag and `GIT_COMMIT` env fallback are derived from `HEAD`,
  which is still `7cba48d2ed315bf3028983edfa4cbb2cd3a3322f` while the
  capacity-ingestion changes are uncommitted.
- The RayCon deploy helper now hard-stops all `--execute` attempts on a dirty
  working tree. The previous `--allow-dirty` escape hatch was removed so the
  deploy path cannot publish uncommitted capacity-ingestion code while tagging
  the image and `/version` as the old `HEAD`.
- RayCon now strips thousands separators before parsing Alpha Capacity counts
  from strings, matching DDR's Python-side normalization. This prevents
  formatted values such as `1,114 students` from being interpreted as `1` in
  either the async job idempotency key or the published scenario payload. DDR's
  dispatch-state capacity signature test now locks the same comma-formatted
  string behavior.

Verification:

```powershell
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_workflow_contracts.py tests\test_docs_env_contract.py tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_inbox_scanner.py tests\test_report_pipeline.py tests\test_report_schema.py tests\test_completeness.py -q --basetemp C:\tmp\ddr-raycon-alpha-capacity-focused
uv run pytest tests\test_raycon_followup.py::TestSafetyNetDispatch tests\test_raycon_client.py::test_alpha_capacity_counts_signature_accepts_aliases_and_student_strings -q --basetemp C:\tmp\ddr-raycon-capacity-signature-focused
uv run pytest tests\test_raycon_client.py::test_alpha_capacity_counts_signature_accepts_aliases_and_student_strings tests\test_raycon_client.py::TestReadAlphaCapacityAnalysisFromM1 -q --basetemp C:\tmp\ddr-raycon-capacity-result-container
uv run pytest tests\test_raycon_followup.py::TestSafetyNetDispatch -q --basetemp C:\tmp\ddr-raycon-terminal-capacity-recovery-2
uv run pytest tests\test_raycon_followup.py::TestSafetyNetDispatch -q --basetemp C:\tmp\ddr-raycon-completed-capacity-recovery
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_followup.py tests\test_raycon_client.py tests\test_inbox_scanner.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-capacity-signature-suite
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_followup.py tests\test_raycon_client.py tests\test_inbox_scanner.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-terminal-capacity-suite-2
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_followup.py tests\test_raycon_client.py tests\test_inbox_scanner.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-completed-capacity-suite
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_inbox_scanner.py tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_report_pipeline.py tests\test_report_schema.py tests\test_prompt_contract.py tests\test_completeness.py tests\test_classifier_keywords.py tests\test_docs_env_contract.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-alpha-capacity-current-full
uv run pytest tests\test_raycon_client.py::test_alpha_capacity_counts_signature_accepts_aliases_and_student_strings -q --basetemp C:\tmp\ddr-raycon-capacity-comma-signature
uv run pytest tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_alpha_capacity_analysis.py -q --basetemp C:\tmp\ddr-raycon-capacity-normalization
uv run ruff check src\due_diligence_reporter\alpha_capacity_analysis.py tests\test_alpha_capacity_analysis.py tests\test_workflow_contracts.py
uv run ruff check scripts\raycon_followup.py src\due_diligence_reporter\raycon_client.py tests\test_raycon_followup.py tests\test_raycon_client.py
uv run mypy scripts\raycon_followup.py
uv run mypy src\due_diligence_reporter\raycon_client.py
npx.cmd vitest run src/rayconJobs.test.js src/jobsRoute.test.js src/deployManifest.test.js
node -c api\src\index.js
node -c api\src\rayconJobs.js
node -c scripts\deploy-raycon-cloud-run.mjs
node scripts\deploy-raycon-cloud-run.mjs --out-dir C:\Users\foote\Documents\Codex\2026-06-09\relative-to-the-ddr-process-what\work\raycon-deploy-preview
node scripts\deploy-raycon-cloud-run.mjs --execute --out-dir C:\Users\foote\Documents\Codex\2026-06-09\relative-to-the-ddr-process-what\work\raycon-deploy-preview-execute-check
git diff --check
```

Results:

- DDR focused pytest: `424 passed`.
- DDR capacity-aware dispatch focused pytest: `18 passed`.
- DDR capacity signature/readback focused pytest: `8 passed`.
- DDR terminal no-capacity recovery focused pytest: `18 passed`.
- DDR completed-status/no-capacity recovery focused pytest: `19 passed`.
- DDR affected Alpha Capacity/RayCon follow-up/client/inbox/workflow pytest:
  `243 passed`.
- DDR broader affected Alpha Capacity/RayCon/report-schema/prompt/completeness
  pytest: `481 passed`.
- DDR capacity signature comma-format pytest: `1 passed`.
- DDR focused Alpha Capacity/RayCon client/follow-up pytest after normalization:
  `155 passed`.
- DDR ruff: all checks passed.
- DDR mypy: passed for `scripts\raycon_followup.py` and
  `src\due_diligence_reporter\raycon_client.py` when checked separately to
  avoid the repo's known script/src duplicate-module import pattern.
- RayCon focused Vitest: `115 passed` after the dirty-deploy hard-stop and
  comma-formatted Alpha Capacity string tests.
- RayCon syntax checks passed for `api\src\index.js`, `api\src\rayconJobs.js`,
  and `scripts\deploy-raycon-cloud-run.mjs`.
- RayCon deploy planner dry-run succeeded and printed the expected `gcloud`
  build/deploy commands only. It warned that the tree is dirty and showed the
  deployment tag would still be `7cba48d2ed315bf3028983edfa4cbb2cd3a3322f`
  until the RayCon changes are committed.
- RayCon deploy planner `--execute` on the dirty tree failed before `gcloud`
  with: `Refusing to deploy a dirty RayCon working tree. Commit first so
  /version, the image tag, and deployed code all match.`
- DDR and RayCon `git diff --check`: no whitespace errors; expected Windows
  LF-to-CRLF warnings only.

## 2026-06-09 - Portfolio Gaps Document-Missing Alerts Removed

- Beads issue `ddr-9ga` tracks this slice.
- Portfolio Gaps still reads Rhodes missing-document coverage and keeps it in
  the raw per-site `required_documents` context, but missing current-milestone
  documents no longer count as Portfolio Gaps.
- `portfolio_automation_gaps` no longer adds
  `missing_current_milestone_documents` to `gap_reasons`, no longer includes
  `missing_required_documents` in portfolio totals, and no longer emits
  source ActionRecords for document coverage.
- The Portfolio Gaps Chat formatter no longer includes document-missing counts
  or labels, and it skips stale document-only snapshots instead of posting a
  notification.
- The AADP remediation wrapper no longer appends DDR-owned
  `document_gap_remediation` actions for document gaps.
- Drive Rhodes Reconciliation no longer backfeeds document-registration rows as
  `source_workflow=portfolio-gaps`; document registration and readback health
  remains under DDR reconciliation telemetry.
- The `ddr portfolio-gaps` operator summary no longer prints missing
  current-milestone docs as a gap line.

Validation:

```powershell
uv run pytest tests/test_portfolio_automation_gaps.py tests/test_portfolio_gap_notifications.py tests/test_aadp_portfolio_gap_remediation_trigger.py tests/test_drive_rhodes_reconciliation.py tests/test_ddr_cli.py tests/test_workflow_contracts.py -q --basetemp C:\tmp\ddr-portfolio-doc-gap-removal-tests-2
uv run ruff check scripts/run_aadp_portfolio_gap_remediation.py src/due_diligence_reporter/portfolio_automation_gaps.py src/due_diligence_reporter/portfolio_gap_notifications.py src/due_diligence_reporter/drive_rhodes_reconciliation.py src/due_diligence_reporter/ddr_cli.py tests/test_portfolio_automation_gaps.py tests/test_portfolio_gap_notifications.py tests/test_aadp_portfolio_gap_remediation_trigger.py tests/test_drive_rhodes_reconciliation.py tests/test_ddr_cli.py
uv run mypy scripts/run_aadp_portfolio_gap_remediation.py src/due_diligence_reporter/portfolio_automation_gaps.py src/due_diligence_reporter/portfolio_gap_notifications.py src/due_diligence_reporter/drive_rhodes_reconciliation.py src/due_diligence_reporter/ddr_cli.py
git diff --check
```

Results:

- Focused pytest: 37 passed.
- Ruff: all checks passed.
- Mypy: no issues in 5 source files.
- `git diff --check`: no whitespace errors; expected Windows LF-to-CRLF
  warnings only.

## 2026-06-09 - Opening Plan Integrated Into DDR Publish Flow

- Beads issue `ddr-q75` tracks this implementation slice.
- Opening Plan now runs as a normal DDR enrichment step after source reads and
  School Approval context, before Alpha Phasing and `create_dd_report`.
- `apply_opening_plan_skill` is now exposed to the report-pipeline agent,
  receives canonical site name, address, Drive folder URL, and Rhodes `site_id`,
  and returns `report_data_fields["sources.opening_plan_link"]`.
- The tool now checks the site's M1 folder for an existing Opening Plan before
  checking `ANTHROPIC_API_KEY`, so republish runs reuse an existing document and
  avoid duplicates. New or reused Opening Plans are registered to Rhodes as
  `opening_plan_report` -> `docType=other`, `milestone=acquireProperty` when a
  `site_id` is available.
- M1/readiness recognition, prompt contract text, process docs, schema source
  attribution, Rhodes mapping, and Mermaid process flow were aligned to the new
  flow.

Validation:

```powershell
uv run pytest tests/test_opening_plan.py tests/test_report_pipeline.py::test_canonicalize_site_tool_input_adds_context_for_opening_plan tests/test_report_pipeline.py::test_canonicalize_site_tool_input_adds_context_for_alpha_phasing tests/test_report_pipeline.py::test_canonicalize_site_tool_input_does_not_add_site_id_to_create_report tests/test_report_pipeline.py::TestCheckSiteReadinessDirect::test_picks_up_source_docs_from_site_folder_m1 tests/test_prompt_contract.py tests/test_report_schema.py::TestPipelineToolDefinitions tests/test_rhodes.py::test_ddr_doc_type_mapping_covers_inbox_supported_docs -q --basetemp C:\tmp\ddr-opening-plan-tests
uv run ruff check src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\m1_lookup.py src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\report_schema.py tests\test_opening_plan.py tests\test_report_pipeline.py tests\test_prompt_contract.py tests\test_report_schema.py tests\test_rhodes.py
uv run mypy src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\m1_lookup.py src\due_diligence_reporter\report_schema.py
git diff --check
```

Results:

- Focused pytest: 34 passed.
- Ruff: all checks passed.
- Mypy: no issues in 5 source files.
- `git diff --check`: no whitespace errors; expected Windows LF-to-CRLF
  warnings only.

## 2026-06-09 - DDR Dry-Run Promotion Review Surface

- Beads issue `ddr-2aa` tracked this slice and is closed locally.
- Drive Rhodes Reconciliation dry-run aggregate ActionRecords now include
  `review_url` when the workflow run URL is available. This lets the dashboard
  mark that a review surface exists without publishing the raw URL.
- This intentionally does not auto-promote a broad dry run to mutation. The
  verified dry run found 323 document(s) that would be registered, so this
  remains review-gated until there is explicit approval or a narrower site
  filter.
- Commit pushed to `main`: `1b5b885` (`Mark DDR dry-run actions with review
  surface`).
- Safe verification run completed successfully:
  `Drive Rhodes Reconciliation` GitHub Actions run `27206572885`, triggered
  with `dry_run=true` on commit `1b5b885`.
- Downloaded telemetry artifact verified:
  - status `needs_review`
  - scanned 63 site(s)
  - recognized 505 M1 source file(s)
  - found 23 already linked document(s)
  - queued 323 document(s) for possible Rhodes registration
  - dry-run aggregate ActionRecord has `review_required=true` and
    `review_url` ending in `/actions/runs/27206572885`
  - no Drive URL, Drive file ID sample, or local path appeared in the dry-run
    ActionRecord.

Verification:

```powershell
uv run pytest tests/test_drive_rhodes_reconciliation.py::test_reconciliation_dry_run_reports_would_register_without_writing -q --basetemp C:\tmp\ddr-review-surface-focused
uv run pytest tests/test_drive_rhodes_reconciliation.py tests/test_workflow_contracts.py -q --basetemp C:\tmp\ddr-review-surface-tests
uv run ruff check src/due_diligence_reporter/drive_rhodes_reconciliation.py tests/test_drive_rhodes_reconciliation.py
uv run mypy src/due_diligence_reporter/drive_rhodes_reconciliation.py
git diff --check
gh workflow run "Drive Rhodes Reconciliation" --repo GFooteGK1/due-diligence-reporter --ref main -f dry_run=true
gh run watch 27206572885 --repo GFooteGK1/due-diligence-reporter --exit-status
```

## 2026-06-09 - Armonk DDR Vendor-Doc Republish Sweep With Notifications Disabled

- Beads issue `ddr-5ry` tracks this one-off run.
- Greg asked to run the current DDR process against `Alpha Armonk 355 Main St`
  without sending Chat or email messages.
- Ran `scripts/vendor_doc_republish_sweep.py` through a Python wrapper that set
  `GOOGLE_CHAT_WEBHOOK_URL`, `EMAIL_SENDER`, `EMAIL_APP_PASSWORD`,
  `DD_REPORT_EMAIL_RECIPIENTS`, and `GLOBAL_EMAIL_CC` to empty strings before
  importing DDR modules. The settings preflight printed all notification
  booleans as `False`.
- The sweep matched one Rhodes site and three material source events:
  `raycon_scenario`, `school_approval_report`, and `vendor_sir`.
- All three source-triggered runs stopped at `waiting_on_docs` because
  `Building Inspection` is still outstanding. No new DDR/candidate document was
  produced in this run; `report.generate` was skipped with
  `source_triggered_republish_waiting_on_docs`.
- Local manifests:
  - `20260609124315-alpha-armonk-355-main-st-f10619a1`:
    `raycon_scenario`, quality `94/green`
  - `20260609124339-alpha-armonk-355-main-st-6d1185f4`:
    `school_approval_report`, quality `94/green`
  - `20260609124401-alpha-armonk-355-main-st-5e53f1d7`:
    `vendor_sir`, quality `94/green`
- Verification: `uv run ddr status --run-id ...` returned `waiting_on_docs`,
  failed step `(none)`, SIR review `ready_for_review` for all three manifests.
  `Select-String` over the three manifests found no `notify.email`,
  `google_chat`, or `send_dd_report_email` entries.
- `.dd_republish_state.json` now includes the three Armonk fingerprints, so a
  normal scheduled sweep will not replay these same source events unless the
  source modified time/fingerprint changes or an operator runs a force path.

## 2026-06-09 - Armonk DDR Phasing Verification With Notifications Disabled

- Beads issue `ddr-8zq` tracked this verification and is closed locally.
  Follow-up `ddr-dj0` tracks the remaining Armonk-specific input blocker:
  confirmed Phase II deferred scope is missing, so the Alpha Phasing workbook
  cannot be published yet.
- Greg asked to ignore the missing `Building Inspection` gate temporarily and
  rerun the DDR process for `Alpha Armonk 355 Main St` without Chat or email
  messages, to verify the new Alpha Phasing Plan step.
- Notification safeguards used for the live verification:
  `GOOGLE_CHAT_WEBHOOK_URL`, `EMAIL_SENDER`, `EMAIL_APP_PASSWORD`,
  `DD_REPORT_EMAIL_RECIPIENTS`, and `GLOBAL_EMAIL_CC` were blanked before DDR
  imports; Rhodes report-event posting was monkeypatched to record
  `operator_disabled_notifications_for_phasing_verification` instead of
  sending an operator-visible event.
- First phasing verification run:
  `20260609125908-alpha-armonk-355-main-st-b2bdff9f` failed in
  `report.generate` because `_canonicalize_site_tool_input` injected `site_id`
  into `create_dd_report`, whose server function does not accept that argument.
  Fixed by only passing `site_id` to `lookup_rhodes_site_owner` and
  `apply_alpha_phasing_plan_skill`.
- Second run after the canonicalizer fix:
  `20260609130704-alpha-armonk-355-main-st-3e70272a` created a protected DDR
  republish candidate but did not reach the phasing tool because the active
  prompt still said to call it only "if confirmed phasing inputs exist."
- Prompt/process fix: `docs/prompts/prompt_v4.md` now requires calling
  `apply_alpha_phasing_plan_skill` after source reads and before
  `create_dd_report`; the tool returns `verification.open_items` rather than
  publishing a placeholder workbook when phasing inputs are incomplete.
  `docs/process/HOW-IT-WORKS.md` was aligned with that contract.
- Latest live verification run:
  `20260609131456-alpha-armonk-355-main-st-50db28ed` completed with
  status `republish_candidate_created`, failed step `(none)`, quality
  `81/yellow`, and SIR review `ready_for_review`.
- Latest candidate details:
  - Active protected DDR:
    `https://docs.google.com/document/d/1zDGJCXgFNz3Cy6LMLtP0mvBXS9DObHf03C1SZ4ceyyk/edit?usp=drivesdk`
  - Candidate DDR:
    `https://docs.google.com/document/d/1XIQB38AtkJPRSvQb9PuOM5pWcEXPB-FQp06m0bueVqU/edit?usp=drivesdk`
  - Manifest:
    `.ddr-runs/20260609131456-alpha-armonk-355-main-st-50db28ed.json`
- Outcome: the Armonk run now reaches the Alpha Phasing verification path, but
  it did not publish a workbook because confirmed Phase II deferred scope is
  absent. The manifest captured the open question:
  `Confirm Alpha Phasing Plan Phase II deferred scope so the Alpha Phasing Plan workbook can be published.`
- Verification completed:
  `uv run ddr status --run-id 20260609131456-alpha-armonk-355-main-st-50db28ed`
  returned `republish_candidate_created`, failed step `(none)`, quality
  `81/yellow`.

Validation:

```powershell
uv run pytest tests/test_report_pipeline.py::test_canonicalize_site_tool_input_adds_context_for_alpha_phasing tests/test_report_pipeline.py::test_canonicalize_site_tool_input_does_not_add_site_id_to_create_report tests/test_prompt_contract.py tests/test_alpha_phasing_plan.py -q --basetemp C:\tmp\ddr-armonk-phasing-tests
uv run ruff check src\due_diligence_reporter\report_pipeline.py tests\test_report_pipeline.py tests\test_prompt_contract.py tests\test_alpha_phasing_plan.py
git diff --check
```

## 2026-06-08 - Portfolio Document Gap No-Source Follow-Up Actions

- Beads issue `ddr-wn7` tracks this slice.
- Drive Rhodes Reconciliation now emits sanitized
  `source_workflow=portfolio-gaps` ActionRecord rows when a site cannot be
  remediated because DDR found no source document path to register:
  missing site Drive folder URL, missing M1 folder, or no recognized M1 source
  files.
- These rows are `status=needs_review`, owned by DDR, and tell the operator to
  file or repair the source documents/folders before rerunning reconciliation.
- Rhodes ownership is still reserved for actual Rhodes readback failures after
  a registration attempt.
- Public telemetry still omits Drive URLs, Drive file IDs, raw filenames, and
  raw dependency errors.

Verification:

```powershell
uv run pytest tests/test_drive_rhodes_reconciliation.py -q --basetemp C:\tmp\ddr-portfolio-no-source-actions-tests
uv run pytest tests/test_drive_rhodes_reconciliation.py tests/test_workflow_contracts.py -q --basetemp C:\tmp\ddr-portfolio-no-source-actions-contract-tests
uv run ruff check src/due_diligence_reporter/drive_rhodes_reconciliation.py tests/test_drive_rhodes_reconciliation.py
uv run mypy src/due_diligence_reporter/drive_rhodes_reconciliation.py
git diff --check
```

Results:

- Focused reconciliation tests: 6 passed.
- Contract pytest: 16 passed.
- Ruff: all checks passed.
- Mypy: no issues in `drive_rhodes_reconciliation.py`.
- `git diff --check`: no whitespace errors; expected Windows LF-to-CRLF
  warnings only.

## 2026-06-08 - Site-Level Portfolio Document Gap Readback Actions

- Beads issue `ddr-pw9` tracks this slice.
- `drive-rhodes-reconciliation` telemetry still emits the existing aggregate
  DDR ActionRecord rows, and now also emits sanitized site-level
  `source_workflow=portfolio-gaps` ActionRecord rows for current-milestone
  document-gap remediation.
- Site-level rows are keyed by site, Rhodes milestone, and sanitized document
  type, not by Drive file ID, Drive URL, raw filename, or raw dependency error.
- Verified registrations become `status=completed` with
  `workflow_owner=drive-rhodes-reconciliation`; already-associated documents
  become `skipped_already_corrected`; unverified Rhodes readback routes to
  `owning_workflow=rhodes`; dry-run rows remain `queued`; row errors become
  sanitized `error` actions.
- These rows let the dashboard update the original Portfolio Gaps site alert
  after DDR/Drive/Rhodes readback runs, instead of leaving the site table stuck
  at the initial queued action.

Verification:

```powershell
uv run pytest tests/test_drive_rhodes_reconciliation.py tests/test_workflow_contracts.py -q --basetemp C:\tmp\ddr-portfolio-readback-actions-contract-tests-2
uv run ruff check src/due_diligence_reporter/drive_rhodes_reconciliation.py tests/test_drive_rhodes_reconciliation.py
uv run mypy src/due_diligence_reporter/drive_rhodes_reconciliation.py
git diff --check
```

Results:

- Focused contract pytest: 15 passed.
- Ruff: all checks passed.
- Mypy: no issues in `drive_rhodes_reconciliation.py`.
- `git diff --check`: no whitespace errors; expected Windows LF-to-CRLF
  warnings only.

## 2026-06-08 - Drive Rhodes Reconciliation Telemetry Artifact

- Beads issue `ddr-aav` tracks this slice.
- `drive-rhodes-reconciliation.yml` now emits a sanitized dashboard telemetry
  artifact at `reports/telemetry/drive-rhodes-reconciliation-telemetry.json`
  and uploads it as GitHub artifact `drive-rhodes-reconciliation-telemetry`.
- `scripts/drive_rhodes_reconciliation.py` accepts `--telemetry-output`,
  `--run-id`, `--trigger`, and `--workflow-run-url` so scheduled/manual runs
  can publish a stable WorkflowRun v1 artifact.
- `run_drive_rhodes_reconciliation` now records post-registration Rhodes
  readback status:
  - new registrations with readback become `registered_verified`
  - new registrations without readback become `registered_unverified`
  - already-linked files count as verified readback
- The source artifact emits aggregate ActionRecord v1 rows for verified
  registrations, already-corrected registrations, readback-missing rows,
  dry-run would-register rows, and registration errors.
- Public action rows use `source_workflow=ddr` for tab ownership and
  `workflow_owner=drive-rhodes-reconciliation` for the responsible
  subworkflow. This keeps reconciliation action status under the DDR tab while
  preserving the subworkflow owner.
- The public telemetry intentionally omits Drive URLs, Drive file IDs, raw
  filenames, and raw dependency errors. Row-level details are reduced to site
  identity, doc type/milestone, status, sanitized reason, and Rhodes readback
  status.

Verification:

```powershell
uv run pytest tests/test_drive_rhodes_reconciliation.py tests/test_workflow_contracts.py -q --basetemp C:\tmp\ddr-drive-rhodes-telemetry-tests
uv run ruff check src/due_diligence_reporter/drive_rhodes_reconciliation.py scripts/drive_rhodes_reconciliation.py tests/test_drive_rhodes_reconciliation.py tests/test_workflow_contracts.py
uv run mypy src/due_diligence_reporter/drive_rhodes_reconciliation.py
git diff --check
```

Results:

- Focused pytest: 15 passed.
- Ruff: all checks passed.
- Mypy: no issues in `drive_rhodes_reconciliation.py`.
- `git diff --check`: no whitespace errors; expected Windows LF-to-CRLF
  warnings only.

## 2026-06-08 - DDR Document Gap Action State Queue

- Beads issue `ddr-3mc` tracks this slice.
- Portfolio Gaps current-milestone document gap actions now make the DDR route
  explicit instead of stopping at generic review wording:
  - `owning_workflow=ddr`
  - `workflow_owner=drive-rhodes-reconciliation`
  - `status=queued`
  - `retryable=true`
- The source action text points at DDR Drive Rhodes Reconciliation or
  source-document follow-up, then a Portfolio Gaps rerun.
- Completion is still not inferred. The action remains open until later
  Rhodes/Drive readback proves the required documents are associated.
- The AADP remediation enrichment wrapper now preserves the same contract and
  emits a stable `action_id` for document gap rows.
- Process docs now state that Portfolio Gaps emits ActionRecord telemetry for
  document gaps, but closure requires later Rhodes/Drive readback evidence.

Verification:

```powershell
uv run pytest tests/test_aadp_portfolio_gap_remediation_trigger.py tests/test_portfolio_automation_gaps.py tests/test_workflow_contracts.py -q --basetemp C:\tmp\ddr-doc-gap-action-state-tests
uv run ruff check scripts/run_aadp_portfolio_gap_remediation.py src/due_diligence_reporter/portfolio_automation_gaps.py tests/test_aadp_portfolio_gap_remediation_trigger.py tests/test_portfolio_automation_gaps.py
uv run mypy scripts/run_aadp_portfolio_gap_remediation.py src/due_diligence_reporter/portfolio_automation_gaps.py
git diff --check
```

Results:

- Focused pytest: 18 passed.
- Ruff: all checks passed.
- Mypy: no issues in the two source files; repo still prints the known unused
  pyproject override note.
- `git diff --check`: no whitespace errors; expected Windows LF-to-CRLF
  warnings only.
- Dashboard projection smoke in `workflow-telemetry-center` confirmed the new
  row projects as `status=queued` with
  `workflow_owner=drive-rhodes-reconciliation`.

## 2026-06-08 - Portfolio Gaps Evidence Summary Emission

- Beads issue `ddr-h44` tracks this slice.
- Initial `uv run ddr portfolio-gaps --json` source action records already
  emitted sanitized `evidence_summary` values; tests now lock those fields for
  missing P1 DRI, missing Drive folder, current-milestone document gaps, and
  snapshot read errors.
- `scripts/run_aadp_portfolio_gap_remediation.py` now preserves the same
  contract when it replaces source actions during enrichment:
  - AADP unavailable actions explain that AADP remediation/readback has not
    been verified.
  - DDR document-gap actions explain that Rhodes/Drive document association has
    not been verified.
  - Rhodes snapshot-read actions explain that successful Rhodes snapshot
    readback has not been verified.
- Evidence text is sanitized and does not include raw document names, emails,
  private URLs, Rhodes request IDs, or raw dependency payloads.

Verification:

```powershell
$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; uv run pytest tests/test_portfolio_automation_gaps.py tests/test_aadp_portfolio_gap_remediation_trigger.py -q --basetemp C:\tmp\ddr-portfolio-evidence-tests
uv run ruff check scripts/run_aadp_portfolio_gap_remediation.py tests/test_aadp_portfolio_gap_remediation_trigger.py tests/test_portfolio_automation_gaps.py
uv run mypy scripts/run_aadp_portfolio_gap_remediation.py
git diff --check
```

Results:

- Focused Portfolio Gaps / remediation tests: 9 passed.
- Ruff: all checks passed.
- Mypy: no issues in the enrichment wrapper; repo still prints the known unused
  pyproject override note.
- `git diff --check`: no whitespace errors; expected Windows LF-to-CRLF
  warnings only.

## 2026-06-08 - Portfolio Gaps Source Action Routing

- `uv run ddr portfolio-gaps --json` now emits initial source-owned
  `action_record.v1` rows in each gapped site's `remediation_actions` list at
  snapshot creation time.
- The records route each alert to one owning workflow before later remediation
  steps run:
  - `missing_p1_dri` and `missing_drive_folder` -> AADP, status `queued`
  - `missing_current_milestone_documents` -> DDR, status `needs_review`
  - `snapshot_read_errors` -> Rhodes, status `needs_review`
  - `open_automation_failures` -> the single detected source workflow when
    unambiguous, otherwise Portfolio Gaps
  - `pending_review_tasks` -> Rhodes
- The existing AADP/DDR/Rhodes enrichment steps still replace matching
  `remediation_actions` for the same gap/source with completed, blocked, or
  error status after they attempt remediation.
- Source action records intentionally do not include outstanding document names,
  raw Rhodes errors, emails, or private URLs.

Verification:

```powershell
$env:TEMP='C:\tmp'; $env:TMP='C:\tmp'; uv run pytest tests/test_portfolio_automation_gaps.py tests/test_aadp_portfolio_gap_remediation_trigger.py
uv run ruff check src/due_diligence_reporter/portfolio_automation_gaps.py tests/test_portfolio_automation_gaps.py
uv run mypy src/
git diff --check
```

Results:

- Focused Portfolio Gaps/remediation tests: 9 passed.
- Scoped ruff: all checks passed.
- Mypy: no issues in 44 source files.
- `git diff --check`: no whitespace errors; expected Windows LF-to-CRLF
  warnings only.
- Full `uv run pytest tests --ignore=tests/_tmp` was attempted with
  `TEMP/TMP=C:\tmp`: 1100 passed, 14 failed. The failures were pre-existing
  outside this slice: assignment API signature tests, prompt compactness, and
  sender-filter mocks for `build_site_summary`.
- Full `uv run pytest` without ignores also hit pre-existing Windows
  `PermissionError` collection failures in locked `pytest-cache-files-*`
  directories, so those dirs were not deleted.

## 2026-06-08 - Alpha Phasing Plan DDR Integration

Greg approved the default goal for `ddr-411`: integrate Alpha Phasing Plan as
a first-class DDR enrichment, not a first-round DDR blocker and not a final
vendor-readiness gate until explicitly changed.

Changed:

- Added `alpha_phasing_plan_report` classification, M1 recognition, source-event
  mapping, vendor-doc sweep inclusion, and open-question inference/closure.
- Added `apply_alpha_phasing_plan_skill`, which validates minimum phasing
  inputs, refuses to invent generic Phase II scope, generates the required
  six-tab XLSX workbook, uploads it to the site's M1 folder, and returns
  `sources.alpha_phasing_plan_link` plus compact `exec.alpha_phasing_*` fields.
- Added schema tokens and Google Doc rendering for a compact "Alpha Phasing
  Plan" subsection after Buildout Analysis and before Detailed Cost Breakdown.
  The full detail remains in the workbook; Referenced Reports now has a
  distinct Alpha Phasing Plan row.
- Added Rhodes registration for the generated workbook. Greg approved logging
  DDR support docs as `other` when no specific LocationOS document type exists,
  so `alpha_phasing_plan_report` maps to Rhodes `docType=other` /
  `milestone=acquireProperty`; registration is idempotent by Drive file ID and
  remains non-blocking if Rhodes is unavailable or no `site_id` is known.
- Updated `docs/process/HOW-IT-WORKS.md`, `docs/prompts/prompt_v4.md`, and
  `docs/templates/Site_DD_Report_Template_V4.md` with the process placement,
  source handling, token contract, Rhodes support-doc behavior, and
  missing-input behavior.
- Beads closed: `ddr-411.2`, `ddr-411.3`, `ddr-411.4`, `ddr-411.5`,
  `ddr-411.6`, `ddr-411.7`, `ddr-411.8`, and epic `ddr-411`.

Open:

- No open Alpha Phasing Plan beads remain. Broader auto-registration for other
  generated DDR support documents can be tracked separately if the team wants
  `save_skill_report` outputs registered in Rhodes too.

Verification:

```powershell
uv run pytest tests/test_alpha_phasing_plan.py tests/test_classifier_keywords.py tests/test_open_questions.py tests/test_vendor_doc_sweep.py tests/test_report_schema.py tests/test_google_doc_builder.py tests/test_report_pipeline.py tests/test_rhodes.py tests/test_drive_rhodes_reconciliation.py -q --basetemp C:\tmp\ddr-alpha-rhodes-tests
uv run ruff check src/due_diligence_reporter/alpha_phasing_plan.py src/due_diligence_reporter/classifier.py src/due_diligence_reporter/open_questions.py src/due_diligence_reporter/vendor_doc_sweep.py src/due_diligence_reporter/report_schema.py src/due_diligence_reporter/google_doc_builder.py src/due_diligence_reporter/report_pipeline.py src/due_diligence_reporter/server.py src/due_diligence_reporter/m1_lookup.py src/due_diligence_reporter/rhodes.py tests/test_alpha_phasing_plan.py tests/test_classifier_keywords.py tests/test_open_questions.py tests/test_vendor_doc_sweep.py tests/test_report_schema.py tests/test_google_doc_builder.py tests/test_report_pipeline.py tests/test_rhodes.py tests/test_drive_rhodes_reconciliation.py
uv run mypy src/due_diligence_reporter/alpha_phasing_plan.py src/due_diligence_reporter/classifier.py src/due_diligence_reporter/open_questions.py src/due_diligence_reporter/vendor_doc_sweep.py src/due_diligence_reporter/report_schema.py src/due_diligence_reporter/google_doc_builder.py src/due_diligence_reporter/report_pipeline.py src/due_diligence_reporter/server.py src/due_diligence_reporter/m1_lookup.py src/due_diligence_reporter/rhodes.py
git diff --check
```

Results:

- Focused pytest: 310 passed.
- Scoped ruff: all checks passed.
- Scoped mypy: no issues in 10 source files.
- `git diff --check`: no whitespace errors; expected Windows LF-to-CRLF
  warnings only.

## 2026-06-08 - Portfolio Snapshot Read Error Action Telemetry

- Portfolio Gaps snapshot read errors were still reaching the dashboard as
  `Awaiting action telemetry` because the enrichment wrapper only emitted
  actions for AADP-owned P1 DRI / Drive-folder gaps and DDR-owned document
  gaps.
- `scripts/run_aadp_portfolio_gap_remediation.py` now emits Rhodes-owned
  `action_record.v1` rows for `snapshot_read_errors`:
  - `source_workflow=portfolio-gaps`
  - `owning_workflow=rhodes`
  - `workflow_owner=rhodes`
  - status `needs_review`
  - retryable `true`
- Public action text is sanitized. Raw per-site read errors remain in the
  source snapshot for troubleshooting, but the public action record only says
  Portfolio Gaps could not read Rhodes snapshot sections and needs the Rhodes
  read path restored plus a rerun.
- Commit pushed to `main` as `b04d8b1`
  (`Route portfolio snapshot read errors to Rhodes`).

Verification:

```powershell
uv run pytest tests/test_aadp_portfolio_gap_remediation_trigger.py tests/test_workflow_contracts.py -q --basetemp C:\tmp\ddr-snapshot-read-actions-tests-2
uv run ruff check scripts/run_aadp_portfolio_gap_remediation.py tests/test_aadp_portfolio_gap_remediation_trigger.py tests/test_workflow_contracts.py
uv run mypy scripts/run_aadp_portfolio_gap_remediation.py
git diff --check
```

Results:

- Focused Portfolio Gaps / workflow contract tests: 15 passed.
- Scoped ruff: all checks passed.
- Scoped mypy: no issues in the enrichment wrapper; repo still prints the
  known unused pyproject override note.
- `git diff --check`: no whitespace errors; expected Windows LF-to-CRLF
  warnings only.
- Broader `uv run pytest` is not currently clean outside this slice:
  collection hits stale temp cache directories with Windows permission errors;
  `uv run pytest tests --ignore=tests/_tmp` then reports 13 unrelated existing
  failures in assignment and sender-filter tests.
- Broader `uv run ruff check .` is not currently clean outside this slice due
  to unrelated existing lint in `scripts/reprocess_mislabeled.py`,
  `tests/test_cds_verification.py`, `tests/test_opening_plan.py`, and
  `tests/test_sender_filter.py`.

## 2026-06-08 - Portfolio Gaps Uses Real AADP Firestore Database

- The `Portfolio Automation Gaps` workflow now passes
  `PIPELINE_STATUS_FIRESTORE_DATABASE=edu-ops-email-router` to the AADP
  remediation runner.
- Evidence: project `ap-automation-464623` has Firestore database
  `edu-ops-email-router`; reads against `(default)` returned 404, so the old
  env value could let AADP remediation run while source WorkflowRun persistence
  silently fell back to memory.
- `tests/test_workflow_contracts.py` now locks the corrected database value in
  the Portfolio Gaps remediation workflow contract.

Verification pending in this handoff entry until tests are rerun after the
database-name patch.

## 2026-06-08 - Portfolio Gaps Passes AADP Telemetry Env

- The `Portfolio Automation Gaps` workflow now passes AADP pipeline status
  Firestore settings into the `Trigger AADP remediation for correctable gaps`
  step:
  - `PIPELINE_STATUS_STORE=firestore`
  - `PIPELINE_STATUS_FIRESTORE_PROJECT_ID=ap-automation-464623`
  - `PIPELINE_STATUS_FIRESTORE_DATABASE=edu-ops-email-router`
  - `PIPELINE_STATUS_FIRESTORE_COLLECTION=alphaAnalysisPipelineStatus`
  - `GCP_FIRESTORE_SERVICE_ACCOUNT_JSON`
- The workflow emits a GitHub Actions warning if
  `GCP_FIRESTORE_SERVICE_ACCOUNT_JSON` is not configured, because AADP
  remediation would still run but source WorkflowRun facts would not persist to
  the dashboard-readable Firestore store.
- Verified current repo secrets include `GCP_FIRESTORE_SERVICE_ACCOUNT_JSON`,
  so the scheduled workflow should have the credential once this change is on
  `main`.
- `tests/test_workflow_contracts.py` now locks the AADP telemetry env contract
  for the Portfolio Gaps remediation step.

Verification:

```powershell
uv run pytest tests/test_workflow_contracts.py -q --basetemp C:\tmp\ddr-aadp-telemetry-contract
uv run ruff check tests\test_workflow_contracts.py
git diff --check
gh secret list -R GFooteGK1/due-diligence-reporter
```

Results:

- Workflow contract tests: 8 passed.
- Ruff: all checks passed.
- `git diff --check`: passed with expected Windows LF-to-CRLF notices only.
- GitHub secret list confirmed `GCP_FIRESTORE_SERVICE_ACCOUNT_JSON` exists.

## 2026-06-08 - Portfolio Document Gap Action Telemetry

Portfolio Gaps already routes missing P1 DRI and Drive-folder alerts to AADP,
but missing current-milestone document alerts were still falling back to the
dashboard's `Not routed yet` state. The enrichment wrapper now emits a
DDR-owned ActionRecord v1 row for those document gaps, so operators can see the
owning workflow, status, action taken, and as-of time.

Changed:

- `scripts/run_aadp_portfolio_gap_remediation.py` now appends DDR-owned
  `needs_review` actions for `missing_current_milestone_documents` after AADP
  remediation enrichment runs.
- The action text is sanitized and does not enumerate missing document names in
  the public action row; it states that DDR flagged current-milestone source
  document follow-up and no document readback has been verified yet.
- Existing AADP P1 DRI / Drive-folder remediation actions are preserved because
  action replacement is now scoped by both gap type and source workflow.
- `tests/test_aadp_portfolio_gap_remediation_trigger.py` covers DDR document
  gap action emission and preservation of existing AADP actions.

Verification:

```powershell
uv run pytest tests/test_aadp_portfolio_gap_remediation_trigger.py tests/test_workflow_contracts.py -q --basetemp C:\tmp\ddr-doc-gap-actions-tests-2
uv run ruff check scripts/run_aadp_portfolio_gap_remediation.py tests/test_aadp_portfolio_gap_remediation_trigger.py tests/test_workflow_contracts.py
uv run mypy scripts/run_aadp_portfolio_gap_remediation.py
git diff --check
```

Results:

- Focused pytest: 13 passed.
- Ruff: all checks passed.
- Mypy: no issues in the enrichment wrapper; the repo still prints the known
  unused pyproject override note.
- `git diff --check`: passed with expected Windows LF-to-CRLF notices only.

## 2026-06-08 - DDR ActionRecord v1 Manifest Emission

Greg wants the dashboard to show not only workflow health, but the actual alert,
owning workflow, action taken, status, and as-of time for operator-visible
issues. DDR run manifests now emit sanitized `action_records` alongside the
existing step and open-question summary.

Changed:

- `src/due_diligence_reporter/pipeline_contracts.py` now includes
  `action_records` in `PipelineRun.to_dict()`.
- Failed or blocked DDR steps emit an ActionRecord v1 row with the DDR run ID,
  step, site, status, operator action, readback evidence, and retryability.
- Open DDR verification items emit sanitized `needs_review` ActionRecord rows
  without duplicating the open-question display text into the dashboard action
  surface.
- `tests/test_pipeline_contracts.py` covers failed-step and open-question
  ActionRecord serialization.

Verification:

```powershell
uv run pytest tests/test_report_pipeline.py tests/test_pipeline_contracts.py -q --basetemp C:\tmp\ddr-action-records-report-pipeline-clean
uv run ruff check src/due_diligence_reporter/pipeline_contracts.py tests/test_pipeline_contracts.py
uv run mypy src/due_diligence_reporter/pipeline_contracts.py
```

Results:

- Focused pytest: 62 passed.
- Ruff: all checks passed.
- Mypy: no issues in `pipeline_contracts.py`; the repo still prints the known
  unused pyproject override note.

## 2026-06-05 - Portfolio Gaps AADP Remediation Trigger

Greg wanted Portfolio Gaps alerts to show what action agents took instead of
only notifying humans. Missing P1 DRI and missing Drive-folder gaps should be
handed back to AADP so they can be corrected shortly after discovery, while the
dashboard shows per-alert action status.

Changed:

- `.github/workflows/portfolio-automation-gaps.yml` now checks out
  `trilogy-group/alpha-analysis-downstream-processing` into `aadp-remediation`
  after building `portfolio-automation-gaps.json`, then runs the new wrapper
  before posting the Chat summary or uploading artifacts.
- Added `scripts/run_aadp_portfolio_gap_remediation.py`. It imports AADP's
  remediation runner from the checked-out repo, enriches the snapshot in place,
  and falls back to explicit `blocked`/`error` action records when AADP is
  unavailable instead of leaving the dashboard with no action telemetry.
- Added workflow-dispatch input `trigger_remediation`, support for
  `AADP_DRIVE_PARENT_FOLDER_ID`, and optional `AADP_REMEDIATION_REPO_TOKEN` for
  private cross-repo checkout.
- Added regression tests covering unavailable-runner fallback, checked-out AADP
  runner import, CLI overwrite behavior, and workflow contract guardrails.

Verification:

```powershell
uv run pytest tests/test_aadp_portfolio_gap_remediation_trigger.py tests/test_workflow_contracts.py -q --basetemp C:\tmp\ddr-aadp-remediation-broad
uv run ruff check scripts\run_aadp_portfolio_gap_remediation.py tests\test_aadp_portfolio_gap_remediation_trigger.py tests\test_workflow_contracts.py
uv run mypy scripts\run_aadp_portfolio_gap_remediation.py
git diff --check
```

Results:

- Focused pytest: 11 passed.
- Ruff: all checks passed.
- Mypy: no issues in the wrapper script; the repo still prints the known unused
  pyproject override note.
- `git diff --check`: passed with expected Windows LF-to-CRLF warnings only.

Next:

- Commit/push this repo together with the AADP remediation runner before
  expecting scheduled runs to emit real action status.
- Configure `AADP_DRIVE_PARENT_FOLDER_ID` as a repo variable or secret. Do not
  omit it for Drive-folder creation, because AADP can otherwise fall back to an
  unintended Drive parent.
- Configure `AADP_REMEDIATION_REPO_TOKEN` if the default `github.token` cannot
  read `trilogy-group/alpha-analysis-downstream-processing`.

## 2026-06-05 - Clean DDR Republish Notifications And Revision-Safe Updates

Greg asked for the DDR process to be easier to understand: republish only when
a vendor/source doc changes, say which doc caused the republish, show which
vendor docs are still outstanding, keep current first/final email rules, and
avoid overwriting human edits in the DDR Google Doc.

Changed:

- `src/due_diligence_reporter/dd_republish.py` now treats each
  `(site_id, source_type, fingerprint)` as a one-time scheduled republish
  trigger. The old 12-hour same-fingerprint replay is gone; `force=True`
  remains the explicit operator recovery path.
- Protected DDR candidate creation now counts as a dedupe success, so the
  hourly vendor-doc sweep does not keep creating new candidate docs for the
  same source fingerprint.
- `src/due_diligence_reporter/server.py` now records the last automation-owned
  Google Docs `revisionId` in Drive `appProperties`. Existing active DDRs are
  rebuilt in place only when the current Docs revision still matches that
  watermark. Missing/mismatched revisions create a candidate DDR instead of
  clearing the active DDR.
- `src/due_diligence_reporter/automation_event.py` and
  `src/due_diligence_reporter/report_pipeline.py` now include the trigger
  source and outstanding vendor-doc list in DDR republish notes. Source-triggered
  update events bypass the open-ask frequency cap so the actual vendor-doc
  republish is visible.
- Candidate DDR events are recorded as decision-required Rhodes notes with the
  active DDR URL, candidate DDR URL, trigger source, outstanding vendor docs,
  and guard reason.
- Workflow comments in `.github/workflows/inbox-scan.yml` and
  `.github/workflows/raycon-followup.yml` now describe one-time fingerprint
  dedupe instead of the old 12-hour replay window.

Verification:

```powershell
uv run pytest tests/test_report_pipeline.py tests/test_automation_event.py tests/test_vendor_gate.py tests/test_dd_output_fixes.py tests/test_dd_republish.py tests/test_vendor_doc_sweep.py tests/test_raycon_followup.py::TestDDReportRepublish tests/test_workflow_contracts.py --basetemp C:\tmp\ddr-republish-final-slice -q
uv run ruff check src\due_diligence_reporter\google_client.py src\due_diligence_reporter\server.py src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\dd_republish.py src\due_diligence_reporter\vendor_doc_sweep.py tests\test_dd_output_fixes.py tests\test_report_pipeline.py tests\test_vendor_gate.py tests\test_dd_republish.py tests\test_vendor_doc_sweep.py tests\test_raycon_followup.py
uv run mypy src\due_diligence_reporter\google_client.py src\due_diligence_reporter\server.py src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\dd_republish.py src\due_diligence_reporter\vendor_doc_sweep.py
git diff --check
```

Results:

- Touched test slice: 201 passed.
- Ruff: all checks passed.
- Mypy: no issues in 6 source files.
- `git diff --check`: passed; Git printed LF-to-CRLF working-copy warnings for
  touched files.

## 2026-06-05 - RayCon DDR Source-Of-Truth Review And Patch

Greg asked for a team deep dive on the two RayCon repos and the safest way to
keep RayCon aligned with DDR.

Conclusion:

- DDR's live integration is the current `RayCon` repo, deployed as Cloud Run
  service `raycon-api` in project `brandon-gee`, not `RayCon-v2-service`.
- Live `/version` on `raycon-api` returned commit
  `50f9b74c1452e61739f5b6d59e070a9f2eaeb00e`; the local checkout is
  `C:\tmp\raycon-direct-RayCon`.
- `RayCon-v2-service` is separately deployed as `raycon-v2-api` and does not
  expose the DDR `/v1/jobs` contract.
- The key contract bug was RayCon treating a completed calculation as callback
  success even when `raycon_scenario.json` failed to persist to M1.
- DDR follow-up already refuses to publish if the M1 JSON is missing; the needed
  code fix is RayCon-side status/callback correctness.

Changed in `C:\tmp\raycon-direct-RayCon` on branch
`codex/raycon-ddr-contract`:

- `api/src/index.js` now reports expected scenario-file persistence failures as
  terminal `failed` for sync responses and async worker state, and duplicate
  async dispatches can requeue failed persistence states.
- `api/src/rayconJobCallback.js` now refuses `succeeded`/`partial` callback
  status when the expected `raycon_scenario.json` was not written.
- `api/src/jobStateStore.js` exposes `drive_error` in public status metadata.
- `api/src/openApiSpec.js` now marks `raycon_run_id` as required in the accepted
  response schema.
- Added route/callback/OpenAPI regression tests covering sync callback, async
  worker/status polling, and the accepted-response schema.

Verification:

```powershell
cd C:\tmp\raycon-direct-RayCon\api
$env:NODE_ENV='test'; npm.cmd exec vitest run src/rayconJobCallback.test.js src/jobsRoute.test.js src/openApiSpec.test.js
$env:NODE_ENV='test'; npm.cmd exec vitest run src/jobsRoute.test.js src/rayconJobCallback.test.js src/rayconJobs.test.js src/openApiSpec.test.js

cd C:\Users\foote\.claude\Work\repos\due-diligence-reporter
uv run pytest tests/test_raycon_client.py tests/test_raycon_followup.py
```

Results:

- Focused RayCon tests: 3 files passed, 65 tests passed.
- Broader RayCon API slice: 4 files passed, 114 tests passed.
- DDR RayCon tests: 119 passed.
- `git -C C:\tmp\raycon-direct-RayCon diff --check` passed; Git printed
  LF-to-CRLF working-copy warnings for touched files.

Remaining deploy/config work:

- Published `RayCon` PR #1 and deployed merged commit
  `fe23b69c08a6464946df6998c759aa7d77fa4af0` to Cloud Run `raycon-api`.
- Live verification after deploy:
  - `raycon-api-00186-jw4` served image `gcr.io/brandon-gee/raycon-api:fe23b69`.
  - `/version` returned git commit `fe23b69c08a6464946df6998c759aa7d77fa4af0`.
  - Callback wiring was tested on revisions `raycon-api-00187-7g5` and
    `raycon-api-00188-2lq`; both existing candidate token secrets
    (`github-token`, then `github-pat`) produced GitHub workflow dispatch HTTP
    403.
  - Callback env/secrets were removed again in `raycon-api-00189-8ps` so live
    RayCon does not emit repeated failed callback attempts.
- Created Secret Manager entry `raycon-job-callback-secret` for callback HMAC
  signing, but callback activation still needs a valid GitHub token with
  permission to dispatch `GFooteGK1/due-diligence-reporter` workflows.
- DDR workflow commit `ce280c1` allows `validation_failed` as a
  `raycon-followup.yml` dispatch status; this was required before enabling
  callback because RayCon can emit that status.
- Enable inbound HMAC/API-key enforcement when ready; code support exists, but
  the current deploy config leaves RayCon public.
- Treat `/v1/chat` convergence with the deterministic job engine as a later
  Phase 4 product task, not a DDR blocker.

## 2026-06-04 - DDR First/Final Email Gate

Greg asked to stop emailing every interim DDR update. The desired behavior is:
email the first DDR, suppress interim source-triggered republishes, and email
again only when the final vendor-reviewed DDR is ready.

Changed:

- `src/due_diligence_reporter/report_pipeline.py` now gates `notify.email`
  before calling `_email_pipeline_report`.
- Initial successful DDR publishes still email, even when the report has open
  verification items.
- Source-triggered updates now skip email when the full vendor input set is not
  present or when open verification items remain.
- Source-triggered updates email again only when the full vendor input set is
  present and the regenerated report has no open verification items.
- Skipped interim emails are recorded as `notify.email` skipped steps with a
  reason in the run manifest.
- `docs/process/HOW-IT-WORKS.md` now documents the first/final-only email gate.

Verification:

```powershell
uv run pytest tests/test_report_pipeline.py -q --basetemp C:\tmp\pytest-ddr-email-gate
uv run ruff check src\due_diligence_reporter\report_pipeline.py tests\test_report_pipeline.py
uv run mypy src\due_diligence_reporter\report_pipeline.py
git diff --check
```

Results:

- Report pipeline tests: 54 passed.
- Ruff: all checks passed.
- Mypy: no issues in 1 source file.
- `git diff --check`: passed; Git printed existing LF-to-CRLF working-copy
  warnings for touched files.

## 2026-06-01 - Route Manual DDR Publishes to M1

Greg reported a manual DDR run published the DD Report to My Drive instead of
the site's M1 folder.

Root cause:

- `create_dd_report` parsed the supplied site Drive folder URL and created the
  DDR in that raw site root, while other skill report paths already resolved
  `M1 - Acquire Property`.
- `GoogleClient.create_document()` created a Google Doc via the Docs API and
  then tried to move it by removing `"root"` instead of reading/removing the
  document's actual My Drive parent ID.

Changed:

- `create_dd_report` now resolves/creates `M1 - Acquire Property` with
  `allow_legacy_fallback=False` and creates new DDR docs in that M1 folder.
- Existing DDR lookup checks M1 first. If a legacy same-site DDR is found in
  the site root, it is moved into M1 and rebuilt in place instead of leaving a
  duplicate root-level report.
- Added `GoogleClient.move_file_to_folder()`, which reads actual current parent
  IDs before calling Drive `files.update(addParents=..., removeParents=...)`.
- `create_document()` now uses that move helper, so new Docs do not depend on
  the `"root"` alias and should not remain in My Drive after creation.
- The `create_dd_report` response now includes `document.folder_id` and
  `document.folder_url` for the resolved target folder.
- `create_dd_report` and `list_drive_documents` reject `/folders/root` instead
  of letting a stale/root folder URL create or read reports from My Drive.
- Existing report replacement data now prefers a real tool-supplied Drive folder
  URL over a blank or root URL persisted in earlier report metadata.
- `find_existing_dd_report` now searches the site's M1 folder before checking
  the legacy site root, so vendor-doc republish updates the active DDR instead
  of missing it.
- `vendor_doc_sweep` dry-run collection now passes `read_only=True` into the
  provenance classifier, so dry runs do not mutate provenance state.

Live artifact follow-up:

- Drive search for DD Reports modified on 2026-06-01 found:
  - `Alpha Miami Beach DD Report - 06/01/2026`
    (`1Ym8ZIzuUuSheIX8MnlRccf8F4rqUJqGn6aAXanlDNSc`) already in untrashed
    `M1 - Acquire Property` (`1DuceE9iu0y45G6wncl4cRZyTkgP7IiYL`).
  - Older duplicate `Alpha Miami Beach DD Report - 06/01/2026`
    (`18P6t2agXzgz9_MxItX-ESxgO3AqsgFQLBoOeCmSgrj4`) under a trashed M1 folder
    (`1HYk3KndRA_VLcklX7V5T09xh29JEEXJ5`).
  - `Alpha Palo Alto 4260 El Camino Real DD Report - 06/01/2026`
    (`1HPIVhrcc5mnJdq8RoEBOn5YF6TFo-LAXyhGQWSKgCjU`) in the site root
    `Alpha Palo Alto 4260 El Camino Real` (`12T6gDf43NZtAPZ1yRybYteD53eKLUjfT`).
- Resolved Palo Alto's correct M1 folder through Rhodes:
  `1Fpo4IlSChNLTnpL74TBgasBtYp7snHYL`.
- Attempted `driveMoveFile` for the Palo Alto DDR from site root to M1, but
  LocationOS returned `Permission denied for this Google Drive operation`
  (`Request ID: a174908e-f1f0-4f90-98cf-f9907cb41296`).
- Manual/permissioned move still needed for that live Palo Alto document unless
  the Drive write permission is fixed.

Miami Beach follow-up for `Alpha Miami Beach 300 71st St`:

- Rhodes site `k972ay4w964539mq0naqyde5ws85fr3r` had no Drive folder linked and
  no registered documents when the manual DDR issue was reviewed.
- Linked the real site Drive folder
  `Alpha Miami Beach 300 71st St` (`1qjyrtHSFkPOQjTHPo8VSORCGh9h7KqOt`) back to
  Rhodes. M1 now resolves to `1DuceE9iu0y45G6wncl4cRZyTkgP7IiYL`.
- Registered the vendor SIR
  `Alpha School - Miami Beach, FL (300 71st Street) SIR 5.1.2026.pdf`
  (`1wUn5FAWlT_mq9ghh17kBj4_HJW-LBo2s`) as `siteInvestigationReport`.
- Republished the corrected DDR:
  `Alpha Miami Beach 300 71st St DD Report - 06/01/2026`
  (`1QXQcCqO3NPHY8sG6DmcbTk9Y_xyLw3G7UQ2yQ7YYcbQ`) in M1.
- Renamed the stale short-name report to
  `Superseded - Alpha Miami Beach DD Report - 06/01/2026 (missing vendor docs)`
  (`1Ym8ZIzuUuSheIX8MnlRccf8F4rqUJqGn6aAXanlDNSc`) and left it in M1 for audit
  trail continuity.
- Reviewed the apparent Building Inspection file. Its filename referenced
  `300 71st`, but extraction showed the report contents describe
  `1021 Biarritz Drive`, so it is not valid property-condition evidence for
  this site.
- Copied that mismatched PDF into M1 only as a review artifact, renamed it to
  `Needs review - 1021 Biarritz content - not 300 71st.pdf`, and updated the
  Rhodes document record to `docType=other` with notes that a site-specific
  building inspection remains required.
- Current Rhodes missing-document readback shows the vendor SIR present and
  `propertyConditionAssessment` still missing, which matches the corrected DDR.
- Added a Rhodes site note documenting the recovery, corrected DDR, valid SIR,
  and invalid/mislabeled Building Inspection candidate.

Additional Miami Beach Block Plan correction:

- Greg identified Drive file `10dPoeXlUcuYwvEGflf0r9zo4RQMCfErM` as the site
  Block Plan. It was already in M1 but was named
  `2026.05.19_AlphaMiami_ProgressSet.pdf`, which did not reliably expose it as
  a Block Plan to deterministic automation.
- Renamed it in Drive to
  `2026.05.19 - Alpha Miami Beach 300 71st St Block Plan.pdf`.
- Registered it in Rhodes as `floorPlan` for the `acquireProperty` milestone
  using the Drive link. The LocationOS direct `driveFileId` registration path
  hit the same noninteractive elicitation schema bug seen earlier, so the
  external URL path was used.
- Re-ran `list_drive_documents` and verified the DDR scanner now returns the
  file as `doc_type=block_plan`.
- Rebuilt the corrected DDR in place:
  `1QXQcCqO3NPHY8sG6DmcbTk9Y_xyLw3G7UQ2yQ7YYcbQ`.
- Readback from the DDR shows `View Block Plan`, a partial banner noting
  `Block Plan submitted 2026-06-01 19:04 UTC`, and Block Plan scenario notes:
  Scenario 1 / Alpha Standard = 114 students; Scenario 2 / Code = 199 students.
- The refreshed DDR adds a new open question that the Block Plan references
  3rd floor while the E-Occupancy report references 4th floor; this discrepancy
  must be resolved before permit submittal.
- Ran the RayCon follow-up safety net for this site. It dispatched a RayCon job
  for block plan file `10dPoeXlUcuYwvEGflf0r9zo4RQMCfErM` with job ID
  `c9cb1c0309ebc26abb1f1dc5a73f42ff`, status `queued`. Do not paste the
  generated RayCon status URL into handoffs or notes because it contains an
  access token.
- Added a Rhodes site note documenting the Block Plan correction and RayCon
  dispatch.

Validation:

```powershell
uv run pytest --basetemp C:\tmp\ddr-m1-publish tests/test_dd_output_fixes.py::TestGoogleClientDocumentCreation::test_create_document_removes_actual_parent_when_moving_to_target_folder tests/test_dd_output_fixes.py::TestAsyncOffloading::test_create_dd_report_uses_to_thread tests/test_dd_output_fixes.py::TestAsyncOffloading::test_create_dd_report_rebuilds_existing_same_day_doc tests/test_dd_output_fixes.py::TestAsyncOffloading::test_create_dd_report_moves_legacy_root_report_to_m1 -q
uv run pytest --basetemp C:\tmp\ddr-m1-publish-broad tests/test_dd_output_fixes.py tests/test_m1_lookup.py -q
uv run pytest --basetemp C:\tmp\ddr-m1-report-pipeline tests/test_report_pipeline.py -q
uv run ruff check src\due_diligence_reporter\google_client.py src\due_diligence_reporter\server.py tests\test_dd_output_fixes.py
uv run mypy src\due_diligence_reporter\google_client.py src\due_diligence_reporter\server.py
git diff --check
```

Results:

- Focused M1/Drive publish tests: 4 passed.
- Broader affected output/M1 tests: 58 passed.
- Report pipeline suite: 49 passed.
- Ruff and focused mypy passed.
- `git diff --check` passed with expected Windows LF-to-CRLF warnings only.
- Beads state could not be updated because `bd` is not available on PATH in
  this shell, and `uv run bd` also failed with `program not found`.

## 2026-06-01 - RayCon Follow-up Action Failure Recheck

Greg flagged that the RayCon Follow-up GitHub Action has been failing a lot.

Live checks:

- `gh run list --workflow raycon-followup.yml --limit 100` showed 37 failures
  and 63 successes in the latest 100 runs.
- Current consecutive failure streak: 28 runs.
- First failure in the current streak:
  - run `26581936500`
  - `2026-05-28T14:44:13Z`
  - SHA `c71de5e672ea70ce71d6fa06c29d2e90ba0e28eb`
  - event `workflow_dispatch`
- Previous success:
  - run `26581095061`
  - `2026-05-28T14:29:44Z`
  - SHA `e7227be6269a84842c418ec961e5b8d815f3afcc`
  - event `schedule`
- Latest sampled run `26768030545` on current `main`
  (`25ec37b2b400d8726a56530adfbcfdcc47b93ac8`) failed after processing
  all sites with `published=0 dispatched=4 alerts=16 errors=0 total_sites=44`.

Conclusion:

- The recurring failure signature is still the Rhodes MCP note-write blocker,
  not a runner/setup/secret failure.
- `addNote` returns `status: "rejected"`,
  `rejectionReason: "elicitation_unsupported"`, and no note ID for the
  site-id and slug retry paths.
- Google Chat fallback posts, but owner notification is not considered
  delivered because `add_rhodes_site_note` now correctly requires a concrete
  Rhodes note ID.
- Commit `c71de5e` (`Require verified Rhodes note IDs`) is where the workflow
  started failing closed instead of falsely passing when no note was actually
  created.

Next:

- Fix the Rhodes/LocationOS write surface so API-key/noninteractive automation
  can create a site note with mentions and return the note ID.
- After that, rerun RayCon Follow-up on current `main` and verify the log shows
  `status: "created"`, `owner_notification: "mentioned"`, and a non-empty
  `rhodes_note_id`.
- Do not "fix" this by making Chat fallback count as owner delivery unless Greg
  explicitly decides that GitHub Action green status is more important than
  verified Rhodes owner notification.

Rhodes write-fix source check:

- The DDR repo only contains the API-key JSON-RPC client and RayCon
  fail-closed guardrails. It does not contain the deployed
  `location-os-mcp.ephor.workers.dev` write-surface implementation.
- Local/GitHub-visible searches did not find the LocationOS MCP worker source.
- Cloudflare API inspection was attempted from this session, but the configured
  Cloudflare API token returned `10000: Authentication error`.
- Durable fix remains Rhodes-side: trusted automation/API-key note writes need
  a noninteractive path that creates the note, honors `mentions`, returns the
  note ID, and records audit/source context.
- DDR-side fallback is possible but less desirable: prior Rhodes write work
  succeeded only with a direct OAuth-backed MCP client plus an elicitation
  approval callback, which would require explicit OAuth configuration in the
  GitHub Action path.

## 2026-05-29 - Daily DDR Completed-Report Notification Batch

Context:

- Greg asked to reduce notification volume during the DDR scan when sites
  already have completed report bundles.
- The scheduled daily DDR scan was posting `post_pipeline_result(...)` for
  every `report_exists` result, creating one Google Chat message per already
  completed site.

Actions completed:

- Added `post_completed_report_bundle_summary(...)` in
  `src/due_diligence_reporter/report_pipeline.py`.
- Updated `scripts/daily_dd_check.py` to defer `report_exists` notifications
  during the per-site loop and send one end-of-run summary listing all sites
  that already have completed DD Reports.
- Kept per-site notifications unchanged for waiting, created, incomplete,
  failed, and error statuses.
- Added regression coverage in `tests/test_report_pipeline.py` and
  `tests/test_daily_dd_check.py`.

Verification:

- `uv run pytest tests/test_daily_dd_check.py tests/test_report_pipeline.py -q --basetemp C:\tmp\pytest-ddr-notifications`
  -> `47 passed`
- `uv run ruff check scripts/daily_dd_check.py src\due_diligence_reporter\report_pipeline.py tests\test_daily_dd_check.py tests\test_report_pipeline.py`
  -> `All checks passed`

Notes:

- Beads tracking could not be updated in this shell because `bd` was not found
  on PATH or in the checked local locations.

## 2026-05-29 - Action-First DDR Report Event Notes

Started after Greg flagged DD report automation notes as noisy/repetitive and
not clear enough about what the user needs to do next.

Branch: `codex/ddr-clear-action-event-notes`

Current state: branch pushed and draft PR open:
https://github.com/GFooteGK1/due-diligence-reporter/pull/145

Changed:

- Kept the `AutomationEvent v1` contract intact, but added a custom renderer
  for `dd_report_created` and `dd_report_updated` events.
- DD report notes now put operator action first:
  - `Action needed`
  - site
  - open ask count
  - DD report link
  - latest source reviewed
  - how to close the asks
  - up to five `Ask N` lines
  - resolved items from the latest update
- The close instruction says asks come from DD report Open Items to Verify,
  answers/evidence must be moved into the right report section or Rhodes/source
  record, and answers left under an ask still count as open.
- System/debug metadata now appears below a `System details` separator.
- Long open-item lists show the first five asks and an `Additional asks` count
  pointing the user back to the DD report.
- Decision-required report-created/report-updated notifications with open asks
  are capped at once per site every two business days. Capped runs record
  `rhodes.report_event` as `skipped` with `reason=frequency_cap` in the run
  manifest and do not send a Rhodes or Google Chat notification.
- Updated process docs and regression coverage.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-clear-action-events tests/test_automation_event.py tests/test_report_pipeline.py::test_dd_report_event_frequency_cap_blocks_two_business_days tests/test_report_pipeline.py::test_dd_report_event_frequency_cap_allows_after_two_business_days tests/test_report_pipeline.py::TestProcessSitePipeline::test_report_created_records_rhodes_summary_event tests/test_report_pipeline.py::TestProcessSitePipeline::test_report_created_with_open_items_alerts_chat_when_owner_not_mentioned tests/test_report_pipeline.py::TestProcessSitePipeline::test_report_created_frequency_cap_skips_owner_and_chat_notifications -q
uv run ruff check src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\report_pipeline.py tests\test_automation_event.py tests\test_report_pipeline.py
uv run mypy src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\report_pipeline.py
uv run pytest --basetemp C:\tmp\ddr-clear-action-events-broad tests/test_automation_event.py tests/test_report_pipeline.py -q
git diff --check
```

Results:

- Focused report-event tests: 14 passed.
- Ruff on touched source/tests: passed.
- Mypy on touched source modules: passed.
- Broader affected automation/report pipeline suite: 56 passed.
- Diff check: passed with expected Windows LF-to-CRLF warnings only.

Next:

- Wait for CI/review/merge on DDR PR #145.

## 2026-05-29 - Portfolio Milestone Document Gaps

Started from clean `main` after the portfolio gap alert showed every active
site missing required documents.

Branch: `codex/portfolio-milestone-doc-gaps`

Current state: implementation complete locally; commit/PR pending.

Changed:

- Changed `portfolio_automation_gap_snapshot` document-gap logic from a flat
  three-document checklist to Rhodes' milestone-specific
  `getMissingDocuments` breakdown.
- The snapshot now loads each site's current P1 milestone and only flags
  missing documents required for that milestone.
- Future-milestone documents no longer create a portfolio gap before the site
  reaches that milestone.
- Changed the gap reason to `missing_current_milestone_documents` while keeping
  the existing `totals.missing_required_documents` count key for compatibility.
- Updated CLI and Google Chat wording to say `missing current-milestone docs`.
- Stopped treating a missing Drive folder as `snapshot_read_errors`; it remains
  its own `missing_drive_folder` gap.
- Added `RhodesClient.get_missing_documents()`.
- Updated process docs and regression tests.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-milestone-gaps tests/test_portfolio_automation_gaps.py tests/test_portfolio_gap_notifications.py tests/test_ddr_cli.py tests/test_rhodes.py -q
uv run ruff check src\due_diligence_reporter\portfolio_automation_gaps.py src\due_diligence_reporter\portfolio_gap_notifications.py src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\rhodes.py tests\test_portfolio_automation_gaps.py tests\test_portfolio_gap_notifications.py tests\test_ddr_cli.py tests\test_rhodes.py
uv run mypy src\due_diligence_reporter\portfolio_automation_gaps.py src\due_diligence_reporter\portfolio_gap_notifications.py src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\rhodes.py
git diff --check
```

Results:

- Focused portfolio/Rhodes tests: 36 passed.
- Ruff on touched source/tests: passed.
- Mypy on touched source modules: passed.
- Diff check: passed with expected Windows LF-to-CRLF warnings only.

Next:

- Commit, push, and open the DDR PR.
- After merge, rerun `Portfolio Automation Gaps`; the missing-doc count should
  drop to sites missing documents for their current active milestone, not all
  future milestone requirements.

## 2026-05-28 - Portfolio Gap Google Chat Alert

Confirmed PR #141 was merged:

- `due-diligence-reporter` PR #141 merged at `9696127`.

Continued the portfolio-health wrap-up by making the scheduled snapshot
operator-visible when gaps exist.

Branch: `codex/ddr-portfolio-gap-chat-alert`

Current state: branch pushed and draft PR open:
https://github.com/GFooteGK1/due-diligence-reporter/pull/142

Changed:

- Added `portfolio_gap_notifications.py` to format and post compact Google
  Chat summaries from the Rhodes-backed portfolio gap snapshot.
- Added `scripts/post_portfolio_gap_summary.py` for the GitHub Actions
  workflow.
- Updated `Portfolio Automation Gaps` to post a Chat summary when
  `sites_with_gaps > 0` and `GOOGLE_CHAT_WEBHOOK_URL` is configured.
- Clean snapshots skip notification; missing webhook configuration skips
  without failing the read-only workflow.
- Updated workflow contract tests, notification tests, and process docs.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-portfolio-gap-chat-focused tests/test_portfolio_gap_notifications.py tests/test_workflow_contracts.py tests/test_ddr_cli.py tests/test_portfolio_automation_gaps.py -q
uv run ruff check src\due_diligence_reporter\portfolio_gap_notifications.py scripts\post_portfolio_gap_summary.py tests\test_portfolio_gap_notifications.py tests\test_workflow_contracts.py
uv run mypy src\due_diligence_reporter\portfolio_gap_notifications.py
uv run mypy scripts\post_portfolio_gap_summary.py
git diff --check
```

Results:

- Focused notification/CLI/snapshot/workflow tests: 23 passed.
- Ruff on touched source/script/tests: passed.
- Mypy on touched source module: passed.
- Mypy on touched script: passed.
- Diff check: passed with expected Windows LF-to-CRLF warnings only.

Next:

- Wait for CI/review/merge on DDR PR #142.
- After merge, continue the remaining non-blocked plan item from clean `main`,
  or return to the hosted Rhodes MCP blocker once Rhodes-side note-write
  support is ready.

## 2026-05-28 - Portfolio Gap CLI and Workflow

Confirmed PR #140 was merged:

- `due-diligence-reporter` PR #140 merged at `a0c7e1a`.

Continued the portfolio-health wrap-up by making the new Rhodes-backed snapshot
usable outside MCP.

Branch: `codex/ddr-portfolio-gap-cli`

Current state: branch pushed and draft PR open:
https://github.com/GFooteGK1/due-diligence-reporter/pull/141

Changed:

- Added `uv run ddr portfolio-gaps` as an operator-facing CLI for the
  read-only `build_portfolio_automation_gap_snapshot` output.
- The CLI defaults to gap-only output, supports `--include-clean`, and can emit
  raw JSON with `--json`.
- Added the scheduled/manual `Portfolio Automation Gaps` GitHub Actions
  workflow. It requires only Rhodes credentials, writes a run summary, and
  uploads text plus JSON artifacts.
- Added regression tests for CLI human output, CLI JSON output, safe workflow
  input handling, and the read-only Rhodes-only workflow contract.
- Updated process docs with the CLI/workflow entrypoints.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-portfolio-gap-cli-focused tests/test_ddr_cli.py tests/test_portfolio_automation_gaps.py tests/test_workflow_contracts.py -q
uv run ruff check src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\portfolio_automation_gaps.py tests\test_ddr_cli.py tests\test_workflow_contracts.py
uv run mypy src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\portfolio_automation_gaps.py
```

Results:

- Focused CLI/snapshot/workflow tests: 19 passed.
- Ruff on touched source/tests: passed.
- Mypy on touched source modules: passed.

Next:

- Wait for CI/review/merge on PR #141.
- After merge, continue the remaining non-blocked plan item from clean `main`,
  or return to the hosted Rhodes MCP blocker once Rhodes-side note-write
  support is ready.

## 2026-05-28 - Portfolio Automation Gap Snapshot

Continued the next non-blocked wrap-up item after the nested Rhodes record ID
alignment work.

Branch: `codex/ddr-portfolio-automation-gap-snapshot`

Current state: branch pushed and draft PR open:
https://github.com/GFooteGK1/due-diligence-reporter/pull/140

Changed:

- Added a read-only Rhodes-backed `portfolio_automation_gap_snapshot` MCP tool.
- The snapshot rolls up active-site Drive-folder linkage, required DD source
  document coverage, `AutomationEvent v1` notes, pending automation review
  tasks, P1 DRI assignment, owner-routing status, latest DDR status, and
  source-event fingerprints.
- Added `RhodesClient.list_tasks()` plus task-list response coercion for the
  snapshot's pending-review task read.
- Added focused tests for portfolio totals, per-site gap reasons, missing-owner
  routing, missing Drive/doc coverage, open RayCon automation failures, pending
  data-repair tasks, and clean-site filtering.
- Documented that the snapshot is read-only and does not write to Rhodes,
  Drive, Gmail, or Chat.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-portfolio-gaps-focused tests/test_portfolio_automation_gaps.py -q
uv run pytest --basetemp C:\tmp\ddr-portfolio-gaps-final tests/test_portfolio_automation_gaps.py tests/test_rhodes.py tests/test_rhodes_events.py tests/test_workflow_contracts.py -q
uv run ruff check src\due_diligence_reporter\portfolio_automation_gaps.py src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\server.py tests\test_portfolio_automation_gaps.py
uv run mypy src\due_diligence_reporter\portfolio_automation_gaps.py src\due_diligence_reporter\rhodes.py src\due_diligence_reporter\server.py
git diff --check
```

Results:

- Focused snapshot tests: 2 passed.
- Affected Rhodes/workflow suite: 35 passed.
- Ruff on touched source/tests: passed.
- Mypy on touched source modules: passed.
- Diff check: passed with expected Windows LF-to-CRLF warnings only.

Next:

- Wait for CI/review/merge on PR #140.
- After merge, continue any remaining non-blocked plan item from a clean
  `main`, or revisit the hosted Rhodes MCP blocker if Rhodes-side support is
  ready.

## 2026-05-28 - Nested Rhodes Record IDs

Continued the non-blocked Phase 3 adapter-alignment work.

Branch: `codex/ddr-nested-rhodes-record-ids`

Current state: implementation complete locally; commit/PR pending.

Changed:

- Added shared nested response ID extraction in `rhodes.py`.
- DDR note creation now accepts Rhodes note IDs returned in nested response
  shapes such as `{note: {id: ...}}`, `{result: {...}}`, `{record: {...}}`,
  or `{data: {...}}`.
- Owner user resolution now accepts nested user ID response shapes too.
- Added regression coverage proving nested note IDs do not trigger readback or
  retry, and nested owner IDs still produce `@` mentions.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-nested-rhodes-ids-focused tests/test_rhodes.py::test_add_rhodes_site_note_accepts_nested_note_id_response tests/test_rhodes.py::test_add_rhodes_site_note_resolves_nested_owner_user_id tests/test_rhodes.py::test_add_rhodes_site_note_requires_returned_note_id -q
uv run pytest --basetemp C:\tmp\ddr-nested-rhodes-ids-broad tests/test_rhodes.py tests/test_rhodes_events.py -q
uv run ruff check src\due_diligence_reporter\rhodes.py tests\test_rhodes.py
uv run mypy src\due_diligence_reporter\rhodes.py
git diff --check
```

Results:

- Focused nested-ID tests: 3 passed.
- Broader Rhodes/Rhodes-event suite: 26 passed.
- Ruff on touched code/tests: passed.
- Mypy on touched Rhodes module: passed.
- Diff check: passed with expected Windows LF-to-CRLF warnings only.

Next:

- Commit, push, and open PR.
- After merge, continue the next non-blocked task: another adapter alignment
  slice or the portfolio automation-gap snapshot.

## 2026-05-28 - DDR Report Event Test Gap

Confirmed PR #137 was merged:

- `due-diligence-reporter` PR #137 merged at `c8fcf1a`.

Closed the local validation gap called out in the prior handoff where
`tests/test_report_pipeline.py` was excluded because one success-path test did
not stub the newer Rhodes report-event write.

Branch: `codex/ddr-report-event-test-gap`

Changed:

- Updated `test_report_created_does_not_record_publish_step` so it stubs the
  Rhodes report-event note, passes a site ID, and still verifies no legacy
  publish/upload side effect happens.
- This keeps the test aligned with the current contract: report-created success
  writes a Rhodes `dd_report_created` event and should not resurrect the old
  publish side effect.

Verification:

```powershell
uv run pytest --basetemp C:\tmp\ddr-report-pipeline-fix tests/test_report_pipeline.py -q
uv run ruff check tests\test_report_pipeline.py
uv run pytest --basetemp C:\tmp\ddr-report-pipeline-gap-broad tests/test_automation_event.py tests/test_report_pipeline.py tests/test_dd_republish.py tests/test_vendor_doc_sweep.py tests/test_raycon_followup.py tests/test_inbox_scanner.py tests/test_workflow_contracts.py tests/test_rhodes_events.py -q
git diff --check
```

Results:

- Report pipeline suite: 44 passed.
- Ruff on touched test: passed.
- Broader affected DDR suite including report pipeline: 242 passed.
- Diff check: passed with expected Windows LF-to-CRLF warnings only.

Next:

- Commit, push, and open the PR.
- After merge, continue with the next remaining shared-adapter/record-completion
  wrap-up item that is not blocked by the hosted Rhodes MCP `addNote` issue.

## 2026-05-28 - DDR Republish Failure Events

Confirmed downstream PR #54 was merged, then continued the next DDR
notification/record-completion slice.

Branch: `codex/ddr-republish-failure-events`

Draft PR: https://github.com/GFooteGK1/due-diligence-reporter/pull/137

Implementation commit: `94d31de` (`Record DDR republish failure events`)

Current state: branch pushed, draft PR open. GitHub reports merge state
`CLEAN`; no checks were reported when checked.

Changed:

- Added `dd_report_republish_failed` to the DDR `AutomationEvent v1` contract.
- Failed event-driven DDR republish attempts now write a Rhodes decision note
  carrying source trigger, fingerprint, run ID, manifest, Drive folder, and
  failure reason.
- If the Rhodes owner note is not verified, the same event body is posted to
  the configured Google Chat webhook.
- Wired the failure-event recorder into inbox scan, RayCon follow-up, and the
  vendor doc republish sweep.
- Prompt-missing branches now surface the same failure event in live mode while
  keeping dry runs side-effect free.
- Updated process docs and regression coverage.

Verification:

```powershell
uv run pytest tests/test_automation_event.py tests/test_dd_republish.py tests/test_vendor_doc_sweep.py tests/test_raycon_followup.py --basetemp C:\tmp\ddr-republish-failure-events-focused -q
uv run pytest tests/test_automation_event.py tests/test_dd_republish.py tests/test_vendor_doc_sweep.py tests/test_raycon_followup.py tests/test_inbox_scanner.py tests/test_workflow_contracts.py tests/test_rhodes_events.py --basetemp C:\tmp\ddr-republish-failure-events-broad -q
uv run ruff check src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\dd_republish.py src\due_diligence_reporter\vendor_doc_sweep.py scripts\scan_inbox.py scripts\raycon_followup.py tests\test_automation_event.py tests\test_dd_republish.py tests\test_vendor_doc_sweep.py tests\test_raycon_followup.py
uv run mypy src\due_diligence_reporter\automation_event.py src\due_diligence_reporter\dd_republish.py src\due_diligence_reporter\vendor_doc_sweep.py src\due_diligence_reporter\rhodes_events.py
uv run mypy scripts\scan_inbox.py scripts\raycon_followup.py
git diff --check
git diff --cached --check
```

Results:

- Focused event/republish/RayCon/vendor sweep suite: 110 passed.
- Broader affected DDR suite: 198 passed.
- Ruff on touched code/tests: passed.
- Focused source Mypy: no issues in 4 source files.
- Script Mypy: no issues in 2 script files.
- Diff checks: passed with expected Windows LF-to-CRLF warnings and the
  existing user-level ignore permission warning only.

Note:

- A broader run that included `tests/test_report_pipeline.py` previously showed
  the local setup failure where the report-created test does not stub the
  Rhodes report-event write. That failure is unrelated to this branch and was
  not included in the final affected-suite gate.

Next:

- Wait for CI/review on PR #137.
- After merge, continue with the next remaining plan item. The separate hosted
  Rhodes MCP blocker still remains: GitHub Actions service clients cannot
  create verified Rhodes notes until the Rhodes team exposes a non-interactive
  automation-safe note write path.

## 2026-05-28 - RayCon Rhodes MCP Elicitation Blocker

Confirmed PR #135 merged at `0836280` and retested on `main`.

Live test:

- Deleted only the two stale RayCon runtime caches from the failed PR #134
  retest runs:
  - `raycon-runtime-state-26585081215`
  - `raycon-runtime-state-26585081257`
- Tulsa run:
  https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26585435369
- Santa Clara run:
  https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26585435328
- Both runs failed closed with `missing_note_id`.
- Both runs posted Google Chat fallback.
- `note_response_summaries` showed that both the `site_id` attempt and the
  `site_slug_retry` attempt returned:
  - `status: "rejected"`
  - `rejectionReason: "elicitation_unsupported"`
  - no Rhodes note ID

Conclusion:

- The RayCon follow-up caller is now doing the right fail-closed behavior.
- The remaining blocker is the hosted Rhodes MCP `addNote` write surface:
  GitHub Actions cannot satisfy an interactive elicitation/confirmation flow,
  so the note write is rejected before any owner `@` mention can notify.
- More RayCon caller retries will not fix this unless the Rhodes write surface
  exposes a trusted automation-safe note creation path.

Next:

- Locate the deployed Rhodes MCP/write-surface source.
- Add or adjust a non-interactive service/API-key path for automation-created
  site notes that still records audit context and honors `mentions`.
- Retest Tulsa/Santa Clara RayCon Follow-up after that Rhodes-side fix. Keep
  `RAYCON_FOLLOWUP_EXTRA_MENTION_USER_IDS` set to Greg until notification
  delivery is confirmed.

## 2026-05-28 - RayCon Note Rejection Diagnostics

Confirmed PR #134 merged at `7f1aa37` and retested on `main`.

Live test:

- Deleted only the two stale RayCon runtime caches from the failed PR #133
  retest runs:
  - `raycon-runtime-state-26584277976`
  - `raycon-runtime-state-26584277931`
- Tulsa run:
  https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26585081257
- Santa Clara run:
  https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26585081215
- Both runs failed closed with `missing_note_id`.
- Both runs posted Google Chat fallback.
- Live Rhodes `listNotes` still showed no new RayCon note on either site.
- New diagnostic showed both the site-ID attempt and site-slug retry returned
  dicts with keys `["rejectionReason", "status"]`, no note ID. The actual values
  were not logged yet, so the next slice must expose those safe scalar fields.

Branch: `codex/raycon-note-rejection-diagnostics`

Changed:

- `note_response_summaries` now includes capped scalar values for:
  - `status`
  - `reason`
  - `rejectionReason`
  - `message`
  - `error`
- This keeps note body and secrets out of logs while surfacing the hosted MCP
  write rejection reason.

Verification:

```powershell
uv run pytest tests/test_rhodes.py tests/test_rhodes_events.py tests/test_raycon_followup.py --basetemp C:\tmp\pytest-raycon-note-rejection-diagnostics
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

- Open PR for `codex/raycon-note-rejection-diagnostics`.
- After merge, clear the fresh RayCon runtime caches from the failed PR #134
  retest runs if they would suppress the new test:
  - `raycon-runtime-state-26585081257`
  - `raycon-runtime-state-26585081215`
- Rerun RayCon Follow-up for Tulsa/Santa Clara.
- Use the logged `status` / `rejectionReason` values to decide whether the
  durable fix is a caller payload change or a deployed Rhodes MCP write-surface
  change.

## 2026-05-28 - RayCon Note Response Diagnostics

Confirmed PR #133 merged at `41709ae` and retested on `main`.

Live test:

- Deleted only the two stale RayCon runtime caches from the failed PR #132
  retest runs:
  - `raycon-runtime-state-26583402921`
  - `raycon-runtime-state-26583402885`
- Santa Clara run:
  https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26584277976
- Tulsa run:
  https://github.com/GFooteGK1/due-diligence-reporter/actions/runs/26584277931
- Both runs failed closed with `missing_note_id`.
- Both runs included Devin Bates plus Greg Foote in `mentioned_user_ids`.
- Both runs posted the Google Chat fallback.
- Live Rhodes `listNotes` and audit-log readback still showed no new RayCon
  note on either site.
- The failure now survives explicit `anchorType: "site"` / `anchorId` payloads
  as well as prior site-ID, site-slug, and readback recovery paths.

Branch: `codex/raycon-note-response-diagnostics`

Changed:

- Missing-note failures now include `note_response_summaries`, a sanitized
  shape-only summary of each `addNote` response attempt.
- The summary includes attempt name, Python type, response keys, whether a note
  ID was present, and a capped text prefix only when the MCP returned plain text.
- It does not log the generated RayCon note body or secrets.

Verification:

```powershell
uv run pytest tests/test_rhodes.py tests/test_rhodes_events.py tests/test_raycon_followup.py --basetemp C:\tmp\pytest-raycon-note-response-diagnostics
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

- Open PR for `codex/raycon-note-response-diagnostics`.
- After merge, clear the fresh RayCon runtime caches from the failed PR #133
  retest runs if they would suppress the new test:
  - `raycon-runtime-state-26584277976`
  - `raycon-runtime-state-26584277931`
- Rerun RayCon Follow-up for Tulsa/Santa Clara.
- Use `note_response_summaries` in the workflow logs to decide the actual
  hosted Rhodes MCP/API fix. If the response is empty or confirmation-shaped,
  the durable fix belongs in the deployed Rhodes MCP write surface.

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
  `sources.trace_link`, dashboard publishing text, and retired work-management references.

Verification completed:

```powershell
uv run ruff check src/due_diligence_reporter/google_doc_builder.py docs/prompts/prompt_v4.md tests/test_google_doc_builder.py tests/test_prompt_contract.py
uv run mypy src/
uv run pytest --basetemp C:\tmp\ddr-pytest-greg-format tests/test_google_doc_builder.py tests/test_report_schema.py tests/test_prompt_contract.py tests/test_dd_output_fixes.py
git diff --check
rg -n "Source Quality Notes|Lease Conditions|Trade-Offs and Deficiencies|Report Trace|sources\.trace_link|dashboard publishing" src/due_diligence_reporter docs/prompts/prompt_v4.md docs/templates/Site_DD_Report_Template_V4.md
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
rg -n "apply_opening_plan_skill|Always.*send_dd_report_email|Every DD Report answers four questions|How to Use Me|\[1\]|â|Ã|RayCon API|calls the RayCon" docs\prompts\prompt_v4.md
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
rg -n "retired work-management|project_notes|work-management|source citations|Citations" docs\prompts\prompt_v4.md docs\process\HOW-IT-WORKS.md src\due_diligence_reporter tests
```

Results:

- Targeted ruff: all checks passed.
- Targeted mypy: no issues in 4 source files.
- Focused pytest: 248 passed.
- Retired work-management reference grep is clean.
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

## 2026-06-02 - School Approval Routed Through Ops-Skills

DDR no longer relies on the stale local school approval state table in
`server.py`. `apply_school_approval_skill` now loads the hosted
`school-approval` skill from Ops-Skills, preferring `OPS_SKILLS_REPO_PATH`, then
the sibling Ops-Skills checkout, then the installed Ops Skills plugin cache.
When the source is a git checkout, it reads `origin/main:skills/school-approval/SKILL.md`
so the local dirty/behind worktree does not silently downgrade the rules.

What changed:

- Added `src/due_diligence_reporter/school_approval_skill.py` to resolve,
  load, and parse the hosted skill's version, rules version, and Baseline Score
  Table.
- Updated `apply_school_approval_skill` to accept `address`, derive the state
  from address when needed, and return provenance fields:
  `rules_version`, `school_approval_skill_version`, and
  `school_approval_skill_source`.
- Removed the stale `_STATE_APPROVAL_TABLE` from `server.py`.
- Updated the report pipeline tool schema/canonicalizer so the site address is
  passed into `apply_school_approval_skill`.
- Documented `OPS_SKILLS_REPO_PATH` in `.env.example` and config metadata.

Verification:

```powershell
uv run pytest tests/test_school_approval.py tests/test_report_pipeline.py -q --basetemp C:\tmp\pytest-ddr-school-approval
uv run ruff check src/due_diligence_reporter/school_approval_skill.py src/due_diligence_reporter/server.py src/due_diligence_reporter/report_pipeline.py tests/test_school_approval.py tests/test_report_pipeline.py
uv run mypy src/due_diligence_reporter/school_approval_skill.py src/due_diligence_reporter/server.py src/due_diligence_reporter/report_pipeline.py
```

Results:

- Focused pytest: 56 passed.
- Ruff: all checks passed.
- Mypy: success for 3 touched source files.
- Live local smoke invocation read `Ops-Skills origin/main` and returned
  v3.3.0: CA `REGISTRATION_SIMPLE` / 14 days, OK `NONE` / 7 days, RI
  `CERTIFICATE_OR_APPROVAL_REQUIRED` / 45 days.

## 2026-06-02 - Ease of Conversion Routed Through Ops-Skills

DDR no longer flattens E-Occupancy/ease-of-conversion ratings through the stale
local `GREEN only at 100 / RED only at 0 / otherwise YELLOW` zone rule.
`apply_e_occupancy_skill` now loads the hosted `ease-of-conversion` skill from
Ops-Skills and reads its `references/site-eval-brainlift.md` rating-band
contract, using the same source resolution path as school approval:
`OPS_SKILLS_REPO_PATH`, sibling `Ops-Skills`, then installed Ops Skills plugin
cache. Git checkouts are read from `origin/main` when possible.

What changed:

- Added `src/due_diligence_reporter/ops_skill_loader.py` as the shared hosted
  skill loader.
- Refactored `src/due_diligence_reporter/school_approval_skill.py` onto the
  shared loader without changing its public behavior.
- Added `src/due_diligence_reporter/ease_conversion_skill.py` to load
  `ease-of-conversion/SKILL.md`, parse scorecard metadata, and parse/validate
  the hosted E-Occupancy Rating Bands from
  `references/site-eval-brainlift.md`.
- Updated `apply_e_occupancy_skill` to derive `zone` from the hosted
  GREEN/YELLOW/ORANGE/RED bands and to return provenance fields:
  `ease_conversion_skill_version`, `ease_conversion_skill_source`,
  `ease_conversion_reference_source`, and
  `ease_conversion_scorecard_theme_id`.
- Added matching `q2.e_occupancy_*` report data fields and included provenance
  in the standalone E-Occupancy assessment document.
- Updated process docs and `OPS_SKILLS_REPO_PATH` config text to cover both
  school approval and ease-of-conversion.

Verification:

```powershell
uv run pytest tests/test_ease_conversion.py tests/test_school_approval.py tests/test_report_pipeline.py -q --basetemp C:\tmp\pytest-ddr-ease-conversion-rerun
uv run ruff check src\due_diligence_reporter\ops_skill_loader.py src\due_diligence_reporter\ease_conversion_skill.py src\due_diligence_reporter\school_approval_skill.py src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\config.py tests\test_ease_conversion.py tests\test_school_approval.py
uv run mypy src\due_diligence_reporter\ops_skill_loader.py src\due_diligence_reporter\ease_conversion_skill.py src\due_diligence_reporter\school_approval_skill.py src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\config.py
git diff --check
```

Results:

- Focused pytest: 59 passed.
- Ruff: all checks passed.
- Mypy: success for 6 source files.
- `git diff --check` passed with expected Windows LF-to-CRLF warnings only.
- Live local E-Occupancy smoke invocation read:
  - `C:\Users\foote\.claude\Work\repos\Ops-Skills origin/main:skills/ease-of-conversion/SKILL.md`
  - `C:\Users\foote\.claude\Work\repos\Ops-Skills origin/main:skills/ease-of-conversion/references/site-eval-brainlift.md`
- Hosted `ease-of-conversion` currently has no frontmatter `version`, so DDR
  reports `ease_conversion_skill_version=unversioned`.
- Live smoke returned score `58`, zone `ORANGE`, and
  `ease_conversion_scorecard_theme_id=site-due-diligence-opening` for
  `warehouse with hvac`.

## 2026-06-09 - RayCon Uses Alpha Capacity Analysis for Published Capacity

RayCon/DDR Block Plan handoff was hardened so capacity ownership is explicit:
Alpha Capacity Analysis is the authoritative source for published Fast Path
and Max Capacity student counts when a capacity artifact is present; RayCon
owns construction estimates, schedule, pricing categories, and calculator audit
evidence.

What changed in DDR:

- `post_raycon_job` accepts optional `capacity_analysis_file_id` and
  `capacity_analysis` payloads and preserves deterministic body ordering.
- `_run_block_plan_downstream` now resolves the site's M1 folder, searches for
  the latest Alpha Capacity Analysis or legacy Capacity Brainlift artifact, and
  attaches it to the RayCon job when it can read a JSON payload or clearly
  labeled Strict/Max totals from a saved skill report. If no artifact exists,
  DDR now runs the hosted `alpha-capacity-analysis` skill contract through
  `alpha_capacity_analysis.py` using extracted Block Plan text plus the Block
  Plan PDF bytes when available, saves a machine-readable JSON artifact in M1,
  and passes that payload to RayCon.
- `scripts/raycon_followup.py` now uses the same Alpha Capacity contract for
  safety-net dispatches when Block Plans arrive outside inbox intake. It reuses
  an existing M1 Alpha Capacity artifact when present, otherwise downloads the
  Block Plan PDF, generates the JSON artifact, and attaches it to
  `post_raycon_job`. Capacity generation remains fail-soft: a missing/failed
  artifact is recorded in the row and dispatch state, but RayCon still receives
  the Block Plan job.
- `scripts/raycon_followup.py` also has `--suppress-notifications` for
  controlled single-site validation runs. It still processes the site, writes
  dispatch state, uploads Alpha Capacity artifacts, and calls RayCon when the
  normal branch does so, but skips Rhodes/Google Chat alert delivery if that
  test run produces alerts/errors.
- `scripts/raycon_followup.py --env-file <path>` loads an explicit runtime
  config before settings are built and anchors relative Google credential/token
  paths to that env file's directory. This lets a validation checkout run
  against the existing canonical local config without copying `.env` or token
  files into the validation workspace.
- `read_alpha_capacity_analysis_from_m1` was added beside the existing RayCon
  M1 JSON reader. It reads JSON files directly, exports Google Docs as text
  when needed, skips malformed candidates without blocking dispatch, and never
  calculates capacity itself.
- `apply_alpha_capacity_analysis_skill` is now exposed to the report-pipeline
  agent and MCP server. The prompt requires calling it after reading a Block
  Plan, before relying on RayCon scenario values. The tool downloads the Block
  Plan PDF by Drive file ID when Drive context is available and only reports
  success when both Strict/Fast Path and Max Capacity student counts are
  present.
- `OPENAI_CAPACITY_MODEL` / `openai_capacity_model` controls the model used for
  Alpha Capacity Analysis generation; missing `OPENAI_API_KEY` causes a
  fail-soft result instead of blocking RayCon dispatch.
- `.env.example`, MCP Hive publish packaging, inbox scan, RayCon follow-up,
  daily DD check, and vendor republish workflows now surface optional
  `OPENAI_CAPACITY_MODEL` so operators can tune the capacity model without code
  changes. The code still defaults to `gpt-4o` when the variable is unset.
- Image-only Block Plan PDFs no longer automatically skip capacity generation:
  inbox passes the attachment bytes directly, and the MCP/report tool downloads
  the PDF when `drive_folder_url` and `block_plan_file_id` are provided.
- Filename classification recognizes `Alpha Capacity Analysis - ...` as the
  existing `capacity_brainlift_report` bucket for M1 compatibility.
- DDR maps RayCon `capacity_students` into
  `exec.fastest_open_capacity` and `exec.max_capacity_capacity`, while
  completeness still treats those tokens as Alpha Capacity Analysis sourced
  rather than RayCon-blocking.
- `docs/reference/RayCon-DDR-Rebuild-Package.md` documents the optional
  capacity payload and the fail-soft dispatch behavior.

What changed in RayCon:

- `/v1/jobs` accepts `capacity_analysis`, `alpha_capacity_analysis`,
  `capacity_analysis_file_id`, and `capacity_analysis_url`.
- RayCon normalizes strict/Fast Path and max-capacity scenarios from the Alpha
  artifact and uses those counts for published scenario capacity and
  student-scaled pricing.
- RayCon's internal capacity calculator remains audit/fallback evidence.
  Disagreement with Alpha Capacity Analysis is emitted as a warning/caveat, not
  as a failed scenario.
- Provenance is emitted in `analysis.site_context.capacity_analysis`,
  `provenance.capacity_analysis`, and scenario `capacity_trace`.

Verification:

```powershell
uv run ruff check src\due_diligence_reporter\raycon_client.py src\due_diligence_reporter\inbox_scanner.py src\due_diligence_reporter\classifier.py tests\test_raycon_client.py tests\test_inbox_scanner.py tests\test_classifier_keywords.py
uv run mypy src\due_diligence_reporter\raycon_client.py src\due_diligence_reporter\inbox_scanner.py src\due_diligence_reporter\classifier.py
uv run pytest tests\test_raycon_client.py tests\test_inbox_scanner.py tests\test_classifier_keywords.py tests\test_completeness.py tests\test_report_schema.py -q
uv run ruff check src\due_diligence_reporter\alpha_capacity_analysis.py src\due_diligence_reporter\config.py src\due_diligence_reporter\inbox_scanner.py src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py tests\test_alpha_capacity_analysis.py tests\test_inbox_scanner.py tests\test_report_pipeline.py tests\test_report_schema.py tests\test_prompt_contract.py
uv run mypy src\due_diligence_reporter\alpha_capacity_analysis.py src\due_diligence_reporter\config.py src\due_diligence_reporter\inbox_scanner.py src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_inbox_scanner.py tests\test_raycon_client.py tests\test_report_pipeline.py tests\test_report_schema.py tests\test_prompt_contract.py tests\test_completeness.py tests\test_classifier_keywords.py -q --basetemp C:\tmp\ddr-raycon-alpha-capacity-tests
uv run ruff check scripts\raycon_followup.py tests\test_raycon_followup.py tests\test_workflow_contracts.py
uv run mypy scripts\raycon_followup.py
uv run pytest tests\test_raycon_followup.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-followup-capacity
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_inbox_scanner.py tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_report_pipeline.py tests\test_report_schema.py tests\test_prompt_contract.py tests\test_completeness.py tests\test_classifier_keywords.py tests\test_docs_env_contract.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-alpha-capacity-full
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_inbox_scanner.py tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_report_pipeline.py tests\test_report_schema.py tests\test_prompt_contract.py tests\test_completeness.py tests\test_classifier_keywords.py tests\test_docs_env_contract.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-alpha-capacity-live-test-ready
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_inbox_scanner.py tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_report_pipeline.py tests\test_report_schema.py tests\test_prompt_contract.py tests\test_completeness.py tests\test_classifier_keywords.py tests\test_docs_env_contract.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-alpha-capacity-envfile-ready
uv run ruff check src\due_diligence_reporter\alpha_capacity_analysis.py src\due_diligence_reporter\config.py src\due_diligence_reporter\inbox_scanner.py src\due_diligence_reporter\raycon_client.py src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py scripts\raycon_followup.py tests\test_alpha_capacity_analysis.py tests\test_inbox_scanner.py tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_report_pipeline.py tests\test_report_schema.py tests\test_prompt_contract.py tests\test_completeness.py tests\test_classifier_keywords.py tests\test_docs_env_contract.py tests\test_workflow_contracts.py
uv run mypy src\due_diligence_reporter\alpha_capacity_analysis.py src\due_diligence_reporter\config.py src\due_diligence_reporter\inbox_scanner.py src\due_diligence_reporter\raycon_client.py src\due_diligence_reporter\server.py src\due_diligence_reporter\report_pipeline.py
npx.cmd vitest run src/rayconJobs.test.js src/jobsRoute.test.js
node -c api\src\rayconJobs.js
node -c api\src\index.js
node -c api\src\openApiSpec.js
node -c api\src\rayTools.js
git diff --check
```

Results:

- DDR ruff: all checks passed.
- DDR mypy: success for the original RayCon handoff files, the 6 Alpha
  Capacity/RayCon source files, and `scripts/raycon_followup.py` when checked
  separately to avoid the script/src duplicate-module import pattern.
- DDR focused pytest: 314 passed for the original handoff suite; 386 passed for
  the expanded Alpha Capacity generation, inbox, RayCon client, report
  pipeline, schema, prompt, completeness, and classifier suite; 74 passed for
  RayCon follow-up/workflow contracts; 462 passed for the combined Alpha
  Capacity, inbox, RayCon client, follow-up, report pipeline, schema, prompt,
  completeness, classifier, docs-env, and workflow contract suite; 463 passed
  after adding the controlled live-test notification suppression flag; 466
  passed after adding the explicit env-file/credential-path support, with
  explicit `C:\tmp` pytest base temp.
- RayCon focused Vitest: 106 passed.
- RayCon syntax checks passed for touched JS files.
- DDR and RayCon `git diff --check` passed with expected Windows CRLF warnings
  only.

Remaining rollout dependency:

- DDR now attempts to create the Alpha Capacity Analysis artifact when a Block
  Plan returns and no existing artifact is present. Production rollout still
  needs `OPENAI_API_KEY` plus the desired `OPENAI_CAPACITY_MODEL` configured in
  the DDR runtime, and one live Block Plan run should verify that M1 receives
  the JSON artifact and RayCon consumes it for Fast Path and Max Capacity
  pricing. If the model cannot produce both counts, or neither Block Plan text
  nor PDF bytes are available to the skill runner, DDR intentionally dispatches
  RayCon without external capacity and RayCon falls back to its calculator/audit
  path with caveats.
- Suggested live test site: `Alpha Miami Beach 300 71st 3rd`
  (`site_id=k972ay4w964539mq0naqyde5ws85fr3r`). Current read-only inventory
  showed M1 folder `1DuceE9iu0y45G6wncl4cRZyTkgP7IiYL`, Block Plan
  `2026.05.19 - Alpha Miami Beach 300 71st 3rd Block Plan.pdf`
  (`10dPoeXlUcuYwvEGflf0r9zo4RQMCfErM`), no Alpha Capacity artifact, no
  `raycon_scenario.json`, and 2,674 extracted Block Plan text characters with
  room SF / explicit student-count evidence. A modified-checkout dry run using
  the canonical runtime env succeeded read-only and scoped to exactly this
  site:
  `uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --dry-run --skip-dd-republish --suppress-notifications`.
  Run the scoped live validation from the validation checkout with:
  `uv run python scripts\raycon_followup.py --env-file C:\Users\foote\.claude\Work\repos\due-diligence-reporter\.env --site-id k972ay4w964539mq0naqyde5ws85fr3r --skip-dd-republish --suppress-notifications`.
  Expected evidence after the run: M1 contains the generated Alpha Capacity JSON
  artifact, RayCon dispatch state records `capacity_analysis_attached=true`, and
  RayCon writes a `raycon_scenario.json` whose provenance points to Alpha
  Capacity Analysis for Fast Path and Max Capacity student counts.

Latest validation update:

- First controlled Miami Beach live run dispatched RayCon but did not attach
  capacity because the model returned `insufficient_evidence`. RayCon later
  wrote a failed `raycon_scenario.json` with run
  `rc_20260609202847_ef340148f3`; the failure reason cited a mismatched
  126-student capacity, which confirmed the old no-capacity RayCon path is the
  behavior this change is meant to eliminate.
- The Alpha Capacity generator now falls back to explicit Block Plan schedule
  counts when the hosted skill/model extracts the plan but refuses to publish
  counts due missing support facts. The Miami Beach no-upload live probe now
  returns success with Strict/Fast Path `114` and Max Capacity `199` from
  student-count pairs `40/70`, `24/42`, and `50/87`.
- The parser was hardened for PDF text where `STUDENTS` is glued to the next
  level label (for example `STUDENTSL3...`) and for repeated whole-schedule
  extraction, without de-duping legitimate duplicate rows.
- A read-only candidate scan also found `Alpha Tampa 2409 S MacDill Ave` and
  `Alpha Plano 5509 Pleasant Valley Dr`, but no-upload probes returned
  `insufficient_evidence` for both, so they are not good full-flow tests for
  the capacity-backed RayCon path.
- `scripts/raycon_followup.py` no longer lets stale `queued`/`running` RayCon
  job status block forever. In-progress status is respected inside the
  redispatch window; outside that window, DDR re-dispatches so a fixed capacity
  payload can recover a stuck or failed old run.
- `read_alpha_capacity_analysis_from_m1` now skips existing M1 Alpha Capacity
  or legacy Capacity Brainlift artifacts unless they contain both
  Strict/Fast Path and Max Capacity counts. This prevents a partial old artifact
  from being treated as authoritative and forces DDR to generate a fresh hosted
  Alpha Capacity artifact instead.
- A Miami Beach suppressed dry run with `--redispatch-after-minutes 0` now
  reaches the failed-scenario retry path and returns `dispatch_skipped=dry_run`
  instead of staying on the stale running-job branch.
- The deployed RayCon `/version` currently reports commit `7cba48d`, which is
  the local base commit before these uncommitted capacity-ingestion changes.
  Full live proof requires publishing the RayCon changes first; otherwise the
  deployed API strips/ignores the new capacity fields. After RayCon is deployed,
  rerun Miami Beach from the DDR validation checkout with
  `--redispatch-after-minutes 0 --skip-dd-republish --suppress-notifications`.
- Rechecked `/version` after context compaction on 2026-06-09; production still
  reports `git_commit=7cba48d`. The Miami Beach test site remains ready, but the
  next non-dry-run proof should wait until RayCon is deployed with the
  capacity-ingestion changes so the job can consume the Alpha Capacity payload.
- Added a RayCon dry-run deploy planner at
  `scripts/deploy-raycon-cloud-run.mjs` in the RayCon checkout. It reads
  `deploy-manifest.yaml`, generates a Cloud Build config that passes
  `GIT_HASH=<commit>` into the Docker build, generates a Cloud Run env file
  with `GIT_COMMIT=<commit>`, prints the exact `gcloud` build/deploy commands,
  and refuses `--execute` on a dirty tree unless explicitly overridden. This
  keeps the eventual deploy from repeating the known manual-source-deploy
  `/version=unknown` failure mode.
- Dry-run verification produced commands only and warned that the current
  RayCon tree is dirty. The generated scratch deploy files were removed.
- RayCon API docs now show the current source-selection contract
  `source_contract:2026-06-09-alpha-capacity-input-v1`, and
  `deployManifest.test.js` asserts the API reference contains the exported
  active contract while rejecting the old
  `2026-05-20-scout-rerun-evidence-v1` contract. This keeps docs, idempotency
  examples, and the retry/recovery behavior aligned for the live Miami Beach
  proof.
- Inbox Block Plan dispatch result rows now include the same capacity
  observability fields as the RayCon follow-up safety net:
  `capacity_analysis_status`, `capacity_analysis_attached`,
  `capacity_analysis_file_id`, `capacity_analysis_url`, and
  `capacity_analysis_error`. This makes the first intake path auditable when a
  Block Plan dispatches without a complete Alpha Capacity payload, instead of
  relying only on logs.
- RayCon now treats Alpha Capacity Analysis as authoritative only when the
  supplied payload contains both Fast Path/Strict and Max Capacity counts. A
  partial Alpha artifact is ignored instead of creating a mixed-authority
  scenario. RayCon also passes the Alpha Fast Path count into the Fastest Path
  `estimate_costs` call, while the Maximum Capacity call continues to receive
  both Alpha Fast Path and Max counts for student-scaled delta pricing.
- The RayCon authoritative-capacity test now uses the Miami Beach proof numbers
  from the selected Block Plan: Fast Path `114` and Max Capacity `199`.
  Together with DDR's schedule fallback test for `40/70`, `24/42`, and
  `50/87`, local coverage now mirrors the intended live proof site before
  deployment.

Latest verification:

```powershell
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_inbox_scanner.py tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_report_pipeline.py tests\test_report_schema.py tests\test_prompt_contract.py tests\test_completeness.py tests\test_classifier_keywords.py tests\test_docs_env_contract.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-alpha-capacity-final
uv run ruff check scripts\raycon_followup.py src\due_diligence_reporter\alpha_capacity_analysis.py src\due_diligence_reporter\classifier.py src\due_diligence_reporter\completeness.py src\due_diligence_reporter\config.py src\due_diligence_reporter\inbox_scanner.py src\due_diligence_reporter\raycon_client.py src\due_diligence_reporter\report_pipeline.py src\due_diligence_reporter\report_schema.py src\due_diligence_reporter\server.py tests\test_alpha_capacity_analysis.py tests\test_classifier_keywords.py tests\test_completeness.py tests\test_docs_env_contract.py tests\test_inbox_scanner.py tests\test_prompt_contract.py tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_report_pipeline.py tests\test_report_schema.py tests\test_workflow_contracts.py
uv run mypy src\due_diligence_reporter\alpha_capacity_analysis.py
uv run mypy scripts\raycon_followup.py
npx.cmd vitest run src/rayconJobs.test.js src/jobsRoute.test.js
npx.cmd vitest run src/rayconJobs.test.js src/jobsRoute.test.js src/deployManifest.test.js
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_raycon_followup.py tests\test_raycon_client.py -q --basetemp C:\tmp\ddr-raycon-alpha-capacity-local-audit
uv run pytest tests\test_inbox_scanner.py::TestBlockPlanDownstream tests\test_alpha_capacity_analysis.py tests\test_raycon_client.py -q --basetemp C:\tmp\ddr-inbox-alpha-capacity-observability
uv run pytest tests\test_alpha_capacity_analysis.py tests\test_inbox_scanner.py tests\test_raycon_client.py tests\test_raycon_followup.py tests\test_report_pipeline.py tests\test_report_schema.py tests\test_prompt_contract.py tests\test_completeness.py tests\test_classifier_keywords.py tests\test_docs_env_contract.py tests\test_workflow_contracts.py -q --basetemp C:\tmp\ddr-raycon-alpha-capacity-observability-full
uv run ruff check src\due_diligence_reporter\alpha_capacity_analysis.py tests\test_alpha_capacity_analysis.py
uv run ruff check src\due_diligence_reporter\inbox_scanner.py tests\test_inbox_scanner.py src\due_diligence_reporter\alpha_capacity_analysis.py tests\test_alpha_capacity_analysis.py
uv run mypy src\due_diligence_reporter\alpha_capacity_analysis.py
uv run mypy src\due_diligence_reporter\inbox_scanner.py src\due_diligence_reporter\alpha_capacity_analysis.py
node -c api\src\index.js
node -c api\src\rayconJobs.js
node -c scripts\deploy-raycon-cloud-run.mjs
git diff --check
```

Results: DDR pytest `474 passed` after the strict existing-artifact guard; DDR
ruff passed; DDR mypy passed for the latest touched Alpha Capacity module,
RayCon client, and RayCon follow-up script; RayCon
Vitest `106 passed` before the deploy planner and `109 passed` including
`deployManifest.test.js`; after the source-contract docs guard, RayCon
Vitest `110 passed` for `rayconJobs`, `jobsRoute`, and `deployManifest`.
After the partial-payload authority guard and Fastest Path cost-call capacity
input, the same RayCon focused suite reported `111 passed`; it still reports
`111 passed` after changing the authority test to the Miami Beach `114/199`
capacity payload.
DDR focused Alpha Capacity/RayCon client/follow-up pytest `148 passed`; DDR
inbox/Alpha Capacity/RayCon client pytest `86 passed`; broad affected DDR suite
`474 passed` after adding inbox capacity observability. DDR Alpha Capacity and
inbox ruff/mypy passed. RayCon syntax checks passed; DDR and RayCon
`git diff --check` passed with expected Windows LF-to-CRLF warnings only.

## 2026-06-18 - 35 E 62nd St DDR run

Request: run a new DDR for `35 E 62ND ST, New York, NY 10065`
(`site_id=k17fsrj9m5y8843d04x5nmf0ch88daws`, Drive folder
`1eYYvDFoXpHrcTBEHEakLE0waRuIHc-YA`).

Outcome:

- Normal DDR runs prepared SOR-ready DD data but stopped before DD Report
  rendering because `rhodes.due_diligence_update` failed.
- Latest normal run before SDK probing:
  `20260618183427-35-e-62nd-st-new-york-ny-10065-3fec8fc1`.
- Latest SDK-backed scoped pipeline probe:
  `20260618185254-35-e-62nd-st-new-york-ny-10065-503af102`.
- Status remains `report_data_prepared`; no DD Report `doc_id` or `doc_url`
  was produced.

Fix applied locally:

- Normalized DDR score fields before `updateDueDiligence` so LocationOS gets
  numeric enum values `1|2|3` for `regulatoryScore`, `buildingScore`,
  `playAreaScore`, and `schoolOperationsScore`.
- Focused validation passed:
  `uv run pytest tests\test_report_pipeline.py -q --basetemp C:\tmp\ddr-score-normalization-test-final2`,
  `uv run ruff check src\due_diligence_reporter\report_pipeline.py tests\test_report_pipeline.py`,
  and `uv run mypy src\due_diligence_reporter\report_pipeline.py`.

Remaining blocker:

- After score normalization, LocationOS rejects the normal HTTP write with
  `elicitation_unsupported`.
- An SDK-backed no-op `updateDueDiligence` probe negotiated MCP
  `2025-11-25` but returned a server tool error instead of emitting an
  elicitation callback: `Error: [Request ID: 298902344d8de90e] Server Error`.
- Repo docs and tests intentionally require the SOR write before rendering a
  DD Report, so the DDR should not be marked complete until LocationOS accepts
  the `updateDueDiligence` write and the pipeline can proceed to
  `report.render`.

## 2026-06-29 - M2 source packet and repo-owned runner containment

Request: implement the M2 Direct DD Source Packet and repo-owned runner plan.

Outcome:

- Added a repo-tracked M2 field/source matrix snapshot at
  `docs/reference/m2-diligence-field-source-matrix.json` and wired
  `source_packet.py` tests to assert the live-sheet field set and approved
  overrides. The active matrix now removes `ddr_attached`,
  `max_plan_mode_confirmed`, and `permit_of_record_confirmed`, and uses
  `fast_open_occupancy_type_confirmed` plus
  `max_plan_occupancy_type_confirmed` as schema-gap confirmation fields.
- Hardened the M2 source packet gate so registered-but-unmapped supporting docs
  block dependent field writes and create explicit open items. Schema-gap holds
  remain non-blocking once evidence is registered and mapped.
- Expanded source document coverage across classifier/readiness/source-sweep
  paths for Alpha Capacity, Outdoor Play Space, Opening Plan, Alpha Phasing,
  School Approval, KH traffic, CO/permit, measured floor plan, floor plan, and
  LiDAR source docs.
- Added repo CLI scheduler surfaces: `uv run ddr daily-check` and
  `uv run ddr source-sweep`. The scheduled daily and source-sweep workflows now
  call those CLI commands, while the existing scripts remain compatibility
  wrappers.
- Hard-disabled the `DD_REPORT_OWNER=pipeline` escape hatch for M2 execution.
  The env var is tolerated as legacy input but no longer delegates execution
  outside this repo.
- Removed active Braintrust wording from the runtime surfaces covered by tests.
  Historical references are not treated as active process guidance.
- Updated pytest config so the exact repo gate `uv run pytest` ignores local
  cache/temp directories and uses repo-local `.pytest-tmp`, avoiding Windows
  temp-root permission failures.

Validation:

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy src/
git diff --check
```

Results: pytest `1290 passed`; ruff passed; mypy passed for `49 source files`;
`git diff --check` passed with expected LF-to-CRLF warnings only.

Remaining operational proof:

- Repo inspection cannot prove that external historical Braintrust schedules are
  disabled. Before declaring the old scheduler fully retired operationally,
  verify outside the repo that no Braintrust job still invokes M2 DDR execution.
