# Due Diligence Reporter â€” How It Works

**Version:** 4.4.0
**Team:** EDU Ops Intelligence
**Last Updated:** 2026-06-29

---

## Overview

The Due Diligence Reporter is an AI agent powered by Claude that prepares direct Site Due Diligence (DD) fields and optional report views for potential Alpha School locations. It operates in interactive and automated modes.

Current V4.4 behavior:

- First-round readiness is `SIR found AND no existing DD source packet`. Missing vendor docs do not block the first direct-field publish.
- SIR candidate permit/phase traces are first-round handoff evidence, not a first-round publish blocker and not final Phase 1 / Phase 2 truth.
- Rhodes / LocationOS is the source of truth for site ID, Drive folder URL, P1 DRI / site owner, and registered document links.
- M2 closure uses registered supporting documents plus per-field DD writes. A rendered DDR is optional presentation, not the source of truth.
- The M2 source packet is complete only when required source docs are registered, writable DD fields are readback-verified, LocationOS schema gaps are explicitly held, and no required source gaps remain.
- Source-packet completion proves evidence registration and DD-field write/readback status; it does not replace human/legal validation for source-sensitive code path, education approval, permit, lease, cost, schedule, or phasing assumptions.
- Open verification items are stored as structured run state and rendered only as `Open Items to Verify` in the DDR body.
- Existing DD data can be refreshed when a core source type changes: vendor SIR, Building Inspection, RayCon scenario JSON, E-Occupancy report, School Approval report, Opening Plan, Alpha Capacity Analysis, Outdoor Play Space Report, Security Due Diligence report, Alpha Phasing Plan, KH traffic analysis, CO/permit of record, measured floor plan, or LiDAR.
- The active source sweep entrypoint is `uv run ddr source-sweep`; it scans active Rhodes sites with linked Drive folders and calls the shared `dd_republish` path. `scripts/vendor_doc_republish_sweep.py` remains a compatibility wrapper.

Legacy mode summary:

1. **Interactive** â€” A human gives it a site name in chat via MCP Hive. The agent gathers data, runs analytical skills, and produces an executive-ready Google Doc.
2. **Event-Driven (Inbox Scan)** -- A scheduled script scans the `edu.ops@trilogy.com` inbox for new site documents, matches them to Rhodes site records, uploads them to the matched site's M1 folder, and registers the Drive file on the Rhodes site record.
3. **Daily Sweep (Safety Net - 9 AM)** - The scheduled repo CLI command scans all site folders in active DD stages. When a site has an SIR / AI SIR and no existing DD source packet, it triggers first-round DD data preparation. M2 source packet documents upgrade the DD field/source packet path as they land.

The agent gathers facts. It does not make recommendations. The decision belongs to the leadership team.

---

## The V4 Report

The V4 DD view is a **structured executive one-pager** -- not the multi-page narrative of the original report. It uses structured checklists, pick-menu dimensions, and bare values instead of prose paragraphs. It is optional presentation; the registered source packet and LocationOS DD fields are the operating record.

**61 template tokens** across three sections:

| Section | Count | What it covers |
|---------|-------|----------------|
| **meta** | 8 | Site name, address, school type, marketing name, report date, prepared by, site ID, Drive folder link |
| **exec** | 45 | "Can this school be open in time for the current school year?" card, direct answer, two build scenarios, Alpha Phasing Plan summary, detailed cost breakdown, lease conditions, trade-offs |
| **sources** | 8 | Links to SIR, Building Inspection, Block Plan, site record, E-Occupancy Assessment, School Approval Assessment, Opening Plan, and Alpha Phasing Plan |

### Alpha Phasing Plan

Run `apply_alpha_phasing_plan_skill` after the source reads and the E-Occupancy, School Approval, and RayCon context are available, but before `prepare_due_diligence_data`. This is an enrichment step, not a first-round publish blocker and not part of the final vendor-readiness gate.

The tool publishes an Excel workbook in the site's M1 folder, auto-registers the workbook on the Rhodes site record as a `phasing` support document for the `acquireProperty` milestone when `site_id` is available, and returns a source link plus a compact Buildout Analysis summary:

- `sources.alpha_phasing_plan_link`
- `exec.alpha_phasing_phase_i_scope`
- `exec.alpha_phasing_phase_ii_scope`
- `exec.alpha_phasing_phase_ii_allowance`
- `exec.alpha_phasing_recommended_timing`
- `exec.alpha_phasing_quality_bar_status`

If the confirmed source of truth, quality-bar target, opening target date, Phase I opening scope, or Phase II deferred scope is missing, the tool returns concrete `verification.open_items` instead of creating generic Phase II line items.

### "Can this school be open in time for the current school year?" card

Four dimensions, each a fixed pick-menu:

| Dimension | Source | Options |
|-----------|--------|---------|
| `exec.c_answer` | Agent synthesis | Yes / No (binary) — the literal answer to "Can this be a school by [date]?". |
| `q2.e_occupancy_score` | E-Occupancy tool | Integer 0–100 emitted by `apply_e_occupancy_skill`. |
| `dd_risk_flags[]` (Phase 4) | Multiple | Canonical, deduped list of `{category, severity, source, summary}` derived from four upstream signals: `permit_history.risk_flags` (produced by the upstream AI SIR / source-evidence build — DDR no longer calls Shovels directly), `q2.ibc_flags` / `q2.e_occupancy_ibc_summary`, `q1.school_approval_zone`, and `sir.risk_watch`. Categories: `zoning`, `occupancy`, `ahj_history`, `parking`, `traffic`, `environmental`, `flood_zone`, `historic_district`, `accessibility`, `ed_reg`. Severity per source rule (see `risk_flags.py`). |
| `exec.c_zoning` | SIR | Permitted by right / Use Permit Required (Admin) / Use Permit Required (Public) / Prohibited |
| `exec.c_occupancy` | E-Occupancy skill | Has E-Occupancy / Change of use required, meets E-Occupancy / Change of use required, needs work |
| `exec.c_edreg` | School Approval skill | Not required / Required and have done / Required have not done |

### Build Scenarios and Delta Analysis

The report now uses three scenarios:

- `Fastest Open`: minimum work required to reach E-occupancy compliance. The token names remain `*_mvp_*` for backward compatibility.
- `Max Capacity`: the highest student count supportable by the space.
- `Max Value`: the highest capacity achievable for the least amount of money.

| Row | Fastest Open token | Max Capacity token | Max Value token |
|-----|---------------|-------------------|----------------|
| Capacity | `exec.fastest_open_capacity` | `exec.max_capacity_capacity` | `exec.max_value_capacity` |
| Cost | `exec.fastest_open_capex` | `exec.max_capacity_capex` | `exec.max_value_capex` |
| Timeline | `exec.fastest_open_open_date` | `exec.max_capacity_open_date` | `exec.max_value_open_date` |

Delta analysis compares each scenario against Fastest Open:

| Row | Max Capacity delta | Max Value delta |
|-----|-------------------|----------------|

Rules: capacity comes from Alpha Capacity Analysis or a RayCon scenario that
explicitly carries Alpha Capacity Analysis provenance; RayCon owns construction
cost and schedule. Cost = single midpoint number (not a range), timeline =
MM/YY format only, and sourced team notes may override cost or schedule numbers
only, not published capacity.

### Detailed Cost Breakdown

The report also carries a fixed-row cost table across the same three scenario columns. These rows are normalized from RayCon's variable category labels so the template layout stays stable:

- Demolition
- Framing / Doors
- MEP / Fire / Life Safety
- Plumbing / Bathrooms
- Finish Work
- Furniture
- Tech / Security / Signage
- Other Hard Costs
- Soft Costs
- GC Fee
- Contingency
- Grand Total

### Notes for Acquistion Negoations and Risk Notes

Two separate tokens with distinct classification rules:

| Token | Purpose | Classification test |
|-------|---------|-------------------|
| `exec.acquisition_conditions` | TI allowance ask OR landlord-must-fix items | Type A: quantifiable buildout cost â†’ negotiate TI. Type B: landlord's existing obligation â†’ not acceptable in current state. |
| `exec.risk_notes` | Confirmed document findings that threaten timeline or viability | "Did we actually find this in the documents AND does it directly threaten timeline or viability?" |

Both require clean source notes. `risk_notes` must tie back to a specific document finding -- no speculative or generic items.

---

## Architecture at a Glance

```
                              â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                              â”‚  Inbox Scan workflow â”‚
                              â”‚  scan_inbox.py            â”‚
                              â”‚  three-tier classifier    â”‚
    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”‚  (regex â†’ GPT-4o-mini)    â”‚
    â”‚  Daily Sweep (9 AM) â”‚   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
    â”‚  ddr daily-check   â”‚                â”‚
    â”‚  (active stages only)â”‚                â”‚
    â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜                â”‚
               â”‚                           â”‚
Human (chat)   â”‚       report_pipeline.py  â”‚  shared pipeline module
    â”‚          â”‚          â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
    â”‚          â”‚          â”‚
    â”‚  docs/prompts/prompt_v4.md â”‚  same tools, same prompt
    â–¼          â–¼          â–¼
Claude AI Agent â—„â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
    â”‚
    â”‚  calls tools via MCP protocol (stdio) or direct Python
    â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  MCP Server  (FastMCP / Python)                          â”‚
â”‚  dd-reporter â€” 13 tools                                  â”‚
â”‚                                                          â”‚
â”‚  Tools:                                                  â”‚
â”‚  â”œâ”€ lookup_rhodes_site_owner (Rhodes P1 DRI lookup)       â”‚
â”‚  â”œâ”€ list_drive_documents     (Drive + shared folders)    â”‚
â”‚  â”œâ”€ read_drive_document      (Drive file reader)         â”‚
â”‚  â”œâ”€ apply_alpha_capacity_analysis_skill (Capacity source)â”‚
â”‚  â”œâ”€ apply_outdoor_play_space_skill (Outdoor source)      â”‚
â”‚  â”œâ”€ apply_e_occupancy_skill / school_approval_skill      â”‚
â”‚  â”œâ”€ create_dd_report         (Optional report view)      â”‚
â”‚  â”œâ”€ check_site_readiness     (Doc presence gate)         â”‚
â”‚  â”œâ”€ check_report_completeness (Token scan)               â”‚
â”‚  â”œâ”€ check_site_readiness     (Doc presence gate)         â”‚
â”‚  â”œâ”€ generate_marketing_pack  (MatterBot rendering)       â”‚
â”‚  â”œâ”€ save_skill_report        (Publish assessment to Drive)â”‚
â”‚  â””â”€ send_dd_report_email     (Gmail SMTP)                â”‚
â”‚                                                          â”‚
â”‚  Report Schema:                                          â”‚
â”‚  â””â”€ report_schema.py         (70 tokens + alias map)     â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
               â”‚
    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
    â–¼          â–¼              â–¼           â–¼
 Google Drive  Google          Building    Gmail SMTP +
 (v4)       Drive/Docs/     Optimizer   Google Chat
            Gmail (OAuth)   Pricing API Webhook
```

---

## Inbox Scanner â€” Event-Driven Pipeline

**Script:** `scripts/scan_inbox.py`
**Module:** `src/due_diligence_reporter/inbox_scanner.py`
**Schedule:** See `.github/workflows/inbox-scan.yml`
**Workflow:** `.github/workflows/inbox-scan.yml`

The inbox scanner is one trigger for report updates. When a vendor emails a supported DD source document to `edu.ops@trilogy.com`, the scanner files it to the matched Rhodes site's M1 folder, registers the Drive file as a Rhodes document, and can trigger in-place DDR republish if a DDR already exists.

### Phase 1 â€” Scan, Classify, Upload

```
For each unprocessed email with PDF attachments:
  1. Extract email metadata (subject, sender, body snippet, attachments)
  2. Classify each PDF by filename using the three-tier classifier:
     - Tier 1: regex keyword matching (free, instant)
     - Tier 2: GPT-4o-mini on filename if Tier 1 returns unknown (~$0.001)
     - Tier 3: GPT-4o-mini on first-page text if Tier 2 returns unknown (~$0.002)
     - Output: doc_type ("sir", "building_inspection", "isp", or unknown), confidence 0.0â€“1.0
  3. If confidence >= 0.7 and doc_type is supported:
     a. Generate date-prefixed filename: "{date} - {original_filename}"
     b. Check for duplicates in target shared folder
     c. Upload to correct shared Drive folder (SIR â†’ SIR folder, BI â†’ BI folder, ISP â†’ ISP folder)
  4. If confidence < 0.7 â†’ flag for manual review and write a Rhodes decision note when the site is known
  5. Mark email as processed (DD-Processed label)
  6. If any SIR was uploaded â†’ send SIR arrival email to jake.petersen@trilogy.com,
     joshua.rockers@trilogy.com, edu.ops@trilogy.com
```

**Supported doc types:** `sir`, `building_inspection`, `isp`, `block_plan`.

**Current M1 filing and Rhodes registration behavior:** Supported documents are matched to a Rhodes site record, uploaded to the site's `M1 - Acquire Property` Drive folder, and then registered on the Rhodes site record. The registration mapping is:

| DDR doc type | Rhodes docType | Milestone |
|--------------|----------------|-----------|
| `sir` | `siteInvestigationReport` | `acquireProperty` |
| `building_inspection` | `propertyConditionAssessment` | `acquireProperty` |
| `block_plan` | `floorPlan` | `acquireProperty` |
| `isp` | `other` | `acquireProperty` |
| `opening_plan_report` | `other` | `acquireProperty` |
| `alpha_phasing_plan_report` | `phasing` | `acquireProperty` |
| `alpha_capacity_analysis` | `capacityCalculation` | `acquireProperty` |
| `outdoor_play_space_report` | `other` / quality bar `outdoorRecreation` | `acquireProperty` |
| `security_due_diligence_report` | `other` | `acquireProperty` |
| `school_approval_report` | `regulatoryApproval` | `acquireProperty` |
| `certificate_of_occupancy` | `certificateOfOccupancy` | `acquireProperty` |
| `permit_of_record` | `permit` | `acquireProperty` |
| `measured_floor_plan` | `floorPlan` | `acquireProperty` |
| `lidar` | `lidar` | `acquireProperty` |
| `traffic_analysis` | `other` / quality bar `transportation` | `acquireProperty` |

Current Phase 2 behavior uses the matched Rhodes site record from Phase 1 (`site_title`, `matched_site_id`, address, and Drive folder URL). Older references below to site matching being inactive are stale and retained only until this process doc is fully cleaned up.

Rhodes registration is a non-blocking post-upload side effect, but approval-gated or unavailable registration paths use the document-registration handoff pattern. After DDR has created or found the artifact and has a human-openable Drive URL, it attempts `registerDocument`. If the registration path cannot complete because it needs approval, OAuth, or another eligible LocationOS/Aerie handoff, DDR writes and reads back one grouped Rhodes site note for the owner. Multi-artifact skills group all eligible document-registration failures from that run into one note instead of writing one note per artifact. The note body is optimized for copy/paste into Aerie: `Site:`, `Address:`, `Documents to register:`, then each document display name and Drive URL. File IDs, doc types, milestones, blockers, Gmail IDs, and task keys stay in the receipt/manifest. When the note and owner mention read back, the automation-owned work can complete with `rhodes_registration_status=pending_user_action`, `human_followup_required=true`, and `human_followup_type=document_registration`. Missing artifact URLs, ambiguous site identity, unresolved owner/fallback routing, note-write failure, or note-readback failure remain hard blockers.

Registration retry state is persisted through a store boundary. Local development defaults to `.rhodes_registration_retry_state.json`; scheduled/production runs should set `RHODES_RETRY_STATE_STORE=firestore` and `RHODES_RETRY_STATE_FIRESTORE_PROJECT_ID=<project>` so retry attempts, Rhodes note IDs, and Google Chat fallback metadata survive runner changes. GitHub Actions can provide those values through repository variables plus the optional `GCP_FIRESTORE_SERVICE_ACCOUNT_JSON` secret, which is written to `GOOGLE_APPLICATION_CREDENTIALS` for the scan step. If Firestore is unconfigured or unavailable, the scanner falls back to the local JSON file and keeps filing documents.

DD Report republish dedupe state uses the same store pattern. Local development defaults to `.dd_republish_state.json`; scheduled/production runs should set `DD_REPUBLISH_STATE_STORE=firestore` and `DD_REPUBLISH_STATE_FIRESTORE_PROJECT_ID=<project>` so inbox-scan, RayCon follow-up, and vendor-source sweeps share durable dedupe state across runner changes. Successful Firestore saves refresh the local JSON file so the existing GitHub Actions cache remains a current fallback. If Firestore is unconfigured or unavailable, the workflows continue with the local JSON/cache state.

RayCon follow-up runtime state is also behind a store boundary. Local development defaults to `.raycon_dispatch_state.json` and `.raycon_followup_alerts.json`; scheduled/production runs should set `RAYCON_RUNTIME_STATE_STORE=firestore` and `RAYCON_RUNTIME_STATE_FIRESTORE_PROJECT_ID=<project>` so RayCon dispatch dedupe and stuck-site owner/Chat notification suppression survive runner changes. Successful Firestore saves refresh the local JSON files so the existing GitHub Actions cache remains a current fallback.

Material automation outcomes render through `src/due_diligence_reporter/automation_event.py`. DDR-owned site notes use concise user-facing headers such as `DD report update`, `DD report candidate review`, `Document intake review`, `Document filing review`, `Source document review`, `DDR source review`, `DD report republish review`, and `RayCon follow-up review`. The visible note body does not include raw Rhodes errors, request IDs, run IDs, document IDs, internal guard reasons, long field lists, Gmail IDs, trace links, or source file IDs. Those technical details stay in the run manifest, returned event status, and structured `action_record.v1` telemetry. Legacy non-DDR automation notes may still use the generic `AutomationEvent v1` body. If a decision is required and the P1 DRI cannot be mentioned in Rhodes, the same concise event body is posted to the configured Google Chat webhook.

The read-only `portfolio_automation_gap_snapshot` MCP tool rolls those Rhodes records up across active sites. It reads Rhodes-linked Drive folder status, the site's current P1 milestone, Rhodes' milestone-specific missing-document breakdown, automation-review note headers, pending automation review tasks, and P1 DRI assignment status, then returns per-site gap reasons plus portfolio totals. Missing source documents remain available as site context in the raw snapshot, but they are not treated as Portfolio Gaps because incomplete document coverage is normal DDR operating work. It does not write to Rhodes, Drive, Gmail, or Chat. Operators can run the same check with `uv run ddr portfolio-gaps`; the `Portfolio Automation Gaps` workflow runs the check on weekdays, stores both a text summary and JSON artifact, and posts a compact Google Chat summary when the Rhodes-backed snapshot contains non-document gaps.

Portfolio Gaps emits ActionRecord telemetry for non-document gaps such as missing P1 DRI, missing Drive folder, open automation failures, pending review tasks, and Rhodes snapshot read errors. DDR document registration and readback health remain under Drive-to-Rhodes reconciliation telemetry rather than Portfolio Gaps.

**Drive-to-Rhodes reconciliation:** `scripts/drive_rhodes_reconciliation.py` is the safety sweep for files that already exist in a site's `M1 - Acquire Property` folder. It loads Rhodes-linked sites, scans each M1 folder, classifies recognized source files, and idempotently registers missing Rhodes document records by Drive file ID. The scheduled `Drive Rhodes Reconciliation` workflow runs this backfill on weekdays; manual runs can pass `--dry-run` / workflow `dry_run=true` to report what would be registered without writing to Rhodes. Generated support documents are registered when they have an explicit mapping, such as Alpha Phasing Plan -> `phasing` / `acquireProperty`; unmapped reports are surfaced as skipped rows instead of being forced into unsafe Rhodes document types.

### Phase 2 â€” Per-Site Pipeline

After all uploads complete, the scanner attempts to run the DD field/source-packet pipeline for each site that received a new document. Phase 2 requires a `site_title` to look up the site context. The current classifier routes by `doc_type` only and does not match files to sites, so `site_title` is `None` in all uploads - **Phase 2 is currently inactive** and first-round source-packet preparation falls to the daily sweep.

When site matching is re-enabled (e.g., via a future `matched_site_id` returned from classification), Phase 2 will run:

```
For each unique site that received an upload:
  1. Look up site context â†’ get Drive folder URL â†’ build match terms
  2. Refresh shared folder cache (picks up just-uploaded files)
  3. Run process_site_pipeline():
     a. Check readiness (SIR + Inspection present? ISP is informational only)
     b. If missing docs â†’ post Google Chat alert with checklist
     c. If an M2 source packet exists and no source changed -> skip
     d. If ready -> trigger Claude agent loop -> register supporting docs -> prepare DD fields -> optional report view
  4. Post result to Google Chat
```

**Flags:**
- `--scan-only` â€” Run Phase 1 only (inbox scan), skip the pipeline
- `--dry-run` â€” Classify and match without uploading or marking emails

---

## Document Detection â€” Three-Tier Strategy

The system must find three source documents before generating a report. Documents can live in two places.

### Primary: Shared Drive Folders

Three shared folders hold documents across all sites:

| Doc Type | Folder | Config Key |
|----------|--------|------------|
| SIR (Site Investigation Report) | `1TTjxOEfjeJZoXMAeGueJ1QbVBzXBDE4C` | `SIR_FOLDER_ID` |
| ISP (Instant School Plan) | `1E9RXgVeKxeITUdFw5lvyolCx6CJLEFUg` | `ISP_FOLDER_ID` |
| Building Inspection | `15dfKaAnic9VRKhp_-vFSpTr7uPk_hhKo` | `BUILDING_INSPECTION_FOLDER_ID` |

Files are matched by checking if any of the site's match terms (site title, city name, street number) appear as a case-insensitive substring in the filename. When substring matching fails, an LLM fallback (`match_file_to_site_llm()`) fuzzy-matches filenames to the site.

**PDF mime preference:** When multiple files match for a doc type, `application/pdf` is preferred over `application/vnd.google-apps.document` (Drive auto-converts PDFs to Docs; the system wants the original).

### Fallback: Site's Own Drive Folder

If a document isn't found in the shared folders, the system searches the site's root folder recursively (`list_files_recursive(folder_id, max_depth=2)`). Files are classified using a three-tier classification pipeline in `classifier.py`:

| Tier | Method | Cost |
|------|--------|------|
| 1 | Regex keyword matching (`classify_by_keywords`) | Free, instant |
| 2 | GPT-4o-mini on filename (`classify_by_filename_llm`) | ~$0.001/call |
| 3 | GPT-4o-mini on first-page PDF text (`classify_by_content_llm`) | ~$0.002/call |

Only escalates when the previous tier returns unknown/low confidence. Falls back to regex if OpenAI is unavailable.

### Readiness Gate

The first-round report is generated when these conditions are met:

```
ready_for_report = sir_found AND NOT report_exists
```

The full-report diagnostic still tracks vendor SIR, Building Inspection,
and RayCon scenario readiness. Missing vendor inputs no longer block the
first publish; they are logged as open verification items and the report
republishes as authoritative inputs arrive. ISP remains informational.

RayCon readiness distinguishes a physically present `raycon_scenario.json`
from a usable one. A JSON with `status: failed` or `validation.passed: false`
is treated as `failed_validation`, not as a successful RayCon input. Failed
RayCon fields render as `RayCon validation failed` in the partial DDR banner
and table cells, and full-report readiness waits for a valid replacement
`raycon_scenario.json`.

### SIR Learning Loop

Readiness also records non-blocking SIR comparison metadata. When both an
AI-generated SIR and a CDS/vendor SIR are present, the pipeline writes a
`sir.learning_review` step with status `ready_for_review`. Missing pairs are
recorded as `waiting_for_cds_sir`, `waiting_for_ai_sir`, or `not_applicable`.

This does not change report readiness. It makes recent AI/CDS SIR pairs visible
in the run manifest, Google Chat observability lines, and `ddr status` so the
team can run the review process in `docs/process/sir-learning-loop.md`.

---

## Step-by-Step Workflow

### Step 1 â€” Receive Request (Interactive) or Trigger (Automated)

**Interactive mode:** Human provides a site name in chat. The agent begins the workflow.

**Inbox scan mode:** `scan_inbox.py` detects a new upload and triggers the pipeline for that specific site.

**Daily sweep mode:** `uv run ddr daily-check` fetches all site folders, filters to active DD stages only ("1. Looking for Sites" and "2. Evaluating Potential Sites (LOI)"), pre-fetches the three shared Drive folders once, then checks each site's readiness.

---

### Step 2 -- Identify Site Folder Context

**Input:** supplied site name, address, and Drive folder URL

**Activity:**
1. Uses the supplied site name and address as the report identity
2. Uses the supplied or scanned Drive folder URL as the document source
3. Builds match terms for shared source-folder matching

**Output:** Site title, address, Drive folder URL, and shared-folder match terms.

---

### Step 3 â€” Check Document Availability

**Tool:** `check_site_readiness(site_name)`

**Activity:**
1. Searches shared Drive folders for SIR, ISP, and Building Inspection using site match terms
2. Falls back to recursive site folder search with three-tier classification
3. Checks if a DD report already exists
4. Records SIR learning-review state when AI/CDS SIR candidates are present
5. Returns presence booleans, file metadata, review metadata, and missing doc list

**Output:** `sir_found`, `isp_found`, `inspection_found`, `report_exists`, `sir_learning_review`, `files` dict with `name`/`id`/`webViewLink` per doc type, and `missing_docs` list.

---

### Step 3.5 -- Resolve Rhodes P1 DRI / site owner

**Tool:** `lookup_rhodes_site_owner(site_name, site_address)`

**Activity:**
1. Resolves the supplied site name/address against Rhodes / LocationOS
2. Reads the matched site record
3. Pulls `p1Dri.name` and `p1Dri.email` into the DDR as `meta.prepared_by` and the P1 email recipient

**Key rule:** Rhodes is the owner source of truth. If no Rhodes site or P1 DRI is found, the report uses `[Not found - P1 DRI not assigned]` and continues from the supplied site/address/Drive context.

---

### Step 4 â€” Read Source Documents

**Tool:** `read_drive_document(file_id, file_name)` â€” called once per document

**Activity per file:**
- **Google Docs / Sheets / Slides** â€” exported as plain text via Drive API
- **PDFs** â€” downloaded as bytes, text extracted with `pypdf` (large docs truncated to ~15,000 chars for context)
- **Plain text** â€” downloaded directly

**Key documents and what the agent extracts:**

| Document | Extracted Data |
|----------|---------------|
| **SIR** | Zoning, AHJ contacts, permits required, pre-app meeting, permit timeline, cost risks, schedule risks |
| **Building Inspection** | Year built, construction type, stories, SF, sprinklers, fire alarm, ADA deficiencies, egress, restrooms, scope of work, conversion risk level |
| **ISP (Program Fit Analysis)** | Room list with types/sqft, program fit score, classroom count, ADA pre-check score, optimization proposals |
| **Matterport** | 3D scan link |

---

### Step 5 â€” Run Skill Tools

M2 skill tools analyze the source data and produce structured outputs. E-Occupancy, School Approval, Opening Plan, Outdoor Play Space, Alpha Capacity Analysis, Security Due Diligence, and Alpha Phasing each produce or identify source artifacts that must be registered before related DD fields write. These enrichment tools should run after source reads and before `prepare_due_diligence_data`.

**E-Occupancy Skill** â€” `apply_e_occupancy_skill(building_type_description, stories, ..., ibc_occupancy_group, fire_area_sqft, has_below_grade_space, already_sprinklered, construction_type, max_travel_distance_ft, existing_exit_count, projected_occupant_load, site_name, drive_folder_url)`
1. Loads the hosted `ease-of-conversion` skill and rating-band reference from Ops-Skills (`OPS_SKILLS_REPO_PATH`, the sibling Ops-Skills checkout, or the installed Ops Skills plugin cache)
2. Matches building type against the DDR deterministic scoring matrix
3. Applies IBC group override (H -> score 0, I -> cap 20)
4. Applies height ceiling and tenant deductions
5. Runs IBC compliance gates: sprinkler requirement, travel distance, exit count, construction type
6. Returns score (0-100), zone (GREEN/YELLOW/ORANGE/RED), tier, confidence, hosted skill provenance, `ibc_gates`, `ibc_flags`, and `q2.e_occupancy_ibc_summary`
7. Publishes assessment -> `sources.e_occupancy_link`
8. Phase 4: `ibc_flags` and `q2.e_occupancy_zone` remain available in report data for downstream analysis.

**School Approval Skill** â€” `apply_school_approval_skill(address, state, site_name, drive_folder_url)`
1. Loads the hosted `school-approval` skill from Ops-Skills (`OPS_SKILLS_REPO_PATH`, the sibling Ops-Skills checkout, or the installed Ops Skills plugin cache)
2. Applies the hosted skill baseline/rules version for approval type, archetype, gating status, timeline, and required steps
3. Publishes assessment â†’ `sources.school_approval_link`

**Opening Plan** - `apply_opening_plan_skill(site_name, site_address, sir_content, drive_folder_url, site_id, school_approval_data, building_inspection_content, target_open_date)`
1. Runs the deterministic Pass 1 Opening Plan v2 workflow from the SIR baseline, with School Approval and Building Inspection text when available.
2. Reuses an existing M1 Opening Plan when present, so republish runs do not create duplicates.
3. Publishes a Google Doc in the site's M1 folder and registers it on Rhodes as `other` / `acquireProperty` when the pipeline has a `site_id`.
4. Returns `sources.opening_plan_link` for inclusion in the DDR Referenced Reports table.
5. Owns the final Fast Open / Max Plan opening dates and the final Permit-Scope Trace / Phase Scope Register. The Cost/Timeline Estimate remains required supporting schedule/cost evidence for source-packet writes; it does not override the Opening Plan's final opening-date call.

**Alpha Capacity Analysis** - `apply_alpha_capacity_analysis_skill(site_name, site_address, block_plan_content, drive_folder_url, block_plan_file_id, total_building_sf)`
1. Loads hosted `alpha-capacity-analysis` skill instructions and Microschool / 250+ rulesets from Ops-Skills.
2. Uses the full extracted Block Plan text plus the Block Plan PDF itself when Drive file context is available, so image-only PDFs do not silently skip the capacity source of truth.
3. Requires both capacity counts to mark the result successful; otherwise it returns `insufficient_evidence` with concrete open items.
4. Publishes `Alpha Capacity Analysis - <site> - <block_plan_file_id>.json` in M1 when Drive context is available and registers it as `capacityCalculation` when `site_id` is available.
5. RayCon consumes this JSON artifact for published capacity and student-scaled pricing; RayCon remains responsible for construction cost, schedule, and calculator audit evidence.
6. If neither an existing artifact nor a newly generated artifact has both counts, DDR records `dispatch_skipped=capacity_analysis_not_available` and does not dispatch a no-capacity RayCon job from the automated Block Plan path.

**Outdoor Play Space** - `apply_outdoor_play_space_skill(site_name, site_id, address, drive_folder_url, student_count, ...)`
1. Runs `ops-skills:outdoor-play-space` with `--skip-drive-upload`.
2. Uses Max Plan capacity for final scoring. Fast Open capacity is interim screening only unless Max Plan capacity is explicitly not applicable.
3. Uploads the generated Markdown, JSON, PNG, and HTML artifacts to the site's M1 folder through DDR's Drive client.
4. Registers those artifacts on Rhodes as `other` under quality bar `outdoorRecreation`.
5. Returns `exec.play_area_score`, `exec.play_area_comment`, `supporting_documents`, and field-source metadata.

**Security Due Diligence** - `ops-skills:security-due-diligence`
1. Runs after a Block Plan or Floor Plan source and Alpha Capacity Analysis are present.
2. Produces a one-page pre-lease security go/no-go memo covering securability, lease alteration rights, adverse neighbors/co-tenants, and weapons posture.
3. The memo is filed to M1 and registered as `security_due_diligence_report` / `other` / `acquireProperty`.
4. It is source-packet evidence only today; it does not write LocationOS DD fields until a field owner/schema exists.

**Alpha Phasing Plan** - `apply_alpha_phasing_plan_skill(site_name, site_address, drive_folder_url, source_of_truth, quality_bar_target, opening_target_date, must_complete_before_opening, deferred_scopes, ...)`
1. Loads the hosted `alpha-phasing-plan` skill from Ops-Skills.
2. Requires confirmed Phase I opening scope and confirmed Phase II deferred scope.
3. Publishes a workbook with Executive Summary, Quality Bar Matrix, Phase I Budget Schedule, Phase II Budget Schedule, Render Deck Inputs, and Source Notes tabs.
4. Registers the workbook on Rhodes as `phasing` / `acquireProperty` when the pipeline has a `site_id`.
5. Returns `sources.alpha_phasing_plan_link` and compact `exec.alpha_phasing_*` summary fields.
6. If minimum inputs are missing, returns concrete `verification.open_items` and does not publish generic Phase II scope.
7. DDR normalizes `phase-1-phase-2`, `Phase 1 Phase 2`, `Phase Scope Register`, and `phasing` document aliases to the canonical `alpha_phasing_plan_report` source type so the M2 source packet can unlock CapEx and building fields.

---

### Step 6 - Prepare Direct DD Fields and M2 Source Packet

**Tool:** `prepare_due_diligence_data(site_name, drive_folder_url, report_data, token_evidence=evidence, supporting_documents=supporting_documents)`

**Activity:**

1. **Normalize report_data** - `normalize_report_data()` from `report_schema.py`:
   - Flattens nested dicts to dot-separated keys
   - Injects defaults for `meta.site_name` and `meta.report_date`
   - Applies the alias map to translate known agent key variations to canonical token names
   - Filters to only keys matching the 34 canonical template tokens
   - Returns diagnostics: replacements applied, unmatched keys, unfilled tokens, token sources

2. **Compute deltas** - Server-side computation of the Max Capacity and Max Value delta columns against Fastest Open.

3. **Build source packet** - Combines `supporting_documents[]` with normalized values to produce `dd_field_updates[]`, source holds, and concise source-note lines.

4. **Return diagnostics** - Returns applied replacement counts, unmatched keys, unfilled tokens, normalized report data, and the M2 source packet to the pipeline.

**Skill output contract:** analytical skills must return machine-readable `supporting_documents[]` entries, not only prose statements that an artifact was linked. Each entry needs a canonical `source_type`, title, Drive link or file ID, Rhodes doc type, and registration/readback status so the source packet can decide which DD fields are writable.

**Token evidence:** As the agent reads each source document, it builds a parallel `evidence` dict recording the raw excerpt supporting each token value. Evidence is kept in the local run manifest instead of publishing a companion trace file.

**Output:** Normalized DD data + diagnostics + M2 source packet.

**Direct-field split:** The current agent contract calls
`prepare_due_diligence_data(site_name, drive_folder_url, report_data,
token_evidence=evidence, supporting_documents=supporting_documents)` before any
Google Doc is rendered. That tool performs deterministic normalization and
builds the M2 source packet without creating a document. The pipeline writes
only fields whose required source docs are registered and readback-capable. It
holds LocationOS schema-gap fields explicitly. `create_dd_report` remains a
legacy/optional render path for a human-readable view.

---

### Step 7 - Optional Report View Completeness

**Tool:** `check_report_completeness(doc_id)`

**Activity:** This step runs only when `create_dd_report` is used to render a human-readable view.
1. Exports the generated Google Doc as plain text
2. Scans for two patterns:
   - `{{token}}` â€” a template placeholder that was never filled (hard block â€” do not send)
   - `[Not found â€” ...]` â€” a sourced gap label where the agent tried but data wasn't available (acceptable)

**Decision:** If any `{{token}}` hard blocks remain, the report is flagged as incomplete. If only `[Not found â€” ...]` labels remain, the report is ready to send.

---

### Step 8 -- Record Rhodes Report Event

**Tool:** `addNote` through the Rhodes MCP client

**Source-read activity:** When the agent trace shows an unreadable SIR or Building Inspection, the `source.alert` step records a concise `Source document review` note on the Rhodes site before failing the step for operator follow-up. The note asks the owner to review unreadable source documents, repair or re-upload them, and rerun DDR. Run IDs, Drive folders, trace links, and detailed source-read issue rows stay in the run manifest and structured event status.

**Source-read fallback:** If the pipeline does not know the Rhodes site ID, Rhodes cannot create the note, or the P1 DRI cannot be mentioned, the same `source_review_required` event body is posted to the configured Google Chat webhook.

**Vendor-gate activity:** When the full vendor input set is present (vendor SIR, vendor Building Inspection, and a usable RayCon Scenario JSON) but report generation still fails or the generated report remains incomplete, the pipeline records a concise `DDR source review` site note in Rhodes. The note names the required input set and asks the owner to repair the source issue and rerun DDR. Run IDs, raw failure reasons, Drive folders, and trace links stay in the run manifest and structured event status.

**Vendor-gate fallback:** If the pipeline does not know the Rhodes site ID, Rhodes cannot create the note, or the P1 DRI cannot be mentioned, the same `vendor_gate_review_required` event body is posted to the configured Google Chat webhook.

**RayCon failed-validation activity:** When RayCon follow-up sees a failed
`raycon_scenario.json`, it records a `raycon_followup_alert` note in Rhodes
even if it also starts an automatic retry. The note asks the owner to review
RayCon scenario generation and rerun follow-up after the source issue is fixed.
RayCon failure messages, run IDs, Block Plan file IDs, and Drive folders stay
in the state file and structured event status. If no site owner can be
mentioned, the same concise event body goes to the configured Google Chat
webhook.

**Activity:** When the pipeline has normalized due-diligence data from `prepare_due_diligence_data`, it calls the Rhodes / LocationOS `updateDueDiligence` writer with only the fields allowed by the M2 source packet. Interim writes use `status=data-gathering` and intentionally leave `dateCompleted` and `ddReportLink` blank. Source-triggered updates with open verification items still unresolved use `status=follow-up`. The workflow writes `status=complete` and `dateCompleted` only when the M2 source packet is complete and no open verification items remain. Whether the write succeeds or fails, the next Rhodes note tags the P1 DRI with what was written or what failed and includes concise field -> value -> source document lines from the source packet. If the due-diligence write fails, the workflow records `rhodes.due_diligence_update` as failed, suppresses the success email, and records a decision-required `rhodes.report_event` note/fallback alert so the P1 DRI can repair the SOR write.

After the due-diligence write attempt, the pipeline records a site note in Rhodes. Source-packet notes are user-facing: they put the operator ask first, state whether DD fields were written or held, include concise field -> value -> source document lines, show only the open-item count and missing vendor docs, and give short next steps. They do not render raw Rhodes errors, request IDs, run IDs, document IDs, internal guard reasons, or long field lists into the site note. Technical context such as run ID, trigger source, document IDs, attempted Rhodes fields, raw request errors, and note readback stays in the run manifest and structured `action_record.v1` telemetry. It mentions the P1 DRI when a Rhodes user can be resolved from the owner context. To avoid repeating the same open-ask notification while operators wait on outside answers, decision-required notifications without a new Rhodes due-diligence write are capped at once per site every two business days.

Run manifests emit structured `action_record.v1` facts for the successful SOR write (`ddr_sor_updated`), successful Rhodes note (`ddr_p1_note_created` or `ddr_rhodes_note_created`), failed or blocked steps, and open verification items. Successful and failed Rhodes-note records include a structured `technical_context` block for debugging, so the user-facing note can stay concise without losing run IDs, request IDs, document IDs, or raw Rhodes error detail. If Rhodes owner lookup finds the site but no P1 DRI, the workflow emits a queued `missing_p1_dri` action record with `owning_workflow=aadp` and does not attempt a direct AADP assignment.

**Fallback:** If open verification items require a decision and the P1 DRI cannot be mentioned in Rhodes, the pipeline posts the same event body to the configured Google Chat webhook. If Rhodes cannot be written at all, the run records a failed `rhodes.report_event` step so the manifest shows that the system of record was not updated.

---

### Step 9 â€” Send Email Notification

**Tool:** `send_dd_report_email(site_name, report_url, key_findings, additional_recipients)`

**Activity:** Sends an HTML email to configured recipients (base list + Rhodes P1 DRI when found) with the site name, key findings summary, and a link when an optional DD report view exists. The shared pipeline suppresses interim source-triggered emails while vendor inputs or open verification items remain. Skipped interim emails are recorded as `notify.email` skipped steps in the run manifest.

---

## Shared Report Pipeline

**Module:** `src/due_diligence_reporter/report_pipeline.py`

The report pipeline module contains all shared logic used by both the inbox scanner and the daily sweep:

| Function | Purpose |
|----------|---------|
| `TOOL_DEFINITIONS` | Tool schema list for Claude API calls |
| `route_tool_call()` / `route_tool_call_sync()` | Async/sync tool router mapping to server.py functions |
| `list_shared_folders_once(gc)` | Pre-fetch SIR/ISP/Inspection shared folder files |
| `match_site_in_shared_cache(terms, cache)` | Find docs for a site in pre-fetched cache |
| `check_site_readiness_direct(gc, url, terms, cache)` | Readiness check bypassing MCP layer |
| `run_dd_report_agent(site_title, prompt)` | Claude agentic loop (up to 40 iterations) |
| `process_site_pipeline(gc, title, url, terms, cache, prompt, settings)` | Full pipeline: readiness -> source packet -> direct DD write -> optional report view/email gate |
| `post_pipeline_result(webhook_url, result, url)` | Google Chat notification per result |
| `PipelineResult` | Dataclass with status, missing_docs, doc_id, doc_url, etc. |

**Pipeline statuses:** `waiting_on_docs`, `report_data_prepared`, `report_exists`, `report_created`, `report_incomplete`, `generation_failed`, `error`

---

## Daily Sweep (Safety Net)

**CLI:** `uv run ddr daily-check`
**Compatibility wrapper:** `scripts/daily_dd_check.py`
**Schedule:** 9 AM Central, Monday-Friday (GitHub Actions cron: `0 14 * * 1-5` UTC)
**Workflow:** `.github/workflows/daily-dd-check.yml`
**Agent model:** `claude-sonnet-4-6`

**Stage filter:** Only processes sites in these Overall Site Stages:
- `1. Looking for Sites`
- `2. Evaluating Potential Sites (LOI)`

Sites in later stages (FTO in progress, FTO signed, operational) are skipped.

**Flow per site:**

```
For each site folder in the Drive root:
  1. Check readiness (SIR found and no existing DD source packet; missing vendor docs become open verification items)
  2. If missing first-round docs -> post Google Chat alert listing what's missing
  3. If an M2 source packet exists and no source changed -> skip
  4. If ready -> run Claude agent loop:
     a. check_site_readiness -> list_drive_documents -> read available first-round sources
     b. apply_alpha_capacity_analysis_skill -> apply_outdoor_play_space_skill -> apply_e_occupancy_skill + apply_school_approval_skill -> apply_opening_plan_skill -> apply_alpha_phasing_plan_skill
     c. prepare_due_diligence_data with supporting_documents (normalize DD data and build M2 source packet)
     d. create_dd_report only when an optional DD report view is needed
     e. check_report_completeness only for optional report views
     f. Write allowed LocationOS DD fields and append the source-packet site note
     g. Send email only when the email gate passes
     h. If blocked -> post Google Chat alert with source-packet open items
```

**Optimization:** Shared folder file lists are fetched once at the start and reused for all sites, avoiding redundant API calls.

---

## Sourced Gap Labels

The bare word `[Pending]` is banned. When a field can't be filled, the agent uses a sourced gap label that names exactly what was checked:

```
[Not found â€” {source checked}]
```

Examples:
- `[Not found â€” SIR did not include AHJ contact]`
- `[Not found â€” ISP not yet in shared Drive folder]`
- `[Not found â€” building inspection did not state year built]`

This tells `check_report_completeness` and human reviewers exactly why each field is empty.

---

## What the Agent Will Not Do

- **Make lease or buy recommendations.** It presents data. The executive team decides.
- **Editorialize.** No "well below standard", "executive review recommended", or "consider before proceeding" language.
- **Override skill scores.** E-Occupancy and School Approval scores are authoritative.
- **Fabricate system IDs.** Every source system ID, folder ID, and document ID comes from an actual API call.
- **Leave unsourced gap labels.** Every unfilled field names the source that was checked.

---

## MatterBot Integration

**Tool:** `generate_marketing_pack(space_sid, space_name, tier, max_rooms, room_types)`
**Base URL:** `https://matterbot-1819903979408.us-central1.run.app`

Fire-and-forget call to MatterBot rendering service. Generates marketing pack images from the Matterport scan and deposits them into the site's M1 subfolder in Drive. No auth required (internal service).

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | OpenAI access for inbox classification, fuzzy site matching, and Alpha Capacity Analysis from Block Plans |
| `OPENAI_CAPACITY_MODEL` | Optional model override for Alpha Capacity Analysis generation; defaults to `gpt-4o` |
| `ANTHROPIC_API_KEY` | Claude API for automated report generation agent |
| `LOCATIONOS_MCP_API_KEY` | Preferred LocationOS MCP bearer token for site roster, Drive-folder context, P1 DRI lookup, due-diligence SOR writes, notes, and document registration |
| `RHODES_API_KEY` | Legacy alias for the LocationOS MCP bearer token |
| `RHODES_MCP_URL` | Optional Rhodes / LocationOS MCP endpoint override |
| `DD_TEMPLATE_V3_GOOGLE_DOC_ID` | Master DD report template Google Doc ID |
| `GOOGLE_DRIVE_ROOT_FOLDER_ID` | Legacy/root fallback Drive folder ID; not the source of truth for daily site roster |
| `SIR_FOLDER_ID` | Shared SIR folder in Google Drive |
| `ISP_FOLDER_ID` | Shared ISP folder in Google Drive |
| `BUILDING_INSPECTION_FOLDER_ID` | Shared Building Inspection folder |
| `GOOGLE_CLIENT_CONFIG` | Path to OAuth client secrets JSON |
| `GOOGLE_TOKEN_FILE` | Path to saved OAuth token file |
| `EMAIL_SENDER` | Gmail address for sending reports |
| `EMAIL_APP_PASSWORD` | Gmail App Password for the sender account |
| `DD_REPORT_EMAIL_RECIPIENTS` | Comma-separated recipient email addresses |
| `GOOGLE_CHAT_WEBHOOK_URL` | Google Chat incoming webhook for alerts |
| `INBOX_INTERNAL_SKIP_LABEL` | Gmail label for internally generated attachments skipped by sender filtering |

---

## Key Files

| File | What It Is |
|------|-----------|
| `docs/prompts/prompt_v4.md` | Agent system prompt -- V4 first-round workflow, exec summary format, report data schema |
| `src/due_diligence_reporter/server.py` | MCP server - Drive, Rhodes, skill publisher, DDR rendering, and notification tools |
| `src/due_diligence_reporter/report_pipeline.py` | Shared pipeline â€” readiness check, Claude agent loop, notifications |
| `src/due_diligence_reporter/report_schema.py` | Template token schema, alias map, and `normalize_report_data()` |
| `src/due_diligence_reporter/classifier.py` | Three-tier document classification (regex â†’ LLM filename â†’ LLM content) |
| `src/due_diligence_reporter/inbox_scanner.py` | Gmail inbox scan, three-tier filename classification, Drive upload |
| `src/due_diligence_reporter/open_questions.py` | Structured open-question and source-event state for partial DDR closure |
| `src/due_diligence_reporter/vendor_doc_sweep.py` | Rhodes-backed core source sweep that triggers in-place republish |
| `src/due_diligence_reporter/rhodes.py` | Rhodes / LocationOS MCP client for P1 DRI lookup, due-diligence SOR writes, note writes, document registration, and readback verification |
| `src/due_diligence_reporter/google_client.py` | Google Drive v3 + Docs v1 + Gmail API client (OAuth), `list_files_recursive()` |
| `src/due_diligence_reporter/config.py` | Pydantic settings loader |
| `src/due_diligence_reporter/utils.py` | PDF extraction, placeholder builder, email, Google Chat |
| `uv run ddr daily-check` | Daily sweep â€” stage-filtered readiness check + report pipeline |
| `scripts/scan_inbox.py` | Inbox scan + per-site report pipeline trigger |
| `uv run ddr source-sweep` | Active source sweep for M2 source packet document updates |
| `tests/test_report_schema.py` | Schema integrity + normalization + delta tests (24 tests) |
| `tests/test_report_pipeline.py` | Pipeline tool routing + readiness tests (13 tests) |
| `tests/test_inbox_scanner.py` | Inbox scanner tests (19 tests) |
| `tests/test_hyperlinks.py` | Link token insertion tests (17 tests) |
| `tests/test_dd_output_fixes.py` | Output formatting + floorplan + rendering tests (25 tests) |
| `.github/workflows/publish-to-mcp-hive.yml` | CI/CD â€” push to `main` deploys to MCP Hive |
| `.github/workflows/inbox-scan.yml` | Inbox scan schedule and Gmail filing workflow |
| `.github/workflows/vendor-doc-republish-sweep.yml` | Active core source sweep for in-place DDR updates |
| `.github/workflows/daily-dd-check.yml` | Daily sweep at 9 AM Central, Mon-Fri |

---

## GitHub Secrets

**Publish workflow:** `MCP_HIVE_API_KEY`, `MCP_HIVE_ID`, `OPENAI_API_KEY`, optional `OPENAI_CAPACITY_MODEL`, `ANTHROPIC_API_KEY`, `DD_TEMPLATE_V3_GOOGLE_DOC_ID`, `GOOGLE_DRIVE_ROOT_FOLDER_ID`, `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OAUTH_REFRESH_TOKEN`, preferred `LOCATIONOS_MCP_API_KEY` or legacy `RHODES_API_KEY`, optional `RHODES_MCP_URL`, and optional RayCon secrets.

**Cron + inbox workflows:** OAuth secrets, shared Drive folder IDs, `OPENAI_API_KEY`, optional `OPENAI_CAPACITY_MODEL`, `ANTHROPIC_API_KEY`, preferred `LOCATIONOS_MCP_API_KEY` or legacy `RHODES_API_KEY`, optional `RHODES_MCP_URL`, notification/email secrets, and optional RayCon secrets. `PRICING_API_KEY` is not a current DDR workflow requirement.


