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
import re
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import requests
from tenacity import retry

from .config import get_settings
from .google_client import GoogleClient
from .m1_lookup import _resolve_m1_folder
from .retry import retry_config
from .utils import extract_folder_id_from_url

logger = logging.getLogger("[raycon_client]")

# The single filename RayCon writes back into M1. Filename is the
# idempotency key — RayCon overwrites on re-run rather than appending
# a timestamp so we don't accumulate duplicates.
RAYCON_SCENARIO_FILENAME = "raycon_scenario.json"
RAYCON_JOB_ACCEPTED_STATUS_CODE = 202
RAYCON_IN_PROGRESS_STATUSES: frozenset[str] = frozenset({"queued", "running"})
RAYCON_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "validation_failed", "failed"}
)
RAYCON_ACCEPTED_RESPONSE_FIELDS: frozenset[str] = frozenset(
    {
        "status",
        "job_id",
        "raycon_run_id",
        "idempotency_key",
        "retry_after_seconds",
        "status_url",
        "cached",
    }
)

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

ALPHA_CAPACITY_ANALYSIS_NAME_PATTERNS: tuple[str, ...] = (
    "alpha capacity analysis",
    "alpha_capacity_analysis",
    "capacity analysis",
    "capacity_analysis",
    "capacity brainlift",
    "capacity_brainlift",
)
GOOGLE_DOC_MIME_TYPE = "application/vnd.google-apps.document"


class RayConSchemaError(ValueError):
    """Raised when RayCon's response payload doesn't match the agreed schema."""


def _is_alpha_capacity_analysis_candidate(file_info: dict[str, Any]) -> bool:
    name = str(file_info.get("name", "")).strip().lower()
    if not name:
        return False
    normalized = re.sub(r"[\s_\-]+", " ", name)
    return any(
        pattern.replace("_", " ") in normalized
        for pattern in ALPHA_CAPACITY_ANALYSIS_NAME_PATTERNS
    )


def _sort_drive_candidates(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        files,
        key=lambda item: str(item.get("modifiedTime") or ""),
        reverse=True,
    )


def _parse_json_object_from_text(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    for match in re.finditer(
        r"```(?:json)?\s*(.*?)```",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        try:
            parsed = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", stripped):
        try:
            parsed, _end = decoder.raw_decode(stripped[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _line_capacity_value(line: str) -> int | None:
    numbers = [
        int(match.group(1).replace(",", ""))
        for match in re.finditer(r"\b(\d{1,4}(?:,\d{3})?)\b", line)
    ]
    if not numbers:
        return None
    # For markdown capacity tables, the last numeric cell is usually Total.
    value = numbers[-1]
    if value <= 0 or value > 5000:
        return None
    return value


def _line_has_strict_capacity_label(line: str) -> bool:
    lowered = line.lower()
    return bool(
        re.search(r"\bstrict\b", lowered)
        or re.search(r"\bfastest\s+(?:open|path)\b", lowered)
    )


def _line_has_max_capacity_label(line: str) -> bool:
    lowered = line.lower()
    if re.search(r"\bmax(?:imum)?\s+capacity\b", lowered):
        return True
    return bool(re.match(r"^\s*\|?\s*max\s*\|", lowered))


def _parse_capacity_analysis_from_text(text: str) -> dict[str, Any] | None:
    if not text.strip():
        return None
    lowered = text.lower()
    if not (
        "alpha capacity analysis" in lowered
        or "capacity brainlift" in lowered
        or "capacity scenarios" in lowered
    ):
        return None

    strict_value: int | None = None
    max_value: int | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if strict_value is None and _line_has_strict_capacity_label(line):
            strict_value = _line_capacity_value(line)
        if max_value is None and _line_has_max_capacity_label(line):
            max_value = _line_capacity_value(line)
        if strict_value is not None and max_value is not None:
            break

    payload: dict[str, Any] = {"source_label": "Alpha Capacity Analysis"}
    if strict_value is not None:
        payload["strict"] = {"capacity_students": strict_value}
    if max_value is not None:
        payload["max"] = {"capacity_students": max_value}
    return payload if "strict" in payload or "max" in payload else None


def _capacity_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    try:
        parsed = int(float(text))
    except (TypeError, ValueError):
        match = re.search(r"\d+", text)
        if match is None:
            return None
        parsed = int(match.group(0))
    if parsed <= 0:
        return None
    return parsed


def _capacity_value_from_scenario(value: Any) -> int | None:
    if isinstance(value, (int, float, str)):
        return _capacity_int(value)
    if not isinstance(value, dict):
        return None
    for key in (
        "capacity_students",
        "capacityStudents",
        "student_count",
        "studentCount",
        "students",
        "total_students",
        "totalStudents",
        "total",
        "capacity",
    ):
        parsed = _capacity_int(value.get(key))
        if parsed is not None:
            return parsed
    return None


def _first_capacity_scenario(
    payload: dict[str, Any],
    *keys: str,
) -> int | None:
    for key in keys:
        parsed = _capacity_value_from_scenario(payload.get(key))
        if parsed is not None:
            return parsed
    for container_key in (
        "scenarios",
        "capacity_scenarios",
        "capacityScenarios",
        "analysis",
        "result",
    ):
        container = payload.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in keys:
            parsed = _capacity_value_from_scenario(container.get(key))
            if parsed is not None:
                return parsed
    return None


def _has_required_alpha_capacity_counts(payload: dict[str, Any]) -> bool:
    strict = _first_capacity_scenario(
        payload,
        "strict",
        "fastest_open",
        "fastestOpen",
        "fast_path",
        "fastPath",
        "as_is",
        "asIs",
    )
    max_capacity = _first_capacity_scenario(
        payload,
        "max",
        "max_capacity",
        "maxCapacity",
        "maximum",
        "maximum_capacity",
        "maximumCapacity",
    )
    return strict is not None and max_capacity is not None


def alpha_capacity_counts_signature(payload: dict[str, Any] | None) -> str:
    """Return ``strict-max`` signature for complete Alpha Capacity payloads."""

    if not isinstance(payload, dict):
        return ""
    strict = _first_capacity_scenario(
        payload,
        "strict",
        "fastest_open",
        "fastestOpen",
        "fast_path",
        "fastPath",
        "as_is",
        "asIs",
    )
    max_capacity = _first_capacity_scenario(
        payload,
        "max",
        "max_capacity",
        "maxCapacity",
        "maximum",
        "maximum_capacity",
        "maximumCapacity",
    )
    if strict is None or max_capacity is None:
        return ""
    return f"{strict}-{max_capacity}"


def _read_alpha_capacity_analysis_file(
    gc: GoogleClient,
    file_info: dict[str, Any],
) -> dict[str, Any] | None:
    file_id = str(file_info.get("id", "")).strip()
    if not file_id:
        return None

    name = str(file_info.get("name", "")).strip()
    mime_type = str(file_info.get("mimeType", "")).strip()
    if mime_type == GOOGLE_DOC_MIME_TYPE:
        text = gc.export_google_doc_as_text(file_id)
    else:
        raw = gc.download_file_bytes(file_id)
        text = raw.decode("utf-8", errors="replace")

    payload = _parse_json_object_from_text(text)
    if payload is None:
        payload = _parse_capacity_analysis_from_text(text)
    if payload is None:
        logger.warning(
            "Skipping Alpha Capacity Analysis candidate %s (%s): no parseable "
            "capacity payload found",
            name or "(unnamed)",
            file_id,
        )
        return None

    payload.setdefault("source_label", "Alpha Capacity Analysis")
    return payload


def read_alpha_capacity_analysis_from_m1(
    gc: GoogleClient,
    m1_folder_id: str,
    *,
    m1_files: list[dict[str, Any]] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Return a complete Alpha Capacity Analysis payload found in M1.

    DDR does not calculate capacity here. It only passes through the hosted
    capacity skill's machine-readable artifact, or a conservative parse of a
    clearly labeled saved skill report, so RayCon can use those numbers as the
    authoritative student-count source while pricing construction scope. A
    candidate must contain both Strict/Fast Path and Max Capacity counts; partial
    artifacts are skipped so callers can generate a fresh hosted-skill artifact.
    """
    if not m1_folder_id:
        return None, None

    files = m1_files if m1_files is not None else gc.list_files_in_folder(
        m1_folder_id
    )
    candidates = _sort_drive_candidates(
        [
            file_info
            for file_info in files
            if _is_alpha_capacity_analysis_candidate(file_info)
        ]
    )
    for file_info in candidates:
        file_id = str(file_info.get("id", "")).strip()
        if not file_id:
            continue
        try:
            payload = _read_alpha_capacity_analysis_file(gc, file_info)
        except Exception as exc:
            logger.warning(
                "Skipping Alpha Capacity Analysis candidate %s (%s): %s",
                file_info.get("name") or "(unnamed)",
                file_id,
                exc,
            )
            continue
        if payload is not None and _has_required_alpha_capacity_counts(payload):
            return file_id, payload
        if payload is not None:
            logger.warning(
                "Skipping Alpha Capacity Analysis candidate %s (%s): missing "
                "Strict/Fast Path or Max Capacity count",
                file_info.get("name") or "(unnamed)",
                file_id,
            )
    return None, None


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


def _normalize_drive_folder_url(value: str) -> str | None:
    """Return a clean canonical Drive folder URL or None if unparseable.

    RayCon's ``/v1/jobs`` validator on ``drive_folder_url`` is strict: it
    rejects HTML anchor wrapping, leading/trailing text, and file URLs
    (``/file/d/<id>``) with ``"drive_folder_url must be a Google Drive
    folder URL or folder ID containing a 10-200 character folder ID"``.
    Source Google Folder fields can sometimes store an
    HTML anchor (e.g. ``<a href="...folders/<id>">Site</a>``) or has
    extra text appended by PMs. We normalize defensively here so a
    sloppy source entry doesn't burn a RayCon dispatch slot.

    Reuses :func:`extract_folder_id_from_url` (which unwraps anchors and
    matches both ``/folders/<id>`` and ``?id=<id>``) to pull the ID, then
    rebuilds the canonical URL shape RayCon's validator accepts.
    """
    folder_id = extract_folder_id_from_url(value)
    if not folder_id:
        return None
    return f"https://drive.google.com/drive/folders/{folder_id}"


def _unwrap_html_anchor(value: str) -> str:
    """Return the href content of an HTML anchor, or the value unchanged.

    Rich-text fields can wrap raw URLs in ``<a href="...">label</a>``
    when a PM pastes via the rich-text editor. RayCon's validators are
    strict about extraneous markup, so this small unwrapper keeps us
    forward-safe across fields where the validator currently happens to
    be lenient.
    """
    import re as _re

    match = _re.search(r'href="([^"]+)"', value)
    if match:
        return match.group(1).replace("&amp;", "&")
    return value


def _raise_for_unexpected_raycon_status(
    *,
    response: requests.Response,
    site_name: str,
    block_plan_file_id: str,
) -> None:
    """Raise when RayCon job dispatch does not return the async 202 contract."""
    if response.status_code == RAYCON_JOB_ACCEPTED_STATUS_CODE:
        return
    body_text = (response.text or "").strip()
    logger.error(
        "RayCon job dispatch returned unexpected status: site=%s "
        "block_plan_file_id=%s status=%s body=%s",
        site_name,
        block_plan_file_id,
        response.status_code,
        body_text[:2000],
    )
    raise requests.HTTPError(
        "RayCon job dispatch expected 202 Accepted, got "
        f"{response.status_code} | RayCon response body: {body_text[:2000]}",
        response=response,
    )


def _parse_raycon_accepted_response(response: requests.Response) -> dict[str, Any]:
    """Parse and validate RayCon's 202 Accepted job metadata body."""
    try:
        data = response.json()
    except ValueError as exc:
        raise RayConSchemaError("RayCon 202 response must include JSON metadata") from exc
    if not isinstance(data, dict):
        raise RayConSchemaError("RayCon 202 response must be a JSON object")
    missing = sorted(RAYCON_ACCEPTED_RESPONSE_FIELDS.difference(data))
    if missing:
        raise RayConSchemaError(
            "RayCon 202 response missing required field(s): " + ", ".join(missing)
        )
    return data


@retry(**retry_config())  # type: ignore[untyped-decorator]
def get_raycon_job_status(status_url: str) -> dict[str, Any]:
    """Fetch a signed RayCon job status URL without logging the sensitive URL."""
    if not status_url:
        raise ValueError("get_raycon_job_status requires status_url")
    response = requests.get(status_url, timeout=60)
    if response.status_code >= 400:
        body_text = (response.text or "").strip()
        logger.error(
            "RayCon job status poll failed: status=%s body=%s",
            response.status_code,
            body_text[:2000],
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise requests.HTTPError(
                f"{exc} | RayCon status response body: {body_text[:2000]}",
                response=response,
                request=getattr(exc, "request", None),
            ) from exc
    data = response.json()
    if not isinstance(data, dict):
        raise RayConSchemaError("RayCon status response must be a JSON object")
    return data


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
    capacity_analysis_file_id: str | None = None,
    capacity_analysis: dict[str, Any] | None = None,
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

    # Normalize source-system URLs before they hit RayCon's strict
    # validators. Live runs surfaced 400s from HTML-anchor-wrapped folder
    # URLs in the source Google Folder field (e.g. NYC 156 William, Dallas
    # 4152 Cole on 2026-05-05).
    normalized_folder = _normalize_drive_folder_url(drive_folder_url)
    if not normalized_folder:
        raise ValueError(
            "post_raycon_job: drive_folder_url is not parseable as a Google "
            f"Drive folder URL or ID (got {drive_folder_url!r}). Fix the "
            "site's Google Folder field."
        )
    drive_folder_url = normalized_folder
    block_plan_url = _unwrap_html_anchor(block_plan_url)

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
    }
    if sf_int is not None:
        body["total_building_sf"] = sf_int
    if capacity_analysis_file_id:
        body["capacity_analysis_file_id"] = capacity_analysis_file_id
    if capacity_analysis:
        body["capacity_analysis"] = capacity_analysis
    body["callback_marker"] = RAYCON_CALLBACK_MARKER
    body["requested_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

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
    _raise_for_unexpected_raycon_status(
        response=response,
        site_name=site_name,
        block_plan_file_id=block_plan_file_id,
    )
    return _parse_raycon_accepted_response(response)


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

    # Same defensive normalization as post_raycon_job: a source folder field
    # wrapped in an HTML anchor (or with extra text) makes RayCon's strict
    # validator return 400 here too.
    normalized_folder = _normalize_drive_folder_url(drive_folder_url)
    if not normalized_folder:
        raise ValueError(
            "post_raycon_folder_ping: drive_folder_url is not parseable as a "
            f"Google Drive folder URL or ID (got {drive_folder_url!r}). Fix "
            "the site's Google Folder field."
        )
    drive_folder_url = normalized_folder
    if file_url:
        file_url = _unwrap_html_anchor(file_url)

    body: dict[str, Any] = {
        "schema_version": "1.0",
        "site_id": site_id,
        "site_name": site_name,
        "address": address,
        "drive_folder_url": drive_folder_url,
        "m1_folder_id": m1_folder_id,
        "event": "folder_updated",
        "callback_marker": RAYCON_CALLBACK_MARKER,
        "requested_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    if response.status_code >= 400:
        body_text = (response.text or "").strip()
        logger.error(
            "RayCon folder ping failed: site=%s doc_type=%s status=%s body=%s",
            site_name,
            doc_type or "(unspecified)",
            response.status_code,
            body_text[:2000],
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise requests.HTTPError(
                f"{exc} | RayCon response body: {body_text[:2000]}",
                response=response,
                request=getattr(exc, "request", None),
            ) from exc
    try:
        return cast(dict[str, Any], response.json())
    except ValueError:
        return {"status": "accepted"}


# ---------------------------------------------------------------------------
# Inbound: read RayCon's result file from M1
# ---------------------------------------------------------------------------


def read_raycon_scenario_from_m1(
    gc: GoogleClient,
    drive_folder_url: str,
    *,
    m1_folder_id: str | None = None,
    m1_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return the parsed ``raycon_scenario.json`` from the site's M1 folder.

    Returns ``None`` when the file isn't there yet (the common case while
    RayCon is still computing). Raises ``RayConSchemaError`` when the
    file exists but its ``schema_version`` is unsupported, so callers
    don't silently map the wrong shape.
    """
    if not drive_folder_url:
        return None
    if m1_folder_id is None:
        m1_folder_id, _ = _resolve_m1_folder(gc, drive_folder_url)
    if not m1_folder_id:
        return None

    candidate = None
    files = m1_files if m1_files is not None else gc.list_files_in_folder(m1_folder_id)
    for file_info in files:
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


def _coerce_number(value: Any) -> float | None:
    """Return a numeric value, preserving explicit zero and rejecting absence."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_currency(value: float | int) -> str:
    """Format a numeric amount as ``$1,234`` (whole-dollar)."""
    return f"${round(float(value)):,}"


def _format_optional_currency(value: Any) -> str:
    """Format a RayCon amount, returning blank when the field is absent."""
    amount = _coerce_number(value)
    if amount is None:
        return ""
    return _format_currency(amount)


def _format_optional_students(value: Any) -> str:
    """Format a RayCon student count as an integer string."""
    students = _coerce_number(value)
    if students is None:
        return ""
    return str(round(students))


def _is_alpha_capacity_source(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {"alpha_capacity_analysis", "alpha capacity analysis"}


def _mapping_has_alpha_capacity_source(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        _is_alpha_capacity_source(value.get("source_system"))
        or _is_alpha_capacity_source(value.get("source"))
        or _is_alpha_capacity_source(value.get("capacity_source"))
        or _is_alpha_capacity_source(value.get("source_label"))
    )


def _alpha_capacity_payload_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_analysis = payload.get("analysis")
    analysis: dict[str, Any] = raw_analysis if isinstance(raw_analysis, dict) else {}
    raw_site_context = analysis.get("site_context")
    site_context: dict[str, Any] = (
        raw_site_context if isinstance(raw_site_context, dict) else {}
    )
    candidates: list[dict[str, Any]] = []
    for candidate in (
        payload.get("capacity_analysis"),
        analysis.get("capacity_analysis"),
        site_context.get("capacity_analysis"),
    ):
        if isinstance(candidate, dict) and _mapping_has_alpha_capacity_source(candidate):
            candidates.append(candidate)
    return candidates


def _alpha_capacity_trace_count(
    payload: dict[str, Any],
    scenario_key: str,
) -> int | None:
    raw_analysis = payload.get("analysis")
    analysis: dict[str, Any] = raw_analysis if isinstance(raw_analysis, dict) else {}
    trace = analysis.get("capacity_trace")
    if not _mapping_has_alpha_capacity_source(trace):
        return None
    field_names = (
        (
            "fastest_open_capacity_students",
            "fastestOpenCapacityStudents",
            "strict_capacity_students",
            "strictCapacityStudents",
        )
        if scenario_key == "fastest_open"
        else (
            "max_capacity_capacity_students",
            "maxCapacityCapacityStudents",
            "maximum_capacity_students",
            "maximumCapacityStudents",
        )
    )
    if not isinstance(trace, dict):
        return None
    for field_name in field_names:
        parsed = _capacity_int(trace.get(field_name))
        if parsed is not None:
            return parsed
    return None


def _alpha_capacity_count_from_payload(
    payload: dict[str, Any],
    scenario_key: str,
) -> int | None:
    scenario_keys = (
        (
            "strict",
            "fastest_open",
            "fastestOpen",
            "fast_path",
            "fastPath",
            "as_is",
            "asIs",
        )
        if scenario_key == "fastest_open"
        else (
            "max",
            "max_capacity",
            "maxCapacity",
            "maximum",
            "maximum_capacity",
            "maximumCapacity",
        )
    )
    for candidate in _alpha_capacity_payload_candidates(payload):
        parsed = _first_capacity_scenario(candidate, *scenario_keys)
        if parsed is not None:
            return parsed
    return _alpha_capacity_trace_count(payload, scenario_key)


def _scenario_has_alpha_capacity_source(scenario: dict[str, Any]) -> bool:
    trace = (
        scenario.get("capacity_trace")
        if isinstance(scenario.get("capacity_trace"), dict)
        else {}
    )
    return (
        _mapping_has_alpha_capacity_source(scenario)
        or _mapping_has_alpha_capacity_source(trace)
    )


def _format_alpha_sourced_capacity(
    payload: dict[str, Any],
    scenario: dict[str, Any],
    scenario_key: str,
) -> str:
    alpha_count = _alpha_capacity_count_from_payload(payload, scenario_key)
    if alpha_count is not None:
        return str(alpha_count)
    if not _scenario_has_alpha_capacity_source(scenario):
        return ""
    return _format_optional_students(scenario.get("capacity_students"))


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
    bucket_totals: dict[str, float | None] = {
        row_key: None for row_key, _ in RAYCON_BREAKDOWN_ROWS
    }
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
        subtotal = _coerce_number(cat.get("subtotal"))
        if subtotal is None:
            continue
        bucket_totals[bucket] = (bucket_totals[bucket] or 0.0) + subtotal

    for key in ("soft_costs", "gc_fee", "contingency", "grand_total"):
        if key in scenario:
            bucket_totals[key] = _coerce_number(scenario.get(key))
    if bucket_totals["furniture"] is None and "furniture" in scenario:
        bucket_totals["furniture"] = _coerce_number(scenario.get("furniture"))

    return {
        f"exec.cost_{row_key}_{suffix}": _format_optional_currency(
            bucket_totals[row_key]
        )
        for row_key, _ in RAYCON_BREAKDOWN_ROWS
    }


# Status values from RayCon's top-level envelope. RayCon emits ``"failed"``
# when validation didn't pass (no scenarios computed); ``"completed"`` /
# ``"success"`` for a happy run. Anything else is treated as completed for
# back-compat with payloads that predate the envelope.
RAYCON_FAILED_STATUSES: frozenset[str] = frozenset({
    "failed",
    "validation_failed",
    "error",
})


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

    Capacity fields are stricter than cost/schedule fields: DDR only publishes
    RayCon scenario capacity when the payload or scenario carries Alpha
    Capacity Analysis provenance. RayCon-owned capacity math can remain in the
    JSON as audit/fallback evidence, but it does not satisfy DDR's
    Alpha-sourced capacity requirement.

    Always-emitted traceability fields (regardless of status):
      * ``exec.raycon_status``
      * ``exec.raycon_failure_reason``
      * ``exec.raycon_run_id``
      * ``exec.raycon_summary``
      * ``exec.raycon_block_plan_used``

    The output contract for ``exec.fastest_open_*`` / ``exec.cost_<bucket>_*`` /
    ``exec.max_capacity_*`` matches the contract previously satisfied by the
    synchronous /v1/chat integration so the rest of the report pipeline
    and Google Doc builder are unaffected.
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
    # the Google Doc builder would mis-render it.
    if failed:
        fields["exec.fastest_open_capacity"] = ""
        fields["exec.fastest_open_capex"] = ""
        fields["exec.fastest_open_open_date"] = ""
        fields.update(
            {f"exec.cost_{k}_fastest_open": "" for k, _ in RAYCON_BREAKDOWN_ROWS}
        )
        fields["exec.max_capacity_capacity"] = ""
        fields["exec.max_capacity_capex"] = ""
        fields["exec.max_capacity_open_date"] = ""
        fields.update(
            {f"exec.cost_{k}_max_capacity": "" for k, _ in RAYCON_BREAKDOWN_ROWS}
        )
        return fields

    fields["exec.fastest_open_capacity"] = _format_alpha_sourced_capacity(
        payload,
        fastest,
        "fastest_open",
    )
    fields["exec.fastest_open_capex"] = _format_optional_currency(
        fastest.get("grand_total")
    )
    fields["exec.fastest_open_open_date"] = _weeks_to_open_date(
        fastest.get("timeline_weeks")
    )
    fields.update(_scenario_breakdown(fastest, "fastest_open"))

    fields["exec.max_capacity_capacity"] = _format_alpha_sourced_capacity(
        payload,
        max_cap,
        "max_capacity",
    )
    fields["exec.max_capacity_capex"] = _format_optional_currency(
        max_cap.get("grand_total")
    )
    fields["exec.max_capacity_open_date"] = _weeks_to_open_date(
        max_cap.get("timeline_weeks")
    )
    fields.update(_scenario_breakdown(max_cap, "max_capacity"))

    return fields
