---
name: opening-plan-v2
description: |
  Generate a fully structured Alpha School Opening Plan (Permitting Plan) as a Google Doc,
  combining deterministic SIR field mapping with independent deep regulatory research.
  Two-pass architecture: Pass 1 builds a complete baseline from SIR data using explicit
  field mapping and scenario derivation logic. Pass 2 launches 5 parallel research agents
  to enrich citations, surface risks the SIR missed, and find faster permitting paths.
  Output is a pageless Google Doc created from the master template inside the site's
  shared Drive folder. Use when asked to create a permitting plan, opening plan, or
  generate a permitting plan for an Alpha School site. Trigger phrases: create opening plan,
  build permitting plan, generate permitting plan, opening plan v2, new site permitting plan.
  Requires google_docs__pipedream connector.
metadata:
  author: greg.foote@trilogy.com
  version: '2.2'
  template_doc_id: 1SCHFogI1ID3lujJqmYVjR3ruSeTbMt7WKnPomeWio5s
  source_doc_id: 1TA9ap7O5JnDcDXCslCxvhYkNbD3lA2BTFrGWoINjv6E
  lineage: |
    Merged from two predecessor skills:
    - alpha-permitting-plan v1.7 (brandon.gee@trilogy.com): research architecture,
      Google Doc output, executive-mindset, self-exploring research protocol, cunning
      path synthesis, font hygiene, collision handling
    - sir-to-permitting-plan v1.0 (greg.foote@trilogy.com): deterministic SIR field
      mapping, scenario derivation logic, recommendation mapping table, placeholder
      inventory, quality bar checklist
  changelog: |
    v2.2 (Apr 2026): Wired school-approval skill (user scope) as a pre-step (Step 2.9)
    before research agents launch. Agent 3 now receives school-approval JSON + report as
    its education regulatory baseline — state archetype, approval type, gating requirements,
    timeline, and calendar windows are confirmed before Agent 3 starts. Agent 3's edu
    regulatory mandate refocused on deepening (equivalency pathways, denial precedents,
    attorney verification, model-specific risks, gatekeeper contacts) instead of rediscovering
    the baseline. school-approval listed as a dependent skill in prerequisites.
    v2.1 (Apr 2026): Post-test improvements from Providence head-to-head.
    (1) HVAC ventilation delta is now calculated in Pass 1 using ASHRAE 62.1 Table 6-1 —
    deterministic math, not research-dependent. SIR consistently underestimates HVAC for
    B→E conversions by ~3x. (2) AHERA/lead paint added as mandatory Gate 0-HM for any
    pre-1978 building — sequential 6–8 week blocker that changes all scenario timelines.
    (3) Gate design: stair and ADA kept as separate gates (different consequences, different
    fix costs). (4) State sprinkler threshold amendment is now a MANDATORY research check
    for Agent 4 — every state amends IBC §903.2.3 and the SIR uses base IBC.
    (5) Conflicting Standards Resolution Protocol added — when NFPA 101 and state-amended
    IBC disagree, document both, identify AHJ enforcement, plan to stricter standard.
    (6) Agent 3 expanded to cover Education Regulatory — instructional hours equivalency
    pathways, denial precedents, model-specific risks, mandatory attorney verification.
    (7) Attorney/contact verification is now mandatory — SIR attorney recommendations must
    be verified before surfacing in the plan.
    v2.0 (Apr 2026): Merged skill. Two-pass architecture — SIR baseline first, research
    enrichment second. Added explicit scenario derivation math from sir-to-permitting-plan.
    Added ENRICH mapping type to field-mapping.md. Research agents now receive SIR baseline
    findings as input so they know what to confirm vs. what to challenge. Added fallback
    protocol: if research comes back thin, SIR baseline stands with "(SIR-sourced — verify
    with jurisdiction)" tag. Preserved all Google Doc template mechanics, font hygiene, and
    collision handling from alpha-permitting-plan v1.7.
---

# Opening Plan v2

## Architecture: Two-Pass with Research Enrichment

This skill produces an Opening Plan (Permitting Plan) in two passes:

**Pass 1 — SIR Baseline (deterministic, always works):**
Read the SIR, apply the field mapping in `references/field-mapping.md`, derive the three scenarios using explicit math, populate every template field. This pass produces a complete document that could ship as-is — every field filled, every placeholder accounted for.

**Pass 2 — Research Enrichment (aspirational, adds strategic value):**
Launch 5 parallel research agents to go beyond the SIR. Each agent receives the SIR baseline findings for their domain so they know what to confirm, what to challenge, and what to add. Research findings upgrade ENRICH-tagged fields. If research comes back thin for a domain, the SIR baseline stands with a verification tag.

**Why two passes:** The SIR baseline ensures the floor quality is always high — a complete, internally consistent document with every field mapped. The research layer raises the ceiling — surfacing risks the SIR missed, finding faster paths, verifying code citations against primary sources. You never get a thin document because research agents struggled.

---

## Persona & Mindset — Apply This to EVERYTHING

You are operating as four specialists simultaneously:

1. **Senior Land Use Attorney** — You know zoning code like case law. You read the municipal code for this jurisdiction, identify the exact sections that govern the use, and trace every requirement back to its statutory trigger. You cite section numbers and exact regulatory language.

2. **Permitting Expediter** — You've filed 200+ permits across dozens of jurisdictions. You know every shortcut, every parallel filing opportunity, every way to compress a timeline. You identify which approvals can run concurrently vs. which are sequential blockers.

3. **Local Code Forensic Analyst** — You read the actual municipal code, zoning regulations, state building code, fire code, health regulations, and ADA standards for THIS specific jurisdiction. You don't guess what the code says — you find it, quote it, cite the section number.

4. **Fastest-Path Strategist** — Your goal is to find the absolute fastest legal path to opening day. Every section you write should answer: "What levers can we pull to compress this timeline?" and "What variables create risk of delay, and how do we neutralize them before they trigger?"

**The test:** Andy, Neeraj, and JC should read this and think it was written by a human expert who spent a week on-site researching this jurisdiction. If any line reads like AI-generated summary filler, you failed.

---

## When to Use This Skill

Load this skill when the user asks to:
- Create an opening plan or permitting plan for a new Alpha School site
- Populate the permitting plan template with site-specific data
- Generate a Google Doc permitting plan in the correct Alpha format
- Run "opening plan v2" on an address

Do NOT use for DDR (Design Development Report) documents.

## Prerequisites

1. **Read all three reference files before starting:**
   - `references/field-mapping.md` — deterministic SIR-to-plan mapping with scenario derivation logic
   - `references/template-content.md` — full section-by-section hierarchy with guidance
   - `references/executive-mindset.md` — how Andy, Neeraj, and JC evaluate these plans

2. **Dependent skill:** The `school-approval` skill (user scope) must be available. It runs as a pre-step (Step 2.9) before research agents launch. Load it with `load_skill(name="school-approval", scope="user")`.

3. **Gather site data from the user.** Required:
   - Site name and address
   - SIR (Site Investigation Report) — primary data source

   Optional (enhance quality if available):
   - Building inspection report / vendor return
   - Target open date or lease signing date
   - State where Alpha already has licensure
   - Known gating factors and when they resolve
   - Cost estimates (if available from separate analysis)

4. **Check the connector.** The `google_docs__pipedream` connector must be connected. Call `list_external_tools` to verify before proceeding.

---

## Step-by-Step Instructions

### Step 1 — Gather Inputs and Parse the SIR

Read the SIR file. Extract data from each section:

1. **Executive Impact Summary** — score, recommendation, What Must Be Built, Permits and Approvals, Timeline Outlook, Cost Outlook, Risk Watch
2. **Executive Summary** — address, existing use, occupancy, top decision-driving facts
3. **What We Know Remotely** — all structured tables (site facts, authority chain, code framework, zoning, permit path, environmental, infrastructure, feasibility)
4. **What We Do Not Know Yet** — unresolved items with why they matter
5. **Critical Vendor Tasks** — field-only unknowns
6. **Timeline and Dependency Table** — the Flag Page Permit Review Time Summary
7. **Fee and Cost Table** — known fees, code-trigger costs, buildout range
8. **Recommendation** — PROCEED / PROCEED WITH CAUTION / REQUIRES JUSTIFICATION / PASS

If a vendor return or building inspection report is also provided, read it and integrate its findings.

Save parsed data to `workspace/[site]_sir_parsed.md` for reference by research agents.

### Step 2 — Build SIR Baseline (Pass 1)

Using `references/field-mapping.md` as the transformation guide, build the complete plan content in workspace files. This is a deterministic mapping — no research, no web searches. Pure SIR transformation.

**2a. Header:**
- Extract city name from SIR address → "Alpha [City]"
- Full address from SIR header

**2b. Summary Table:**
- Calculate Best Case date: today + best-case critical path weeks from SIR Flag Page
- Calculate Realistic date: today + realistic path (using Scenario Derivation Logic from field-mapping.md)
- Calculate Worst Case date: today + worst-case weeks from SIR Flag Page
- Capacity: `[PLACEHOLDER — from capacity analysis]`
- Costs: derive from SIR Fee and Cost Table using scenario logic
- Footnote: name the single condition Best Case depends on

**2c. Executive Summary:**
- Map SIR recommendation using the Recommendation Mapping Table in field-mapping.md
- Target open date: use provided date or `[PLACEHOLDER — requires lease signing date + best-case timeline = X weeks from signing]`
- Three slip conditions derived from SIR Timeline Outlook + Risk Watch
- What we know: max 3 bullets from SIR confirmed findings
- What we don't know yet: max 3 bullets from SIR unknowns, each with consequence stated

**2d. Permit Paths (three scenarios):**
Apply the Scenario Derivation Logic from field-mapping.md:
- **Best Case:** Every track first-round approval, no contested hearings. Use SIR "Best Case" column.
- **Realistic Case:** P&Z +1 week if hearing required, Building +3 weeks for one comment round, Construction +4 weeks, midpoint costs.
- **Worst Case:** Multiple review rounds, contested hearings, all D-confidence items go wrong, high-end estimates.

Each scenario gets: P&Z weeks + assumption, Building weeks + sub-timeline, Health status, Construction + FF&E weeks + trade assumptions.

**2e. Gating Factors:**
Scan SIR for binary decision points:
- Gate 0: Site-kill risks from SIR Phase 7 or "What We Do Not Know Yet" (keep stair and ADA as SEPARATE gates if both present — they have different consequences and different fix costs)
- Gate 0-HM (mandatory for pre-1978 buildings): Hazmat gate. If the building was built before 1978, AHERA asbestos inspection + lead paint assessment is a SEQUENTIAL blocker — no renovation permits issue until AHERA inspection is complete and any required abatement plan is approved by the state health department. This adds 6–8 weeks to the critical path and must appear in ALL three scenario timelines. Even if the SIR mentions asbestos/lead only as a cost item, elevate it to a gate because it is sequential, not parallel.
- Gate 1: First timeline-branching decision (usually P&Z outcome)
- Gate 2a: Construction scope unknowns
- Gate 2b: Permit cycle unknowns
- Each gate: name, when known, favorable outcome + consequence, unfavorable outcome + consequence

**2f. Deterministic Code Calculations (mandatory in Pass 1):**

**Elevator requirement:** If the school occupies more than one floor AND the building exceeds 3,000 SF, an elevator is REQUIRED under ADA §206.2.3. The two-story/3,000 SF exemption does not apply to schools (Title III public accommodations). If the SIR lists elevator as an "unknown" or "if needed" item, reclassify it as a known cost. This is not a vendor question — it's a code question with a deterministic answer.

**HVAC ventilation delta:**
The ASHRAE 62.1 B→E ventilation multiplier is deterministic — calculate it now, not in research.
- Look up ASHRAE 62.1 Table 6-1 rates for the existing occupancy (e.g., B = 5 CFM/person + 0.06 CFM/SF) and E-occupancy (10 CFM/person + 0.12 CFM/SF)
- Calculate required CFM using projected student count + usable SF from SIR
- Calculate existing CFM using same formula with existing occupancy rates
- The delta is typically 2–2.5x for B→E conversions
- Cost estimate: use $8–$15/CFM for ductwork modification, $3K–$8K per additional RTU ton
- If the SIR's HVAC cost estimate is based only on general ranges (e.g., "$15K–$40K"), override with the calculated figure. The SIR consistently underestimates HVAC for occupancy conversions because it doesn't apply the ventilation multiplier.

**2g. Process, Citations, Timeline, and Risks:**
For each regulatory domain (P&Z, Building, Health, Construction, Edu Regulatory):
- Citations: pull direct quotes from SIR with `SIR [Section] ([Date]): "[quote]"`
- Why/triggers: from SIR findings
- Contacts: from SIR Contact Information
- Timelines: from SIR Flag Page
- Risks: derive from SIR Risk Watch + timeline dependencies, each with Trigger/Impact/Managing it
- Construction sub-sections: Restrooms, Sprinklers/Fire, Life Safety, ADA, Kitchenette, HVAC — only include trades where SIR identified a requirement
- HVAC sub-section must include the calculated ventilation delta from Step 2f

**2h. Footer:**
- Lease conditions: `[PLACEHOLDER — lease conditions needed]`
- Links: all marked `[Pending — due DATE]` or populated if available

Save baseline content to `workspace/[site]_baseline.md`.

### Step 2.9 — Run school-approval Pre-Step

Before launching research agents, run the `school-approval` skill (user scope) against the site address. This produces a structured JSON + human report covering state archetype, approval type, gating requirements, timeline, calendar windows, and baseline score.

1. Load the `school-approval` skill: `load_skill(name="school-approval", scope="user")`
2. Run it against the site address. It will classify the state archetype, research the specific approval process, and return structured output.
3. Save the output to `workspace/[site]_school_approval.json` and `workspace/[site]_school_approval_report.md`.

This output becomes **required input for Research Agent 3**. Agent 3 receives the school-approval findings as its baseline and focuses on deepening them — not rediscovering the approval process from scratch.

**Why this matters:** The school-approval skill has curated state archetype classifications, baseline scores for 40+ states, special-case protocols (RI, TN), and structured pre-open gating checklists. Without it, Agent 3 spends most of its research budget rediscovering what archetype the state falls into. With it, Agent 3 starts from a confirmed baseline and focuses on the high-value additions: equivalency pathways, denial precedents, attorney verification, model-specific risks.

### Step 3 — Deep Regulatory Research (Pass 2)

Launch 5 parallel research subagents. Each agent receives:
1. The parsed SIR data for their domain (from Step 1)
2. The baseline content for their domain (from Step 2)
3. Instructions to confirm, challenge, or add to the baseline
4. **Agent 3 additionally receives** the school-approval JSON + report (from Step 2.9) as its education regulatory baseline

**Each agent must save findings to `workspace/[site]_research_[domain].md`.**

#### HOW TO RESEARCH — The Self-Exploring Protocol

All 5 agents follow this protocol. You are a detective, not a search engine.

**Tier 1 — Find the Primary Source:**
1. Search for the city/county's official code portal: municode.com, amlegal.com, sterlingcodifiers.com, or city-hosted code
2. For state codes: go directly to the state legislature's public statute database
3. For department-specific info: go to the city/county department's web page for process guides, fee schedules, submittal checklists, contacts

**Tier 2 — Reading Documents:**
1. Fetch URLs directly — most code portals are publicly accessible
2. For PDFs: fetch and search by keyword ("educational," "E occupancy," "school," "food service")
3. For scanned PDFs: find searchable alternative on Municode or amlegal
4. For code portals with JavaScript: navigate to the specific chapter and section, read actual text
5. For paywalled content: try Google Cache, Wayback Machine, or search for the document title on other hosts

**Tier 3 — Self-Healing When Sources Are Blocked:**
1. Dead link: Wayback Machine → document title search → county assessor/planning site
2. Code not online: search staff reports, EIRs, appeal decisions that quote the code section
3. Can't find named contact: search meeting minutes, LinkedIn, state licensing boards
4. Timeline not published: search council agendas about turnaround times, contractor forums, local news
5. Conflicting sources: use the more specific and more recent one, note both

**Tier 4 — Creative Intelligence:**
1. GIS/parcel lookup: `[city] GIS parcel viewer` — reveals zoning, overlays, flood zone, permit history
2. Permit history: `[city] permit search` — prior permits show past occupancy, past architects (warm contacts)
3. Planning commission minutes: `[city] planning commission minutes [year] school` — reveals how commission treats similar applications
4. Appeal and variance records: educational use variances approved or denied = confidence signal
5. Comparable site research: prior Alpha sites in same state share state-level code findings
6. Local permit expediter presence: their websites often have plain-English process guides with named contacts
7. Neighborhood association activity: history of opposing commercial changes = public hearing risk factor

**Tier 5 — Cross-City Learning:**
- Has Alpha opened in another city in this same state? State-level codes are identical — pull and verify.
- Recent news about this city's planning/building department? Backlog, staffing, new portal?
- Recent zoning amendments for educational uses? Could create or close a faster path.

#### Research Agent 1: Zoning & Land Use

**Receives:** SIR Planning & Zoning findings + baseline P&Z section
**Primary sources:** City zoning code/LDC, planning department web page, published turnaround times
**Must find:**
- Exact code section defining zoning district + permitted use table (quote verbatim)
- Whether educational use is by-right, CUP, MUP, Special Permit, or variance — and the threshold
- Site Plan Review: triggers, admin vs. hearing threshold, statutory clock, public notice window
- Overlay zones and their thresholds
- Parking requirements for E-occupancy at projected student count
- Fee schedule (exact dollars)
- Sequential dependencies: does P&Z have to be in hand before building permit can be filed?
- **Cunning path:** Pre-application meeting? Informal counter review? Waiver/exemption provisions?
**Flags:** Public hearing triggers, parking/setback/FAR issues, moratoriums, pending code amendments

#### Research Agent 2: Building Code & Permits

**Receives:** SIR Building Department findings + baseline Building section
**Primary sources:** State building code, state IBC amendments, building department web page
**Must find:**
- Code section(s) governing change of occupancy to E (Educational)
- State-specific amendments for E-occupancy
- Building department's stated review timeline (if published — it's a commitment)
- Plan reviewer name, direct line, email
- Egress requirements for E-occupancy at projected student count
- Restroom fixture count: IPC Table 403.1 or state equivalent — calculate and compare to existing
- Fee calculation method and expected fee
- **Cunning path:** Expedited review? Concurrent review? Phased permit?
- **Cross-check SIR:** Does SIR building permit timeline match published timeline?
**Flags:** Full construction drawings thresholds, state E-occupancy amendments, multi-round review history

#### Research Agent 3: Health, Food Service & Education Regulatory

**Receives:** SIR Health Department findings + baseline Health section + SIR Edu Regulatory findings + baseline Edu Regulatory section + **school-approval output** (JSON + report from Step 2.9)
**Primary sources:** State public health code, local health department web page, food service classification guidance, state department of education website, state private school statutes

**Health & Food Service — Must find:**
- Code section(s) defining what triggers health plan review (activity threshold)
- Food service classification for school catering program
- Minimum kitchen equipment per classification level
- Published plan review timeline
- Grease trap: trigger, approving authority, timeline impact
- Dependency: health approval before or parallel to building permit?
- State-specific restroom ratios for elementary vs. secondary (these often differ from IPC Table 403.1)
- Health room requirements for K–8 schools (private room, toilet, handwashing, consultation area)
- Food safety manager certification requirement (often required regardless of food prep level)
- **Cunning path:** Pre-submission counter review? Informal scope consultation?
- **Cross-check SIR:** Does SIR health timeline match published timeline?
**Flags:** Scope thresholds that jump classification, sewer/grease trap dependencies, annual inspection requirements

**Education Regulatory — Deepen the school-approval baseline:**
The school-approval skill (Step 2.9) already provides the state archetype, approval type, gating requirements, timeline, and baseline score. DO NOT re-research those — they are confirmed. Instead, focus Agent 3’s education regulatory research on the gaps school-approval does not cover:

- **Instructional hours equivalency pathways.** school-approval identifies the hours requirement; Agent 3 must find the ACTUAL statute text (not summary) and search for annual-hours equivalency pathways (e.g., 990-hour annual equivalency) that may offer flexibility beyond daily-hour minimums. These alternative pathways are strategic levers for Alpha's model.
- **Denial precedents for similar school models.** Search for "[state] private school denial" and "Alpha School" or "Unbound Academy" — any denial precedent is a material risk factor. Also check neighboring states. school-approval does not track model-specific denial history.
- **Curriculum and model-specific risks.** school-approval flags whether curriculum pre-approval is required; Agent 3 must assess whether Alpha's specific model (tech-forward, micro-school, hybrid) creates friction with stated curriculum requirements.
- **MANDATORY: Attorney verification.** If the SIR recommends a specific attorney or law firm, verify that person exists and practices education law in this state. Search their name + state bar + practice area. If unverifiable, search for a better recommendation: look for attorneys who represent private schools or charter school organizations in this state. Prioritize attorneys who have defended schools against state education agency actions.
- **Gatekeeper contacts beyond what school-approval found.** school-approval identifies the approving body; Agent 3 must find the specific person (name, title, direct phone, email) who handles private school applications.
- **Cross-check school-approval findings against SIR.** If the SIR’s edu regulatory section contradicts school-approval (e.g., SIR says notification-only but school-approval says APPROVAL_REQUIRED), flag the discrepancy and use school-approval’s classification (it has curated state-specific protocols).
- **Cunning path:** Pre-filing consultation with state education department? Informal scope call? Provisional status available?
**Flags:** Hard deadlines (from school-approval `calendar_window`), denial precedents, model-specific risks, unverifiable SIR contacts, discrepancies between SIR and school-approval

#### Research Agent 4: Fire Code & Life Safety

**Receives:** SIR Fire Department findings + baseline Fire/Life Safety section
**Primary sources:** State fire code, local fire marshal's web page, NFPA 101 and 72
**Must find:**
- State fire code sections for E-occupancy: sprinkler requirements, alarm requirements, emergency lighting
- Current NFPA edition adopted by state + state amendments
- **MANDATORY: State sprinkler threshold amendment.** Every state amends the IBC sprinkler threshold for E-occupancy (IBC §903.2.3). The SIR uses the base IBC threshold. Search for "[state] building code amendments sprinkler educational" and find the actual state-adopted threshold. If the state threshold differs from the SIR's, flag it prominently — this can eliminate or add a $9K–$18K cost item.
- **MANDATORY: NFPA 101 cross-check.** NFPA 101 (Life Safety Code) §14.3.5.1 has separate sprinkler requirements for educational occupancies that may be stricter than the state-amended IBC. Search for which standard the local AHJ enforces. If NFPA 101 and state-amended IBC conflict, document BOTH standards and flag the conflict for resolution with the fire prevention bureau.
- Fire alarm system tiers: determine occupant load thresholds for manual-only vs. voice EVACS. E-occupancy with OL >100 typically requires full voice EVACS — a major cost escalation over simple manual pull stations.
- Fire marshal review: concurrent with building or sequential?
- Fire marshal name, direct line, pre-submittal call opportunity
- Assess inspection report fire system data against E-occupancy code
- Fire alarm panel model — is it discontinued? Replacement cost implication?
- **Cunning path:** Parallel fire + building review? Who to call?
- **Cross-check SIR:** Does SIR fire scope match what code requires?
**Flags:** Mandatory sprinkler/alarm upgrades, system replacement thresholds, emergency lighting gaps, NFPA 101 vs. IBC conflicts

**Conflicting Standards Resolution Protocol:**
When research finds two standards that impose different requirements (e.g., NFPA 101 says sprinklers required, state-amended IBC says exempt):
1. Document both standards with exact section numbers and verbatim text
2. Identify which standard the local AHJ enforces (search for "[city] fire prevention bureau adopted codes" or call records)
3. If unresolvable from public sources, flag it as: "CONFLICTING STANDARDS — [Standard A] requires X, [Standard B] exempts. Resolution requires pre-submittal call to [AHJ name + contact if found]. Use the stricter standard for cost/timeline planning until resolved."
4. Never silently pick one standard over the other

#### Research Agent 5: ADA, HVAC & Accessibility

**Receives:** SIR Phase 7 findings + baseline ADA/HVAC section
**Primary sources:** ADA Standards 2010, state accessibility code, ASHRAE 62.1 Table 6-1, inspection report
**Must find:**
- ADA §206.4.1: accessible entrance percentage requirement vs. current
- Accessible route: door widths, ramps, thresholds, restroom dimensions
- Restroom ADA: clearances, grab bars, sink height, mirror height, turning radius
- State accessibility beyond federal ADA
- HVAC: ASHRAE 62.1 Table 6-1 for E-occupancy → calculate required CFM, compare to existing, state the delta as a number
- HVAC system model, age, tonnage from inspection report — look up specs
- Electrical: does occupancy change trigger panel/service upgrade?
- **Cunning path:** Phased ADA improvement (Priority 1 first per 28 CFR §36.403(g))?
- **Cross-check SIR:** Does SIR identify all ADA gaps? Are any underweighted?
**Flags:** Priority 1 vs. 4 ADA deficiencies, HVAC delta requiring structural work, electrical service issues

---

**Research output format — each agent saves findings as:**
```
### [Finding Title]
- Code Section: [exact section number]
- Quote: "[exact text from regulation]"
- Source URL: [direct URL to the primary source page]
- Implication: [what this means for timeline/cost/risk — be specific]
- Fastest Path: [cunning angle — what can we do with this to go faster or cheaper]
- SIR Delta: [does this match, contradict, or add to what the SIR says?]
```

**Quality Gate — before proceeding to Step 3.5:**
Each finding must have:
- An actual code section number (e.g., "IBC §1010.2.9" not "the fire code")
- A direct URL to the primary source
- A verbatim quote or specific numerical requirement
- A Fastest Path note that is a concrete action

If any research file fails the gate, re-run that agent with more specific instructions. If re-run still fails, accept the SIR baseline for that domain and tag fields with `(SIR-sourced — verify with jurisdiction)`.

### Step 3.5 — Cunning Path Synthesis

Before writing into the template, synthesize research into a strategy brief. Save to `workspace/[site]_strategy_brief.md`.

The brief must answer:

1. **Single biggest timeline lever:** The one thing, if done in week 1, that compresses the critical path the most. Name it, name who to call, name what to say.

2. **What the SIR got wrong or missed:** Compare research findings to SIR claims. Flag any discrepancy — a 2-week difference is material.

3. **The cunning path:** Informal pre-app consultation? Waiver argument? Parallel filing? Scope structuring to avoid higher-level review?

4. **Hidden landmines:** Requirements not in the SIR — overlay thresholds, state amendments, equipment discontinuation, sewer triggers, neighbor objection windows.

5. **Parallel vs. sequential map:** Which approvals are genuinely sequential blockers, which can run concurrently? Document where the "run everything parallel" assumption breaks.

6. **Fastest realistic opening:** If every parallel track is optimized — what date? What must go right? Name each condition.

### Step 4 — Merge Baseline + Research into Final Content

Read the baseline (`[site]_baseline.md`) and all research files. For each ENRICH field in the field mapping:

1. If research found a better source (primary code quote vs. SIR paraphrase) → use research version
2. If research found a new risk, contact, or faster path → add it
3. If research contradicts SIR → flag the discrepancy and use the more specific/recent source
4. If research came back empty for a field → keep SIR baseline, add `(SIR-sourced — verify with jurisdiction)`

Apply the Cunning Path Synthesis to shape the strategic recommendations throughout.

Save final merged content to `workspace/[site]_final_content.md`.

### Step 5 — Create the Google Doc

#### Step 5a — Find the location folder

Use `google_docs-find-document` to search for the site's folder inside `All Locations` (root folder ID: `1RqwLyx0duTeWQPJWu7-HOpfQNlbe5jzQ`):

```
tool: google_docs-find-document
source_id: google_docs__pipedream
arguments:
  searchQuery: "mimeType='application/vnd.google-apps.folder' and name='[Site Name]' and '1RqwLyx0duTeWQPJWu7-HOpfQNlbe5jzQ' in parents"
```

If not found, tell the user: "Could not find folder '[Site Name]' in Education Ops > All Locations. Please confirm the folder exists and the exact name."

#### Step 5b — Create the doc from template

```
tool: google_docs-create-document-from-template
source_id: google_docs__pipedream
arguments:
  templateId: "1SCHFogI1ID3lujJqmYVjR3ruSeTbMt7WKnPomeWio5s"
  name: "Alpha [Site Name] — Opening Plan"
  folderId: "[folder ID from Step 5a]"
  mode: ["Google Doc"]
  replaceValues: {}
```

**IMPORTANT:** Parameter is `templateId` (not `templateDocumentId`). `mode` must be `["Google Doc"]`. Pass `replaceValues: {}` — template uses `[bracket]` placeholders, not `{{mustache}}`.

### Step 6 — Replace Placeholders in the Google Doc

Use `google_docs-replace-text` to replace each placeholder with content from `[site]_final_content.md`.

**Exact parameter names:**
- `docId` (not `documentId`) for the document ID
- `replaced` (not `matchText`) for the text to find
- `text` (not `replaceWith`) for the replacement text
- `matchCase: true` for exact matching

**Font hygiene — CRITICAL:**
The template is entirely Arial. Research subagents often pull text from web sources carrying hidden font tags. Rules:
- Write all replacement text as plain ASCII/UTF-8
- Research agents write findings to `.md` files as plain text only
- Before any replace-text call, treat the replacement string as plain text
- The template font (Arial, 11pt) is the source of truth

**Collision handling:**
The template uses unique scenario placeholders to prevent collision:
- `[BEST_PZ_WEEKS]`, `[REAL_PZ_WEEKS]` — P&Z weeks per scenario
- `[BEST_BP_WEEKS]`, `[REAL_BP_WEEKS]` — Building Permit weeks per scenario
- `[BEST_CFFFE_WEEKS]`, `[REAL_CFFFE_WEEKS]` — Construction + FF&E weeks per scenario
- `[BEST_HEALTH_STATUS]`, `[REAL_HEALTH_STATUS]` — Health permit per scenario
- `[BEST_HVAC_STATUS]`, `[REAL_HVAC_STATUS]` — HVAC per scenario
- `[BEST_RESTROOM_STATUS]`, `[REAL_RESTROOM_STATUS]` — Restrooms per scenario
- `[BEST_EXIT_STATUS]`, `[REAL_EXIT_STATUS]` — 2nd exit per scenario
- `[BEST_KITCH_STATUS]` — Best Case kitchenette
- `[REAL_SINK_1]`, `[REAL_SINK_2]`, `[REAL_SINK_3]` — Realistic sink items
- `[WORST_SINK_1]`, `[WORST_SINK_2]`, `[WORST_SINK_3]` — Worst Case sink items
- Worst case uses `[X] Weeks` (capital W) — unique to worst case

**NEVER put `\n` in a `replaced` string.** The API does not join cross-paragraph content. Multi-line `replaced` strings silently fail or destroy bullet nesting. Each `replaced` string must match a single paragraph only. `\n` in the `text` (replacement) side is safe.

**Response shape:** A successful replace returns `{"textContent": "...", ...}`. A null/empty response means the string was not found — check for exact character match.

**Replacement order:**
1. Document Title and Header
2. Summary Table cells
3. Executive Summary (Recommendation, Target open, What we know, What we don't know yet)
4. Permit Paths (Best/Realistic/Worst — use scenario-specific placeholders)
5. Gating Factors (Gates 0, 1, 2a, 2b — delete unused gate headings)
6. Process, Citations, Timeline, and Risks (all regulatory domains)
7. Footer sections (Lease Conditions, Links)

### Step 7 — Quality Verification

Before delivering, verify against all three executive checklists from `references/executive-mindset.md`:

**Andy's checklist:**
- [ ] Recommendation line is first in Executive Summary — Go/Conditional Go/No Go, one sentence, no hedging
- [ ] Every date has a named condition
- [ ] All major risks appear in Executive Summary, not only section 4
- [ ] Every citation is an exact quote from a named source (SIR or primary)
- [ ] Summary table matches scenario timelines in Permit Paths

**Neeraj's checklist:**
- [ ] Every requirement explains what triggers it
- [ ] Every code citation includes specific section number
- [ ] Every risk has a specific trigger event
- [ ] Sequential vs. parallel stated explicitly at least once per section
- [ ] At least two independent sources confirm each key dependency (where research succeeded)

**JC's checklist:**
- [ ] Every major bullet = the answer; sub-bullets = the proof
- [ ] Every timeline in specific weeks
- [ ] Nothing important is buried
- [ ] Contacts include Name, Title, Phone, AND Email

**Formatting checklist:**
- [ ] H2: Executive Summary, Permit Paths, Gating Factors, Process Citations Timeline and Risks
- [ ] H3: scenario names, Gate names, regulatory domain names
- [ ] H4: Citations, Timeline, Risks, construction sub-items
- [ ] H5: individual requirements, risk names
- [ ] H6: sub-risks under Building/Health
- [ ] Bullet nesting levels 0-4 preserved
- [ ] Doc is pageless (inherited from template)

**Completeness checklist:**
- [ ] Header has site name and address
- [ ] Summary table has all 12 cells filled (or placeholder)
- [ ] Three permit path scenarios with dates and costs
- [ ] At least 2 gating factors identified
- [ ] Every permit track has citations, timeline, and risks
- [ ] Construction section covers all trades from SIR
- [ ] Edu Regulatory present
- [ ] All citations traceable (SIR or primary source URL)
- [ ] Placeholder section present for human-input items

### Step 8 — Deliver

Return the Google Doc URL:
```
https://docs.google.com/document/d/[documentId]/edit
```

Tell the user:
1. The doc URL
2. Research enrichment summary: what domains got upgraded vs. stayed SIR-baseline
3. Any `[TBD]` or `[PLACEHOLDER]` fields still outstanding and why
4. Cunning path highlights: the top 2-3 strategic findings from research
5. That headings are collapsible by clicking the triangle next to each H2/H3

Also save a local markdown copy to:
```
reports/[address-kebab-case]_opening-plan_{YYYY-MM-DD}.md
```

---

## Hard Rules

1. **Follow the template layout exactly.** Section order, heading levels, and formatting from `references/template-content.md` are the spec.
2. **Do not invent SIR quotes.** Every `SIR [Section] ([Date]): "..."` citation must contain text that actually appears in the SIR.
3. **Do not invent research quotes.** Every primary-source citation must link to an actual URL the reader can verify.
4. **Every risk must have Trigger + Impact + Managing it.** No partial risk entries.
5. **Gating factors must be binary.** Each gate has two outcomes with consequences.
6. **Three scenarios required.** Best, Realistic, Worst. Realistic uses explicit derivation logic from field-mapping.md.
7. **Plain English only.** 8th-grade reading level. No code jargon without explanation.
8. **Construction sub-sections only for identified issues.** Do not pad with "no issues found."
9. **Dollar amounts with commas.** $521,000 not $521000.
10. **Timeline ranges with en-dashes.** 2–16 weeks not 2-16 weeks.
11. **Flag every placeholder.** `[PLACEHOLDER — reason]` for anything not populatable. Never leave a field blank or delete a section.
12. **Edu Regulatory always last** in the Process section.
13. **Permit Paths total cost** must equal sum of permit fees + estimated construction for that scenario.
14. **SIR baseline is the floor.** If research cannot improve a field, the SIR value stands — tagged `(SIR-sourced — verify with jurisdiction)`.
15. **Use correct API parameter names.** `docId`, `replaced`, `text`, `matchCase`. Wrong names silently fail.
16. **Never use `\n` in `replaced` strings.** Destroys bullet nesting. Only in `text` (replacement) side.
17. **Site kill language when warranted.** Say "site kill" — not "significant timeline risk."
18. **Sequential vs. parallel, always explicit.** Never leave ambiguous.

---

## Working With Partial Data

- **SIR not available:** Cannot run. SIR is the required baseline input.
- **Zoning unknown:** Write `[TBD — zoning status not confirmed. File MUP/CUP/by-right TBD pending planner call]`
- **Contacts missing:** Write `[TBD — contact needed. Call [Department] at [phone if known]]`
- **Cost unknown:** Write `[PLACEHOLDER — awaiting contractor estimate]`
- **Timeline uncertain:** Write `[TBD — depends on [Gate X] resolution]`
- **Vendor return not available:** Use SIR data only for construction scope. Tag construction fields: `(SIR-sourced — no vendor inspection available)`
- **Target open date not provided:** Insert `[PLACEHOLDER — requires lease signing date + best-case timeline = X weeks from signing]`

Never leave a placeholder blank or delete a section. The structure's value is showing what's known and unknown.

---

## Common Errors to Avoid

1. **Don't add or remove heading levels.** H2→H3→H4→H5→H6 must be preserved exactly.
2. **Don't flatten bullet nesting.** Position communicates information hierarchy.
3. **Don't use generic citations.** "According to city code" is not a citation.
4. **Don't omit contacts.** Name, Title, Phone, Email — all four fields.
5. **Don't hedge the Recommendation.** "Conditional Go, pending zoning confirmation — MUP approval expected within 6 weeks per Town Planner" is a recommendation. "It seems like it could work" is not.
6. **Don't use replace-text on heading-level placeholder brackets alone.** Replace the entire heading text string.
7. **Handle collisions with unique scenario placeholders.** Use the template's built-in `[BEST_*]` / `[REAL_*]` / `[WORST_*]` tokens.
8. **BP Risks nesting offset.** Building Permit Risks bullets are 2 nesting levels deeper than original Burlingame doc. This is template design, not a bug.

---

## Reference Files

- `references/field-mapping.md` — Complete SIR-to-plan field mapping with scenario derivation logic and ENRICH protocol
- `references/template-content.md` — Full section-by-section template hierarchy with guidance
- `references/executive-mindset.md` — Andy, Neeraj, and JC evaluation standards

Read all three before generating any content.
