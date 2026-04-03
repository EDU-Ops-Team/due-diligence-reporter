---
name: school-approval
description: Score addresses for education approval difficulty. Rates how hard it is to legally operate a private K-8 school by state. Used as a sub-skill of alpha-building-suitability.
version: 3.0.0
requires:
  tools:
    - WebSearch
    - WebFetch
---

# Score Addresses for Education Approval

Given an address, determine how hard it is to legally open a recognized private K-8 school there and what must be completed before doors can open.

The three questions this skill must answer: **Can we do this? What will it take? How long will it take?**

## Scope Boundary

This skill covers **education regulatory approval only** — what the state or local education authority requires before a private K-8 school may legally operate.

**In scope:**
- State education department registration, licensing, or approval requirements
- Education authority-mandated inspections (e.g., TN SFMO inspection required by SBE approval; NC fire/sanitation required by DNPE notification)
- Teacher certification requirements imposed by the education authority
- Curriculum pre-approval required by the education authority
- Local education bodies that must approve school operation (e.g., MA school committee)
- Calendar windows controlled by the education approving body

**Out of scope — do not research or report on:**
- Certificate of Occupancy (CO) process — universally required for any occupancy; covered by separate Site Investigation Report workflow
- Zoning, land use, or planning approvals — covered by SIR workflow
- Building permits, fire marshal inspections as part of CO, architectural submissions for occupancy
- Business entity registration (LLC, nonprofit formation)

> A building inspection or fire inspection is only **in scope** if the education authority explicitly requires it as a condition of granting school approval. The TN State Fire Marshal inspection (required by SBE before school approval) is in scope. A standard fire marshal sign-off as part of a CO is not.

---

## Step 1 — Classify State Archetype

Extract the state from the address. Classify it into one of five archetypes using the **State Archetype Reference** below. This single determination drives every subsequent step.

| Archetype | Description | Typical Timeline |
|---|---|---|
| `MINIMAL` | No registration, licensing, or approval required | 0 days post-CO |
| `NOTIFICATION` | File/register only — no approval review, no gating | 0–14 days, concurrent with CO |
| `APPROVAL_REQUIRED` | Government body must approve before opening | 4–16+ weeks |
| `HEAVILY_REGULATED` | Multiple gatekeeping requirements, high upfront burden | 8–24+ weeks |
| `WINDOWED` | Approval tied to fixed calendar cycles; missing a window = months of delay | Calendar-dependent |

> **Critical:** Wrong archetype = wrong plan. If the archetype is uncertain, default to `APPROVAL_REQUIRED` and flag `"ARCHETYPE_UNCERTAIN"`.

---

## Step 2 — Apply Archetype-Specific Research Protocol

Web search depth and targets vary by archetype. Do not run a generic search. Use the protocol for the classified archetype.

### MINIMAL States

The goal is to **confirm** no process exists, not to find one.

Search:
1. `[STATE_FULL_NAME] private school registration requirements site:[state].gov`
2. `[STATE_FULL_NAME] Department of Education private school`

Confirm:
- No state approval, registration, or licensing required for private K-8
- What minimum curriculum subjects are required (if any)
- Whether CO alone is sufficient to open

Set `confidence_0_1 = 0.9` if official DOE page confirms minimal requirements. Do not inflate timeline.

### NOTIFICATION States

The goal is to find the **exact filing** required and confirm it does not gate opening.

Search:
1. `[STATE_FULL_NAME] private school notice of intent filing requirements`
2. `[STATE_FULL_NAME] private school fire safety sanitation inspection`

Confirm:
- What form/filing is required and where it is submitted
- Whether any inspections (fire, sanitation) are required before opening
- Whether fingerprinting/background check is required before opening
- Whether the filing can run concurrent with the CO process

### APPROVAL_REQUIRED States

The goal is to find **who approves, what they require, and how long it takes**.

Search:
1. `[STATE_FULL_NAME] private school approval application requirements`
2. `[STATE_FULL_NAME] [approving body] private school approval timeline`
3. `[STATE_FULL_NAME] private school teacher certification requirement`
4. `[STATE_FULL_NAME] private school curriculum approval`

Fetch the official state DOE or approving body page. Extract:
- Name and contact of approving body
- Required documents (governance, credentials, curriculum, emergency plans, policy manuals)
- Whether teacher certification is required before opening
- Whether curriculum must be pre-approved
- Stated review timeline
- Whether an on-site inspection is required before approval

### HEAVILY_REGULATED States

Apply the APPROVAL_REQUIRED protocol above, plus search for:
1. `[STATE_FULL_NAME] private school cash reserve requirement`
2. `[STATE_FULL_NAME] private school architectural submission requirement`

Flag any financial or submission requirements that must be satisfied before opening.

### WINDOWED States

The calendar constraint is the critical path item. Run this first.

Search:
1. `[STATE_FULL_NAME] private school approval calendar deadline [YEAR]`
2. `[STATE_FULL_NAME] [approving body] meeting schedule application deadline`

Determine:
- Next available approval window date
- Application submission deadline for that window
- Whether provisional or interim status is available if the next window is >3 months away

If the next window is more than 3 months out, flag `"CALENDAR_RISK"` and note the impact on the opening timeline.

### Tennessee (Special Case)

Tennessee uses a five-category approval system. Run this specific protocol:

1. Determine which category Alpha School would qualify under — target Category II (SBE-approved accrediting agency) or Category V (baccalaureate-degreed teachers)
2. Search: `Tennessee private school category II accrediting agency application`
3. Contact: `Private.Schools@tn.gov` — State Fire Marshal inspection must be scheduled before approval
4. Category II approval can run concurrent with building work
5. Set timeline: 4–12 weeks depending on category and accrediting agency responsiveness

---

## Step 3 — Check for Local Education Overlay

In some cities and counties, a **local education authority** layers additional approval requirements on top of state requirements. This is most common in `APPROVAL_REQUIRED` and `HEAVILY_REGULATED` states.

Search:
- `[CITY] [COUNTY] private school education approval local requirement`

A local overlay exists only if a **local education body** (school board, school committee, county education office) must formally approve the school before it may operate. Zoning boards, planning commissions, and building departments are not education authorities and are never a local overlay for this analysis.

Flag any local education overlay in `local_requirements.has_local_overlay`. Add the additional timeline to `timeline_days_preopen`.

> MA is uniquely high-risk here: each town's school committee operates independently and has discretionary authority. There is no state-level appeal. Treat each MA city as a distinct approval environment.

---

## Step 4 — Identify Pre-Open Gating Requirements

For every state, confirm whether any of the following must be completed **before doors open**. All items must be required by the **education authority** — not as a general business or building requirement.

| Requirement | When it applies |
|---|---|
| Teacher certification | WA (all teachers must be certificated), some others |
| Curriculum pre-approval | WA, NY, some Approval Required states |
| Education authority-mandated inspection | NC (fire/sanitation required by DNPE as part of NOI), TN (SFMO inspection required by SBE before approval) — only when the education authority explicitly conditions approval on it |
| Background checks/fingerprinting | FL (owner fingerprinting required as part of DOE registration), some others |
| Cash reserve demonstration | MI ($50K for non-church schools) |
| Architectural/traffic submission | NV (required by education approval process, even for <20 students) |
| On-site inspection by education authority | NY (NYSED site visit before registration) |

> **Do not flag** fire marshal sign-offs, building inspections, or CO requirements that are part of standard occupancy — these are out of scope regardless of archetype.

---

## Step 5 — Compose Output

Return the full JSON structure. Never crash, never return null.

## Output Contract

```json
{
  "factor_id": "education_approval",
  "address": "123 Main St, Austin, TX 78701",
  "state": "TX",
  "locality": { "city": "Austin", "county": "Travis County", "state": "TX" },
  "archetype": "MINIMAL",
  "approval_authority": "state",
  "approval_authority_url": "https://tea.texas.gov/",
  "approval_type": "NONE",
  "gating_before_open": false,
  "calendar_window": null,
  "ease_score_0_10": 9.5,
  "score_0_100": 95,
  "zone": "green",
  "timeline_days_preopen": { "min": 0, "likely": 7, "max": 30 },
  "requirements_summary": "Texas has minimal requirements. No state approval needed.",
  "requirements_steps": [
    { "step": "Confirm curriculum covers reading, spelling, grammar, math, good citizenship", "gating": false },
    { "step": "Open when CO is issued", "gating": false }
  ],
  "preopen_requirements": {
    "teacher_certification_required": false,
    "teacher_notes": "",
    "curriculum_approval_required": false,
    "health_safety_inspection_required": false,
    "background_check_required": false,
    "background_check_notes": "",
    "financial_reserve_required": false,
    "financial_reserve_notes": "",
    "architectural_submission_required": false
  },
  "local_requirements": {
    "has_local_overlay": false,
    "local_notes": ""
  },
  "esavoucher_flag": "NOT_PARTICIPATING",
  "source_urls": ["https://tea.texas.gov/"],
  "source_search_date": "2026-04-03",
  "confidence_0_1": 0.9,
  "data_quality_flags": [],
  "rules_version": "3.0.0"
}
```

### `esavoucher_flag` values
- `NOT_PARTICIPATING` — operating under baseline requirements only
- `PARTICIPATING` — ESA/voucher participation adds accreditation/reporting layers (scope expands significantly)
- `UNKNOWN` — not determined; assume NOT_PARTICIPATING for this report

### `calendar_window` (Windowed states only)
```json
{
  "next_window_date": "2026-06-15",
  "submission_deadline": "2026-03-01",
  "provisional_available": false,
  "calendar_risk": true
}
```

---

## Zone Thresholds

- **GREEN**: score_0_100 >= 80
- **YELLOW**: score_0_100 41–79
- **RED**: score_0_100 <= 40

## Approval Types

- `NONE` — No approval needed, just open (Minimal)
- `REGISTRATION_SIMPLE` — File only, no review gate (Notification)
- `LOCAL_APPROVAL_REQUIRED` — Local body approval required (e.g., MA school committee)
- `LICENSE_REQUIRED` — State license required before operating
- `CERTIFICATE_OR_APPROVAL_REQUIRED` — Formal state approval or certificate
- `COMPLEX_OR_OVERSIGHT` — Multi-step, long timelines, or calendar-gated
- `UNKNOWN` — No data found; use default

---

## Scoring Adjustments from Research

Start from the baseline score below. Adjust based on what research confirms:

| Finding | Adjustment |
|---|---|
| Teacher certification required before opening | −5 |
| Curriculum must be pre-approved before opening | −5 |
| State or local inspection required before opening | −5 |
| Local overlay approval required before opening | −10 |
| Cash reserve or financial submission required | −8 |
| Calendar window adds >90 days to timeline | −10 |
| Background check or fingerprinting required before opening | −3 |
| Official DOE page confirms simpler process than baseline | +5 (max) |

---

## Missing Data Rule

If state cannot be determined or no search results found:
- Return zone="yellow", score_0_100=70, confidence=0.3, archetype="UNKNOWN"
- Flags: `"ADDRESS_STATE_UNRESOLVED"` and/or `"NO_OFFICIAL_SOURCE_FOUND"`
- NEVER crash or return null

---

## State Archetype Reference

Use this as the starting point for Step 1 classification. Statutory citations are embedded to guide web search.

### MINIMAL — No Registration or Approval Required (0 days post-CO)

| State | Key Requirements | Statute |
|---|---|---|
| TX | No registration, licensing, or TEA approval. Offer reading, spelling, grammar, math, good citizenship. | TEC §25.086 |
| AZ | No accreditation, registration, licensing, or approval. File affidavit with county superintendent ages 6–16 (notification, not approval). | ARS §15-802 |
| AR | No accreditation, registration, licensing, or approval from state. | US DOE State Regulation report |
| AL | Non-church: register annually by Oct 10 with AL DOE. Church schools: fully exempt. No state approval or licensing. | AL Code §16-28-1, §16-28-7 |
| UT | No state approval required. | EdChoice Rankings 2025 |
| ID | No approval required. | EdChoice Rankings 2025 |
| WY | No approval required. | EdChoice Rankings 2025 |
| MT | No approval required. | EdChoice Rankings 2025 |
| MO | No approval required. | EdChoice Rankings 2025 |
| KS | No approval required. | EdChoice Rankings 2025 |
| NE | No approval required. | EdChoice Rankings 2025 |
| IL | No approval required. | EdChoice Rankings 2025 |

### NOTIFICATION — File/Register Only, No Approval Gate (0–14 days, concurrent with CO)

| State | Key Requirements | Statute |
|---|---|---|
| FL | Register with DOE + annual survey. Owner fingerprinting/background check. DOE has no jurisdiction over private schools. | FL Stat. §1002.42 |
| CA | File Private School Affidavit (PSA) annually with CDE. Online, immediate confirmation. Not approval. | CA EC §33190 |
| NC | Notice of Intent to DNPE 30–60 days before operation. Fire/sanitation inspections. Annual standardized testing. | NC GS §115C-547–562 |
| DE | Register with DDOE (online). DDOE does not approve or monitor curriculum. | 14 Del. C. §2703, §2704 |
| GA | Registration required. | EdChoice Rankings 2025 |
| SC | Registration required. | EdChoice Rankings 2025 |
| VA | Registration required. | EdChoice Rankings 2025 |
| TN | See Tennessee special case above — Category system, 4–12 weeks. | TCA §49-6-3001; SBE Rule 0520-07-02 |

### APPROVAL_REQUIRED — Government Body Must Approve (4–16+ weeks)

| State | Approving Body | Key Gate | Statute |
|---|---|---|---|
| MA | Local school committee (varies by town) | "Thoroughness and efficiency" equal to public schools. No state-level appeal. | MA Gen. Laws c.76 §1 |
| WA | State Board of Education | Annual approval. Certificated teachers required. 180 days/1,000 hours. Health/fire inspections. | RCW 28A.195.010 |
| NY | Board of Regents / NYSED | Provisional charter (nonprofit) or commissioner consent (for-profit). On-site visit required. | NY Education Law |
| MD | MD State Board of Education | Certificate of approval. Church orgs may be exempt. | MD Ed. §2-206(e) |
| OH | State Board | Certificate or approval required. | EdChoice Rankings 2025 |
| OR | State Board | Certificate or approval required. | EdChoice Rankings 2025 |
| KY | State Board | Certificate or approval required. | EdChoice Rankings 2025 |
| HI | State Board | Certificate or approval required. | EdChoice Rankings 2025 |
| PA | State Board | Certificate or approval required. | EdChoice Rankings 2025 |
| NJ | State Board | Certificate or approval required. | EdChoice Rankings 2025 |

### HEAVILY_REGULATED — Multiple Gatekeeping Requirements (8–24+ weeks)

| State | Key Burden | Source |
|---|---|---|
| NV | Traffic studies + architectural renderings even for <20 students. Most regulated nationally. | EdChoice Rankings 2025 |
| ND | State approval + teacher certification. | ND Century Code Title 15.1 |
| MI | Non-church schools: $50,000 cash reserves before opening. | MI Revised School Code |
| IN | Teacher certification requirements. | EdChoice Rankings 2025 |

### WINDOWED / CALENDAR-DEPENDENT (0–6+ months added delay on top of review time)

| State | Calendar Constraint | Source |
|---|---|---|
| NY | Board of Regents: March 1 submission deadline → June approval | NYSED PSR process |
| WA | Annual SBE approval cycle | RCW 28A.195.010 |
| MD | Fixed approval cycles; may block mid-year openings | MD Ed. §2-206(e) |

> NY and WA appear in both APPROVAL_REQUIRED and WINDOWED. Classify as `WINDOWED` — the calendar is the binding constraint.

### Puerto Rico — LICENSING REQUIRED (8–16+ weeks, adaptive)

Compulsory licensing for all private K–12 (3 L.P.R.A. §148l). Requirements: teacher certification from PR Secretary of Education, facilities inspection, education plan, economic viability study. Authority: Board of Postsecondary Institutions under PR Department of State (Law 212-2018). Local engagement and pre-briefing meetings are critical to managing timeline.

---

## Baseline Score Table

Starting scores before research adjustments.

| State | Score | Archetype | Approval Type | Gating | Timeline (days) |
|---|---|---|---|---|---|
| TX | 95 | MINIMAL | NONE | No | 7 |
| ID | 92 | MINIMAL | NONE | No | 7 |
| AZ | 90 | MINIMAL | NONE | No | 7 |
| WY | 90 | MINIMAL | NONE | No | 7 |
| MT | 88 | MINIMAL | NONE | No | 7 |
| MO | 88 | MINIMAL | NONE | No | 7 |
| IL | 86 | MINIMAL | NONE | No | 7 |
| KS | 86 | MINIMAL | NONE | No | 7 |
| NE | 86 | MINIMAL | NONE | No | 7 |
| AL | 85 | MINIMAL | NONE | No | 7 |
| AR | 85 | MINIMAL | NONE | No | 7 |
| UT | 85 | MINIMAL | NONE | No | 7 |
| AK | 82 | MINIMAL | NONE | No | 7 |
| OK | 82 | NOTIFICATION | REGISTRATION_SIMPLE | No | 30 |
| CO | 80 | NOTIFICATION | REGISTRATION_SIMPLE | No | 30 |
| FL | 78 | NOTIFICATION | REGISTRATION_SIMPLE | No | 14 |
| GA | 78 | NOTIFICATION | REGISTRATION_SIMPLE | No | 14 |
| NC | 78 | NOTIFICATION | REGISTRATION_SIMPLE | No | 14 |
| SC | 76 | NOTIFICATION | REGISTRATION_SIMPLE | No | 14 |
| VA | 75 | NOTIFICATION | REGISTRATION_SIMPLE | No | 14 |
| WI | 75 | NOTIFICATION | REGISTRATION_SIMPLE | No | 30 |
| CA | 73 | NOTIFICATION | REGISTRATION_SIMPLE | No | 14 |
| DE | 72 | NOTIFICATION | REGISTRATION_SIMPLE | No | 14 |
| MI | 55 | HEAVILY_REGULATED | LICENSE_REQUIRED | Yes | 90 |
| NM | 72 | NOTIFICATION | REGISTRATION_SIMPLE | No | 30 |
| TN | 68 | APPROVAL_REQUIRED | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes | 60 |
| OH | 65 | APPROVAL_REQUIRED | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes | 90 |
| OR | 65 | APPROVAL_REQUIRED | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes | 90 |
| KY | 65 | APPROVAL_REQUIRED | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes | 90 |
| HI | 63 | APPROVAL_REQUIRED | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes | 90 |
| NH | 63 | APPROVAL_REQUIRED | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes | 90 |
| CT | 62 | APPROVAL_REQUIRED | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes | 90 |
| PA | 60 | APPROVAL_REQUIRED | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes | 90 |
| NJ | 60 | APPROVAL_REQUIRED | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes | 90 |
| LA | 60 | APPROVAL_REQUIRED | CERTIFICATE_OR_APPROVAL_REQUIRED | Yes | 90 |
| MA | 55 | APPROVAL_REQUIRED | LOCAL_APPROVAL_REQUIRED | Yes | 120 |
| MD | 50 | WINDOWED | COMPLEX_OR_OVERSIGHT | Yes | 180 |
| WA | 48 | WINDOWED | COMPLEX_OR_OVERSIGHT | Yes | 180 |
| IN | 45 | HEAVILY_REGULATED | LICENSE_REQUIRED | Yes | 150 |
| NY | 42 | WINDOWED | COMPLEX_OR_OVERSIGHT | Yes | 365 |
| ND | 40 | HEAVILY_REGULATED | COMPLEX_OR_OVERSIGHT | Yes | 180 |
| NV | 35 | HEAVILY_REGULATED | COMPLEX_OR_OVERSIGHT | Yes | 180 |
| DC | 35 | HEAVILY_REGULATED | COMPLEX_OR_OVERSIGHT | Yes | 365 |

---

## Key Reference Facts (Embedded Knowledge)

Use these facts to guide research and verify findings. Do not re-derive what is already confirmed below.

**Regulatory structure:**
- No federal private school license exists. CO (Certificate of Occupancy) is universally required regardless of archetype.
- 34 states require some form of registration; 25 require approval; 35 make accreditation optional (EdChoice 2025).
- Least restrictive: DE, UT, AR, AZ, FL, NC. Most restrictive: NV, ND, NY, MD, VT, ME, IN (EdChoice 2025).

**Common failure modes:**
- Missing or misordered documents cause 60% of delays in Approval Required states; correction adds 4–8 weeks.
- Early legal review saves 2–3 months in Approval Required states (Husch Blackwell).
- Operators waste 3–6 weeks preparing approval packages for Minimal/Notification states that don't require them.
- Calendar constraints in Windowed states (NY, WA, MD) are often discovered after lease commitment — flag early.

**ESA/Voucher interaction:**
- TX (TEFA), AZ (ESAs), FL (FES): minimal baseline to operate, but choice program participation adds accreditation, testing, and financial reporting layers.
- These are optional programs — they do not affect the right to open a private school.
- ESA/voucher requirements are out of scope for this skill; flag and note separately.

**Political/relational risk:**
- In Approval Required states with discretionary authority, public statements critical of public schools or administrators can damage the application. Note this risk in `requirements_summary` for MA, WA, NY, MD.

**Primary reference sources to check during research:**
- US DOE Office of Non-Public Education: `ed.gov/birth-grade-12-education/education-choice/state-regulation-of-private-and-home-schools`
- EdChoice School Starter Checklist Rankings (2025): `edchoice.org/research/school-starter-checklist-rankings/`
- State DOE websites (official, always preferred over secondary sources)

---

## Test Cases

1. `Austin, TX` → MINIMAL, GREEN, ~95, gating=false, source_urls populated, 0-day timeline
2. `Boston, MA` → APPROVAL_REQUIRED, YELLOW, ~50–55, LOCAL_APPROVAL_REQUIRED, gating=true, ~120 days, political risk noted
3. `Las Vegas, NV` → HEAVILY_REGULATED, RED, ~35, gating=true, architectural submission flagged
4. `Nashville, TN` → APPROVAL_REQUIRED (Category system), YELLOW, ~68, gating=true, SFMO inspection flagged
5. `Albany, NY` → WINDOWED, RED, ~42, calendar_window populated, gating=true
6. `Seattle, WA` → WINDOWED, RED, ~48, calendar_window populated, certificated teachers flagged
7. `Any address` → `source_urls` non-empty OR `data_quality_flags` includes "NO_OFFICIAL_SOURCE_FOUND"
8. `Unknown State, XX` → YELLOW, 70, archetype="UNKNOWN", "ADDRESS_STATE_UNRESOLVED" flag

---

## Step 6 — Produce Human-Readable Report

After the JSON, produce the human-readable report defined in `references/report-template.md`. Follow that template exactly — every section in order, no sections skipped. Populate from the JSON fields you just composed. The report is the primary deliverable for human analysts; the JSON is the machine-readable companion.

---

## Output for Parent Skill (alpha-building-suitability)

Return:
- `approval_score`: score_0_100 / 100 (0–1)
- `zone`: GREEN / YELLOW / RED
- `archetype`: the five-archetype classification
- `gating`: boolean — must approve before opening?
- `timeline_days`: likely timeline in days
- `calendar_risk`: boolean — is a calendar window the binding constraint?
- `teacher_certification_required`: boolean — pre-open requirement
- `has_local_overlay`: boolean — additional local approval required
