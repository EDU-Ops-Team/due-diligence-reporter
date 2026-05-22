# Due Diligence Reporter Handoff

## 2026-05-21 - AI SIR vs. CDS SIR First-Pass Deep Dive

Objective: compare AI-generated Site Investigation Report packets against completed CDS SIRs and the supporting evidence in the reports. This pass used the model `AI SIR vs. CDS SIR vs. underlying evidence`, not a simple AI/CDS scorecard.

Source access notes:

- Google Drive connector required reauthorization, so source files were pulled from the usable `greg.foote@trilogy.com` Gmail searches and downloaded locally.
- Synced shared drive path `G:\Shared drives\Education Ops\All Locations` returned access denied in this session.
- Source attachments are local under `C:\Users\foote\.google_workspace_mcp\attachments\edu_ops`.
- Extracted text files are under `C:\tmp\sir_review_text`.

Reviewed first-pass cohort:

- `5601 Stone Rd, Centreville, VA`
- `1726 Whitley Ave, Los Angeles, CA`
- `421 E 11th St, Tulsa, OK`
- `6940 S Utica Ave, Tulsa, OK`
- `2409 S Macdill Ave, Tampa, FL`

Structured outcomes recorded with:

```powershell
uv run ddr sir-review add ...
```

Outcome store:

```text
.ddr-runs/sir-review-outcomes.jsonl
```

Recorded issue IDs:

- `112df21001e94d3d9fbd9cdc30ca2a22` - Centreville zoning: AI inferred likely SUP from wrong zoning assumption; CDS found C-6 by-right.
- `118e9058b29f4a378a687a7d93f953f4` - Centreville health: AI treated health inspection as required; CDS found no health permit if students bring lunches.
- `a21510d52c434fc4b446faff31e6c571` - 421 E 11th zoning: AI inferred SUP/public hearing; CDS found CBD by-right and no discretionary review.
- `b219f5f86a614f07949a8b0ed046a7d0` - 421 E 11th parking: AI estimated parking ratio; CDS found no parking required in CBD.
- `0b3f6457849f47dfb66d2f9e3e8487ad` - 421 E 11th health: AI overgeneralized school health inspection; CDS narrowed to food-service trigger.
- `0b5d63ed484547e9ab703b10588ad974` - 6940 S Utica zoning: AI inferred SUP; CDS found PUD-287/base OM by-right.
- `8178575aa7e64e889eede109148e8a47` - 6940 S Utica parking: AI estimated 10-15 spaces; CDS found 4 spaces.
- `2988d70f78504c5d8f92f3805424abe4` - 6940 S Utica health: AI overgeneralized health inspection; CDS narrowed to food-service trigger.
- `6084d624e7744e2f99a417c85cd304a7` - 1726 Whitley zoning: AI undercalled entitlement as CUP; CDS found zoning variance plus CPIO and Historic Design Review.
- `44899df9b1a7436a8d0d2177679d8b05` - 1726 Whitley historic/entitlements: AI flagged generic historic likelihood but missed named designations and sequencing.
- `e43e285d26b1414faff923d1bd63a7c6` - Tampa parking: AI used generic parking basis; CDS found jurisdiction-specific classroom ratio.

Trend command run:

```powershell
uv run ddr sir-trends --since 30d
```

Trend output:

```text
Issues: 11
Sites reviewed: 5
SIR pairs reviewed: 5
AI missed items/SIR: 0.4
AI unsupported claims/SIR: 1.2
CDS missed items/SIR: 0.0
DDR-impacting findings: 11
Blocking/material findings: 10
Top categories:
  AI unsupported claim: 6
  Better wording needed: 3
  AI missed item: 2
Top sections:
  Zoning: 4
  Health: 3
  Parking: 3
  Historic / Entitlements: 1
Repeat issues:
  Health | Better wording needed: 3
  Parking | AI unsupported claim: 3
  Zoning | AI unsupported claim: 3
```

Process changes suggested by this batch:

1. Retrieval rule: verify exact zoning district, PUD/base zoning, and use table before generating SUP/CUP/variance conclusions.
2. Retrieval rule: compute parking from the actual jurisdiction/district/use table before using generic student or square-foot ratios.
3. Prompt/template rule: make health findings conditional on the operating food model. Own-lunch/no cooking should not be treated the same as catering, serving, or cold-holding food.

Do not treat these as source-code changes yet. The next step should be deciding whether these repeated issues are enough to update retrieval/prompt/template guidance, then summarizing accepted learning outcomes back into the relevant operating workflow or system of record.

## 2026-05-21 - Continuous SIR Improvement Loop Implementation

Added repo support for making SIR review a recurring operating process:

- `ddr sir-review queue`
  - Scans local `.ddr-runs/*.json` pipeline manifests for `sir_learning_review` metadata.
  - Defaults to unreviewed `ready_for_review` pairs.
  - Deduplicates repeated manifests for the same site/SIR pair.
  - Marks pairs as reviewed when matching rows already exist in `.ddr-runs/sir-review-outcomes.jsonl`.
  - Useful options: `--status all`, `--include-reviewed`, `--limit 25`, `--manifest-dir`, `--store`.
- `ddr sir-monthly-summary --since 30d`
  - Reads the existing SIR review outcome store.
  - Prints a markdown operating memo with review volume, reliability signals, repeat patterns, accepted learning actions, and monthly decision prompts.

Files changed:

- `src/due_diligence_reporter/sir_review_queue.py`
- `src/due_diligence_reporter/ddr_cli.py`
- `src/due_diligence_reporter/sir_trends.py`
- `tests/test_ddr_cli.py`
- `docs/process/sir-learning-loop.md`

Verification:

```powershell
$env:TMP = 'C:\tmp\pytest-ddr'
$env:TEMP = 'C:\tmp\pytest-ddr'
uv run pytest tests/test_ddr_cli.py tests/test_sir_trends.py tests/test_sir_learning.py
uv run ruff check src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\sir_trends.py src\due_diligence_reporter\sir_review_queue.py tests\test_ddr_cli.py
uv run mypy src\due_diligence_reporter\ddr_cli.py src\due_diligence_reporter\sir_trends.py src\due_diligence_reporter\sir_review_queue.py
uv run ddr sir-review queue --limit 5 --include-reviewed
uv run ddr sir-monthly-summary --since 30d
```

Results:

- Focused pytest: 12 passed.
- Ruff: all checks passed.
- Mypy: success for touched modules. It still prints the pre-existing unused `pyproject.toml` mypy override note.
- Live queue smoke test found one local ready candidate from existing manifests: `Alpha Keller`.
- Live monthly summary smoke test consumed the 11 current SIR review outcomes and reproduced the expected repeat patterns.

## 2026-05-21 - Google Workspace MCP Auth Repair

Google Workspace MCP auth was repaired so Drive-backed SIR evidence retrieval can resume after a Codex restart.

What changed outside this repo:

- `C:\Users\foote\.codex\bin\google-workspace-mcp-stdio.ps1` now uses the `greg_trilogy` credential profile.
- The wrapper launches `workspace-mcp==1.15.0` with read-only Drive, Docs, and Sheets permissions.
- Token/cache path is `C:\Users\foote\.google_workspace_mcp\credentials\greg_trilogy`.
- OAuth credentials are stored in Windows Credential Manager target `Codex:google_workspace_mcp_oauth`; do not print or inspect the secret.

Verification completed:

- OAuth callback log showed successful authorization-code exchange for `greg.foote@trilogy.com`.
- Token file appeared at `C:\Users\foote\.google_workspace_mcp\credentials\greg_trilogy\greg.foote@trilogy.com.json`.
- A direct local MCP Drive read succeeded with the new token.
- Temporary auth-helper processes were stopped afterward so port `8000` was clear.

Important operational lesson:

- If the local callback page shows only `Authentication Error`, check the MCP log before changing credentials. In this case the log said `No authorization code received from Google`, which meant the callback endpoint was opened directly or the in-app browser lost the `?code=...&state=...` query. The fix was to open the generated Google authorization URL in external Chrome/Edge.

Global lesson note added:

- `C:\Users\foote\.codex\memories\extensions\ad_hoc\notes\20260521-203015-google-workspace-mcp-auth-loopback.md`
