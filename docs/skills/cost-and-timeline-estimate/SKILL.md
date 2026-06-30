---
name: cost-and-timeline-estimate
description: Estimates Alpha School buildout cost and opening timeline from Fastest Open and Max Capacity counts read from Rhodes / LocationOS. Use when the user asks for a cost estimate, timeline estimate, buildout estimate, opening-date estimate, or downstream-ready estimate for a specific Rhodes site. Do NOT use to derive capacity from floor plans, judge whether the capacity is good, call RayCon, or replace a GC bid.
metadata:
  scorecard:
    themeId: construction-cost-commercial-review
---

# Cost And Timeline Estimate

## Usage telemetry

At the start of this skill run, if telemetry is configured, create one stable `runId` and log `started` with:

```bash
node ~/.ops-skills/track-skill-usage.mjs --skill-id cost-and-timeline-estimate --run-id "$RUN_ID" --outcome started
```

Before your final response, log `completed` with the same `runId`. If the skill fails after starting, log `failed` instead. Telemetry is best-effort: never interrupt the user if telemetry fails, and never send prompts, file names, file contents, or outputs.

Use this skill after Rhodes has the accepted Fastest Open and Max Capacity
student counts. Do not analyze whether capacity is correct. Read the current
counts from Rhodes and estimate cost and timeline against those values.

This skill is standalone. Do not call RayCon, dispatch `/v1/jobs`, require
`raycon_scenario.json`, or use Alpha Capacity Analysis to derive new counts.

## Required Inputs

- A specific Rhodes / LocationOS site: name, slug, or address.

Normal runs must read capacity from Rhodes:

- `dueDiligence.foCapacity` -> Fastest Open capacity.
- `dueDiligence.maxCapCapacity` -> Max Capacity.

If either value is missing in Rhodes, stop and report the missing field. Only
use manual capacity overrides when the user explicitly says to override Rhodes
for a one-off estimate.

Useful optional inputs:

- `gross_sf` or `building_sf`: planning area. If absent, use 55 SF per
  student and flag the assumption.
- `start_date`: ISO date used to convert timeline weeks into open dates.
- `overrides`: scenario-specific category, allowance, multiplier, or timeline
  overrides when known quotes or schedule facts are available.

## Workflow

1. Resolve the site with LocationOS / Rhodes.
   - Use `resolveSite` first for a named site.
   - Use `getSite` to read the current Rhodes site record.
2. Extract capacity from `site.dueDiligence.foCapacity` and
   `site.dueDiligence.maxCapCapacity`.
   - Treat these as accepted inputs.
   - Do not recompute or validate capacity quality.
   - Stop if either field is missing unless the user explicitly approves an
     override.
3. Build the estimator payload with the full `rhodes_site` object plus optional
   start date or overrides.
4. Run `scripts/estimate.py` with that JSON payload.
5. Pass `downstream_inputs` or `report_data_fields` directly into the next skill
   or DDR report merge path.

Example:

```powershell
uv run python docs\skills\cost-and-timeline-estimate\scripts\estimate.py .\payload.json --pretty
```

Input shape:

```json
{
  "rhodes_site": {
    "name": "Alpha Example",
    "address": "Austin, TX",
    "dueDiligence": {
      "foCapacity": 80,
      "maxCapCapacity": 120
    }
  },
  "gross_sf": 6000,
  "start_date": "2026-07-01"
}
```

Override shape:

```json
{
  "rhodes_site": {
    "name": "Alpha Example",
    "dueDiligence": {
      "foCapacity": 80,
      "maxCapCapacity": 120
    }
  },
  "gross_sf": 6000,
  "overrides": {
    "fastest_open": {
      "category_overrides": {
        "mep_fire_life_safety": 50000
      },
      "additional_allowances": {
        "other_hard_costs": 10000
      },
      "timeline_weeks": 9
    }
  }
}
```

## Output Contract

Return a JSON object with:

- `source_system: "cost_and_timeline_estimate"`
- `estimate_version`
- `scenarios.fastest_open` and/or `scenarios.max_capacity`
- `report_data_fields` using the DDR cost token vocabulary
- `downstream_inputs` for subsequent skills
- `rhodes_capacity_read` showing which Rhodes fields supplied capacity
- `warnings`
- `assumptions`

Downstream skills should consume `downstream_inputs` rather than scraping prose.
`report_data_fields` remains available for DDR report merging.

Cost categories must use this stable vocabulary:

- `demolition`
- `framing_doors`
- `mep_fire_life_safety`
- `plumbing_bathrooms`
- `finish_work`
- `furniture`
- `tech_security_signage`
- `other_hard_costs`
- `soft_costs`
- `gc_fee`
- `contingency`
- `grand_total`

Read `references/assumptions.md` when changing default rates, schedule logic,
Rhodes field extraction, downstream payload shape, or override behavior.

## Best-effort telemetry

If telemetry is configured, log skill start and completion with `~/.ops-skills/track-skill-usage.mjs`.
Use this skill folder name as `skillId` and a stable `runId` for the run.
Never interrupt the user if telemetry fails, and do not ask for tracking permission during normal use.
