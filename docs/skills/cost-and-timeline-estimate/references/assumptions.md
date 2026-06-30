# Cost And Timeline Estimate Assumptions

Use these assumptions for a ROM estimate when capacity is approved but vendor
quotes, permit feedback, or final construction documents are not available.

## Rhodes Capacity Source

Normal skill runs read capacity from Rhodes / LocationOS:

- `dueDiligence.foCapacity` is the Fastest Open capacity.
- `dueDiligence.maxCapCapacity` is the Max Capacity.

Fallback aliases are supported only for compatibility with exported report data:

- `due_diligence.fastest_open_capacity`
- `dueDiligence.fastestOpen.capacity`
- `dueDiligence.fastestOpenCapacity`
- `exec.fastest_open_capacity`
- `due_diligence.max_capacity_capacity`
- `dueDiligence.maxCapacity.capacity`
- `dueDiligence.maxCapacityCapacity`
- `exec.max_capacity_capacity`

If Rhodes has no Fastest Open or Max Capacity value, do not derive one in this
skill. Ask for the Rhodes record to be updated or for an explicit manual
override.

## Scenario Defaults

Fastest Open assumes a light-touch path focused on opening with minimal
construction.

- Complexity: `light`
- Area fallback: approved capacity x 55 SF
- Permit review: 2 weeks
- Construction: 4 weeks
- Restroom delta: 0
- Soft costs: 10% of hard cost subtotal
- GC fee: 10% of hard cost subtotal
- Contingency: 12% of hard costs plus soft costs plus GC fee

Max Capacity assumes a fuller buildout to support the approved maximum count.

- Complexity: `standard`
- Area fallback: approved capacity x 55 SF
- Permit review: 6 weeks
- Mobilization: 1 week
- Construction: 10 weeks
- Restroom delta: 1
- Soft costs: 12% of hard cost subtotal
- GC fee: 12% of hard cost subtotal
- Contingency: 15% of hard costs plus soft costs plus GC fee

## Cost Categories

The estimator uses the DDR/RayCon reporting vocabulary so results can flow into
the same report fields:

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

Default unit costs are deliberately simple ROM values, not a bid:

| Category | Fastest Open | Max Capacity |
| --- | ---: | ---: |
| demolition | $1/SF | $4/SF |
| framing_doors | $2/SF | $14/SF |
| mep_fire_life_safety | $6/SF | $28/SF |
| plumbing_bathrooms | $25,000/restroom delta | $30,000/restroom delta |
| finish_work | $10/SF | $14/SF |
| furniture | $750/student | $900/student |
| tech_security_signage | $15,000 + $150/student | $20,000 + $180/student |
| other_hard_costs | $2/SF | $4/SF |

Complexity multipliers apply to SF-based construction categories:

- `light`: 0.85
- `standard`: 1.00
- `heavy`: 1.25

Location multipliers are broad ROM adjustments. Keep them conservative and
overridable; do not treat them as market pricing.

## Overrides

Use `overrides.<scenario>.category_overrides` to replace a category amount when
there is a known quote. Use `additional_allowances` to add known scope without
discarding the default category math.

Use `overrides.<scenario>.timeline_weeks` when schedule facts are known. For
more granular schedule changes, override `permit_weeks`, `construction_weeks`,
`mobilization_weeks`, `closeout_weeks`, or set
`parallel_permit_and_construction: true`.

Do not add capacity-quality scoring or capacity derivation here. That belongs
upstream.

## Downstream Contract

Downstream skills should consume `downstream_inputs`, not narrative prose.

The contract includes:

- `site`: Rhodes site name, slug, address, and id when provided.
- `capacity`: Fastest Open and Max Capacity counts plus field-level provenance.
- `scenarios`: compact cost and timeline summary by scenario.
- `report_data_fields`: DDR-compatible flat report fields.
- `warnings`: assumptions or missing facts that downstream skills must preserve.

Use `report_data_fields` for DDR merge paths. Use `downstream_inputs.scenarios`
for skills such as opening plan, phase planning, business case, construction
RFP creation, or executive summaries that need structured cost/timeline context.
