Capacity Brainlift

Goal
- Read a Block Plan and convert it into DD-report-ready scenario inputs for exactly two scenarios: Furniture Only and Max Capacity.

Required outputs
- `furniture_only.capacity_students`
- `furniture_only.classroom_count`
- `max_capacity.capacity_students`
- `max_capacity.classroom_count`
- `raycon_rooms`
- `block_plan_summary`
- `assumptions`

Rules
- Use only facts supported by the Block Plan content provided in the prompt.
- Do not create extra scenarios.
- If a capacity is not stated or cannot be derived with confidence, leave it null.
- `raycon_rooms` must use RayCon room types:
  `learningroom`, `office`, `restroom`, `hallway`, `storage`, `workshop`,
  `breakroom`, `conferenceroom`, `limitlessroom`, `rocketroom`,
  `multipurpose`, `reception`, `lobby`, `otherroom`.
- Return valid JSON only.

Room extraction guidance
- Preserve explicitly named rooms when possible.
- Include `name`, `type`, and `sqft` for each room.
- If the Block Plan is too abstract for room-by-room extraction, return an empty `raycon_rooms` list and rely on classroom counts only.

Scenario intent
- Furniture Only: lightest viable school-opening path supported by the Block Plan.
- Max Capacity: highest supported student count reflected by the Block Plan assumptions.
