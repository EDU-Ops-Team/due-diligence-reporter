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
- **Retry-After parser cap (defense-in-depth).** The parser now caps
  the returned value at 1200 seconds (20 minutes). The downstream wait
  strategy was already capping; the parser-level cap protects callers
  that bypass the wait strategy.
- **uv version pinned to `0.5.14` across all GitHub Actions workflows**
  that use `astral-sh/setup-uv`. Previously most workflows used
  `version: "latest"`, which let lockfile-incompatible uv releases land
  silently between runs.
- **`DASHBOARD_PUBLISH_URL` and `DASHBOARD_PUBLISH_SECRET` in
  `.env.example`.** These were previously undocumented; the recover
  script and dashboard publish flow both require them.

### Operator notes

#### Rebl outage vs. "no matches" — log signature

The recovery and validation scripts call Rebl's batch slug-resolution
endpoint via `canonical_slugs_for_addresses()`. Both an outage and a
legitimate empty result return `{}`, but only the outage path logs:

- **Rebl is down or rate-limited past the retry budget** (outage):
  the wrapper logs at `ERROR` with `"canonical_slugs_for_addresses:
  Rebl batch resolve failed after retries; falling back to empty
  mapping (N address(es) affected)"` *with a traceback (`exc_info`)*
  before returning `{}`. Equivalent ERROR for the singleton path:
  `"canonical_slug_for_address: Rebl resolve failed after retries…"`
  at `WARNING` level (singleton has caller fallback). Both originate
  from the `due_diligence_reporter.rebl` logger.
- **Rebl returned a result but no addresses resolved** (legitimate
  empty / nothing-matched): silent — no log line. The wrapper just
  returns `{}`. This is normal when Rebl has not yet indexed a fresh
  batch of records.

To tell them apart in workflow logs: look for an ERROR (or WARNING
for the singleton path) from `due_diligence_reporter.rebl` with a
traceback. Its presence means outage; its absence means Rebl was
healthy and just had nothing for these addresses.
