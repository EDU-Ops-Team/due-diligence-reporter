"""RayCon async hand-off client.

DDR pings RayCon's `/v1/jobs` endpoint in two flavors:

1. **Job dispatch** (``post_raycon_job``) — sent when a Block Plan
   lands in a site's M1 folder. Carries ``block_plan_file_id`` as the
   idempotency key. RayCon runs Fastest Open / Max Capacity scenarios
   and writes ``raycon_scenario.json`` back into the same M1 folder.
2. **Folder ping** (``post_raycon_folder_ping``) — sent on every other
   classified doc arrival (CDS SIR, Worksmith inspection, ISP). Lighter
   payload, no Block Plan handle. RayCon walks the Drive folder server-
   side and decides whether the document set is now complete enough to
   start computing. Idempotent on RayCon's side.

This module owns:

* ``post_raycon_job`` — the outbound POST for Block Plan triggers.
* ``post_raycon_folder_ping`` — the lightweight per-doc heads-up.
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

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
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


# Always-on callback marker per spec §2.
RAYCON_CALLBACK_MARKER = RAYCON_SCENARIO_FILENAME


def _compute_hmac_signature(secret: str, body_bytes: bytes) -> str:
    """Compute the ``sha256=<hex>`` signature for the X-RayCon-Signature header.

    Per the integration spec §1.1, RayCon validates an HMAC-SHA256 of the
    *raw request body* using ``RAYCON_WEBHOOK_SECRET`` as the key. We
    serialize the body once with stable separators and sign those exact
    bytes so the signature matches the bytes we send on the wire.
    """
    digest = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@retry(**retry_config())  # type: ignore[untyped-decorator]
def post_raycon_job(
    *,
    site_id: str,
    site_name: str,
    address: str,
    drive_folder_url: str,
    m1_folder_id: str,
    block_plan_file_id: str,
    block_plan_url: str,
    total_building_sf: int | None = None,
) -> dict[str, Any]:
    """Notify RayCon that a Block Plan is ready in a site's M1 folder.

    Request body matches the integration spec §1.2 — 11 fields including
    ``block_plan_file_id`` (idempotency key), ``m1_folder_id`` (where
    RayCon writes ``raycon_scenario.json``), and ``total_building_sf``.

    Auth is currently a no-op: RayCon's ``/v1/jobs`` endpoint is public
    under the ``/v1/*`` rollout (per RayCon team, 2026-04-30) and does
    not validate webhook signatures. We still compute an HMAC-SHA256 of
    the raw body and send it in ``X-RayCon-Signature`` *when*
    ``RAYCON_WEBHOOK_SECRET`` is configured, so the canonical signing
    path is exercised and ready the day RayCon enables verification —
    but the call works fine without a secret. ``RAYCON_API_KEY``, if
    set, is sent in ``X-RayCon-API-Key`` for the optional Firebase auth
    path (gated by ``RAYCON_REQUIRE_FIREBASE_AUTH=true`` on RayCon's
    side, currently disabled).

    The standard ``retry_config`` retries on connection errors and
    retryable HTTP status codes (429/5xx). Re-pinging with the same
    ``block_plan_file_id`` is a spec-defined no-op on RayCon's side.
    """
    settings = get_settings()
    missing_required: list[str] = []
    for arg_name, arg_value in (
        ("site_id", site_id),
        ("site_name", site_name),
        ("address", address),
        ("drive_folder_url", drive_folder_url),
        ("m1_folder_id", m1_folder_id),
        ("block_plan_file_id", block_plan_file_id),
        ("block_plan_url", block_plan_url),
    ):
        if not arg_value:
            missing_required.append(arg_name)
    if missing_required:
        raise ValueError(
            "post_raycon_job missing required fields: " + ", ".join(missing_required)
        )

    # RayCon's validator rejects 0 ("Number must be greater than 0") and
    # null ("Expected number, received null") on `total_building_sf`. When
    # the caller has no real SF value, omit the field entirely — RayCon
    # accepts the payload without it. Only send the field when we have a
    # positive integer.
    sf_int: int | None
    if total_building_sf is not None and int(total_building_sf) > 0:
        sf_int = int(total_building_sf)
    else:
        sf_int = None

    body: dict[str, Any] = {
        "schema_version": "1.0",
        "site_id": site_id,
        "site_name": site_name,
        "address": address,
        "drive_folder_url": drive_folder_url,
        "m1_folder_id": m1_folder_id,
        "block_plan_file_id": block_plan_file_id,
        "block_plan_url": block_plan_url,
        "callback_marker": RAYCON_CALLBACK_MARKER,
        "requested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if sf_int is not None:
        # Insert before callback_marker to preserve the canonical field
        # ordering documented in the spec for any future debuggers.
        body = {
            "schema_version": body["schema_version"],
            "site_id": body["site_id"],
            "site_name": body["site_name"],
            "address": body["address"],
            "drive_folder_url": body["drive_folder_url"],
            "m1_folder_id": body["m1_folder_id"],
            "block_plan_file_id": body["block_plan_file_id"],
            "block_plan_url": body["block_plan_url"],
            "total_building_sf": sf_int,
            "callback_marker": body["callback_marker"],
            "requested_at": body["requested_at"],
        }

    # Serialize once and POST those exact bytes. Using `requests`' `json=`
    # would re-serialize and break the signature when HMAC verification is
    # eventually enabled on RayCon's side.
    body_bytes = json.dumps(body, separators=(",", ":"), sort_keys=False).encode("utf-8")

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.raycon_webhook_secret:
        # Sign and send the signature header now so the canonical path is
        # ready the day RayCon flips on verification. Until then RayCon
        # ignores the header.
        headers["X-RayCon-Signature"] = _compute_hmac_signature(
            settings.raycon_webhook_secret, body_bytes
        )
    if settings.raycon_api_key:
        # Optional, gated by RAYCON_REQUIRE_FIREBASE_AUTH=true on RayCon.
        headers["X-RayCon-API-Key"] = settings.raycon_api_key

    logger.info(
        "Dispatching RayCon job: site=%s block_plan_file_id=%s",
        site_name,
        block_plan_file_id,
    )
    response = requests.post(
        settings.raycon_jobs_url,
        data=body_bytes,
        headers=headers,
        timeout=60,
    )
    if response.status_code >= 400:
        # Capture RayCon's response body so the validation reason is visible
        # in cron logs and in the raised exception. Without this the body
        # was discarded and 400s looked indistinguishable in our telemetry.
        body_text = (response.text or "").strip()
        logger.error(
            "RayCon job dispatch failed: site=%s block_plan_file_id=%s status=%s body=%s",
            site_name,
            block_plan_file_id,
            response.status_code,
            body_text[:2000],
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            # Re-raise with the response body inlined in the message so the
            # validation reason survives into retry logs and bubbles up to
            # the cron's error counter.
            raise requests.HTTPError(
                f"{exc} | RayCon response body: {body_text[:2000]}",
                response=response,
                request=getattr(exc, "request", None),
            ) from exc
    try:
        return response.json()
    except ValueError:
        # RayCon is allowed to return an empty body on success; preserve
        # observability without breaking the caller.
        return {"status": "accepted"}


@retry(**retry_config())  # type: ignore[untyped-decorator]
def post_raycon_folder_ping(
    *,
    site_id: str,
    site_name: str,
    address: str,
    drive_folder_url: str,
    m1_folder_id: str,
    doc_type: str = "",
    file_id: str = "",
    file_url: str = "",
) -> dict[str, Any]:
    """Lightweight ping that a new doc landed in a site's Drive folder.

    Sent on every classified upload (CDS SIR, Worksmith inspection, ISP
    — and also Block Plan, alongside the full ``post_raycon_job`` call).
    RayCon walks the folder server-side using ``drive_folder_url`` and
    decides whether the document set is now complete enough to start
    computing. The body is intentionally minimal: only ``site_id``,
    ``drive_folder_url``, and ``m1_folder_id`` are strictly required
    on the wire; ``doc_type`` / ``file_id`` / ``file_url`` are
    informational hints and may be empty when called from the cron
    safety net.

    Same endpoint as ``post_raycon_job`` (``/v1/jobs``). RayCon
    distinguishes a folder ping from a job dispatch by the absence of
    ``block_plan_file_id`` in the body. Idempotent on RayCon's side
    — re-firing for the same ``file_id`` is a no-op.

    Auth, signing, and retry behavior match ``post_raycon_job``.
    """
    settings = get_settings()
    missing_required: list[str] = []
    for arg_name, arg_value in (
        ("site_id", site_id),
        ("site_name", site_name),
        ("address", address),
        ("drive_folder_url", drive_folder_url),
        ("m1_folder_id", m1_folder_id),
    ):
        if not arg_value:
            missing_required.append(arg_name)
    if missing_required:
        raise ValueError(
            "post_raycon_folder_ping missing required fields: "
            + ", ".join(missing_required)
        )

    body: dict[str, Any] = {
        "schema_version": "1.0",
        "site_id": site_id,
        "site_name": site_name,
        "address": address,
        "drive_folder_url": drive_folder_url,
        "m1_folder_id": m1_folder_id,
        "event": "folder_updated",
        "callback_marker": RAYCON_CALLBACK_MARKER,
        "requested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if doc_type:
        body["doc_type"] = doc_type
    if file_id:
        body["file_id"] = file_id
    if file_url:
        body["file_url"] = file_url

    body_bytes = json.dumps(body, separators=(",", ":"), sort_keys=False).encode("utf-8")

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.raycon_webhook_secret:
        headers["X-RayCon-Signature"] = _compute_hmac_signature(
            settings.raycon_webhook_secret, body_bytes
        )
    if settings.raycon_api_key:
        headers["X-RayCon-API-Key"] = settings.raycon_api_key

    logger.info(
        "RayCon folder ping: site=%s doc_type=%s file_id=%s",
        site_name,
        doc_type or "(unspecified)",
        file_id or "(none)",
    )
    response = requests.post(
        settings.raycon_jobs_url,
        data=body_bytes,
        headers=headers,
        timeout=60,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
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


# Status values from RayCon's top-level envelope. RayCon emits ``"failed"``
# when validation didn't pass (no scenarios computed); ``"completed"`` /
# ``"success"`` for a happy run. Anything else is treated as completed for
# back-compat with payloads that predate the envelope.
RAYCON_FAILED_STATUSES: frozenset[str] = frozenset({"failed", "error"})


def raycon_payload_status(payload: dict[str, Any]) -> str:
    """Return the lower-cased top-level ``status`` if present, else ``""``.

    A blank string means the payload has no envelope status — treat it as
    a successful (legacy-flat) result for back-compat.
    """
    return str(payload.get("status", "") or "").strip().lower()


def raycon_payload_failed(payload: dict[str, Any]) -> bool:
    """Whether the payload represents a failed RayCon run.

    A run is failed if either:
      * top-level ``status`` is in :data:`RAYCON_FAILED_STATUSES`, or
      * ``validation.passed`` is explicitly ``False``.

    A missing/blank status with no validation block is *not* failed —
    that's the legacy flat-payload shape.
    """
    if raycon_payload_status(payload) in RAYCON_FAILED_STATUSES:
        return True
    validation = payload.get("validation") or {}
    if isinstance(validation, dict) and validation.get("passed") is False:
        return True
    return False


def _payload_failure_reason(payload: dict[str, Any]) -> str:
    """Build a human-readable failure reason from validation + summary.

    Prefer ``validation.errors`` (machine-actionable list); fall back to
    ``analysis.summary`` (RayCon's plain-English explanation). Returns ``""``
    when there's nothing to report.
    """
    parts: list[str] = []
    validation = payload.get("validation") or {}
    if isinstance(validation, dict):
        errors = validation.get("errors") or []
        if isinstance(errors, list):
            parts.extend(str(e).strip() for e in errors if str(e).strip())
    if not parts:
        analysis = payload.get("analysis") or {}
        if isinstance(analysis, dict):
            summary = str(analysis.get("summary", "") or "").strip()
            if summary:
                parts.append(summary)
    return "; ".join(parts)


def _payload_summary(payload: dict[str, Any]) -> str:
    """Plain-English RayCon summary if present (top-level or under ``analysis``)."""
    summary = str(payload.get("summary", "") or "").strip()
    if summary:
        return summary
    analysis = payload.get("analysis") or {}
    if isinstance(analysis, dict):
        return str(analysis.get("summary", "") or "").strip()
    return ""


def _payload_block_plan_used(payload: dict[str, Any]) -> str:
    """Drive file id of the Block Plan RayCon actually consumed, if known.

    Per the v1.1 envelope, RayCon reports this under
    ``provenance.selected_block_plan.id``. Older flat payloads echoed it as
    ``block_plan_file_id`` at the top level — we accept either.
    """
    bp = str(payload.get("block_plan_file_id", "") or "").strip()
    if bp:
        return bp
    provenance = payload.get("provenance") or {}
    if isinstance(provenance, dict):
        sel = provenance.get("selected_block_plan") or {}
        if isinstance(sel, dict):
            return str(sel.get("id", "") or "").strip()
    return ""


def _extract_scenarios(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(fastest_open, max_capacity)`` dicts from either envelope.

    RayCon's v1.1 envelope nests scenarios under ``analysis.``; the
    original v1.0 spec had them at the top level. We accept both for
    forward/back compat. Each return value is always a dict — empty when
    the source key is missing or ``null`` — so callers can treat the
    "no scenario" case uniformly.
    """
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}

    def _pick(key: str) -> dict[str, Any]:
        # Prefer envelope (analysis.*); fall back to top level for legacy
        # flat payloads. Either may be explicitly ``null``, in which case
        # we return an empty dict so downstream rendering is consistent.
        if isinstance(analysis, dict) and key in analysis:
            value = analysis.get(key)
            return value if isinstance(value, dict) else {}
        value = payload.get(key)
        return value if isinstance(value, dict) else {}

    return _pick("fastest_open"), _pick("max_capacity")


def raycon_scenario_to_report_fields(payload: dict[str, Any]) -> dict[str, str]:
    """Translate a parsed ``raycon_scenario.json`` into report-field keys.

    Accepts both envelope shapes:

    * **v1.1 envelope** (current production): scenarios live under
      ``analysis.fastest_open`` / ``analysis.max_capacity``; top-level
      ``status`` and ``validation`` describe whether the run succeeded.
    * **v1.0 flat** (original spec, kept for back-compat): scenarios at
      the top level, no ``status`` envelope.

    On a failed run (``status`` in :data:`RAYCON_FAILED_STATUSES` or
    ``validation.passed == False``) all ``exec.cost_*``, ``*_capex``, and
    ``*_open_date`` fields are emitted blank so we don't publish a Doc that
    looks like a successful zero-dollar scenario. ``exec.raycon_status`` and
    ``exec.raycon_failure_reason`` carry the explanation.

    Always-emitted traceability fields (regardless of status):
      * ``exec.raycon_status``
      * ``exec.raycon_failure_reason``
      * ``exec.raycon_run_id``
      * ``exec.raycon_summary``
      * ``exec.raycon_block_plan_used``

    The output contract for ``exec.fastest_open_*`` / ``exec.cost_<bucket>_*`` /
    ``exec.max_capacity_*`` matches the contract previously satisfied by the
    synchronous /v1/chat integration so the rest of the report pipeline
    (Google Doc builder, dashboard publisher) is unaffected.
    """
    fields: dict[str, str] = {}

    # Envelope-level traceability — always populated, even on failure.
    fields["exec.raycon_status"] = raycon_payload_status(payload)
    fields["exec.raycon_failure_reason"] = _payload_failure_reason(payload)
    fields["exec.raycon_run_id"] = str(payload.get("raycon_run_id", "") or "").strip()
    fields["exec.raycon_summary"] = _payload_summary(payload)
    fields["exec.raycon_block_plan_used"] = _payload_block_plan_used(payload)

    failed = raycon_payload_failed(payload)
    fastest, max_cap = _extract_scenarios(payload)

    # On a failed run, force every scenario field blank — emitting $0 here
    # would be indistinguishable from a successful zero-cost scenario and
    # downstream readers (Doc builder, dashboard) would mis-render it.
    if failed:
        fields["exec.fastest_open_capex"] = ""
        fields["exec.fastest_open_open_date"] = ""
        fields.update(
            {f"exec.cost_{k}_fastest_open": "" for k, _ in RAYCON_BREAKDOWN_ROWS}
        )
        fields["exec.max_capacity_capex"] = ""
        fields["exec.max_capacity_open_date"] = ""
        fields.update(
            {f"exec.cost_{k}_max_capacity": "" for k, _ in RAYCON_BREAKDOWN_ROWS}
        )
        return fields

    fields["exec.fastest_open_capex"] = _format_currency(fastest.get("grand_total"))
    fields["exec.fastest_open_open_date"] = _weeks_to_open_date(
        fastest.get("timeline_weeks")
    )
    fields.update(_scenario_breakdown(fastest, "fastest_open"))

    fields["exec.max_capacity_capex"] = _format_currency(max_cap.get("grand_total"))
    fields["exec.max_capacity_open_date"] = _weeks_to_open_date(
        max_cap.get("timeline_weeks")
    )
    fields.update(_scenario_breakdown(max_cap, "max_capacity"))

    return fields
