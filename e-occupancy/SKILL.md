---
name: e-occupancy
description: Evaluate properties for E occupancy (educational use) conversion potential. Scores building type complexity from 0-100 based on current use and conversion barriers, plus IBC compliance gate checks (sprinkler, travel distance, exits). Used as a sub-skill of alpha-building-suitability.
version: 3.0.0
---

# E Occupancy Conversion Evaluator

Evaluate commercial properties for conversion to E occupancy (K-12 schools). Score based on current building type, floor position, and conversion complexity.

## Overview

This skill:
1. Researches the property using web search to determine current use/occupancy
2. Checks for floor-level vs. whole-building evaluation
3. Looks up building type in `scoring_matrix.json`
4. Applies tenant space deductions if evaluating a specific floor/suite
5. Returns a score (0-1) based on conversion complexity

**Reference Data**: `scoring_matrix.json` (in this skill folder)

---

## Scoring System

| Score | Rating | Meaning | Timeline |
|-------|--------|---------|----------|
| **100** | GREEN | Current K-12 school with E occupancy | Ready to proceed |
| **95** | YELLOW | Daycare (E occupancy, needs school code upgrade) | 3-6 months |
| **90-94** | YELLOW | Very easy conversion | 3-6 months |
| **70-89** | YELLOW | Easy to moderate | 6-9 months |
| **50-69** | YELLOW | Moderate complexity | 9-12 months |
| **30-49** | YELLOW | Complex conversion | 12-18 months |
| **15-29** | YELLOW | Very complex | 18-24+ months |
| **1-14** | YELLOW | Extremely difficult | 24+ months |
| **0** | RED | Do not pursue | N/A |

**CRITICAL**: Score 100 = GREEN, Scores 1-99 = ALL YELLOW, Score 0 = RED

**Higher score = Easier conversion**

### Scoring Weights

Scores are based on weighted conversion complexity:
- **40%** Building type complexity (code path, conversion precedents)
- **30%** Systems modifications (HVAC, plumbing, electrical)
- **20%** Structural requirements (fire/life safety, egress, ADA)
- **10%** Size and scale (square footage, stories)

---

## Part 1: Research the Property

Use web search to determine:
1. Current occupancy classification (E, B, A-1, A-2, A-3, S-1, etc.)
2. Current business/use type
3. Building characteristics (stories, size)
4. Floor/suite being evaluated (if applicable)

### Search Queries

```
[county] property appraiser [address]
[county] assessor [address]
[city] certificate of occupancy [address]
"[address]" business
site:loopnet.com "[address]"
```

### Key Information to Extract

- **Current occupancy**: E (educational), B (business), A (assembly), S (storage), etc.
- **Building type**: Office, retail, church, warehouse, etc.
- **Stories**: Total building height (1-3, 4-6, or 7+)
- **Floor/Suite**: If address includes "Floor X" or "Suite XXX"
- **Current tenant/use**: School, daycare, office, gym, restaurant, etc.

### Occupancy Classifications (IBC)

- **E**: Educational (schools K-12, daycare >5 children)
- **B**: Business (offices, medical offices, retail)
- **A-1**: Assembly - Fixed Seating (theaters, auditoriums)
- **A-2**: Assembly - Food/Drink (restaurants, bars, nightclubs)
- **A-3**: Assembly - Worship/Recreation (churches, gyms)
- **I-1**: Institutional - Assisted (assisted living)
- **I-2**: Institutional - Medical (hospitals, nursing homes)
- **S-1**: Storage - Moderate Hazard (warehouses)
- **F-1**: Factory - Moderate Hazard (manufacturing)

---

## Part 2: Building Type Scores

Reference `scoring_matrix.json` for complete list. Summary below:

### Score 100 — Current E Occupancy (GREEN)

Property already has E (Educational) occupancy for K-12 - **no conversion needed**.

### Score 95 — Daycare (YELLOW)

Daycare with E occupancy - needs upgrade to school-specific code.
- Keywords: daycare, childcare, preschool, pre-k, child development

### Score 90-94 — Very Easy Conversions

| Type | Score | Keywords |
|------|-------|----------|
| Office (1-3 stories) | 92 | low-rise office, 1-3 story office |
| Gym / Fitness center | 90 | gym, fitness, health club, yoga, crossfit |

### Score 70-89 — Easy to Moderate

| Type | Score | Keywords |
|------|-------|----------|
| Flex / light industrial (w/ HVAC) | 88 | flex space, light industrial |
| Retail strip (individual unit) | 85 | retail unit, small retail |
| Office (general B occupancy) | 82 | office building, professional office |
| Small/mid-size church | 78 | small church, chapel |
| Medical office / clinic | 75 | medical office, dental, clinic, urgent care |
| Retail strip center | 75 | strip mall, shopping center |

### Score 50-69 — Moderate Complexity

| Type | Score | Keywords |
|------|-------|----------|
| Warehouse with HVAC and windows | 58 | conditioned warehouse |
| Small assembly venue | 55 | event space, banquet hall, small theater |

### Score 30-49 — Complex Conversions

| Type | Score | Keywords |
|------|-------|----------|
| High-rise (4-6 stories) | 42 | 4-6 story, mid-rise |
| Large church / worship center | 38 | church, cathedral, megachurch, temple, mosque |
| Warehouse without HVAC | 35 | warehouse, cold shell, distribution center |
| Nightclub / large bar | 32 | nightclub, bar, club, lounge |
| Historic / landmark building | 30 | historic, landmark, SHPO, national register |

### Score 15-29 — Very Complex

| Type | Score | Keywords |
|------|-------|----------|
| Large assembly (theater) | 28 | theater, concert hall, cinema, auditorium |
| Cold storage / refrigerated | 28 | cold storage, freezer storage |
| Data center | 25 | data center, server farm, colocation |
| Big box retail (100k+ SF) | 22 | mall anchor, big box, walmart, target |
| High-rise (7+ stories) | 20 | high-rise, tower, skyscraper |
| Hospital / surgical center | 18 | hospital, medical center, surgical |
| Nursing home / assisted living | 18 | nursing home, assisted living, senior care |
| Bank | 15 | bank, credit union, vault |

### Score 1-14 — Extremely Difficult

| Type | Score | Keywords |
|------|-------|----------|
| Restaurant | 12 | restaurant, cafe, diner, bistro, grill, eatery |

### Score 0 — Do Not Pursue (RED)

| Type | Keywords |
|------|----------|
| Gas station | gas station, fuel, petroleum, chevron, shell, exxon |
| Dry cleaner | dry clean, perc, laundry, perchloroethylene |
| Auto body shop | auto body, collision, paint shop, body shop |
| Heavy manufacturing | factory, industrial plant, fabrication |
| Chemical storage | chemical, hazmat, distribution center chemicals |
| Mortuary | mortuary, funeral home, crematorium |
| Adult entertainment | adult entertainment, strip club |
| Correctional facility | jail, prison, detention |

---

## Part 3: Height Triggers

Height overrides building type score:

| Stories | Score Ceiling |
|---------|---------------|
| 1-3 | No ceiling |
| 4-6 | 42 (max) |
| 7+ | 20 (max) |

**Example**: 26-story office = Score 20 (high-rise), NOT 82 (office)

**CRITICAL**: E occupancy above 3rd floor triggers significant fire/life safety upgrades.

---

## Part 4: Floor-Level Evaluations (Tenant Spaces)

**CRITICAL**: When an address specifies a floor number or suite (e.g., "Floor 2", "Suite 200"), you are evaluating a TENANT SPACE, NOT the entire building.

### Step 1: Identify Floor Number

Parse address for floor indicators:
- "Floor 2", "2nd Floor", "Fl 2"
- "Suite 200" (typically 2nd floor)
- If no floor specified, assume entire building

### Step 2: Check Floor vs. 3rd Floor Threshold

**E occupancy above 3rd floor triggers significant code requirements.**

| Floor Position | Action |
|----------------|--------|
| **Floors 1-3** | Do NOT apply high-rise penalties. Use base building type score. |
| **Floors 4+** | Apply high-rise complexity modifiers (Score 42 or 20 ceiling) |

### Step 3: Determine Base Score

Start with underlying building type:
- Office tenant space → Base 82
- Retail ground floor → Base 75-85
- Medical office suite → Base 75
- Church in multi-tenant → Base 78

### Step 4: Apply Tenant Space Deductions

**Deduct points for shared building constraints:**

| Constraint | Deduction |
|------------|-----------|
| Shared HVAC system with rest of building | -5 |
| Shared egress/elevators (no dedicated entrance) | -5 |
| Building management/landlord approval required | -5 |
| No dedicated entrance from street level | -5 |
| No access to outdoor space | -5 |
| Shared parking (no dedicated school parking) | -3 |
| Mixed-use building with incompatible tenants | -5 |

**Maximum total deduction**: -30 points
**Minimum final score**: 1 (cannot go below 1 unless environmental)

### Step 5: Calculate Final Score

```
Final Score = Base Building Type Score - Sum of Applicable Deductions
Final Score = max(Final Score, 1)  # Cannot go below 1
```

### Floor-Level Examples

**Example 1: Office Floor 2 in 26-story High-Rise**
```
Address: 515 Congress Ave, Floor 2, Austin, TX
Building: 26-story office tower
Floor: 2 (below 3rd floor trigger ✓)

Base score: 82 (office general)
Deductions:
  - Shared HVAC: -5
  - Shared egress: -5
  - Building approval: -5
  - No dedicated entrance: -5
  - No outdoor access: -5
Total deductions: -25

Final Score: 82 - 25 = 57 (Moderate complexity)
Rating: YELLOW
Timeline: 9-12 months
```

**Example 2: Office Floor 8 in High-Rise**
```
Address: 515 Congress Ave, Floor 8, Austin, TX
Building: 26-story office tower
Floor: 8 (ABOVE 3rd floor trigger ❌)

Base score: 20 (high-rise 7+ stories applies because floor > 3)
No additional deductions (high-rise score already accounts for complexity)

Final Score: 20 (Very complex)
Rating: YELLOW
Timeline: 18-24+ months
```

**Example 3: Ground Floor Retail**
```
Address: 123 Main St, Suite 101, Dallas, TX
Building: 3-story mixed-use
Floor: 1 (ground level ✓)

Base score: 85 (retail strip individual)
Deductions:
  - Shared HVAC: -5
  - Mixed-use tenants above: -5
  - Shared parking: -3
Total deductions: -13

Final Score: 85 - 13 = 72 (Easy to moderate)
Rating: YELLOW
Timeline: 6-9 months
```

---

## Part 5: IBC Compliance Gates

These gates are evaluated separately from the building type score. Failing a gate applies a score penalty and adds a flag to the output.

### Gate 1 — Sprinkler Requirement (IBC 903.2.3)

**Two independent triggers — either alone mandates sprinklers:**

| Condition | Result |
|-----------|--------|
| Fire area > 12,000 sq ft | Sprinklers REQUIRED |
| Any space below exit discharge level | Sprinklers REQUIRED |
| Fire area ≤ 12,000 sq ft + no below-grade | Sprinklers NOT required |
| Already sprinklered | No retrofit cost |

Score penalty if sprinklers required and not already present: **−5**
Retrofit cost note: $3–6/sq ft (NFPA 13). Budget this as a known line item.

**Exception**: Below-grade sprinkler requirement waived only if every classroom has a direct exterior exit at ground level (IBC 903.2.3 Exception).

### Gate 2 — Travel Distance (IBC Table 1017.2)

| Condition | Max Travel Distance |
|-----------|-------------------|
| Non-sprinklered | 200 ft |
| Sprinklered | 250 ft |

Score penalty if non-compliant: **−15**

This is the primary conversion killer. Buildings with travel distance violations require structural egress changes (new exits, corridor reconfiguration) that cost 10–20× more than sprinkler retrofits and may be physically impossible.

### Gate 3 — Exit Count (IBC Table 1006.2.1)

| Occupant Load | Required Exits |
|---------------|---------------|
| 1–49 | 1 |
| 50–500 | 2 |
| 501–1,000 | 3 |
| 1,001+ | 4 |

Score penalty if non-compliant: **−10**

### Gate 4 — Construction Type (IBC Chapter 5, Tables 504.3/504.4/506.2)

Informational only — no automatic score penalty. Flag for manual review.
Sprinkler bonus: +1 story, +20 ft height, +200–300% area allowance.

### 50-Occupant Universal Triggers

Any school exceeding 50 occupants (virtually all) triggers all of the following. These are flagged in output but do not adjust the score since they are universal and manageable:

| Requirement | IBC Reference | Cost Note |
|-------------|--------------|-----------|
| Fire alarm system | IBC 907.2.3 | Standard line item |
| Panic hardware on all egress doors | IBC 1010.2.9 | $300–500/door, 4–8 doors typical |
| Minimum 2 exits | IBC Table 1006.2.1 | May already be satisfied |
| Storm shelter in 250 mph wind zones | IBC 423.5 | Geographic — check local wind map |
| CO detectors in classrooms | IBC 915.2.3 | Standard line item |
| Enhanced acoustics (classrooms ≤ 20,000 cu ft) | IBC 1208.2 | Standard finish spec |

---

## Part 6: Confidence Assessment

### HIGH Confidence
- Occupancy confirmed via official records (building permits, certificates)
- Current use clearly identified from multiple sources
- Building specs verified through county assessor
- Score assignment is unambiguous

### MEDIUM Confidence
- Occupancy inferred from typical use (e.g., office = B occupancy)
- Current tenant confirmed but limited building detail
- Single primary data source
- Score assignment based on keywords match

### LOW Confidence
- Limited data available
- Occupancy assumed without verification
- Current use unclear or conflicting information
- Vacant or "for lease" status with unclear history

---

## Part 7: Common Edge Cases

### Vacant Properties
- Note previous tenant if determinable
- Check "for lease" listings for use history
- **Reduce score by 5-10 points** if previous use unknown
- **Confidence**: MEDIUM or LOW

### Mixed-Use Buildings
- Evaluate based on specific unit's use
- Note shared egress considerations
- Consider incompatible neighboring tenants
- May need separate evaluations for different sections

### Former Schools
- **Score 100** if still has E occupancy
- Variable if occupancy changed - check certificate validity
- Excellent candidate even if currently different use

### Unknown Occupancy
- State: "Unknown - assumed [X] based on use"
- **Confidence**: MEDIUM or LOW

---

## Part 8: Scoring for Building Suitability

Convert score to 0-1 scale for the parent skill:

### Conversion Formula

```
occupancy_score = score_0_100 / 100
```

### Examples

| Building Type | score_0_100 | occupancy_score (0-1) |
|---------------|-------------|----------------------|
| Current school (E occupancy) | 100 | 1.00 |
| 2-story office | 92 | 0.92 |
| Floor 2 office (tenant space) | 57 | 0.57 |
| Small church | 78 | 0.78 |
| 5-story office (entire building) | 42 | 0.42 |
| Restaurant | 12 | 0.12 |
| Gas station | 0 | 0.00 |

### Output for Parent Skill

Return to `alpha-building-suitability`:
- `occupancy_score`: 0-1 score (score_0_100 / 100)
- `zone`: GREEN / YELLOW / RED
- `building_type`: identified type
- `floor_level`: floor being evaluated (if applicable)
- `confidence`: HIGH / MEDIUM / LOW
- `timeline`: estimated conversion timeline

---

## Output Format

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
E OCCUPANCY EVALUATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Score:              XX / 100  (0.XX / 1.0)
Zone:               [GREEN | YELLOW | RED]
Confidence:         [HIGH | MEDIUM | LOW]

Current Use:        [Business/building type]
IBC Occupancy Group:[B | A-3 | M | S | R | I | H | Not provided]
Building Type:      [Matched type from scoring_matrix.json]
Stories:            [X total]
Floor Evaluated:    [X or "Entire building"]
Timeline:           [Estimated conversion time]

Scoring Breakdown:
  Base Score:       [XX] ([building type])
  Height Ceiling:   [XX or "N/A"]
  Tenant Deductions:[−XX] (if applicable)
  IBC Gate Adjustment: [−XX] (if applicable)
  Final Score:      [XX]

IBC Compliance Gates:
  Sprinklers:       [YES — fire area exceeds 12,000 sq ft | NO | UNKNOWN]
  Travel Distance:  [COMPLIANT — XX ft | NON-COMPLIANT — XX ft exceeds XXX ft | UNKNOWN]
  Exit Count:       [COMPLIANT — X exits | NON-COMPLIANT — X exits, Y required | UNKNOWN]
  Construction Type:[Type XX — verify IBC Chapter 5 | UNKNOWN]

Required (Universal ≥50 occupants):
  • Fire alarm system (IBC 907.2.3)
  • Panic hardware on all egress doors — $300–500/door (IBC 1010.2.9)
  • Storm shelter in 250 mph wind zones (IBC 423.5)
  • CO detectors in all classrooms (IBC 915.2.3)
  • Enhanced acoustics in classrooms ≤ 20,000 cu ft (IBC 1208.2)

Summary:            [Brief assessment]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Quick Decision Tree

```
1. Current K-12 school with E occupancy? → Score 100 (GREEN) ✅

2. Daycare with E occupancy? → Score 95 (YELLOW)

3. Environmental contamination? → Score 0 (RED) 🛑
   (gas station, dry cleaner, auto body, etc.)

4. Is this a tenant space (floor/suite specified)?
   YES → Go to Step 5
   NO → Go to Step 6

5. Tenant space evaluation:
   a. What floor?
      - Floors 1-3: Use base building type score
      - Floors 4+: Apply high-rise ceiling (42 or 20)
   b. Apply tenant space deductions (-5 to -30)
   c. Final = max(Base - Deductions, 1)

6. Whole building evaluation:
   a. Match building type → Get base score
   b. Check height ceiling:
      - 7+ stories → ceiling 20
      - 4-6 stories → ceiling 42
      - 1-3 stories → no ceiling
   c. Final = min(Base Score, Height Ceiling)
```

---

## Key Rules

1. **Score 100 = Current K-12 school with E occupancy** (GREEN)
2. **Score 95 = Daycare with E occupancy** (YELLOW - needs school code upgrade)
3. **Scores 1-99 = ALL YELLOW** (conversion needed, higher = easier)
4. **Score 0 = Environmental contamination ONLY** (RED)
5. **Height overrides type** - use most restrictive
6. **Floor 2 of high-rise ≠ entire building** - evaluate the specific floor
7. **3rd floor is critical threshold** - E occupancy above 3rd triggers upgrades
8. **Apply tenant space deductions** when evaluating floors/suites
9. **Restaurants & Banks are YELLOW** (scores 12 and 15) - not RED

---

## Reference Files

- `scoring_matrix.json` - Complete building type to score mapping with keywords
- Located in: `.claude/skills/e-occupancy/`

---

## Notes

- Size is irrelevant: Schools operate from 3k SF (microschools) to 150k SF (full campuses)
- This skill does NOT write to the database
- Score is returned to the parent `alpha-building-suitability` skill
- The parent skill combines this with other sub-scores for the final `building_score`
