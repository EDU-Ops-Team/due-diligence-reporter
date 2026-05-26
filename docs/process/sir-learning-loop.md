# SIR Learning Loop

## Purpose

Compare recent AI SIRs against CDS/vendor SIRs, then feed the verified gaps
back into the SIR and DDR pipeline. The comparison is not AI versus CDS as a
winner-take-all exercise. It is AI SIR versus CDS/vendor SIR versus evidence.

## Batch Review Process

1. Select recent sites where both an AI SIR and a CDS/vendor SIR exist.
2. Review the pair section by section against source evidence.
3. Tag every material difference with one primary category:
   - AI missed item
   - CDS missed item
   - AI unsupported claim
   - CDS unsupported claim
   - Better wording needed
   - Template or prompt gap
   - Source retrieval gap
4. Decide whether the difference affects opening risk, permit path, cost, or
   DDR token output.
5. Convert accepted findings into one or more learning outputs:
   - SIR prompt update
   - Source retrieval rule
   - SIR template change
   - DDR prompt or token mapping change
   - QC checklist item

## Review Table

Use one row per issue, not one row per section.

| Field | Meaning |
|---|---|
| Site | site title |
| AI SIR | Link or file ID |
| CDS/vendor SIR | Link or file ID |
| Section | SIR section or DDR token area |
| AI finding | What the AI SIR said or missed |
| CDS finding | What CDS/vendor said or missed |
| Evidence checked | Source used to adjudicate the difference |
| Gap category | One of the standard tags above |
| Severity | Blocking, material, cleanup |
| DDR impact | Token, prompt, local trace, or none |
| Learning action | Concrete change to make |
| Owner | Person or system owner |
| Status | Open, accepted, implemented, rejected |

## Pipeline Hook

`check_site_readiness_direct()` now emits `sir_learning_review` metadata when it
sees SIR candidates. `process_site_pipeline()` records a non-blocking
`sir.learning_review` manifest step:

- `ready_for_review`: both AI SIR and CDS/vendor SIR are present.
- `waiting_for_cds_sir`: AI SIR exists, CDS/vendor SIR is not present yet.
- `waiting_for_ai_sir`: CDS/vendor SIR exists, AI SIR is not present yet.
- `not_applicable`: no usable SIR comparison candidate exists.

The step is observable in the local run manifest, uploaded manifest, Google
Chat pipeline lines, and `ddr status --run-id ...`.

## Weekly Review Queue

Use the queue command to find recent SIR pairs that are ready for claim-level
review:

```bash
ddr sir-review queue
```

By default, the queue scans local `.ddr-runs/*.json` manifests, shows only
`ready_for_review` pairs, deduplicates repeated manifests for the same site/SIR
pair, and hides pairs that already have matching outcomes in
`.ddr-runs/sir-review-outcomes.jsonl`.

Useful options:

```bash
ddr sir-review queue --status all
ddr sir-review queue --include-reviewed
ddr sir-review queue --limit 25
```

Treat Gmail as a discovery fallback only. The operating queue should come from
pipeline manifests and the site/Drive/LocationOS record.

## Outcome Capture

After a reviewer adjudicates one issue, record it as structured data:

```bash
ddr sir-review add \
  --site "Alpha Keller" \
  --section "Zoning" \
  --gap-category "AI missed item" \
  --severity "material" \
  --ddr-impact "exec.c_zoning" \
  --evidence-checked "city code section / AHJ email / source doc" \
  --learning-action "retrieval rule" \
  --status "accepted" \
  --ai-sir "AI SIR file id or link" \
  --cds-sir "CDS SIR file id or link"
```

The CLI appends each issue to `.ddr-runs/sir-review-outcomes.jsonl`. That file
is local runtime state and is ignored by Git.

## 30-Day Trend Review

Use a 30-day window by default:

```bash
ddr sir-trends --since 30d
```

This reports SIR pairs reviewed, issue counts, AI misses per SIR, unsupported
AI claims per SIR, CDS misses per SIR, DDR-impacting findings, high/material
findings, repeated section/category issues, and accepted learning actions.

For a monthly operating memo, use:

```bash
ddr sir-monthly-summary --since 30d
```

This prints a short markdown summary with review volume, reliability signals,
repeat patterns, accepted learning actions, and monthly decision prompts.

## Fit With DDR Improvements

This loop plugs into the broader DDR quality work from the run manifest effort:

- The manifest tells us which sites are review-ready.
- The comparison identifies whether the root problem is source retrieval,
  SIR generation, CDS/vendor interpretation, or DDR synthesis.
- DDR changes should only come from adjudicated findings, not from raw
  disagreement between two documents.
- When a finding affects DDR output, update the narrowest layer that owns the
  issue: retrieval first, SIR prompt/template second, DDR prompt/token mapping
  third, presentation polish last.

## Operating Cadence

Run a weekly sample of recent review-ready sites and use the 30-day trend view
to decide process updates. Do not update the process after every individual SIR
unless the finding is blocking or materially risky.

Track:

- number of SIR pairs reviewed
- AI missed items per SIR
- unsupported AI claims per SIR
- CDS/vendor corrections that changed DDR output
- learning actions implemented
- repeat issues after implementation

Recommended continuous loop:

1. Weekly: run `ddr sir-review queue` and select 3-5 unreviewed
   `ready_for_review` pairs.
2. During review: record only adjudicated issue rows with
   `ddr sir-review add`.
3. Weekly or biweekly: run `ddr sir-trends --since 30d` to watch repeat
   categories and sections.
4. Monthly: run `ddr sir-monthly-summary --since 30d`, decide the 2-3 process
   updates to make, and assign each to the narrowest owner.
5. After implementation: keep the same categories stable so the next 30-day
   window shows whether the repeat issue actually declines.
