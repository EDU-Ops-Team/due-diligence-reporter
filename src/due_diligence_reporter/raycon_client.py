"""RayCon async hand-off client.

DDR pings RayCon's `/v1/jobs` endpoint when a Block Plan lands in a
site's M1 folder. RayCon then reads the Block Plan from Drive on its
own, derives the room schedule, runs the Fastest Open / Max Capacity
scenarios, and writes a single ``raycon_scenario.json`` file back into
the same M1 folder.

This module owns:

* ``post_raycon_job`` — the outbound POST (auth + retry).
* ``read_raycon_scenario_from_m1`` — picks up the JSON RayCon left
  for us in the per-site M1 folder.
* ``raycon_scenario_to_report_fields`` — maps the parsed JSON into the
  ``exec.fastest_open_*`` / ``exec.cost_<bucket>_*`` /
  ``exec.max_capacity_*`` keys the Google Doc template expects.

The result-file contract is documented in
``raycon_ddr_integration_spec.md``. Bumps to ``schema_version`` must
be coordinated with RayCon and validated here before mapping.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

import requests
from tenacity import retry

from .config import get_settings
from .google_client import GoogleClient
from .m1_lookup import _resolve_m1_folder
from .retry import retry_config

logger = logging.getLogger("[raycon_client]")

# The single filename RayCon writes back into M1. Filename is the
# idempotency key — RayCon overwrites on re-run rather than appending
# a timestamp so we don't accumulate duplicates.
RAYCON_SCENARIO_FILENAME = "raycon_scenario.json"

# Schema version DDR understands. Mismatches are logged loudly and the
# payload is rejected so we don't silently mis-map fields.
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})

# Hard-cost categories RayCon uses in `categories[]`. Values are the
# canonical strings RayCon emits; keys are the report-field row keys
# DDR has used since the original /v1/chat integration. Soft Costs,
# GC Fee, Contingency, and Grand Total come from the top-level
# scenario fields, NOT from `categories[]` (avoid double-counting).
RAYCON_CATEGORY_TO_BUCKET: dict[str, str] = {
    "Demolition": "demolition",
    "Framing / Doors": "framing_doors",
    "MEP / Fire / Life Safety": "mep_fire_life_safety",
    "Plumbing / Bathrooms": "plumbing_bathrooms",
    "Finish Work": "finish_work",
    "Furniture": "furniture",
    "Tech / Security / Signage": "tech_security_signage",
    "Other Hard Costs": "other_hard_costs",
}

# All breakdown rows the Google Doc template renders, in display order.
# Pair maps row key -> display label (kept here so server.py doesn't
# need to expose `_RAYCON_BREAKDOWN_ROWS` after the cutover).
RAYCON_BREAKDOWN_ROWS: tuple[tuple[str, str], ...] = (
    ("demolition", "Demolition"),
    ("framing_doors", "Framing / Doors"),
    ("mep_fire_life_safety", "MEP / Fire / Life Safety"),
    ("plumbing_bathrooms", "Plumbing / Bathrooms"),
    ("finish_work", "Finish Work"),
    ("furniture", "Furniture"),
    ("tech_security_signage", "Tech / Security / Signage"),
    ("other_hard_costs", "Other Hard Costs"),
    ("soft_costs", "Soft Costs"),
    ("gc_fee", "GC Fee"),
    ("contingency", "Contingency"),
    ("grand_total", "Grand Total"),
)


class RayConSchemaError(ValueError):
    """Raised when RayCon's response payload doesn't match the agreed schema."""


# ---------------------------------------------------------------------------
# Outbound: DDR -> RayCon
# ---------------------------------------------------------------------------


@retry(**retry_config())  # type: ignore[untyped-decorator]
def post_raycon_job(
    *,
    site_id: str,
    site_name: str,
    address: str,
    site_folder_id: str,
    request_id: str | None = None,
    m1_folder_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Notify RayCon that a Block Plan is ready in a site's M1 folder.

    The request body matches the ``/v1/jobs`` contract RayCon published
    on 2026-04-30. Required fields: ``schema_version``, ``site_id``,
    ``site_name``, ``address``, ``site_folder_id``, ``requested_at``.
    Optional: ``request_id`` (recommended for traceability),
    ``m1_folder_id`` (when caller already resolved it), ``reason``
    (defaults to ``raycon_analysis_requested`` server-side).

    Auth is a static API key in the ``X-RayCon-API-Key`` header. The
    standard ``retry_config`` retries on connection errors and
    retryable HTTP status codes (429/5xx).
    """
    settings = get_settings()
    if not settings.raycon_api_key:
        raise RuntimeError(
            "RAYCON_API_KEY is not configured; cannot dispatch Block Plan "
            "job to RayCon."
        )
    if not site_id or not site_name or not address or not site_folder_id:
        raise ValueError(
            "post_raycon_job requires site_id, site_name, address, and "
            "site_folder_id."
        )

    body: dict[str, Any] = {
        "schema_version": "1.0",
        "site_id": site_id,
        "site_name": site_name,
        "address": address,
        "site_folder_id": site_folder_id,
        "requested_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if request_id:
        body["request_id"] = request_id
    if m1_folder_id:
        body["m1_folder_id"] = m1_folder_id
    if reason:
        body["reason"] = reason

    logger.info(
        "Dispatching RayCon job: site=%s request_id=%s",
        site_name,
        request_id or "(none)",
    )
    response = requests.post(
        settings.raycon_jobs_url,
        json=body,
        headers={
            "Content-Type": "application/json",
            "X-RayCon-API-Key": settings.raycon_api_key,
        },
        timeout=60,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        # RayCon is allowed to return an empty body on success; preserve
        # observability without breaking the caller.
        return {"status": "accepted"}


# ---------------------------------------------------------------------------
# Inbound: read RayCon's result file from M1
# ---------------------------------------------------------------------------


def read_raycon_scenario_from_m1(
    gc: GoogleClient,
    drive_folder_url: str,
) -> dict[str, Any] | None:
    """Return the parsed ``raycon_scenario.json`` from the site's M1 folder.

    Returns ``None`` when the file isn't there yet (the common case while
    RayCon is still computing). Raises ``RayConSchemaError`` when the
    file exists but its ``schema_version`` is unsupported, so callers
    don't silently map the wrong shape.
    """
    if not drive_folder_url:
        return None
    m1_folder_id, _ = _resolve_m1_folder(gc, drive_folder_url)
    if not m1_folder_id:
        return None

    candidate = None
    for file_info in gc.list_files_in_folder(m1_folder_id):
        name = str(file_info.get("name", "")).strip()
        if name == RAYCON_SCENARIO_FILENAME:
            # Pick the most recently modified copy if there's somehow more
            # than one (defensive — RayCon is supposed to overwrite).
            if candidate is None or str(file_info.get("modifiedTime", "")) > str(
                candidate.get("modifiedTime", "")
            ):
                candidate = file_info
    if candidate is None:
        return None

    file_id = candidate.get("id")
    if not file_id:
        return None
    raw = gc.download_file_bytes(file_id)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RayConSchemaError(
            f"raycon_scenario.json in folder {m1_folder_id} is not valid JSON: {e}"
        ) from e
    if not isinstance(payload, dict):
        raise RayConSchemaError(
            f"raycon_scenario.json must be a JSON object, got {type(payload).__name__}"
        )
    schema = str(payload.get("schema_version", "")).strip()
    if schema not in SUPPORTED_SCHEMA_VERSIONS:
        raise RayConSchemaError(
            f"Unsupported RayCon schema_version '{schema}' in folder "
            f"{m1_folder_id}; supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    payload["_drive_file_id"] = file_id
    payload["_drive_modified_time"] = str(candidate.get("modifiedTime", ""))
    return payload


# ---------------------------------------------------------------------------
# Result mapping: RayCon JSON -> report fields
# ---------------------------------------------------------------------------


def _format_currency(value: float | int | None) -> str:
    """Format a numeric amount as ``$1,234`` (whole-dollar)."""
    amount = float(value or 0)
    return f"${round(amount):,}"


def _weeks_to_open_date(weeks: Any, now: datetime | None = None) -> str:
    """Convert RayCon's ``timeline_weeks`` into an MM/DD/YY target open date."""
    try:
        weeks_int = int(weeks)
    except (TypeError, ValueError):
        return ""
    if weeks_int <= 0:
        return ""
    base = now or datetime.now()
    return (base + timedelta(weeks=weeks_int)).strftime("%m/%d/%y")


def _scenario_breakdown(
    scenario: dict[str, Any],
    suffix: str,
) -> dict[str, str]:
    """Build the 12 ``exec.cost_<bucket>_<suffix>`` rows for a scenario."""
    bucket_totals: dict[str, float] = {row_key: 0.0 for row_key, _ in RAYCON_BREAKDOWN_ROWS}
    for cat in scenario.get("categories", []) or []:
        if not isinstance(cat, dict):
            continue
        bucket = RAYCON_CATEGORY_TO_BUCKET.get(str(cat.get("category", "")).strip())
        if bucket is None:
            # RayCon agreed on a closed vocabulary — anything else is a bug
            # on their side, but we don't want to crash report generation.
            logger.warning(
                "Unknown RayCon category '%s' in scenario %s; rolling into other_hard_costs",
                cat.get("category"),
                suffix,
            )
            bucket = "other_hard_costs"
        try:
            bucket_totals[bucket] += float(cat.get("subtotal", 0) or 0)
        except (TypeError, ValueError):
            continue

    bucket_totals["soft_costs"] = float(scenario.get("soft_costs", 0) or 0)
    bucket_totals["gc_fee"] = float(scenario.get("gc_fee", 0) or 0)
    bucket_totals["contingency"] = float(scenario.get("contingency", 0) or 0)
    bucket_totals["grand_total"] = float(scenario.get("grand_total", 0) or 0)
    if not bucket_totals["furniture"]:
        bucket_totals["furniture"] = float(scenario.get("furniture", 0) or 0)

    return {
        f"exec.cost_{row_key}_{suffix}": _format_currency(bucket_totals[row_key])
        for row_key, _ in RAYCON_BREAKDOWN_ROWS
    }


def raycon_scenario_to_report_fields(payload: dict[str, Any]) -> dict[str, str]:
    """Translate a parsed ``raycon_scenario.json`` into report-field keys.

    The output dict matches the contract previously satisfied by the
    synchronous /v1/chat integration so the rest of the report pipeline
    (Google Doc builder, dashboard publisher) is unaffected by this
    cutover.
    """
    fields: dict[str, str] = {}

    fastest = payload.get("fastest_open") or {}
    if isinstance(fastest, dict):
        fields["exec.fastest_open_capex"] = _format_currency(fastest.get("grand_total"))
        fields["exec.fastest_open_open_date"] = _weeks_to_open_date(
            fastest.get("timeline_weeks")
        )
        fields.update(_scenario_breakdown(fastest, "fastest_open"))
    else:
        fields["exec.fastest_open_capex"] = ""
        fields["exec.fastest_open_open_date"] = ""
        fields.update(
            {f"exec.cost_{k}_fastest_open": "" for k, _ in RAYCON_BREAKDOWN_ROWS}
        )

    max_cap = payload.get("max_capacity") or {}
    if isinstance(max_cap, dict):
        fields["exec.max_capacity_capex"] = _format_currency(max_cap.get("grand_total"))
        fields["exec.max_capacity_open_date"] = _weeks_to_open_date(
            max_cap.get("timeline_weeks")
        )
        fields.update(_scenario_breakdown(max_cap, "max_capacity"))
    else:
        fields["exec.max_capacity_capex"] = ""
        fields["exec.max_capacity_open_date"] = ""
        fields.update(
            {f"exec.cost_{k}_max_capacity": "" for k, _ in RAYCON_BREAKDOWN_ROWS}
        )

    return fields
