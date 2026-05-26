# Due Diligence Reporter â€” How It Works

**Version:** 4.1.0
**Team:** EDU Ops Intelligence
**Last Updated:** 2026-03-26

---

## Overview

The Due Diligence Reporter is an AI agent powered by Claude that generates Site Due Diligence (DD) Reports for potential Alpha School locations. It operates in three modes:

1. **Interactive** â€” A human gives it a site name in chat via MCP Hive. The agent gathers data, runs analytical skills, and produces an executive-ready Google Doc.
2. **Event-Driven (Inbox Scan â€” every 15 min)** â€” A scheduled script scans the `edu.ops@trilogy.com` inbox for new SIR, Building Inspection, and ISP PDFs, classifies them by filename using a three-tier classifier (regex â†’ GPT-4o-mini), and uploads to the correct shared Drive folder.
3. **Daily Sweep (Safety Net â€” 9 AM)** â€” A scheduled script scans all site folders in active DD stages. When a site has an SIR / AI SIR and no existing report, it triggers first-round report generation. Vendor SIR, Building Inspection, RayCon, and other documents upgrade the report through republish paths as they land.

The agent gathers facts. It does not make recommendations. The decision belongs to the leadership team.

---

## The V4 Report

The V4 DD Report is a **structured executive one-pager** -- not the multi-page narrative of the original report. It uses structured checklists, pick-menu dimensions, and bare values instead of prose paragraphs.

**56 template tokens** across three sections:

| Section | Count | What it covers |
|---------|-------|----------------|
| **meta** | 8 | Site name, address, school type, marketing name, report date, prepared by, site ID, Drive folder link |
| **exec** | 40 | "Can this school be open in time for the current school year?" card, direct answer, two build scenarios, detailed cost breakdown, lease conditions, trade-offs |
| **sources** | 7 | Links to SIR, Building Inspection, Block Plan, site record, E-Occupancy Assessment, School Approval Assessment, and Opening Plan |

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

Rules: cost = single midpoint number (not a range), timeline = MM/YY format only, and sourced team notes override API numbers.

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
                              â”‚  Inbox Scan (every 15min) â”‚
                              â”‚  scan_inbox.py            â”‚
                              â”‚  three-tier classifier    â”‚
    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”‚  (regex â†’ GPT-4o-mini)    â”‚
    â”‚  Daily Sweep (9 AM) â”‚   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
    â”‚  daily_dd_check.py  â”‚                â”‚
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
â”‚  â”œâ”€ apply_e_occupancy_skill  (E-Occ scoring)             â”‚
â”‚  â”œâ”€ apply_school_approval_skill (State registration)     â”‚
â”‚  â”œâ”€ get_cost_estimate        (RayCon API)                â”‚
â”‚  â”œâ”€ create_dd_report         (Template copy + fill)      â”‚
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
**Schedule:** Every 15 minutes, 6 AMâ€“8 PM Central, Mondayâ€“Friday
**Workflow:** `.github/workflows/inbox-scan.yml`

The inbox scanner is the primary trigger for report generation. When a vendor emails a SIR or Building Inspection to `edu.ops@trilogy.com`, the scanner picks it up within 15 minutes.

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
  4. If confidence < 0.7 â†’ flag for manual review in Google Chat
  5. Mark email as processed (DD-Processed label)
  6. If any SIR was uploaded â†’ send SIR arrival email to jake.petersen@trilogy.com,
     joshua.rockers@trilogy.com, edu.ops@trilogy.com
```

**Supported doc types:** `sir`, `building_inspection`, `isp`.

### Phase 2 â€” Per-Site Pipeline

After all uploads complete, the scanner attempts to run the report pipeline for each site that received a new document. Phase 2 requires a `site_title` to look up the site context. The current classifier routes by `doc_type` only and does not match files to sites, so `site_title` is `None` in all uploads â€” **Phase 2 is currently inactive** and report generation falls to the daily sweep.

When site matching is re-enabled (e.g., via a future `matched_site_id` returned from classification), Phase 2 will run:

```
For each unique site that received an upload:
  1. Look up site context â†’ get Drive folder URL â†’ build match terms
  2. Refresh shared folder cache (picks up just-uploaded files)
  3. Run process_site_pipeline():
     a. Check readiness (SIR + Inspection present? ISP is informational only)
     b. If missing docs â†’ post Google Chat alert with checklist
     c. If report exists â†’ skip
     d. If ready â†’ trigger Claude agent loop â†’ check completeness â†’ email
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

**Daily sweep mode:** `daily_dd_check.py` fetches all site folders, filters to active DD stages only ("1. Looking for Sites" and "2. Evaluating Potential Sites (LOI)"), pre-fetches the three shared Drive folders once, then checks each site's readiness.

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

Three skill tools analyze the source data and produce structured outputs. The first two auto-publish full assessment documents to the site's Drive folder when `site_name` and `drive_folder_url` are provided.

**E-Occupancy Skill** â€” `apply_e_occupancy_skill(building_type_description, stories, ..., ibc_occupancy_group, fire_area_sqft, has_below_grade_space, already_sprinklered, construction_type, max_travel_distance_ft, existing_exit_count, projected_occupant_load, site_name, drive_folder_url)`
1. Matches building type against a scoring matrix
2. Applies IBC group override (H â†’ score 0, I â†’ cap 20)
3. Applies height ceiling and tenant deductions
4. Runs IBC compliance gates: sprinkler requirement, travel distance, exit count, construction type
5. Returns score (0â€“100), zone (GREEN/YELLOW/RED), tier, confidence, `ibc_gates`, `ibc_flags`, and `q2.e_occupancy_ibc_summary`
6. Publishes assessment â†’ `sources.e_occupancy_link`
7. Phase 4: `ibc_flags` and `q2.e_occupancy_zone` remain available in report data for downstream analysis.

**School Approval Skill** â€” `apply_school_approval_skill(state, site_name, drive_folder_url)`
1. Looks up state in built-in approval table (all 50 states + DC)
2. Returns approval type, gating status, timeline, and required steps
3. Publishes assessment â†’ `sources.school_approval_link`

**Cost Estimate** â€” `get_cost_estimate(total_building_sf, rooms=[...])`
1. Uses ISP room list if available; otherwise auto-generates rooms from SF
2. Calls Building Optimizer Pricing API at two finish levels
3. Returns per-tier cost estimates

---

### Step 6 â€” Compile and Write the DD Report

**Tool:** `create_dd_report(site_name, drive_folder_url, report_data, token_evidence=evidence)`

**Activity:**

1. **Copy template** â€” Copies the master Google Doc template (`DD_TEMPLATE_GOOGLE_DOC_ID`) into the site's Drive folder

2. **Normalize report_data** â€” `normalize_report_data()` from `report_schema.py`:
   - Flattens nested dicts to dot-separated keys
   - Injects defaults for `meta.site_name` and `meta.report_date`
   - Applies the alias map to translate known agent key variations to canonical token names
   - Filters to only keys matching the 34 canonical template tokens
   - Returns diagnostics: replacements applied, unmatched keys, unfilled tokens, token sources

3. **Compute deltas** â€” Server-side computation of the Max Capacity and Max Value delta columns against Fastest Open

4. **Fill template** â€” `batchUpdate` to Docs API with `replaceAllText` per token. Link tokens (`sources.*`, `meta.drive_folder_url`) are inserted as clickable hyperlinks with display labels.

5. **Return diagnostics** â€” Returns applied replacement counts, unmatched keys, unfilled tokens, and normalized report data to the pipeline.

**Token evidence:** As the agent reads each source document, it builds a parallel `evidence` dict recording the raw excerpt supporting each token value. Evidence is kept in the local run manifest instead of publishing a companion trace file.

**Output:** Google Doc URL + diagnostics.

---

### Step 7 â€” Check Completeness

**Tool:** `check_report_completeness(doc_id)`

**Activity:**
1. Exports the generated Google Doc as plain text
2. Scans for two patterns:
   - `{{token}}` â€” a template placeholder that was never filled (hard block â€” do not send)
   - `[Not found â€” ...]` â€” a sourced gap label where the agent tried but data wasn't available (acceptable)

**Decision:** If any `{{token}}` hard blocks remain, the report is flagged as incomplete. If only `[Not found â€” ...]` labels remain, the report is ready to send.

---

### Step 8 â€” Send Email Notification

**Tool:** `send_dd_report_email(site_name, report_url, key_findings, additional_recipients)`

**Activity:** Sends an HTML email to configured recipients (base list + Rhodes P1 DRI when found) with the site name, key findings summary, and a link to the Google Doc report. Sent automatically â€” no confirmation prompt.

---

## Shared Report Pipeline

**Module:** `src/due_diligence_reporter/report_pipeline.py`

The report pipeline module contains all shared logic used by both the inbox scanner and the daily sweep:

| Function | Purpose |
|----------|---------|
| `TOOL_DEFINITIONS` | 11-tool schema list for Claude API calls |
| `route_tool_call()` / `route_tool_call_sync()` | Async/sync tool router mapping to server.py functions |
| `list_shared_folders_once(gc)` | Pre-fetch SIR/ISP/Inspection shared folder files |
| `match_site_in_shared_cache(terms, cache)` | Find docs for a site in pre-fetched cache |
| `check_site_readiness_direct(gc, url, terms, cache)` | Readiness check bypassing MCP layer |
| `run_dd_report_agent(site_title, prompt)` | Claude agentic loop (up to 40 iterations) |
| `process_site_pipeline(gc, title, url, terms, cache, prompt, settings)` | Full pipeline: readiness -> report -> completeness -> email |
| `post_pipeline_result(webhook_url, result, url)` | Google Chat notification per result |
| `PipelineResult` | Dataclass with status, missing_docs, doc_id, doc_url, etc. |

**Pipeline statuses:** `waiting_on_docs`, `report_exists`, `report_created`, `report_incomplete`, `generation_failed`, `error`

---

## Daily Sweep (Safety Net)

**Script:** `scripts/daily_dd_check.py`
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
  1. Check readiness (SIR + Inspection present, no report exists; ISP is informational only)
  2. If missing docs -> post Google Chat alert listing what's missing
  3. If report exists -> skip
  4. If ready -> run Claude agent loop:
     a. check_site_readiness -> list_drive_documents -> read all 3 docs
     b. apply_e_occupancy_skill + apply_school_approval_skill + get_cost_estimate
     c. create_dd_report (with normalize_report_data + compute_deltas)
     d. check_report_completeness
     e. If complete -> send email to recipients
     f. If incomplete -> post Google Chat alert with unfilled tokens
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
| `GOOGLE_DRIVE_ROOT_FOLDER_ID` | Parent Drive folder containing all site folders |
| `OPENAI_API_KEY` | GPT-4o-mini for inbox classification (Tier 2/3) and fuzzy site name matching |
| `ANTHROPIC_API_KEY` | Claude API for automated report generation agent |
| `RHODES_API_KEY` | Rhodes / LocationOS MCP token for read-only P1 DRI lookup |
| `RHODES_MCP_URL` | Optional Rhodes / LocationOS MCP endpoint override |
| `DD_TEMPLATE_GOOGLE_DOC_ID` | Master DD report template Google Doc ID |
| `GOOGLE_DRIVE_ROOT_FOLDER_ID` | Parent Drive folder containing all site folders |
| `SIR_FOLDER_ID` | Shared SIR folder in Google Drive |
| `ISP_FOLDER_ID` | Shared ISP folder in Google Drive |
| `BUILDING_INSPECTION_FOLDER_ID` | Shared Building Inspection folder |
| `GOOGLE_CLIENT_CONFIG` | Path to OAuth client secrets JSON |
| `GOOGLE_TOKEN_FILE` | Path to saved OAuth token file |
| `PRICING_API_KEY` | Building Optimizer API key |
| `EMAIL_SENDER` | Gmail address for sending reports |
| `EMAIL_APP_PASSWORD` | Gmail App Password for the sender account |
| `DD_REPORT_EMAIL_RECIPIENTS` | Comma-separated recipient email addresses |
| `GOOGLE_CHAT_WEBHOOK_URL` | Google Chat incoming webhook for alerts |

---

## Key Files

| File | What It Is |
|------|-----------|
| `docs/prompts/prompt_v4.md` | Agent system prompt -- V4 first-round workflow, exec summary format, report data schema |
| `src/due_diligence_reporter/server.py` | MCP server â€” 13 tools + embedded skill logic |
| `src/due_diligence_reporter/report_pipeline.py` | Shared pipeline â€” readiness check, Claude agent loop, notifications |
| `src/due_diligence_reporter/report_schema.py` | Template token list (28), alias map (26), `normalize_report_data()`, `compute_deltas()` |
| `src/due_diligence_reporter/classifier.py` | Three-tier document classification (regex â†’ LLM filename â†’ LLM content) |
| `src/due_diligence_reporter/inbox_scanner.py` | Gmail inbox scan, three-tier filename classification, Drive upload |
| `src/due_diligence_reporter/rhodes.py` | Rhodes / LocationOS MCP client for P1 DRI lookup |
| `src/due_diligence_reporter/google_client.py` | Google Drive v3 + Docs v1 + Gmail API client (OAuth), `list_files_recursive()` |
| `src/due_diligence_reporter/config.py` | Pydantic settings loader |
| `src/due_diligence_reporter/utils.py` | PDF extraction, placeholder builder, email, Google Chat |
| `scripts/daily_dd_check.py` | Daily sweep â€” stage-filtered readiness check + report pipeline |
| `scripts/scan_inbox.py` | Inbox scan + per-site report pipeline trigger |
| `tests/test_report_schema.py` | Schema integrity + normalization + delta tests (24 tests) |
| `tests/test_report_pipeline.py` | Pipeline tool routing + readiness tests (13 tests) |
| `tests/test_inbox_scanner.py` | Inbox scanner tests (19 tests) |
| `tests/test_hyperlinks.py` | Link token insertion tests (17 tests) |
| `tests/test_dd_output_fixes.py` | Output formatting + floorplan + rendering tests (25 tests) |
| `.github/workflows/publish-to-mcp-hive.yml` | CI/CD â€” push to `main` deploys to MCP Hive |
| `.github/workflows/inbox-scan.yml` | Inbox scan every 15 min, 6 AM-8 PM Central, Mon-Fri |
| `.github/workflows/daily-dd-check.yml` | Daily sweep at 9 AM Central, Mon-Fri |

---

## GitHub Secrets (18 total)

**Publish workflow (9):** `MCP_HIVE_API_KEY`, `MCP_HIVE_ID`, `GOOGLE_DRIVE_ROOT_FOLDER_ID`, `OPENAI_API_KEY`, `DD_TEMPLATE_GOOGLE_DOC_ID`, `GOOGLE_DRIVE_ROOT_FOLDER_ID`, `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OAUTH_REFRESH_TOKEN`

**Cron + Inbox workflows (9 additional):** `ANTHROPIC_API_KEY`, `GOOGLE_CHAT_WEBHOOK_URL`, `DD_REPORT_EMAIL_RECIPIENTS`, `EMAIL_SENDER`, `EMAIL_APP_PASSWORD`, `SIR_FOLDER_ID`, `ISP_FOLDER_ID`, `BUILDING_INSPECTION_FOLDER_ID`, `PRICING_API_KEY`


ENT_SECRET`, `OAUTH_REFRESH_TOKEN`

**Cron + Inbox workflows (9 additional):** `ANTHROPIC_API_KEY`, `GOOGLE_CHAT_WEBHOOK_URL`, `DD_REPORT_EMAIL_RECIPIENTS`, `EMAIL_SENDER`, `EMAIL_APP_PASSWORD`, `SIR_FOLDER_ID`, `ISP_FOLDER_ID`, `BUILDING_INSPECTION_FOLDER_ID`, `PRICING_API_KEY`


