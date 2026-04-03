# School Approval Report — Template

This template defines the human-readable report the agent must produce after the JSON output. Use the JSON fields to populate every section. Do not skip sections; write "N/A" where not applicable.

---

## Report Structure

```
# Education Approval Report
## [ADDRESS]

### Verdict
### State Classification
### Scoring Rationale
### Critical Path (Gating Steps)
### Pre-Open Requirements
### Local Requirements
### Open Questions — Verify Before Lease
### ESA / Voucher
### Sources
### Data Quality
```

---

## Section-by-Section Instructions

---

### Header

```
# Education Approval Report
## [address]

**Report Date:** [source_search_date]
**Rules Version:** [rules_version]
```

---

### Verdict

Render as a summary table. Zone label must match: GREEN (≥80), YELLOW (41–79), RED (≤40).

```markdown
| | |
|:---|:---|
| **Zone** | GREEN / YELLOW / RED |
| **Score** | [score_0_100] / 100 |
| **Archetype** | [archetype] |
| **Approval Type** | [approval_type] |
| **Gating Before Open** | Yes / No |
| **Likely Timeline** | [timeline_days_preopen.likely] days ([timeline_days_preopen.min]–[timeline_days_preopen.max] day range) |
| **Confidence** | [confidence_0_1 × 100]% |
```

Follow with the full `requirements_summary` paragraph verbatim.

---

### State Classification

```markdown
**State:** [state]
**Locality:** [locality.city], [locality.county]
**Archetype:** [archetype] — [one-sentence plain-English description of what this archetype means for operations]
**Approving Authority:** [approval_authority]
**Authority URL:** [approval_authority_url]
**Calendar Window:** [calendar_window details if present, otherwise "None"]
```

For WINDOWED states, add a callout block:

```markdown
> ⚠ CALENDAR RISK: Next approval window [next_window_date]. Submission deadline [submission_deadline].
> Provisional status available: [Yes/No]. Missing this window adds [X] months.
```

---

### Scoring Rationale

Show the score build-up transparently. List the baseline, each adjustment applied, and the final score. This helps analysts quickly understand what drove the result.

```markdown
| Factor | Points |
|:---|---:|
| Baseline ([state]) | +[baseline] |
| [Adjustment reason, e.g. "State burden lighter than baseline — ODE has no authority"] | +5 |
| [Adjustment reason, e.g. "Local overlay approval required"] | −10 |
| [Adjustment reason, e.g. "Health/safety inspection required before opening"] | −5 |
| **Final Score** | **[score_0_100]** |
```

If no adjustments were made, write: "No adjustments from baseline — research confirmed archetype as expected."

---

### Critical Path (Gating Steps)

List only the steps where `gating: true`. These are the steps that block opening. Render as a numbered list with the timeline impact noted where known.

```markdown
The following must be completed **before doors open**:

1. **[Step description]** — [any timeline note or authority contact]
2. **[Step description]** — [any timeline note or authority contact]
```

If `gating_before_open: false`, write:
> No state-imposed gating requirements. Certificate of Occupancy (universal) is the only pre-open milestone.

---

### Pre-Open Requirements

Render as a checklist table. Use research findings for notes — do not leave notes blank if research produced relevant detail.

```markdown
| Requirement | Required | Notes |
|:---|:---:|:---|
| Teacher Certification | Yes / No | [teacher_notes] |
| Curriculum Pre-Approval | Yes / No | [note if researched] |
| Health / Safety Inspection | Yes / No | [describe what inspection, who conducts it] |
| Background Check / Fingerprinting | Yes / No | [background_check_notes] |
| Financial Reserve | Yes / No | [financial_reserve_notes] |
| Architectural / Traffic Submission | Yes / No | [note if researched] |
```

---

### Local Requirements

If `has_local_overlay: false` and no local risk flagged:
> No local overlay identified beyond the universal CO process.

If `has_local_overlay: true` OR `LOCAL_ZONING_UNVERIFIED` flag present, render as a callout:

```markdown
> ⚠ LOCAL OVERLAY — [city/county]
>
> [local_notes verbatim]
>
> **Contact:** [authority name, phone, email or URL]
> **Estimated additional timeline if applies:** [X days]
```

---

### Open Questions — Verify Before Lease

List every unresolved item that could change the score, timeline, or gating status. Draw from `data_quality_flags` and `local_notes`. Each item must name the specific action, who to contact, and what the consequence is if the answer is unfavorable.

```markdown
| # | Question | Action | Unfavorable Consequence |
|:---|:---|:---|:---|
| 1 | [Specific unknown] | [Who to call / what to look up] | [Impact: e.g., adds 90 days, requires Type III review] |
| 2 | ... | ... | ... |
```

If no open questions: write "None — research fully resolved all material questions."

---

### ESA / Voucher

```markdown
**ESA/Voucher Flag:** [esavoucher_flag]

[One of:]
- NOT_PARTICIPATING: Operating under baseline requirements only. No additional accreditation, testing, or financial reporting required to open.
- PARTICIPATING: Choice program participation adds accreditation, testing, and financial reporting layers. Confirm scope before opening timeline is finalized.
- UNKNOWN: ESA/voucher participation status not determined. Assumed NOT_PARTICIPATING for this report.
```

---

### Sources

```markdown
| Source | URL |
|:---|:---|
| [Descriptive name] | [url] |
| [Descriptive name] | [url] |
```

Use the `source_urls` array. Give each URL a descriptive label (e.g., "Oregon ODE — Private Schools" not just the raw URL). Date-stamp: "All sources retrieved [source_search_date]."

---

### Data Quality

```markdown
**Confidence:** [confidence_0_1 × 100]%
**Flags:** [data_quality_flags — plain-English explanation of each flag]

[If ARCHETYPE_CORRECTED_FROM_BASELINE]: The baseline archetype table classified [state] as [original], but research found [corrected]. Score reflects the researched finding.
[If LOCAL_ZONING_UNVERIFIED]: Local zoning status could not be confirmed from public sources. Verify before committing to lease.
[If ARCHETYPE_UNCERTAIN]: Insufficient data to classify archetype with confidence. Defaulted to APPROVAL_REQUIRED per skill rules.
[If NO_OFFICIAL_SOURCE_FOUND]: No official government source found. Score based on EdChoice rankings and secondary sources only.
```

---

## Tone and Style Rules

- **Active voice.** "ODE has no authority" not "No authority exists at ODE."
- **Specific over vague.** Name the statute, the form, the contact. Never write "check with local authorities."
- **Flag uncertainty explicitly.** Never imply certainty where the data quality flag says otherwise.
- **Consistent zone language.** Always write GREEN, YELLOW, RED in caps. Never "green zone" or "yellow rating."
- **No trailing summaries.** End with the Data Quality section. Do not add a closing paragraph restating the verdict.
