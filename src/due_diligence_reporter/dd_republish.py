"""Shared helper for event-driven DD Report republish.

Recommendation 3 from ``docs/event-driven-ddr-recommendations.md``:
every authoritative-doc arrival fires a "republish if needed" check.

The DDR has three authoritative documents that change report content:

* ``vendor_sir`` — vendor-confirmed SIR PDF (CDS email path).
* ``building_inspection`` — vendor Building Inspection PDF (Worksmith path).
* ``raycon_scenario`` — RayCon's ``raycon_scenario.json`` written into M1.

Pre-Rec.3, only RayCon's arrival regenerated an existing DD Report
(the Rec. 1 hook in ``scripts/raycon_followup.py``). Post-Rec.3, all
three call this single shared helper so we have ONE definition of
"should we republish."

Design choices:

* The helper is **idempotent** across rapid retries. Dedup is keyed on
  ``(site_id, reason, content_fingerprint)`` and persisted to a single
  state file ``.dd_republish_state.json`` at repo root. The fingerprint
  is caller-supplied (``raycon_run_id`` for RayCon; Drive
  ``file_id:modifiedTime`` for SIR/BI), giving us per-token provenance
  + value without inventing a new diff format on top of the existing
  trace structure.
* **No diff means no republish** (cost guard). A repeat call with the
  same fingerprint is a no-op.
* **First-generation** is not our concern: when no DD Report exists
  yet, the helper returns ``skipped_no_existing_report`` so the daily/
  inbox first-gen path keeps owning that case.
* **Failures are non-fatal**: the caller's primary action (publishing
  the RayCon Doc, filing the SIR/BI to Drive) has already succeeded by
  the time we get here, and a republish error must not undo that.
* **Force-after window** (12h, mirroring the RayCon path) ensures a
  permanently-stuck site still re-enters the pipeline at most once per
  half-day for the same fingerprint.

Observability mirrors PR #85's silent-fail pattern: every decision
emits a structured log line with ``reason``, ``site_id``, the
``decision``, and on skip the rationale.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .classifier import classify_document_type
from .config import Settings
from .google_client import GoogleClient
from .report_pipeline import PipelineResult, process_site_pipeline
from .utils import build_site_match_terms, extract_folder_id_from_url

logger = logging.getLogger("dd_republish")

# Recognized reason codes. Kept narrow so callers can't smuggle in
# arbitrary strings that would silently break dedup keying.
REASON_RAYCON = "raycon_scenario"
REASON_VENDOR_SIR = "vendor_sir"
REASON_BUILDING_INSPECTION = "building_inspection"
SUPPORTED_REASONS = frozenset(
    {REASON_RAYCON, REASON_VENDOR_SIR, REASON_BUILDING_INSPECTION}
)

# Single shared state file for all three reasons. Keyed by
# ``f"{site_id}:{reason}:{content_fingerprint}"``. Lives at repo root
# next to ``.raycon_dispatch_state.json`` so on-call has a uniform
# place to look at observability state.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DD_REPUBLISH_STATE_PATH = _PROJECT_ROOT / ".dd_republish_state.json"

# Pre-Rec.3 path: the old RayCon-only file at
# ``.raycon_dd_republish_state.json`` keyed on ``site_id:run_id``.
# ``load_state`` migrates entries from this file into the shared
# ``site_id:reason:fingerprint`` shape on first read so RayCon dedup
# survives the cutover. The legacy file is left in place; subsequent
# runs no-op once the new file holds the migrated keys.
LEGACY_DD_REPUBLISH_STATE_PATH = (
    _PROJECT_ROOT / ".raycon_dd_republish_state.json"
)

# Force-republish window: regenerate at least once per N hours for the
# same ``(site_id, reason, fingerprint)`` triple if conditions still
# hold. Mirrors ``DD_REPUBLISH_FORCE_AFTER`` in raycon_followup.py so
# the RayCon path's behavior is unchanged after refactoring.
DD_REPUBLISH_FORCE_AFTER = timedelta(hours=12)


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


@dataclass
class RepublishOutcome:
    """Structured result of ``maybe_republish_dd_report``.

    ``decision`` is one of:
      * ``"republish"`` — pipeline ran with ``force_regenerate=True``.
      * ``"skip_no_prior_report"`` — first-generation case; not our job.
      * ``"skip_no_diff"`` — same fingerprint within force-after window.
      * ``"skip_dry_run"`` — dry run; no work performed.
      * ``"skip_bad_input"`` — caller-supplied site fields incomplete.
      * ``"failed"`` — pipeline raised; report unchanged. ``error`` set.

    The dataclass also exposes ``as_dict()`` so callers can merge the
    outcome into a per-site result row, mirroring the shape of the
    RayCon-followup row.
    """

    decision: str
    reason: str
    site_id: str
    fingerprint: str
    error: str = ""
    pipeline_status: str = ""
    doc_url: str = ""
    last_republish: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "dd_report_republish": self.decision,
            "republish_reason": self.reason,
            "site_id": self.site_id,
            "content_fingerprint": self.fingerprint,
            "error": self.error,
            "pipeline_status": self.pipeline_status,
            "doc_url": self.doc_url,
            "last_republish": self.last_republish,
        }


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def load_state(
    path: Path = DD_REPUBLISH_STATE_PATH,
    *,
    legacy_path: Path = LEGACY_DD_REPUBLISH_STATE_PATH,
) -> dict[str, str]:
    """Load the ``{state_key: last_republish_iso}`` dedup map.

    Returns ``{}`` on missing file or any read/parse error so a corrupt
    state file never blocks a republish.

    Also runs the one-shot legacy migration: pre-Rec.3 the file lived
    at ``.raycon_dd_republish_state.json`` and was keyed
    ``site_id:run_id``. We rewrite each legacy entry into the new
    ``site_id:raycon_scenario:run_id`` shape, but only when the new
    state doesn't already have the key (new wins on conflict). Lives
    here (in the shared loader) so both ``raycon_followup`` and
    ``scan_inbox`` get the migration; otherwise scan_inbox would
    silently drop the legacy state.
    """
    state: dict[str, str] = {}
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                state = {k: str(v) for k, v in data.items()}
    except Exception as e:
        logger.warning("Failed to read DD republish state at %s: %s", path, e)
        state = {}

    # Legacy migration. Fail-closed: malformed legacy file → ignored,
    # not crash. We never delete the legacy file; once the new file
    # holds the migrated keys, this branch becomes a no-op overlay.
    try:
        if legacy_path.exists():
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
            if isinstance(legacy, dict):
                for old_key, ts in legacy.items():
                    if not isinstance(old_key, str) or ":" not in old_key:
                        continue
                    site_id, run_id = old_key.split(":", 1)
                    new_key = f"{site_id}:{REASON_RAYCON}:{run_id}"
                    state.setdefault(new_key, str(ts))
    except Exception as e:
        logger.warning(
            "Failed to migrate legacy DD republish state at %s: %s",
            legacy_path,
            e,
        )
    return state


def atomic_write_json(path: Path, data: Any) -> None:
    """Atomically write *data* as pretty JSON to *path*.

    Writes to a sibling tempfile in the same directory then ``os.replace``
    onto the target path so concurrent readers never see a half-written
    file. Tempfile lives in the same directory to keep ``os.replace``
    atomic on a single filesystem. Raises on failure — callers decide
    whether to swallow or propagate.

    Shared utility used by both ``save_state`` (this module) and
    ``_save_failure_state`` (report_pipeline) so any state file written
    at repo root has the same crash-safety guarantees.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, sort_keys=True, indent=2)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    try:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def save_state(state: dict[str, str], path: Path = DD_REPUBLISH_STATE_PATH) -> None:
    """Persist the dedup map atomically. Best-effort; logs but does not raise."""
    try:
        atomic_write_json(path, state)
    except Exception as e:
        logger.warning("Failed to write DD republish state at %s: %s", path, e)


def _state_key(site_id: str, reason: str, fingerprint: str) -> str:
    return f"{site_id}:{reason}:{fingerprint}"


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# DD Report discovery
# ---------------------------------------------------------------------------


def find_existing_dd_report(
    gc: GoogleClient, site_folder_id: str
) -> dict[str, Any] | None:
    """Return the most recently modified DD Report Doc in the site folder.

    DD Reports live at the site-folder root (not M1) and are named like
    ``<site> DD Report - YYYY-MM-DD``. We classify by filename via the
    shared classifier so we accept any historical naming scheme that
    ``classify_document_type`` flags as ``dd_report``.
    """
    candidate: dict[str, Any] | None = None
    try:
        files = gc.list_files_in_folder(site_folder_id)
    except Exception as e:
        logger.warning(
            "Could not list site folder %s while looking for existing DD Report: %s",
            site_folder_id,
            e,
        )
        return None
    for f in files:
        if classify_document_type(str(f.get("name", ""))) != "dd_report":
            continue
        if candidate is None or str(f.get("modifiedTime", "")) > str(
            candidate.get("modifiedTime", "")
        ):
            candidate = f
    return candidate


# ---------------------------------------------------------------------------
# The shared helper
# ---------------------------------------------------------------------------


def maybe_republish_dd_report(
    gc: GoogleClient,
    *,
    site_summary: dict[str, Any],
    reason: str,
    content_fingerprint: str,
    settings: Settings,
    system_prompt: str,
    shared_cache: dict[str, list[dict[str, Any]]],
    republish_state: dict[str, str],
    dry_run: bool = False,
    force: bool = False,
    force_after: timedelta = DD_REPUBLISH_FORCE_AFTER,
    now: datetime | None = None,
    pipeline_runner: Callable[..., PipelineResult] | None = None,
    existing_report_finder: Callable[
        [GoogleClient, str], dict[str, Any] | None
    ]
    | None = None,
) -> RepublishOutcome:
    """Idempotent "republish DD Report if a material input changed" hook.

    Args:
        gc: Authenticated Google client.
        site_summary: Site summary dict with at minimum ``id``,
            ``title``, and ``drive_folder_url``. Also forwards
            ``p1_assignee_email``/``_name`` and ``created_date`` to
            ``process_site_pipeline`` when supplied.
        reason: One of ``vendor_sir``, ``building_inspection``,
            ``raycon_scenario``. Anything else is rejected as
            ``skip_bad_input``.
        content_fingerprint: Caller-supplied identifier of the new
            authoritative input. RayCon: ``raycon_run_id`` (or a
            synthetic fallback derived from JSON ``modifiedTime``).
            SIR/BI: ``f"{drive_file_id}:{drive_modified_time}"``.
            Empty string is rejected as ``skip_bad_input``.
        settings: App settings (forwarded to pipeline).
        system_prompt: DD Report agent system prompt text.
        shared_cache: Pre-fetched Drive shared-folder listing.
        republish_state: Mutated in place on republish for dedup.
        dry_run: If True, returns ``skip_dry_run`` without invoking
            the pipeline.
        force: If True, bypass the same-fingerprint dedup. Reserved for
            operator-driven recovery; default callers leave it off.
        force_after: Force-republish window. Same fingerprint repeated
            inside this window → no-op.
        now: Injectable for tests.
        pipeline_runner: Injectable ``process_site_pipeline`` for tests.
        existing_report_finder: Injectable ``find_existing_dd_report``
            for tests.

    Returns: ``RepublishOutcome``. Never raises; failures are encoded
    in the returned outcome.
    """
    now = now or datetime.now(UTC)
    runner = pipeline_runner or process_site_pipeline
    finder = existing_report_finder or find_existing_dd_report

    site_id = str(site_summary.get("id", "")).strip()
    site_name = str(site_summary.get("title", "")).strip() or "(unnamed)"
    drive_folder_url = str(site_summary.get("drive_folder_url", "")).strip()
    fingerprint = (content_fingerprint or "").strip()

    if reason not in SUPPORTED_REASONS:
        logger.warning(
            "DD republish rejected: unknown reason=%r site_id=%s",
            reason,
            site_id,
        )
        return RepublishOutcome(
            decision="skip_bad_input",
            reason=reason,
            site_id=site_id,
            fingerprint=fingerprint,
            error=f"unknown reason: {reason!r}",
        )
    if not fingerprint:
        logger.warning(
            "DD republish rejected: empty content_fingerprint reason=%s site_id=%s",
            reason,
            site_id,
        )
        return RepublishOutcome(
            decision="skip_bad_input",
            reason=reason,
            site_id=site_id,
            fingerprint=fingerprint,
            error="empty content_fingerprint",
        )
    if not drive_folder_url:
        logger.warning(
            "DD republish rejected: missing drive_folder_url reason=%s site_id=%s",
            reason,
            site_id,
        )
        return RepublishOutcome(
            decision="skip_bad_input",
            reason=reason,
            site_id=site_id,
            fingerprint=fingerprint,
            error="missing drive_folder_url",
        )

    site_folder_id = extract_folder_id_from_url(drive_folder_url) or ""
    if not site_folder_id:
        logger.warning(
            "DD republish rejected: bad drive_folder_url reason=%s site_id=%s url=%s",
            reason,
            site_id,
            drive_folder_url,
        )
        return RepublishOutcome(
            decision="skip_bad_input",
            reason=reason,
            site_id=site_id,
            fingerprint=fingerprint,
            error="could not extract folder id from drive_folder_url",
        )

    existing = finder(gc, site_folder_id)
    if existing is None:
        logger.info(
            "DD republish skip: no_prior_report reason=%s site_id=%s site=%s "
            "(daily/inbox path will create the first report)",
            reason,
            site_id or "?",
            site_name,
        )
        return RepublishOutcome(
            decision="skip_no_prior_report",
            reason=reason,
            site_id=site_id,
            fingerprint=fingerprint,
        )

    # Dedup key uses site_id when available so two sites with the same
    # name don't share state. Falls back to title for legacy callers
    # that don't plumb id (no live caller does today, but keep safe).
    state_key = _state_key(site_id or site_name, reason, fingerprint)
    last_iso = republish_state.get(state_key)
    if last_iso is None and reason == REASON_RAYCON:
        # Legacy migration compat. Pre-Rec.3 the RayCon path keyed on
        # `{site}:raycon_scenario:{run_id}` (no drive_modified_time
        # suffix). load_state preserves those keys verbatim, but live
        # callers now build a fingerprint like
        # `{run_id}:{drive_modified_time}`. The drive_modified_time
        # is ISO 8601 and contains colons of its own, so we can't
        # rpartition on `:`. Instead we strip the trailing
        # `:{drive_modified_time}` chunk by splitting on the run_id
        # boundary and check whether the legacy-shaped key exists.
        # Only applied for the RayCon reason — SIR/BI never had a
        # legacy keying scheme to migrate from.
        run_id_only_fingerprint = fingerprint.split(":", 1)[0]
        if run_id_only_fingerprint and run_id_only_fingerprint != fingerprint:
            legacy_state_key = _state_key(
                site_id or site_name, reason, run_id_only_fingerprint
            )
            last_iso = republish_state.get(legacy_state_key)
    last_dt = _parse_iso(last_iso) if last_iso else None
    if not force and last_dt is not None and (now - last_dt) < force_after:
        logger.info(
            "DD republish skip: no_diff reason=%s site_id=%s site=%s fingerprint=%s "
            "(last republished %s ago)",
            reason,
            site_id or "?",
            site_name,
            fingerprint,
            now - last_dt,
        )
        return RepublishOutcome(
            decision="skip_no_diff",
            reason=reason,
            site_id=site_id,
            fingerprint=fingerprint,
            last_republish=last_iso or "",
        )

    if dry_run:
        logger.info(
            "DD republish dry_run: would_republish reason=%s site_id=%s "
            "site=%s fingerprint=%s",
            reason,
            site_id or "?",
            site_name,
            fingerprint,
        )
        return RepublishOutcome(
            decision="skip_dry_run",
            reason=reason,
            site_id=site_id,
            fingerprint=fingerprint,
        )

    site_address = str(site_summary.get("address", "")).strip() or None
    match_terms = build_site_match_terms(site_name, site_address)
    try:
        result = runner(
            gc,
            site_name,
            drive_folder_url,
            match_terms,
            shared_cache,
            system_prompt,
            settings,
            p1_email=site_summary.get("p1_assignee_email"),
            site_address=site_address,
            p1_name=site_summary.get("p1_assignee_name"),
            site_created_at=site_summary.get("created_date") or None,
            force_regenerate=True,
        )
    except Exception as e:
        logger.exception(
            "DD republish failed: reason=%s site_id=%s site=%s fingerprint=%s",
            reason,
            site_id or "?",
            site_name,
            fingerprint,
        )
        return RepublishOutcome(
            decision="failed",
            reason=reason,
            site_id=site_id,
            fingerprint=fingerprint,
            error=str(e),
        )

    # Record the attempt regardless of result.status so a permanently
    # waiting_on_docs site doesn't re-enter the pipeline on every cron
    # tick. A future fingerprint will get a fresh state_key and try
    # again. Matches the RayCon-followup pre-refactor behavior exactly.
    republish_state[state_key] = now.isoformat()
    logger.info(
        "DD republish ran: reason=%s site_id=%s site=%s fingerprint=%s status=%s",
        reason,
        site_id or "?",
        site_name,
        fingerprint,
        result.status,
    )
    return RepublishOutcome(
        decision="republish",
        reason=reason,
        site_id=site_id,
        fingerprint=fingerprint,
        pipeline_status=result.status,
        doc_url=getattr(result, "doc_url", "") or "",
    )
