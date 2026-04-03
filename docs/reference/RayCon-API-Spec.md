# RayCon API Specification

**Base URL**: `https://raycon-api-738625530258.us-central1.run.app`
**Frontend**: `https://storage.googleapis.com/raycon-dashboard/index.html`
**GitHub**: `https://github.com/b-randongee/RayCon`

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/chat` | Chat with Ray (SSE streaming + structured data) |
| `POST` | `/v1/drive/search-for-space` | Search Google Drive for space documents |
| `POST` | `/v1/drive/content` | Extract content from a Drive document |
| `POST` | `/v1/drive/extract-sir` | Extract structured SIR data from PDF |
| `GET` | `/health` | Health check |
| `GET` | `/` | API info |

---

## POST /v1/chat

Chat with Ray Constellano, the construction cost advisor. Streams Server-Sent Events (SSE) with Ray's narrative text while executing deterministic calculator tools server-side.

Returns **two layers**:
- **`narrative`**: Ray's persona-driven text (non-deterministic — varies per call)
- **`structured`**: Raw tool outputs (deterministic — same inputs = same numbers every time)

### Request

```
POST /v1/chat
Content-Type: application/json
Accept: text/event-stream
```

```json
{
  "messages": [
    { "role": "user", "content": "What is the MVP cost for this space at T1?" }
  ],
  "space": {
    "name": "Alpha School Greenwich 20",
    "rooms": [
      { "name": "Main Room", "type": "otherroom", "sqft": 2800, "perimeterLinearFt": 212, "wallHeightFt": 10, "floor": "1st Floor" },
      { "name": "Office", "type": "office", "sqft": 150, "perimeterLinearFt": 50, "wallHeightFt": 10 },
      { "name": "Restroom", "type": "restroom", "sqft": 60, "perimeterLinearFt": 32, "wallHeightFt": 10 }
    ],
    "spaceDetails": {
      "address": "20 Church St, Greenwich, CT 06830",
      "numFloors": 2,
      "totalArea": 12561,
      "grossFloorArea": 12561,
      "buildingType": "commercial",
      "yearBuilt": "1990",
      "description": "Former office building",
      "floors": [
        { "label": "1st Floor", "area": 6000 },
        { "label": "2nd Floor", "area": 6561 }
      ]
    },
    "metadata": { "roomCount": 25, "totalSqft": 12561 },
    "source": "matterport",
    "floorPlanUrls": [
      { "label": "1st Floor", "url": "https://matterbot.../floorplan1.png" },
      { "label": "2nd Floor", "url": "https://matterbot.../floorplan2.png" }
    ]
  },
  "region": "default",
  "drive_doc_summaries": {
    "inspection": "Inspection summary text...",
    "sir": "SIR summary text...",
    "lease": "LOI/lease summary text..."
  },
  "drive_doc_meta": {
    "inspection": { "name": "Alpha Greenwich Inspection.pdf" },
    "sir": { "name": "Alpha Greenwich SIR.pdf" }
  },
  "sir_structured": {
    "cover": { "address": "20 Church St", "city": "Greenwich" },
    "flagPage": { "importantIssues": ["HVAC undersized for school use"] }
  },
  "floor_plan_urls": ["https://..."],
  "temperature": 0.5,
  "max_tool_rounds": 8
}
```

### Request Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `messages` | array | **Yes** | — | Conversation history. At least 1 user message. |
| `messages[].role` | string | Yes | — | `"user"` or `"assistant"` |
| `messages[].content` | string/array | Yes | — | Message text or content block array |
| `space` | object | No | null | Space data. **Without this, tools are disabled** (general Q&A only). |
| `space.name` | string | Yes* | — | Property name |
| `space.rooms` | array | No | [] | Room data (name, type, sqft, etc.) |
| `space.rooms[].type` | string | No | "otherroom" | One of: `learningroom`, `office`, `restroom`, `hallway`, `storage`, `workshop`, `breakroom`, `conferenceroom`, `limitlessroom`, `rocketroom`, `multipurpose`, `reception`, `lobby`, `otherroom` |
| `space.rooms[].sqft` | number | Yes* | — | Square footage |
| `space.spaceDetails` | object | No | — | Building-level info |
| `space.spaceDetails.address` | string | No | — | Full address (used for city multiplier + permit timeline) |
| `space.spaceDetails.totalArea` | number | No | — | Gross SF |
| `space.floorPlanUrls` | array | No | — | Floor plan PNG URLs (sent to Claude as images) |
| `region` | string | No | "default" | Region identifier |
| `drive_doc_summaries` | object | No | null | Pre-extracted doc summaries (inspection, SIR, lease) |
| `sir_structured` | object | No | null | Structured SIR data (50+ fields across 10 departments) |
| `temperature` | number | No | 0.5 | Claude temperature (0.0–1.0) |
| `max_tool_rounds` | number | No | 8 | Max tool execution rounds (1–12) |

---

### SSE Event Protocol

Response streams as `text/event-stream`. Each event has a named `event` type and JSON `data`.

| Event | When | Data Shape |
|-------|------|------------|
| `round_start` | New Claude API call begins | `{ "round": 1, "messageCount": 1 }` |
| `text_delta` | Text chunk from Claude | `{ "text": "Let me look at this space..." }` |
| `tool_start` | Calculator tool executing | `{ "name": "classify_rooms", "input": {...}, "round": 1 }` |
| `tool_result` | Tool execution complete | `{ "name": "classify_rooms", "output": {...}, "round": 1, "durationMs": 3 }` |
| `estimate_cards` | present_estimate called | `{ "cards": [...], "warnings": [...] }` |
| `done` | Stream complete | **Full structured response** (see below) |
| `error` | Error occurred | `{ "code": "STREAM_ERROR", "message": "..." }` |

**Wire format example:**
```
event: round_start
data: {"round":1,"messageCount":1}

event: text_delta
data: {"text":"Eight rooms, 4,200 gross SF. "}

event: text_delta
data: {"text":"Let me see what we're working with —"}

event: tool_start
data: {"name":"classify_rooms","input":{"rooms":[...]},"round":1}

event: tool_result
data: {"name":"classify_rooms","output":[...],"round":1,"durationMs":3}

event: round_start
data: {"round":2,"messageCount":5}

event: text_delta
data: {"text":"Austin multiplier is 0.89..."}

...

event: done
data: {"narrative":"...","structured":{...},"tool_calls":[...],"usage":{...}}
```

---

### `done` Event — Complete Structured Response

This is the final event. Contains Ray's full narrative AND all deterministic structured data.

```json
{
  "narrative": "Eight rooms, 4,200 gross SF. Let me see what we're working with — ... [Ray's full text across all rounds]",

  "estimate_cards": {
    "cards": [
      {
        "label": "MVP (As-Is)",
        "capacity": {
          "students": 38,
          "guidesNeeded": 5,
          "gradeLevels": "K-8",
          "nla": 2400
        },
        "costBreakdown": [
          { "category": "Finish Work", "amount": 42000, "note": "Paint + clean + selective repairs" },
          { "category": "Furniture", "amount": 22400 },
          { "category": "Internet/Low Voltage", "amount": 2100 },
          { "category": "Signage", "amount": 2000 }
        ],
        "adjustments": [
          { "item": "ADA restroom retrofit", "amount": 12000, "reason": "28-inch door needs widening" },
          { "item": "Skip ceiling replacement", "amount": -4000, "reason": "Existing ceiling in good condition" }
        ],
        "totals": {
          "hardCost": 68500,
          "furniture": 22400,
          "softCosts": 6200,
          "gcFee": 4600,
          "contingency": 6300,
          "adjustmentTotal": 8000,
          "grandTotal": 86000,
          "costPerSF": 20.48
        },
        "timeline": { "weeks": 4, "note": "Permit not required for cosmetic TI" }
      },
      {
        "label": "Ideal (Max Capacity)",
        "capacity": {
          "students": 52,
          "guidesNeeded": 7,
          "gradeLevels": "K-8",
          "nla": 3200
        },
        "costBreakdown": [
          { "category": "Selective Demolition", "amount": 5200 },
          { "category": "Framing & Drywall", "amount": 18500 },
          { "category": "MEP Rough-In", "amount": 45000 },
          { "category": "Interior Doors", "amount": 8000 },
          { "category": "Plumbing (additional restroom)", "amount": 20000 },
          { "category": "Finish Work", "amount": 55000 },
          { "category": "Furniture", "amount": 34000 },
          { "category": "Internet/Low Voltage", "amount": 4200 },
          { "category": "Security", "amount": 0 },
          { "category": "Signage", "amount": 2000 }
        ],
        "totals": {
          "hardCost": 185000,
          "furniture": 34000,
          "softCosts": 22000,
          "gcFee": 15000,
          "contingency": 33000,
          "grandTotal": 245000,
          "costPerSF": 58.33
        },
        "timeline": { "weeks": 14, "note": "Includes 6-10 week permit" }
      }
    ],
    "cityMultiplier": { "value": 1.147, "source": "RS Means (Stamford, CT)" },
    "warnings": [
      "Upper floor detected — verify 2 separate exits per IBC",
      "No on-site play area — verify nearby park within 5-min walk"
    ]
  },

  "structured": {
    "room_classifications": [
      { "name": "Main Room", "type": "multipurpose", "sqft": 2800, "classifiedBy": "size: 2800 SF" },
      { "name": "Kitchen", "type": "breakroom", "sqft": 200, "classifiedBy": "name: \"kitchen\"" },
      { "name": "Office", "type": "office", "sqft": 150, "classifiedBy": "original" },
      { "name": "Restroom", "type": "restroom", "sqft": 60, "classifiedBy": "original" },
      { "name": "Closet", "type": "storage", "sqft": 35, "classifiedBy": "size: 35 SF" }
    ],

    "city_multiplier": {
      "multiplier": 1.147,
      "city": "Stamford",
      "state": "Connecticut",
      "source": "RS Means (Stamford, Connecticut)"
    },

    "capacity": {
      "capacity": 42,
      "nla": 3200,
      "nlaSource": "room data",
      "grossSqft": 12561,
      "grossCeiling": 100,
      "effectiveDensity": 42.0,
      "sharingMultiplier": 1.0,
      "classroomCount": 5,
      "effectiveClassrooms": 5,
      "gradeLevels": 4,
      "guidesNeeded": 6,
      "guideRatio": 8,
      "warnings": [
        "No on-site play area — verify nearby park within 5-min walk for a 7-year-old, no major street crossings",
        "Upper floor detected — verify 2 separate exits per IBC (hard stop)"
      ]
    },

    "costs_mvp": {
      "scope": "MVP",
      "tier": "T1",
      "cityMultiplier": 1.147,
      "hardCost": 68500,
      "furniture": 22400,
      "softCosts": 6200,
      "softCostBreakdown": [
        { "item": "Architect Fees", "rate": "6.0%", "cost": 2760 },
        { "item": "Engineering", "rate": "3.0%", "cost": 1380 },
        { "item": "Permits", "rate": "1.5%", "cost": 690 },
        { "item": "Inspections", "rate": "0.8%", "cost": 368 },
        { "item": "Site Prep", "rate": "3.5%", "cost": 1610 }
      ],
      "gcFee": 4600,
      "gcFeeRate": "10%",
      "contingency": 6300,
      "contingencyRate": "8%",
      "grandTotal": 86000,
      "costPerSF": 20.48,
      "timelineWeeks": 4,
      "categories": [
        {
          "category": "Finish Work",
          "subtotal": 42000,
          "note": "Paint + drywall patch + deep clean + selective floor/ceiling repair + bulb replacement + restroom repair + HVAC clean"
        },
        { "category": "Furniture", "subtotal": 22400, "note": "T1 @ $21.80/SF (furnished rooms only)" },
        { "category": "Internet/Low Voltage", "subtotal": 2100, "note": "$0.50/SF" },
        { "category": "Signage & Wayfinding", "subtotal": 2000, "note": "Basic wayfinding + exterior sign" }
      ],
      "roomBreakdown": [
        {
          "name": "Main Room",
          "type": "multipurpose",
          "sqft": 2800,
          "finishCost": 28000,
          "costPerSF": 10.00,
          "lineItems": [
            { "item": "Paint (fresh coat + surface prep)", "cost": 12880, "rsMeansCode": 4240 },
            { "item": "Drywall patch + ceiling paint", "cost": 5740 },
            { "item": "Deep clean (floors, windows, surfaces)", "cost": 4200, "rsMeansCode": 4370 },
            { "item": "Floor repair & deep clean (selective)", "cost": 3920, "rsMeansCode": 4110 },
            { "item": "Bulb/ballast replacement + lens clean", "cost": 1960 },
            { "item": "Ceiling tile repair (selective)", "cost": 2100 },
            { "item": "Base/trim touch-up", "cost": 2240, "rsMeansCode": 4320 }
          ]
        }
      ]
    },

    "costs_ideal": {
      "scope": "Ideal",
      "tier": "T1",
      "cityMultiplier": 1.147,
      "hardCost": 185000,
      "furniture": 34000,
      "softCosts": 22000,
      "gcFee": 15000,
      "gcFeeRate": "10%",
      "contingency": 33000,
      "contingencyRate": "15%",
      "grandTotal": 245000,
      "costPerSF": 58.33,
      "timelineWeeks": 8,
      "needsConstruction": true,
      "additionalStudents": 15,
      "categories": [
        { "category": "Selective Demolition", "subtotal": 5200, "note": "Reconfigure layout for optimal classroom sizes and flow" },
        { "category": "Framing & Drywall (new partitions)", "subtotal": 18500, "note": "Interior partition walls, glass partition framing for sight lines", "rsMeansCode": 4060 },
        { "category": "MEP Rough-In (HVAC, electrical, fire alarm)", "subtotal": 45000, "note": "HVAC branches to new enclosed rooms, electrical circuits, fire alarm devices" },
        { "category": "Interior Doors", "subtotal": 8000, "note": "4 door assemblies (fire-rated where required)", "rsMeansCode": 4030 },
        { "category": "Plumbing (additional restroom)", "subtotal": 20000, "note": "Additional restroom to meet IBC Table 2902.1 for student count", "rsMeansCode": 4260 },
        { "category": "Finish Work", "subtotal": 55000 },
        { "category": "Furniture", "subtotal": 34000 },
        { "category": "Internet/Low Voltage", "subtotal": 4200 },
        { "category": "Signage & Wayfinding", "subtotal": 2000 }
      ],
      "roomBreakdown": "[ same structure as MVP — per-room line items ]"
    },

    "code_compliance": {
      "studentCount": 42,
      "restroomCount": 2,
      "totalRestroomSF": 120,
      "grossSqft": 12561,
      "totalEstimatedCodeCost": 52000,
      "findings": [
        {
          "category": "Plumbing (IBC 2902.1)",
          "required": "1 WC per gender (2 total), 1 lavs, 1 fountains",
          "existing": "2 restrooms detected (est. 2 WC, 2 lavs)",
          "compliant": true,
          "deficiency": null,
          "estimatedCost": 0,
          "note": "Each new rough-in: $15-25K (includes plumbing, fixtures, partition, ventilation)"
        },
        {
          "category": "ADA Accessibility",
          "required": "32\" clear doors, 18\" pull-side clearance, 60\" restroom turning radius, grab bars",
          "existing": "Cannot fully verify from scan data — verify on site",
          "compliant": "likely",
          "estimatedCost": 0,
          "note": "ADA restroom retrofit: $8-15K. Door widening: $500-1,500. Ramp: $3-8K."
        },
        {
          "category": "Fire/Life Safety",
          "required": "Sprinkler system (>12,000 SF); 2+ exits required (>49 occupants); Panic hardware; Fire alarm; 90-min emergency lighting; 1-hr rated corridors",
          "existing": "Cannot verify from scan — requires site inspection",
          "compliant": "unknown",
          "estimatedCost": 35000,
          "note": "Fire alarm system: $15-30K. Sprinkler (new zone): $3-5/SF."
        },
        {
          "category": "Egress (IBC Chapter 10)",
          "required": "Dead-end corridors: max 20 ft (50 ft if sprinklered); Common path of travel: max 75 ft; Single exit acceptable",
          "existing": "Verify corridor lengths and exit locations on site",
          "compliant": "unknown",
          "estimatedCost": 0,
          "note": "Egress modifications vary widely. New exit door: $3-8K."
        },
        {
          "category": "Ventilation (ASHRAE 62.1)",
          "required": "804 CFM total outdoor air (10 CFM/person + 0.12 CFM/SF)",
          "existing": "Existing HVAC likely sized for office occupancy (1.3 CFM/person) — school needs 7.5x more outdoor air",
          "compliant": "unlikely without HVAC modification",
          "deficiency": "Office-to-school conversion almost always requires DOAS or ERV",
          "estimatedCost": 96000,
          "note": "DOAS/ERV: $15-48/SF depending on system. Budget $25-35/SF for typical retrofit."
        },
        {
          "category": "Grandfathering (IBC 3412)",
          "required": "If improvements exceed 50% of building value, entire building must meet current code",
          "existing": "Verify assessed building value against total improvement budget",
          "compliant": "unknown",
          "estimatedCost": 0,
          "note": "This is the hidden bomb. A $200K improvement to a $350K building triggers full code compliance."
        }
      ],
      "summary": ["Ventilation (ASHRAE 62.1)"]
    },

    "permit_timeline": {
      "weeks": "6-10",
      "city": null,
      "state": "Connecticut",
      "source": "state default (Connecticut)",
      "note": "CT moderate pace."
    }
  },

  "tool_calls": [
    { "name": "classify_rooms", "input": { "rooms": "[...]" }, "output": "[...]", "round": 1, "durationMs": 3 },
    { "name": "lookup_city_multiplier", "input": { "address": "20 Church St, Greenwich, CT" }, "output": { "multiplier": 1.147, "city": "Stamford", "state": "Connecticut" }, "round": 1, "durationMs": 1 },
    { "name": "calculate_capacity", "input": { "rooms": "[...]", "grossSqft": 12561, "classroomCount": 5 }, "output": { "capacity": 42, "guidesNeeded": 6 }, "round": 2, "durationMs": 1 },
    { "name": "check_code_compliance", "input": { "rooms": "[...]", "studentCount": 42 }, "output": { "totalEstimatedCodeCost": 52000 }, "round": 2, "durationMs": 1 },
    { "name": "estimate_costs", "input": { "tier": "T1", "scope": "mvp", "cityMultiplier": 1.147 }, "output": { "grandTotal": 86000 }, "round": 3, "durationMs": 5 },
    { "name": "estimate_costs", "input": { "tier": "T1", "scope": "ideal", "mvpCapacity": 42, "idealCapacity": 52 }, "output": { "grandTotal": 245000 }, "round": 4, "durationMs": 4 },
    { "name": "lookup_permit_timeline", "input": { "address": "20 Church St, Greenwich, CT" }, "output": { "weeks": "6-10" }, "round": 5, "durationMs": 1 },
    { "name": "present_estimate", "input": { "cards": "[...]" }, "output": null, "round": 6, "durationMs": 0 }
  ],

  "usage": {
    "input_tokens": 24500,
    "output_tokens": 6200,
    "rounds": 6
  }
}
```

---

## 7 Calculator Tools (Deterministic)

These execute server-side. Same inputs always produce identical outputs.

### 1. `classify_rooms`
Classifies untyped rooms (from Matterport scans) into microschool room types by name and size.

**Input**: `{ "rooms": [{ "name": "Kitchen", "type": "otherroom", "sqft": 200 }] }`
**Output**: `[{ "name": "Kitchen", "type": "breakroom", "sqft": 200, "classifiedBy": "name: \"kitchen\"" }]`

### 2. `lookup_city_multiplier`
RS Means city cost multiplier from address. 198-city table with state/national fallbacks.

**Input**: `{ "address": "123 Main St, Austin, TX 78701" }`
**Output**: `{ "multiplier": 0.826, "city": "Austin", "state": "Texas", "source": "RS Means (Austin, Texas)" }`

### 3. `calculate_capacity`
Student capacity using JC Fischer microschool spec: 42 SF/student, sharing penalty, guide ratio.

**Input**: `{ "rooms": [...], "grossSqft": 4200, "classroomCount": 5, "gradeLevels": 4 }`
**Output**: `{ "capacity": 42, "nla": 3200, "guidesNeeded": 6, "sharingMultiplier": 1.0, "warnings": [...] }`

### 4. `estimate_costs`
Construction/finish costs. MVP (as-is) or Ideal (with construction). Uses RS Means + city multiplier.

**Input (MVP)**: `{ "rooms": [...], "tier": "T1", "cityMultiplier": 1.147, "totalSqft": 4200 }`
**Input (Ideal)**: `{ "rooms": [...], "tier": "T1", "cityMultiplier": 1.147, "totalSqft": 4200, "scope": "ideal", "mvpCapacity": 38, "idealCapacity": 52 }`
**Output**: `{ "grandTotal": 86000, "hardCost": 68500, "categories": [...], "roomBreakdown": [...], "timelineWeeks": 4 }`

### 5. `check_code_compliance`
IBC plumbing, ADA, fire/life safety, egress, ventilation, grandfathering threshold.

**Input**: `{ "rooms": [...], "studentCount": 42, "grossSqft": 4200 }`
**Output**: `{ "findings": [...], "totalEstimatedCodeCost": 52000, "summary": ["Ventilation (ASHRAE 62.1)"] }`

### 6. `lookup_permit_timeline`
Typical TI permit timeline by city/state. 40+ cities, 15 state defaults.

**Input**: `{ "address": "20 Church St, Greenwich, CT" }`
**Output**: `{ "weeks": "6-10", "state": "Connecticut", "note": "CT moderate pace." }`

### 7. `present_estimate` (output tool)
Called by Ray at the end to structure the estimate cards. Not executed — data is captured for display.

**Input**: `{ "cards": [{ "label": "MVP", ... }, { "label": "Ideal", ... }], "warnings": [...] }`
**Output**: Not executed (captured as `estimate_cards` in the response).

---

## Error Responses

Non-streaming errors return JSON:

| Status | Code | Description |
|--------|------|-------------|
| 400 | `INVALID_JSON` | Request body is not valid JSON |
| 400 | `VALIDATION_ERROR` | Schema validation failed. Includes `path` field. |
| 503 | `UNAVAILABLE` | Claude not configured. Set `GOOGLE_CLOUD_PROJECT`. |

```json
{
  "ok": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Required",
    "path": "messages"
  }
}
```

---

## curl Examples

### General Q&A (no tools)
```bash
curl -N -X POST https://raycon-api-738625530258.us-central1.run.app/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"What makes a good microschool space?"}]}'
```

### With space data (tools enabled)
```bash
curl -N -X POST https://raycon-api-738625530258.us-central1.run.app/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role":"user","content":"What is the MVP cost for T1?"}],
    "space": {
      "name": "Test School",
      "rooms": [
        {"name":"Learning Room 1","type":"learningroom","sqft":450,"wallHeightFt":10},
        {"name":"Office","type":"office","sqft":120,"wallHeightFt":10},
        {"name":"Restroom","type":"restroom","sqft":80,"wallHeightFt":10},
        {"name":"Hallway","type":"hallway","sqft":200,"wallHeightFt":10}
      ],
      "spaceDetails": {"address":"123 Main St, Austin, TX 78701","totalArea":850}
    }
  }'
```

### Health check
```bash
curl https://raycon-api-738625530258.us-central1.run.app/health
```

---

## Determinism Guarantee

The `structured` field in the `done` event is **deterministic**. Given identical:
- `space.rooms` (same types, sqft, dimensions)
- `space.spaceDetails.address` (same city for multiplier)
- `tier` (T1 or T2)
- `classroomCount` and `nlaOverride` (if Ray passes them)

The numeric outputs will be bit-for-bit identical:
- `capacity.capacity`, `capacity.guidesNeeded`
- `costs_mvp.grandTotal`, `costs_mvp.costPerSF`
- `costs_ideal.grandTotal`
- `code_compliance.totalEstimatedCodeCost`
- `permit_timeline.weeks`

The `narrative` text will vary (Claude's persona output is non-deterministic).

---

## Architecture

```
Client → POST /v1/chat → Hono Server → AnthropicVertex (Claude Opus 4.6)
                              ↕
                    estimationEngine.js (pure JS)
                    ├── classifyRooms()
                    ├── lookupCityMultiplier()
                    ├── calculateCapacity()
                    ├── calculateMVPCosts()
                    ├── calculateIdealCosts()
                    ├── checkCodeCompliance()
                    └── lookupPermitTimeline()
```

Multi-turn loop: Claude streams text → calls tool → server executes JS → injects result → Claude continues (max 8 rounds).
