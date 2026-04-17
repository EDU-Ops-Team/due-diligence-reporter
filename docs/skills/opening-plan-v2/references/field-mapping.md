# SIR → Permitting Plan Field Mapping (v2)

This document maps every AI SIR output section to its corresponding Permitting Plan section.
It serves as the deterministic baseline — these mappings produce the floor-quality plan before
research enrichment is layered on top.

## Legend

- **AUTO**: Can be fully populated from SIR data
- **DERIVE**: Requires calculation or inference from SIR data
- **ENRICH**: Populated from SIR first, then upgraded by research agents
- **PRE-ENRICHED**: Populated by the `school-approval` skill (Step 2.9) before research agents launch. Agent 3 receives these as confirmed baseline — do NOT re-research the base finding. Agent 3 deepens only (equivalency pathways, denial precedents, model-specific risks, attorney verification, gatekeeper contacts).
- **MANUAL**: Cannot be populated from SIR — requires human input (flag as placeholder)
- **PARTIAL**: Some data from SIR, some requires human input

---

## Header

| Permitting Plan Field | SIR Source | Mapping Type |
|---|---|---|
| Site Name (e.g., "Alpha Burlingame") | Not in SIR — use "Alpha [City]" from address | DERIVE |
| Address | SIR header: Property Address | AUTO |

---

## Summary Table (3-Second Read)

| Permitting Plan Field | SIR Source | Mapping Type |
|---|---|---|
| Best Case timeline | DERIVE: Today + best-case critical path weeks from SIR Flag Page | DERIVE |
| Realistic timeline | DERIVE: Today + realistic path (see Scenario Derivation Logic below) | DERIVE |
| Worst Case timeline | DERIVE: Today + worst-case from SIR Flag Page | DERIVE |
| Capacity (all rows) | MANUAL — from separate capacity analysis | MANUAL |
| Best Case cost | DERIVE: SIR Fee Table (known fees) + minimum construction scope | DERIVE |
| Realistic cost | DERIVE: Best fees + moderate construction scope (midpoint of ranges) | DERIVE |
| Worst Case cost | DERIVE: All fees + all construction at high-end estimates | DERIVE |
| Footnote contingency | DERIVE: Name the single condition the Best Case depends on | DERIVE |

---

## Executive Summary

| Permitting Plan Field | SIR Source | Mapping Type |
|---|---|---|
| Recommendation (Go/No Go/Conditional Go) | SIR Executive Impact Summary → Recommendation | DERIVE |
| One-sentence reason | SIR Executive Impact Summary → Score at a Glance | AUTO |
| Lease signability sentence | MANUAL — requires lease terms knowledge | MANUAL |
| Target open date | MANUAL — requires lease signing date + best-case timeline | MANUAL |
| Slip conditions (3 branches) | DERIVE from SIR Timeline Outlook + Risk Watch + Gating factors | PARTIAL |
| What we know (max 3) | SIR Executive Impact Summary → Permits and Approvals + What Must Be Built | DERIVE |
| What we don't know yet (max 3) | SIR Section 4: What We Do Not Know Yet + Risk Watch table | AUTO |

### Recommendation Mapping Table

| SIR Recommendation | Permitting Plan Recommendation |
|---|---|
| PROCEED | Go |
| PROCEED WITH CAUTION | Conditional Go |
| REQUIRES JUSTIFICATION | Conditional Go (with explicit caveats) |
| PASS | No Go |

---

## Permit Paths (Best / Realistic / Worst)

### Scenario Derivation Logic

The SIR provides Best Case and Worst Case columns. Realistic Case is DERIVED using these rules:

**Planning & Zoning — Realistic:**
- If CUP/SUP/MUP required: assume hearing happens but no objection. Add 1 week over best case.
- If by-right: same as best case.

**Building Permitting — Realistic:**
- Assume 1 round of review comments. Add 3 weeks over best case (initial review + 1 correction cycle).
- If SIR states expedited review is unavailable, note that.

**Health — Realistic:**
- Assume concurrent with building (no additional critical path time unless SIR flags otherwise).
- If SIR flags health as sequential, add its independent timeline.

**Construction + FF&E — Realistic:**
- Include all Confidence A/B items from SIR + likely C items.
- Add 4 weeks over best case construction timeline.
- For each trade (HVAC, restrooms, exits, kitchenette): use midpoint of SIR range.

**Worst Case:**
- Use SIR "Worst Case" column for all permit tracks.
- Multiple review rounds (2-3) for building.
- CUP/SUP contested if applicable.
- All D-confidence construction items go wrong at high-end estimates.
- Include vendor task items that could expand scope.

### Sub-items per scenario

| Sub-item | SIR Source |
|---|---|
| Planning & Zoning weeks | SIR Flag Page → Planning/Zoning row (Best/Worst columns) |
| P&Z assumption | SIR Planning & Zoning section → permission status, CUP/SUP/MUP details |
| Building Permitting weeks | SIR Flag Page → Building Permit row |
| Building sub-timeline | SIR Building Department → Plan Submittal Procedure (review rounds) |
| Health permit status | SIR Health Department → Summary / Flag Items |
| Construction + FF&E weeks | DERIVE from SIR Phase 7 construction scope checklist items |
| Construction assumptions | SIR What Must Be Built checklist → split items into best/realistic/worst |

---

## Gating Factors

| Permitting Plan Field | SIR Source | Mapping Type |
|---|---|---|
| Gate items | SIR Section 4: What We Do Not Know Yet (go/no-go or site kill items) + Risk Watch (High/Unknown likelihood items) | DERIVE |
| Binary outcomes | DERIVE from SIR risk descriptions — reframe as "if X = proceed" / "if Y = site kill or +$Z" | DERIVE |
| When known | SIR timeline dependencies — when each unknown resolves | DERIVE |
| Time/cost impact | SIR Risk Watch "What happens if it goes wrong" + Construction Scope costs | DERIVE |

### Gate Numbering Logic

- **Gate 0**: Site-kill risks (items that could make the site unviable regardless of money/time). Keep stair width and ADA elevator as SEPARATE gates — they have different consequences (stair = life safety site kill, elevator = cost/phasing question) and different fix costs.
- **Gate 0-HM**: Hazmat gate (MANDATORY for any building built before 1978). AHERA asbestos inspection + lead paint assessment is a sequential blocker — renovation permits cannot issue until the state health department approves the AHERA inspection report and any required abatement plan. Typical timeline: 6–8 weeks (inspection 2–3 weeks + lab analysis 1–2 weeks + abatement plan approval 2–3 weeks). This is sequential, not parallel to construction permits. Even if the SIR treats hazmat as a cost line item only, elevate it here.
- **Gate 1**: First timeline-branching decision (usually planning/zoning outcome)
- **Gate 2a**: Construction scope unknowns (revealed when plans are submitted)
- **Gate 2b**: Permit cycle unknowns (revealed during review rounds)
- Additional gates (3, 4, etc.) for site-specific risks

---

## Process, Citations, Timeline, and Risks

### Planning & Zoning Section

| Permitting Plan Field | SIR Source | Mapping Type |
|---|---|---|
| Section header + timeline range | SIR Flag Page → Planning/Zoning Best/Worst | AUTO |
| Citations: requirement name | SIR Planning & Zoning → Key Finding | ENRICH |
| Citations: Why (trigger) | SIR Planning & Zoning → Zoning Classification + conditions | ENRICH |
| Citations: exact quote | SIR quote first, then research upgrades with primary source quotes | ENRICH |
| Citations: Code section | SIR Planning & Zoning → Source field (municipal code sections) | ENRICH |
| Contact | SIR Contact Information → Planning department | ENRICH |
| Timeline best/worst | SIR Flag Page → Planning/Zoning row | AUTO |
| Risks: Trigger | DERIVE from SIR zoning conditions | ENRICH |
| Risks: Impact | DERIVE from timeline shift if risk materializes | DERIVE |
| Risks: Managing it | DERIVE — jurisdiction-specific mitigation | ENRICH |

### Building Section

| Permitting Plan Field | SIR Source | Mapping Type |
|---|---|---|
| Section header + timeline range | SIR Flag Page → Building Permit Best/Worst | AUTO |
| Citations: requirement | SIR Building Department → Permit Type + Key Finding | ENRICH |
| Citations: Why (trigger) | SIR Building Department → occupancy change explanation | ENRICH |
| Citations: Code | SIR Building Department → Codes Adopted | ENRICH |
| Expedited review | SIR Building Department → Plan Submittal Procedure → Expedited Review | AUTO |
| Contact | SIR Contact Information → Building department | ENRICH |
| Timeline | SIR Flag Page → Building Permit row | AUTO |
| Risks | DERIVE from SIR Building review rounds + expedited availability | ENRICH |

### Health Permit Section

| Permitting Plan Field | SIR Source | Mapping Type |
|---|---|---|
| Section header + timeline range | SIR Flag Page → Health Review row | AUTO |
| Citations | SIR Health Department → all findings | ENRICH |
| Fee | SIR Fee Estimate Summary → Health Review row | AUTO |
| Issued By | SIR Health Department → Authority Level | AUTO |
| Contact | SIR Contact Information → Health department | ENRICH |
| Risks | DERIVE from SIR Health findings + concurrency notes | ENRICH |

### Construction Section

Each sub-section maps to a specific SIR source:

| Construction Sub-section | SIR Source |
|---|---|
| Restrooms | SIR Building Department → Restroom Requirements + Phase 7 bathroom calculations |
| Sprinklers/Fire | SIR Fire Department → Sprinkler Status + Phase 7 sprinkler trigger analysis |
| Life Safety | SIR Phase 7 → egress analysis + Section 5: Critical Vendor Tasks (exit-related) |
| ADA | SIR Phase 7 → ADA path findings + vendor task cards for ADA items |
| Kitchenette | SIR Health Department → Food Service Requirements + kitchen infrastructure |
| HVAC (ventilation delta) | DERIVE: ASHRAE 62.1 Table 6-1 B→E multiplier calculation (see SKILL.md Step 2f). SIR HVAC ranges are unreliable for occupancy conversions — always calculate. Research agent verifies state-specific amendments to ASHRAE adoption. |

Each sub-section output includes:
- **Why**: Plain-English from SIR findings, enriched with code trigger from research
- **Citations**: SIR direct quote first, then research-sourced primary code quotes
- **Code**: SIR code reference, verified/corrected by research agents
- **Vendor/inspection findings**: If vendor return data is available, cite building inspection report

### Edu Regulatory Section

| Permitting Plan Field | Source | Mapping Type |
|---|---|---|
| State archetype | school-approval → `archetype` (MINIMAL / NOTIFICATION / APPROVAL_REQUIRED / HEAVILY_REGULATED / WINDOWED) | PRE-ENRICHED |
| Approval type | school-approval → `approval_type` (NONE / REGISTRATION_SIMPLE / LOCAL_APPROVAL_REQUIRED / LICENSE_REQUIRED / CERTIFICATE_OR_APPROVAL_REQUIRED / COMPLEX_OR_OVERSIGHT) | PRE-ENRICHED |
| Approving body | school-approval → `approval_authority` + `approval_authority_url` | PRE-ENRICHED |
| Gating before open | school-approval → `gating_before_open` (boolean — must approve before doors open?) | PRE-ENRICHED |
| Timeline (min/likely/max days) | school-approval → `timeline_days_preopen` | PRE-ENRICHED |
| Calendar window / deadline | school-approval → `calendar_window` (next window date, submission deadline, provisional available?) | PRE-ENRICHED |
| Teacher certification required | school-approval → `preopen_requirements.teacher_certification_required` | PRE-ENRICHED |
| Curriculum pre-approval required | school-approval → `preopen_requirements.curriculum_approval_required` | PRE-ENRICHED |
| Background check required | school-approval → `preopen_requirements.background_check_required` | PRE-ENRICHED |
| Education authority inspection required | school-approval → `preopen_requirements.health_safety_inspection_required` | PRE-ENRICHED |
| Financial reserve required | school-approval → `preopen_requirements.financial_reserve_required` | PRE-ENRICHED |
| Local education overlay | school-approval → `local_requirements.has_local_overlay` + `local_notes` | PRE-ENRICHED |
| Ease score (0–100) | school-approval → `score_0_100` + `zone` (GREEN/YELLOW/RED) | PRE-ENRICHED |
| Requirements summary | school-approval → `requirements_summary` (plain-English paragraph) | PRE-ENRICHED |
| Requirements steps | school-approval → `requirements_steps[]` (ordered step list with gating flags) | PRE-ENRICHED |
| Instructional hours equivalency pathways | Agent 3 research — find statute text + annual-hours alternatives | ENRICH |
| Denial precedents for Alpha/similar models | Agent 3 research — search for state denials of micro-school / hybrid / tech-forward models | ENRICH |
| Curriculum & model-specific friction | Agent 3 research — assess Alpha model against stated curriculum requirements | ENRICH |
| Attorney recommendation (verified) | Agent 3 research — verify SIR attorney or find better fit; must confirm state bar + practice area | ENRICH |
| Gatekeeper contact (name, title, phone, email) | Agent 3 research — find the specific person who handles private school applications | ENRICH |
| SIR edu regulatory status | SIR Phase 2 → state education/childcare regulators (cross-check against school-approval; use school-approval if they conflict) | AUTO |
| Alpha existing licensure in state | PARTIAL — requires knowledge of Alpha School's existing licenses by state | PARTIAL |

---

## Items NOT in SIR (Must Be Flagged as Placeholders)

These items appear in the Permitting Plan but cannot be populated from the SIR.
The skill must insert clearly marked `[PLACEHOLDER — reason]` for each.

1. **Target open date** — requires lease signing date and business timeline
2. **Lease conditions** — "what conditions do I want, why, what happens without them"
3. **Lease signability sentence** — requires lease terms knowledge
4. **Capacity figures** — from separate capacity analysis
5. **Project schedule link** — external document
6. **Capacity output link** — from separate capacity analysis
7. **Cost estimate link** — from separate cost estimation
8. **LOI link** — external document
9. **Matterport link** — external document
10. **Vendor inspection report citations** — only available if vendor return has been processed

---

## Research Enrichment Layer

Fields marked **ENRICH** follow a two-pass process:

1. **Pass 1 (Baseline):** Populate from SIR data using the mapping above. This produces a complete document with every field filled — SIR quotes, SIR code references, SIR contacts.

2. **Step 2.9 (school-approval):** Fields marked **PRE-ENRICHED** are populated by the `school-approval` skill before research agents launch. These fields enter Pass 2 already confirmed — research agents do NOT re-research the base finding. Agent 3 deepens PRE-ENRICHED fields only where school-approval leaves gaps (equivalency pathways, denial precedents, model-specific risks, attorney verification, gatekeeper contacts).

3. **Pass 2 (Research Upgrade):** Research agents attempt to upgrade each ENRICH field:
   - Replace SIR code references with verified primary-source code sections + verbatim quotes
   - Replace SIR contacts with named individuals (Name, Title, Phone, Email)
   - Add trigger mechanisms that explain WHY a requirement exists, not just THAT it exists
   - Add fastest-path angles the SIR didn't surface (waivers, parallel filings, pre-app meetings)
   - Add risks the SIR underweighted or missed entirely
   - For PRE-ENRICHED fields: deepen only — do not re-derive archetype, approval type, gating status, or timeline. Those are confirmed.

4. **Fallback rule:** If research cannot improve a field, the SIR baseline stands. The field is marked with a trailing note: `(SIR-sourced — verify with jurisdiction)` so the reader knows it hasn't been independently confirmed. PRE-ENRICHED fields do not need this tag — they are already confirmed by school-approval.
