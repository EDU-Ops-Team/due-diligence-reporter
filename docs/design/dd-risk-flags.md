# DD Risk Flags — Design Doc

**Status:** Shipped (Phase 4, April 2026)
**Source of truth:** `src/due_diligence_reporter/risk_flags.py`
**Tracking issue:** [GFooteGK1/due-diligence-reporter#20](https://github.com/GFooteGK1/due-diligence-reporter/issues/20) (closed)

This is a retrospective design doc for the `dd_risk_flags[]` system. It exists so a reader can understand the whole pipeline — from raw report tokens to a colored chip on the Portfolio dashboard — by reading one file.

---

## What this is

A **single, deduped list of risk flags** attached to every DD report. Each flag is a four-field record:

```
{ category, severity, source, summary }
```

The list rolls up signals from four different parts of the DD report into one canonical structure that the dashboard can render as a single column.

### Example

For a site with a yellow school-approval zone and a sprinkler IBC failure, the publisher emits:

```json
"dd_risk_flags": [
  {
    "category": "occupancy",
    "severity": "high",
    "source": "e_occupancy",
    "summary": "Sprinkler system insufficient for Group E occupancy load"
  },
  {
    "category": "ed_reg",
    "severity": "medium",
    "source": "school_approval",
    "summary": "State school approval — Certificate of Approval — ~120-day timeline"
  }
]
```

The dashboard renders this as a yellow-tone "2" chip in the Portfolio Risks column, with a hover tooltip reading "2 risks: 1 high, 1 medium."

---

## Why this exists

Before Phase 4, risks lived in four different shapes scattered across the report:

| Source | Shape before |
|---|---|
| Permit history | Free-text `risk_flags` array with three severity strings |
| E-occupancy / IBC | Structured `ibc_flags` plus a free-text summary, plus a derived zone color |
| School approval | A zone color plus an approval type and a timeline |
| SIR Risk Watch | Pure free text |

Four shapes meant:
- The dashboard had no way to show "how risky is this site overall?" without four separate columns
- The DRI had to hunt across four parts of the report to assemble a risk picture
- Two different sources flagging the same issue (e.g. zoning showing up in both permit history and SIR) couldn't be deduped

`dd_risk_flags[]` solves all three. The reporter does the work of canonicalizing once; the dashboard just renders.

---

## The four fields

### Category — what kind of risk

Locked enum of 10 values:

| Category | Covers |
|---|---|
| `zoning` | Use permitted? Conditional? Variance needed? |
| `occupancy` | IBC Group E gates — sprinklers, exits, travel distance |
| `ahj_history` | Authority Having Jurisdiction track record (slow approvals, friction history) |
| `parking` | Spaces required vs available, ratio gates |
| `traffic` | Drop-off / pickup capacity, queue length |
| `environmental` | Septic, soil, contamination, well, stormwater |
| `flood_zone` | FEMA flood zone risk |
| `historic_district` | Historic preservation review needed |
| `accessibility` | ADA, ramps, elevators |
| `ed_reg` | State education-regulator approval |

**Why these 10:**
- Septic was originally its own category but folded into `environmental` because it always travels with soil/well/stormwater concerns and splitting them produces noise.
- `ed_reg` is the short name for state-education-regulator approval. The longer `state_education_regulator` was rejected because the column has to fit on a dashboard chip tooltip.
- The list is **closed**. New risk types either map to one of the 10 or get a category added via this doc + a code change. The dashboard validates against this list and silently drops anything outside it.

### Severity — how bad

Three levels: `high`, `medium`, `low`.

The severity rules are deterministic and per-source (see below). The reporter never guesses — every severity is derived from a specific signal in the source data.

### Source — where it came from

Locked enum of 4 values, matching the four upstream archetypes:

- `permit_history` — comes from the upstream AI SIR / source-evidence build, which writes a `permit_history.risk_flags` array into the report's token bag. DDR does not initiate live Shovels API calls during report generation; the integration was relocated upstream so permit signals are available at SIR time rather than at DD time.
- `e_occupancy` — comes from the IBC Group E analysis (the q2 report section)
- `school_approval` — comes from the school-approval subsystem's zone derivation
- `sir_risk_watch` — comes from the free-text "Risk Watch" section of the SIR

Source is preserved so the DRI can trace any flag back to its origin report section. The dashboard doesn't currently render source, but the data is there if a future view wants to break flags down by where they came from.

### Summary — human-readable phrase

A short phrase (capped length) describing the specific risk. The summary is what a human reads to understand what the flag actually means.

---

## How flags get derived (per source)

Source: `risk_flags.py` → `derive_risk_flags(report_data)`. The function calls four ingesters in turn and then runs the result through dedup + sort.

### Permit history → severity from upstream tag

The permit-history subsystem already tags its risk flags with one of three labels. We map them directly:

| Upstream tag | Severity |
|---|---|
| `acquisition_condition` | `high` |
| `risk_note` | `medium` |
| `info` | omitted (not a risk) |

Category comes from the permit-history flag's own category field, validated against the 10-value enum. If it doesn't match, the flag is dropped.

### E-occupancy / IBC → severity from keyword scan

The IBC analysis produces `ibc_flags` (structured) and an `ibc_summary` (free text). We scan the text for two things:

**Category** — keyword map. First match wins:
- `sprinkler`, `travel`, `exit`, `egress` → `occupancy`
- `ada`, `ramp`, `accessib` → `accessibility`
- `parking`, `parking ratio` → `parking`
- (other keywords map to other categories)

**Severity** — hard-fail keyword scan:
- Text contains any of `fail`, `exceeds`, `insufficient`, `not permitted`, `prohibited`, `non-compliant`, etc. → `high`
- Otherwise → `medium`

**Fallback:** If `q2.e_occupancy_zone == "Red"` and no IBC flags surfaced (rare — usually the zone is red because there are flags), we emit a single `occupancy:high` flag so the red zone never silently disappears. Dedup handles the case where this collides with a real IBC flag.

### School approval → severity from zone color

The school-approval subsystem produces a zone color. Direct mapping:

| Zone | Severity | Rationale |
|---|---|---|
| `red` | `high` | Approval will block opening |
| `orange` | `high` | "Significant barriers requires explicit business justification" — same framing as red e-occupancy |
| `yellow` | `medium` | Real friction but workable |
| `green` | omitted | No DD risk to surface |

Category is always `ed_reg`. Summary is built from the approval type and timeline (e.g. "State school approval — Certificate of Approval — ~120-day timeline").

### SIR Risk Watch → severity from blocking-language scan

The SIR's free-text Risk Watch entries get scanned for category and severity:

**Category** — first-match keyword map (more specific phrases first):
- Keywords like "flood", "FEMA" → `flood_zone`
- "historic", "preservation" → `historic_district`
- "septic", "soil", "well" → `environmental`
- "zoning", "use" → `zoning`
- (etc. — full list in `_SIR_RISK_WATCH_KEYWORDS`)
- Anything that doesn't match → `ahj_history` as a generic fallback

**Severity** — blocking-language scan:
- Text contains any of `blocking`, `fatal`, `deal-breaker`, `dealbreaker` → `high`
- Otherwise → `medium`

---

## Dedup

After the four ingesters run, we have a list that may contain duplicates (e.g. zoning flagged by both permit history and SIR). The dedup rule:

**Dedup key:** `(category, source)` pair.

This means a `(zoning, permit_history)` flag and a `(zoning, sir_risk_watch)` flag are kept separate — they came from different sources and the DRI may want to see both. But if SIR Risk Watch flags zoning twice in two different sentences, they collapse into one entry.

**Collision rules:**
- Higher severity wins (e.g. high beats medium)
- Summaries merge with " | " separator (capped at the same length limit as a single summary)

**Sort order in the output:**
1. Severity descending (high first)
2. Category alphabetical
3. Source alphabetical

This means when a human reads the list top-to-bottom, the worst stuff is at the top.

---

## Caller-wins precedence

The publisher accepts an explicit `dd_risk_flags` kwarg. The rule:

- **If the caller passes `dd_risk_flags`** (any shape), the publisher runs it through `normalize_caller_flags()` to drop invalid entries, then uses what's left.
- **If the caller doesn't pass anything**, the publisher calls `derive_risk_flags(report_data)` to build the list from report tokens.
- **If the caller passes garbage** (every entry invalid → empty list after normalization), the field is **omitted** from the published payload entirely.

The "omitted on empty" rule is what makes this safe to combine with the dashboard's sticky-preserve logic: a future report that yields zero valid flags doesn't blow away a prior report's flags. The dashboard will keep showing the last known list until a non-empty one arrives.

---

## End-to-end flow

```
┌──────────────────────────────────────────────────────────────┐
│ Report data (q1, q2, sir, permit_history sections)           │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ derive_risk_flags()                                          │
│   _from_permit_history()  → list                             │
│   _from_e_occupancy()     → list                             │
│   _from_school_approval() → list                             │
│   _from_sir_risk_watch()  → list                             │
│                            │                                 │
│   _dedup_and_sort() ───────┘                                 │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ dashboard_publisher.build_site_meta(dd_risk_flags=...)       │
│   - caller passes explicit list?  → normalize_caller_flags() │
│   - else                          → derive_risk_flags()      │
│   - empty result                  → omit field               │
└────────────────────────────┬─────────────────────────────────┘
                             │ POST sites.json
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ Dashboard transform.ts → normalizeRiskFlags()                │
│   - validates each entry against canonical enums             │
│   - silently drops invalid                                   │
│   - sticky-preserve: empty list does not overwrite prior     │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ Portfolio.tsx → RiskFlagsCell                                │
│   - severity-toned count chip (block/warn/ok/em-dash)        │
│   - hover tooltip: "3 risks: 1 high, 2 medium"               │
│   - sortable by count, max-severity tie-breaker              │
└──────────────────────────────────────────────────────────────┘
```

---

## Where each piece lives

| Concern | File | Repo |
|---|---|---|
| Canonical enums + severity rank | `src/due_diligence_reporter/report_schema.py` | reporter |
| Derivation logic + canonicalizer | `src/due_diligence_reporter/risk_flags.py` | reporter |
| Publisher wiring (caller-wins) | `src/due_diligence_reporter/dashboard_publisher.py` | reporter |
| Pipeline skill docs | `skills/dd-report-assembly/references/v3-token-map.md` | alpha-dd-pipeline |
| Pipeline skill main flow | `skills/dd-report-assembly/SKILL.md` (Step 9) | alpha-dd-pipeline |
| Dashboard validator | `api/_lib/transform.ts` (`normalizeRiskFlags`) | dd-dashboard |
| Dashboard types | `client/src/types.ts` | dd-dashboard |
| Dashboard render | `client/src/pages/Portfolio.tsx` (`RiskFlagsCell`) | dd-dashboard |
| Field reference | `docs/column-vocabulary.md` | dd-dashboard |
| Reporter how-it-works | `docs/process/HOW-IT-WORKS.md` (Phase 4 row) | reporter |

---

## How to extend

### Adding a new category

1. Add the category string to `ALLOWED_RISK_FLAG_CATEGORIES` in `report_schema.py`.
2. Add the same string to `RISK_FLAG_CATEGORIES` in dashboard `transform.ts` and `client/src/types.ts`.
3. Wire at least one ingester to emit it (keyword map entry, upstream-token mapping, etc.).
4. Add tests covering the new category.
5. Update this design doc's category table.

The 4-source enum is much harder to extend — adding a new source means writing a new ingester. Categories are the cheap axis to grow on.

### Adding a new severity rule

Severity rules live inside each ingester. Change the function, add a test in `tests/test_risk_flags.py`, and update the per-source rules table above.

### Adding a new source

Rare but possible. Steps:
1. Write `_from_<source>()` ingester in `risk_flags.py`.
2. Wire it into `derive_risk_flags()`.
3. Add the source string to `ALLOWED_RISK_FLAG_SOURCES`, `RISK_FLAG_SOURCES` (dashboard transform), and the `client/src/types.ts` const array.
4. Test coverage for the new ingester (canonicalization + severity + dedup behavior).
5. Update this doc.

---

## Tests to look at

If you're trying to understand a specific behavior, the test that pins it down is usually the fastest way:

| Behavior | Test file |
|---|---|
| Per-source canonicalization | `tests/test_risk_flags.py::TestPermitHistoryIngester`, `TestEOccupancyIngester`, `TestSchoolApprovalIngester`, `TestSirRiskWatchIngester` |
| Severity rules | Same file, `TestSeverityRules` |
| Dedup + sort | Same file, `TestDedupAndSort` |
| Caller-wins precedence | `tests/test_dashboard_publisher.py::TestDdRiskFlagsDerivation` |
| Invalid-entry drop | Same file, `test_normalize_drops_invalid_entries` |
| Empty-omits | Same file, `test_empty_result_omits_field` |
| Constants locked | `tests/test_report_schema.py::TestPhase4RiskFlagConstants` |

Total: 48 Phase 4 tests across the three test files (658 reporter tests pass overall).

---

## Open questions / future work

These were discussed but not built. Listed here so they don't get forgotten:

- **Per-source breakdown view on the dashboard.** The data is there (every flag carries `source`). The Portfolio chip rolls up; a future site-detail or risks-detail view could break flags out by source.
- **`low` severity is currently unused.** All four ingesters emit only `high` or `medium` (with omits for the lowest-tier signals). `low` is in the enum so callers can pass it explicitly, but no auto-derivation produces it. Adding a "soft signal" tier is a future call.
- **Aging.** Flags don't have a timestamp. If a permit-history risk_note is two years old, it's the same severity as one from last week. Adding an `as_of` field is possible but not currently scoped.
