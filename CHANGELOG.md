# Changelog

All notable changes to the Due Diligence Reporter that affect operators,
CI workflows, or external integrations.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/),
but only entries that change observable behavior or operator contracts are
recorded here. Internal refactors, test additions, and pure documentation
changes do not get an entry.

## [Unreleased]

### Changed

- **`scripts/recover_migration_wiped_sites.py`: `--apply` / `--dry-run` are
  now a required mutually-exclusive group.** Previously, a bare invocation
  silently dry-ran. It now exits 2 with an argparse usage error. Every CI
  workflow that calls this script must pass exactly one of `--apply` or
  `--dry-run`. The `recover-migration-wiped-sites.yml` workflow already
  threads the appropriate flag from its dispatch input; ad-hoc local runs
  must do the same.

### Added

- **Retry-After parser hardening (`src/due_diligence_reporter/retry.py`).**
  `_parse_retry_after_seconds` now reads the header from both `exc.headers`
  (Google API style) and `exc.response.headers` (`requests.HTTPError`
  style). Prior to this change, every `requests`-based 429 fell back to
  exponential backoff because the Retry-After path was silently disabled.
  Affects Rebl, dashboard publish, and any other `requests`-based client.
- **Retry-After parser cap on the integer-header branch (defense-in-depth).**
  `_parse_retry_after_seconds` now caps the value returned from the
  integer-header branch at `_RETRY_AFTER_MAX_SECONDS` (1200 seconds /
  20 minutes) and emits a `WARNING` on the
  `due_diligence_reporter.retry` logger when the upstream value
  exceeds the cap. The ISO-timestamp branch remains uncapped at the
  parser level by design — `_rate_limit_aware_wait` clips both branches
  via `min(parsed, _RETRY_AFTER_MAX_SECONDS)` so callers that go through
  the wait strategy are protected on both paths. The parser-level cap
  exists for callers that bypass the wait strategy (e.g. one-shot
  diagnostics) and for the per-call WARNING signal.
- **uv version pinned to `0.5.14` across all GitHub Actions workflows**
  that use `astral-sh/setup-uv`. Previously most workflows used
  `version: "latest"`, which let lockfile-incompatible uv releases land
  silently between runs.
- **`DASHBOARD_PUBLISH_SECRET` rotation procedure documented in
  `.env.example`.** The variable itself was added in a prior change
  (the workflow-hardening / Rebl batch / 429-aware retry release);
  this entry adds the `openssl rand -hex 32` generation hint, the
  Vercel-secret sync requirement, and the rotation order (deploy to
  dd-dashboard first, then update here).

### Operator notes

#### Rebl outage vs. "no matches" — log signature

The recovery and validation scripts call Rebl's batch slug-resolution
endpoint via `canonical_slugs_for_addresses()`. Both an outage and a
legitimate empty result return `{}`, but only the outage path logs:

- **Rebl is down or rate-limited past the retry budget** (outage):
  the batch wrapper logs at `ERROR` with `"canonical_slugs_for_addresses:
  Rebl batch resolve failed after retries; falling back to empty
  mapping (N address(es) affected)"` *with a traceback (`exc_info`)*
  before returning `{}`. The singleton wrapper logs the equivalent
  message at `WARNING` level (it has a caller-supplied fallback so the
  call site can still proceed) — `"canonical_slug_for_address: Rebl
  resolve failed after retries…"` — also with `exc_info`. Both
  originate from the `due_diligence_reporter.rebl` logger.
  **Operator note:** if you grep only for `ERROR` during an incident,
  you will miss singleton-path failures. Filter for the
  `due_diligence_reporter.rebl` logger and include `WARNING`.
- **Rebl returned a result but no addresses resolved** (legitimate
  empty / nothing-matched): silent — no log line. The wrapper just
  returns `{}`. This is normal when Rebl has not yet indexed a fresh
  batch of records.

To tell them apart in workflow logs: look for an ERROR (or WARNING
for the singleton path) from `due_diligence_reporter.rebl` with a
traceback. Its presence means outage; its absence means Rebl was
healthy and just had nothing for these addresses.
