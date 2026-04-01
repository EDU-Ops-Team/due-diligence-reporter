# Due Diligence Reporter — Roadmap

## Status: Active Development

Last updated: 03/16/2026

---

## Completed

- [x] Initial MCP server implementation (4 tools: get_site_record, list_drive_documents, read_drive_document, create_dd_report)
- [x] E-Occupancy + School Approval skills embedded in prompt.md
- [x] Google Doc template with {{placeholder}} tokens
- [x] CI/CD deploy to MCP Hive via GitHub Actions
- [x] First report generated: Alpha Brentwood 307 (02/27/2026)
- [x] Quality fixes from first report review (commit 9b0ebf7):
  - Skill → schema field mappings in prompt.md
  - Exec summary must-be-in-report_data rule
  - Q4 milestone formulas (CO/Ready-to-Open = N/A in preliminary)
  - ISP fallback (`[Pending ISP]` when no ISP doc exists)
  - List formatting rule (newline-separated strings)
  - 6 q4.*_confidence fields added to schema
  - utils.py list join `", "` → `"\n"`
- [x] **Wrike stage + status filtering (03/12/2026):**
  - Daily cron and inbox scanner pipeline phase now filter by Wrike active status group and stages 1–2
  - `_get_active_status_ids()` fetches workflow metadata to resolve active `customStatusId` values
  - Records without a `customStatusId` default to active (safe fallback)
- [x] **Tiered LLM document classification (03/12/2026):**
  - New `classifier.py` module: Tier 1 (regex keywords) → Tier 2 (GPT-4o-mini on filename) → Tier 3 (GPT-4o-mini on first-page PDF content)
  - LLM site-matching for shared folders: `match_file_to_site_llm()` handles non-standard filenames
  - Graceful degradation — falls back to regex-only if OpenAI is unavailable
  - Validated: correctly classified `zoning proof email chain.pdf` as SIR via Tier 3 content analysis
- [x] **Recursive folder search (03/12/2026):**
  - New `GoogleClient.list_files_recursive()` walks subfolders up to configurable depth
  - Replaced hardcoded `01_Due Diligence` subfolder with recursive search (max depth 2)
  - Older sites with non-standard folder structures are now fully searched
- [x] **P1 Accountable email fix (03/12/2026):**
  - `extract_p1_email_from_record()` now handles both string and list contact ID values
  - Added logging for P1 field resolution (field value, type, resolved email)
  - Validated: Bethesda P1 resolved to `robbie.forrest@trilogy.com`
- [x] **Email recipient update (03/12/2026):**
  - `DD_REPORT_EMAIL_RECIPIENTS` secret updated to `jc.fischer@trilogy.com,auth.permitting@trilogy.com`
  - P1 Accountable person added to DD report emails automatically
- [x] **Inbox scan window extended (03/12/2026):**
  - Expanded from 8AM–6PM to 6AM–8PM Central, Mon–Fri
  - Split into two cron entries to avoid GitHub Actions parsing issues
- [x] **Daily DD check site filter (03/12/2026):**
  - Added `--site` input to workflow_dispatch for targeted manual runs
- [x] **Building Optimizer v2 API upgrade (03/16/2026):**
  - Upgraded from v1 (API key required) to v2 (open access, no key)
  - Sprinkler and fire alarm costs now come from the API instead of local per-SF estimates
  - 6 new v2 component keys: `fireAlarm`, `sprinkler`, `emergencyLighting`, `egressHardware`, `fireCompliance`, `fireMonitoring`
  - Structural and ADA remain as per-SF estimates (not in API)
  - Removed `PRICING_API_KEY` from config, all workflows, and scripts
  - Updated `_build_rooms_payload` component lists to match v2 room type catalog
- [x] **Inactive site filtering fix (03/16/2026):**
  - Confirmed Site Records have no `customStatusId` — the Wrike workflow status check was a no-op
  - Cancelled/dead sites removed from EDU Ops space in Wrike (92 → 80 records)
  - Stage filter (stages 1–2 only) confirmed working correctly
- [x] **Inbox scanner account migration (03/16/2026):**
  - Switched inbox scan from `auth.permitting@trilogy.com` to `edu.ops@trilogy.com`
  - Updated Gmail search query in `config.py`
  - OAuth refresh token updated to `edu.ops@trilogy.com` account (GitHub secret + local .env)
  - Granted `edu.ops@trilogy.com` access to shared Drive folders and DD report template

---

## In Progress

### 1. Exec Summary Validation
**Problem:** First report had exec_summary fields left as `{{exec_summary.*}}` tokens — agent wrote the summary in chat but didn't include it in report_data before calling create_dd_report.
**Fix applied:** Added IMPORTANT rule to prompt.md.
**Needs:** Run a test report and confirm all 5 exec_summary fields land in the doc.

### 2. Skill Execution Audit
**Problem:** Agent may be acknowledging the embedded skills without running through the scoring logic step by step (approximating rather than computing).
**Suspicion:** Skills are embedded as text in prompt.md — agent could be skipping or summarizing them, especially if context is long.
**Options to investigate:**
- Option A: Keep embedded in prompt.md but add explicit "show your work" instructions before calling create_dd_report
- Option B: Convert each skill to an MCP tool call (`apply_e_occupancy_skill(building_type, stories)` → returns score/zone/tier/timeline) — forces explicit invocation and returns structured output
- Option C: Add a `read_skill_file(skill_name)` tool so the agent reads the skill on demand (lazy load vs. always in context)

---

## Backlog

- [ ] Template docx: manually add `{{q4.*_confidence}}` tokens to confidence column in Q4 schedule table, re-upload to Drive
- [ ] Renderings section: agent currently skips `renderings_links` — needs guidance on where renderings live in Drive
- [ ] Multi-site batch mode: run DD reports for a list of sites in one agent session
- [ ] LiDAR data extraction: `lidar_summary` and `as_built_links` often left as [TBD]
- [ ] Remove stale `is_record_active` / `_get_active_status_ids` code — Site Records don't use `customStatusId`; the check is a no-op

---

## Known Issues

| Issue | Impact | Fix |
|---|---|---|
| Confidence column in Q4 table has hardcoded "M" not tokens | Confidence values not replaced | Manual docx edit needed (add `{{q4.*_confidence}}` tokens) |
| exec_summary may still land in chat not doc | Report lacks executive summary | Prompt fix deployed; needs validation |
| Skills may be approximated not computed | Inaccurate scores | Under investigation |
