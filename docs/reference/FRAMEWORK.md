# Alpha Analysis Downstream Processing — Framework Document

> **Purpose of this document:** A plain-English guide to how this system works, covering architecture, data flow, integrations, and operational patterns.

---

## 1. What Is This System?

This is an **MCP (Model Context Protocol) server** — a plugin that a Claude AI agent loads as a set of callable tools. It automates the **LOI (Letter of Intent) downstream processing workflow** for Alpha Schools site selection.

When a new real estate site is submitted via email, someone asks Claude (using this server) to process recent LOI emails. Claude uses the tools here to:

1. Find the matching site in Wrike and update it
2. Notify the CDS vendor team with the SIR report
3. Organize email attachments into a structured Google Drive folder
4. Generate a Google Slides presentation with scores and maps

**The server itself doesn't run on a schedule or trigger automatically.** It responds to tool calls made by an AI agent (Claude) operating in a chat session (e.g., Google Chat with Claude integration). The `prompt.md` file serves as the "system prompt" for that Claude agent, defining its behavior and the exact steps it should follow.

---

## 2. Architecture Overview

```
Google Chat / Claude Agent
    │
    │  uses prompt.md as system prompt
    │  calls tools via MCP protocol (stdio)
    ▼
┌─────────────────────────────────────────┐
│   MCP Server (server.py)                │
│   FastMCP: "alpha-analysis-downstream.. │
│                                         │
│   Tools (6):                            │
│   ├─ update_wrike_site_record           │
│   ├─ get_wrike_site_record              │
│   ├─ send_loi_notification              │
│   ├─ list_drive_folders                 │
│   ├─ create_drive_folder_with_attachments│
│   └─ create_location_presentation       │
└──────────────┬──────────────────────────┘
               │
    ┌──────────┼──────────────────────┐
    ▼          ▼          ▼           ▼
 Wrike API  Google     AWS SES    Google Maps
 (v4)       APIs      (email)     (geocoding)
            ├─Gmail
            ├─Drive          OpenAI (GPT)
            └─Slides         (address matching)
```

**Transport:** stdio — the server reads JSON from stdin and writes JSON to stdout. This is the standard MCP protocol for local tool servers.

**Deployment:** Hosted on the MCP Hive platform (`mcp-hive.ti.trilogy.com`). GitHub Actions publishes the server as a ZIP to MCP Hive when code is pushed to `main`.

---

## 3. Source File Map

```
src/alpha_analysis_downstream_processing_mcp/
├── server.py          Main MCP server — all 6 tool definitions (~970 lines)
├── config.py          Settings via Pydantic (env vars, Google OAuth paths)
├── wrike.py           Wrike API client + all custom field ID mappings
├── google_client.py   Google OAuth + Gmail/Drive/Slides API operations
├── email_sender.py    AWS SES email construction and sending
├── presentation.py    Google Slides generation + geocoding via Google Maps
└── utils.py           URL extraction helpers + PDF downloading

prompt.md              "System prompt" for the Claude agent using this server
setup.sh               OAuth credential setup + MCP client config output
.github/workflows/
└── publish-to-mcp-hive.yml   CI/CD: publishes to MCP Hive on push to main
```

---

## 4. The Full Workflow (Step by Step)

This is what happens when an operator asks Claude to "process recent LOI emails":

### Step 1 — Email Discovery (Claude, using Gmail tools)

Claude searches Gmail for emails matching:
- Subject contains "New Site" (but NOT "New Site Kickoff")
- Received within the past 24 hours (configurable)

The "New Site Kickoff" exclusion is important — those are *outgoing* emails this system sends, not incoming LOI submissions.

### Step 2 — Data Extraction (Claude, using LLM parsing)

For each found email, Claude reads the full content and extracts 12 fields:

| Field | Source |
|-------|--------|
| brand | Email subject (e.g., "Alpha School", "Nova Academy") |
| street_address | Email body |
| city, state, zip | Email body |
| loi_signed_date | Email received timestamp (not body content) → MM/DD/YYYY |
| contact_name, email, phone | Email body |
| square_footage | Email body |
| complete_building | Email body (yes/no) |
| move_in_ready | Email body (yes/no) |
| current_space_usage | Email body |

If `street_address`, `city`, `state`, or `zip` are missing → skip this email.

### Step 3 — Four Tool Calls Per Location (in order)

#### 3.1 `update_wrike_site_record`
- Searches Wrike for a Site Record at stage "1. Looking for Sites" matching the address
- Address matching uses **OpenAI GPT** to handle abbreviations and variations
- Updates the record: stage → "2. Evaluating Potential Sites (LOI)", plus all location/contact/property fields
- Appends a "Real Estate Information" HTML section to the Wrike description
- Returns the Wrike record ID and permalink (used by all subsequent steps)

#### 3.2 `send_loi_notification`
- Takes the Wrike record ID from step 3.1
- Fetches the record and parses the SIR (Site Information Report) URL from the description
- Downloads the SIR PDF
- Sends an email via AWS SES to CDS (edu.ops@trilogy.com) with:
  - Subject: "New Site Kickoff: {address}"
  - Body table: address, school type, grades, student count, staff count
  - Attachment: SIR PDF

#### 3.3 `create_drive_folder_with_attachments`
- First checks via `list_drive_folders` whether a folder already exists (de-duplication)
- If not: creates a folder named `{brand}, {city}, {street_address}` in the fixed parent folder (`1RqwLyx0duTeWQPJWu7-HOpfQNlbe5jzQ`)
- Creates 7 standard subfolders inside it
- Downloads all attachments from the original email via Gmail API
- Uploads attachments to `01_Due Diligence` subfolder
- Updates the Wrike record's "Google Folder" custom field with the new Drive link

#### 3.4 `create_location_presentation`
- Copies the Google Slides template (`1s83QkZ_Gq-lQbUgRJw6gePMj-rqu-rNwZas8JYM56lc`)
- Pulls enrollment/wealth scores from the Wrike record
- Geocodes the address via Google Maps API
- Fetches a static map image and a Street View image
- Batch-updates the copied presentation with all data
- Sends an internal email notification (to andrew.vincent@trilogy.com) with the presentation link

### Step 4 — Summary Report (Claude)

Claude outputs a Google Chat-formatted summary with clickable links to the Wrike record, Drive folder, and presentation for each processed location.

---

## 5. External Integrations

### Wrike API (v4)
- **Auth:** Bearer token (`WRIKE_ACCESS_TOKEN` env var)
- **Space:** `IEAGN6I6I5RFSYZI` (hardcoded in `wrike.py`)
- **Site Record type ID:** `IEAGN6I6PIAEZNHZ`
- **Key operations:** Search records by custom field, update custom fields, update description
- **25+ custom field IDs** are hardcoded in `wrike.py` — each maps a human-readable name (e.g., `enrollment_score`) to a Wrike field ID (e.g., `IEAGN6I6JMAEDX5H`)

### Google APIs (OAuth 2.0)
- **Auth flow:** Installed app OAuth with offline access; token cached in `.gcp-saved-tokens.json`
- **Scopes:** `gmail.readonly`, `drive`, `presentations`
- **Gmail:** Download email attachments (base64-decoded from MIME parts)
- **Drive:** Create folders, upload files (resumable media), list child folders
- **Slides:** Copy presentation template, batch-update text/images

### AWS SES
- **Region:** us-east-1
- **Auth:** `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`
- **Sender:** `SES_SENDER_EMAIL` (env var)
- **Two email types:** LOI notification to CDS, presentation notification to internal team
- Recipients are hardcoded in `email_sender.py`

### OpenAI GPT
- **Auth:** `OPENAI_API_KEY`
- **Used for:** Address matching in `wrike.py` — when searching for the right Wrike record, it uses an LLM to compare the email's address against candidate Wrike records, handling abbreviations, typos, and format variations
- **Response format:** JSON with `matched_id` and `reasoning`

### Google Maps API
- **Auth:** `GOOGLE_MAPS_API_KEY`
- **Used for:** Geocoding (address → lat/lon), static map image, Street View image
- All three are used when building the location presentation

---

## 6. Key Data Models

### Wrike Custom Fields (subset of 25+ in `wrike.py`)

| Logical Name | Purpose |
|---|---|
| `overall_site_stage` | Stage in site selection pipeline |
| `address` | Full street address (stored as HTML with `<a>` tags) |
| `market`, `ahj`, `address_county` | Location metadata |
| `enrollment_score`, `wealth_score` | Demographic scores |
| `relative_enrollment_score`, `relative_wealth_score` | Relative comparisons |
| `enrollment_score_plus`, `relative_enrollment_score_plus` | Enhanced score variants |
| `school_type` | "Microschool 25", "Growth 250", or "Flagship 1000" |
| `square_footage`, `square_footage_buildings` | Property size |
| `loi_signed_date` | Date LOI was signed |
| `site_poc` | Point of contact |
| `vendor_team` | Assigned vendor contacts |
| `google_folder` | Link to Google Drive folder |

### Standard Drive Folder Structure

Every new site gets this folder structure:
```
{brand}, {city}, {street_address}/
├── 01_Due Diligence     ← LOI email attachments land here
├── 02_Business Entity
├── 03_Construction
├── 04_Private School Registration
├── 05_Vendors & Contracts
├── 06_Operations
└── 99_Working
```

### School Type Mapping

| Wrike Value | Internal | Grades | Students | Architect |
|---|---|---|---|---|
| "Microschool 25" / "Micro" | `micro` | K-8 | 25 | Apogee |
| "Growth 250" / "250" | `250` | K-12 | 250 | David Bench |
| "Flagship 1000" / "1000" | `1000` | K-12 | 1000 | David Bench |

---

## 7. Configuration & Environment

### Environment Variables Required

| Variable | Used By |
|---|---|
| `WRIKE_ACCESS_TOKEN` | Wrike API auth |
| `OPENAI_API_KEY` | Address matching LLM |
| `AWS_ACCESS_KEY_ID` | AWS SES |
| `AWS_SECRET_ACCESS_KEY` | AWS SES |
| `SES_SENDER_EMAIL` | SES sender identity |
| `GOOGLE_MAPS_API_KEY` | Geocoding + map images |

### Google OAuth
- Client secrets: `credentials/client_secrets.json`
- Cached tokens: `.gcp-saved-tokens.json`
- OAuth callback port: 8765
- Scopes: gmail.readonly, drive, presentations
- Token refresh is handled automatically

### Hardcoded IDs (Important for maintenance)

These are in the source and would need updating if the underlying resources change:

| What | Where | Value |
|---|---|---|
| Wrike Space ID | `wrike.py` | `IEAGN6I6I5RFSYZI` |
| Drive parent folder | `prompt.md` | `1RqwLyx0duTeWQPJWu7-HOpfQNlbe5jzQ` |
| Slides template ID | `presentation.py` | `1s83QkZ_Gq-lQbUgRJw6gePMj-rqu-rNwZas8JYM56lc` |
| Slides output folder | `presentation.py` | `1LHiqkN2OapT2g-jNLWVRnOIwHITQ8nYw` |
| Vendor: Monica Swannie (Wrike ID) | `wrike.py` | `RE5174381` |
| Vendor: Shinpei Kuo (Wrike ID) | `wrike.py` | `RE5174384` |
| CDS email recipients | `email_sender.py` | mswannie@, DHowse@ |
| Presentation notification recipient | `server.py` | andrew.vincent@trilogy.com |

---

## 8. Deployment & CI/CD

```
git push → main branch
    ↓
.github/workflows/publish-to-mcp-hive.yml
    ↓
Verifies 10 GitHub secrets are present
    ↓
Reads version from pyproject.toml
    ↓
Generates .env from secrets
    ↓
Builds metadata payload (name, description, OAuth config)
    ↓
ZIPs repo (excludes .git, __pycache__, .venv, etc.)
    ↓
POST to https://mcp-hive.ti.trilogy.com
    ↓
MCP Hive serves the server to Claude agents
```

The server runs as `uv run alpha-analysis-downstream-processing-mcp` via the entry point defined in `pyproject.toml`.

---

## 9. Testing & Code Quality

There are **no automated tests** in this codebase. Quality is enforced via:

- **`ruff`** — Linting and formatting (100-char line limit, strict rules)
- **`mypy`** — Static type checking (strict mode, Python 3.13)

Development commands:
```bash
uv sync              # Install dependencies
uv run ruff check .  # Lint
uv run ruff format . # Format code
uv run mypy src/     # Type check
```

---

## 10. Expected Success Rates (from prompt.md)

Not every step succeeds for every email — this is by design:

| Step | Expected Success | Why It May Fail |
|---|---|---|
| Wrike update | ~100% | Email data is incomplete |
| LOI email | 80-90% | SIR URL not present in Wrike description |
| Drive folder | 50-70% | Email has no attachments |
| Presentation | 90-100% | Geocoding failure or missing scores |

The system continues processing remaining emails even if one step fails.

---

## 11. Role of `prompt.md`

`prompt.md` is not loaded by the MCP server itself — it is used as the **system prompt** for the Claude agent that *calls* this server. It defines:

- How Claude should search for LOI emails
- What fields to extract and how to validate them
- The exact order of tool calls (3.1 → 3.2 → 3.3 → 3.4)
- Error handling rules (when to skip vs. when to stop)
- Output format for the summary (Google Chat markup style)
- De-duplication logic (check for existing Drive folder before creating)

This means changes to the workflow logic may need to be made in **both** `prompt.md` (agent behavior) and `server.py` (tool implementation).
