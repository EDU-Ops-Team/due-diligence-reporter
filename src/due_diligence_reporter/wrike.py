"""Wrike integration for fetching Site Records for due diligence reporting."""

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import requests
from openai import OpenAI
from tenacity import retry

from .config import get_settings
from .retry import retry_config

logger = logging.getLogger("[wrike]")

WRIKE_API_BASE_URL = "https://www.wrike.com/api/v4"
WRIKE_TIMEOUT_SECONDS = 20.0

# Wrike Space ID - Site Records space
WRIKE_SPACE_ID = "IEAGN6I6I5RFSYZI"

# Site Record Custom Item Type
WRIKE_SITE_RECORD_TYPE_ID = "IEAGN6I6PIAEZNHZ"

# Key custom field IDs for Site Record
WRIKE_CUSTOM_FIELDS: dict[str, str] = {
    # Location fields
    "market": "IEAGN6I6JUAIIP5D",
    "ahj": "IEAGN6I6JUAJA4RM",
    "address": "IEAGN6I6JUAIKSH3",
    "address_alt": "IEAGN6I6JUAJJ4EV",
    "address_county": "IEAGN6I6JUAJNUVF",
    # Property fields
    "square_footage": "IEAGN6I6JUAJJ4FC",
    "square_footage_buildings": "IEAGN6I6JUAJJ4FE",
    # Score fields
    "enrollment_score": "IEAGN6I6JUAKGXNV",
    "enrollment_score_plus": "IEAGN6I6JUAKGXNW",
    "wealth_score": "IEAGN6I6JUAKGXNX",
    "relative_wealth_score": "IEAGN6I6JUAKGXNZ",
    "relative_enrollment_score": "IEAGN6I6JUAKDM2H",
    "relative_enrollment_score_plus": "IEAGN6I6JUAKGXOL",
    # Zoning / K-12 Status
    "zoning": "IEAGN6I6JUAJA4QQ",
    "k12_status": "IEAGN6I6JUAKGXNY",
    # School
    "school_type": "IEAGN6I6JUAITZSN",
    "overall_site_stage": "IEAGN6I6JUAJU2PJ",
    # Other
    "site_poc": "IEAGN6I6JUAKEKBU",
    "p1_accountable": "IEAGN6I6JUAJK2MQ",
    "loi_signed_date": "IEAGN6I6JUAIOUVH",
    "vendor_team": "IEAGN6I6JUAKDCYE",
    "google_folder": "IEAGN6I6JUAIKGJH",
    # --- Phase 2 DD provenance fields (Rhodes data dictionary, 4/24) ---
    # IDs left blank here intentionally — they are resolved by display name
    # at runtime via _resolve_custom_field_id() below. Once we confirm the
    # canonical IDs from Wrike, paste them here and the resolver will
    # short-circuit to the cached value.
    "school_feasibility": "",  # W74 "School Feasibility" (high/medium/low/unknown)
    "timeline_confidence": "",  # W81 "Timeline Confidence" (high/medium/low/unknown)
}

# Display-name lookup used by _resolve_custom_field_id(). When a slot in
# WRIKE_CUSTOM_FIELDS has a blank ID, the resolver hits Wrike's
# /customfields endpoint, finds the field by display name, and caches the
# result for the lifetime of the process.
WRIKE_CUSTOM_FIELD_DISPLAY_NAMES: dict[str, str] = {
    "school_feasibility": "School Feasibility",
    "timeline_confidence": "Timeline Confidence",
}

# Reverse mapping: ID -> name. Skip entries with blank IDs (Phase 2 fields
# that resolve at runtime) so they don't all collide on the empty key.
WRIKE_CUSTOM_FIELD_NAMES: dict[str, str] = {
    v: k for k, v in WRIKE_CUSTOM_FIELDS.items() if v
}


@dataclass(frozen=True)
class WrikeConfig:
    """Wrike API configuration."""

    access_token: str


class WrikeError(RuntimeError):
    """Wrike API error."""

    pass


def load_wrike_config() -> WrikeConfig:
    """Load Wrike configuration from environment variables."""
    access_token = os.getenv("WRIKE_ACCESS_TOKEN", "")

    if not access_token:
        raise WrikeError(
            "Missing WRIKE_ACCESS_TOKEN env var. Add it to .env file or process env."
        )

    logger.info("Wrike config loaded: space_id=%s", WRIKE_SPACE_ID)
    return WrikeConfig(access_token=access_token)


def _wrike_headers(access_token: str) -> dict[str, str]:
    """Build Wrike API request headers."""
    return {
        "Authorization": f"bearer {access_token}",
        "User-Agent": "due-diligence-reporter-mcp/1.0",
    }


def _raise_for_wrike_error(resp: requests.Response) -> None:
    """Raise WrikeError if response is not successful."""
    if resp.ok:
        return
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:2000]}
    raise WrikeError(f"Wrike API error {resp.status_code}: {body}")


_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@retry(**retry_config())  # type: ignore[untyped-decorator]
def _wrike_get(
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str] | None = None,
    timeout: float = WRIKE_TIMEOUT_SECONDS,
) -> requests.Response:
    """Make a GET request to the Wrike API with retry on transient errors.

    Raises ``requests.HTTPError`` for retryable status codes (429, 5xx) so that
    tenacity can intercept them.  Non-retryable errors are raised as ``WrikeError``
    after retries are exhausted or immediately for 4xx (non-429) responses.
    """
    resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    if not resp.ok and resp.status_code in _RETRYABLE_STATUS_CODES:
        resp.raise_for_status()  # raises HTTPError -> tenacity retries
    _raise_for_wrike_error(resp)
    return resp


def enrich_custom_fields_with_names(record: dict[str, Any]) -> dict[str, Any]:
    """Enrich custom fields in a Wrike record with human-readable names."""
    custom_fields = record.get("customFields", [])
    if not isinstance(custom_fields, list):
        return record

    enriched_fields: list[dict[str, Any]] = []
    for field in custom_fields:
        if not isinstance(field, dict):
            enriched_fields.append(field)
            continue

        field_id: str = field.get("id", "")
        field_name = WRIKE_CUSTOM_FIELD_NAMES.get(field_id, field_id)
        enriched_fields.append(
            {
                "name": field_name,
                "id": field_id,
                "value": field.get("value"),
            }
        )

    return {**record, "customFields": enriched_fields}


def extract_address_from_record(record: dict[str, Any]) -> str | None:
    """Extract address from Wrike Site Record custom fields."""
    custom_fields = record.get("customFields", [])
    if not isinstance(custom_fields, list):
        return None

    address_field_id = WRIKE_CUSTOM_FIELDS["address"]

    for field in custom_fields:
        if not isinstance(field, dict):
            continue
        if field.get("id") == address_field_id:
            value = field.get("value", "")
            if isinstance(value, str):
                address = re.sub(r"<[^>]+>", "", value).strip()
                return address if address else None

    return None


def extract_created_date_from_record(record: dict[str, Any]) -> str | None:
    """Return the Wrike Site Record creation date as ISO 8601, or None.

    Wrike's API exposes ``createdDate`` at the top level of every Folder/
    Project record (e.g. ``"2025-09-12T18:04:21Z"``). We surface it raw so
    callers can either render the full timestamp or slice the date prefix.
    The dashboard's ``formatDateCreated`` accepts both shapes.
    """
    raw = record.get("createdDate")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def extract_school_type_from_record(record: dict[str, Any]) -> str | None:
    """Extract and normalise school_type from Wrike Site Record."""
    custom_fields = record.get("customFields", [])
    if not isinstance(custom_fields, list):
        return None

    school_type_field_id = WRIKE_CUSTOM_FIELDS["school_type"]

    for field in custom_fields:
        if not isinstance(field, dict):
            continue
        if field.get("id") == school_type_field_id:
            value = field.get("value", "")
            if not isinstance(value, str):
                continue
            if "Microschool 25" in value or "Micro" in value:
                return "micro"
            elif "Growth 250" in value or value == "250":
                return "250"
            elif "Flagship 1000" in value or value == "1000":
                return "1000"

    return None


def extract_google_folder_from_record(record: dict[str, Any]) -> str | None:
    """Extract Google Drive folder URL from Wrike Site Record."""
    custom_fields = record.get("customFields", [])
    if not isinstance(custom_fields, list):
        return None

    folder_field_id = WRIKE_CUSTOM_FIELDS["google_folder"]

    for field in custom_fields:
        if not isinstance(field, dict):
            continue
        if field.get("id") == folder_field_id:
            value = field.get("value", "")
            if isinstance(value, str) and value.strip():
                return value.strip()

    return None


# --- Phase 2 DD provenance fields (W74 / W81) ---
#
# These two custom fields don't have their canonical IDs hardcoded in
# WRIKE_CUSTOM_FIELDS yet. Instead, _resolve_custom_field_id() looks them
# up by display name on first call and caches the result. Once we confirm
# the IDs they can be pasted into WRIKE_CUSTOM_FIELDS and the resolver
# will short-circuit.
#
# Process-wide cache. Keyed by slot name (e.g. "school_feasibility") and
# stores the resolved Wrike custom field ID. None means "resolution
# attempted and failed" — don't retry on every record.
_CUSTOM_FIELD_ID_CACHE: dict[str, str | None] = {}


def _resolve_custom_field_id(
    slot: str,
    *,
    access_token: str | None = None,
) -> str | None:
    """Return the Wrike custom field ID for the given slot.

    Lookup order:
      1. Hardcoded ID in WRIKE_CUSTOM_FIELDS (if non-empty).
      2. Process-wide cache (populated below on first miss).
      3. Wrike GET /customfields, matched by display name from
         WRIKE_CUSTOM_FIELD_DISPLAY_NAMES.

    Returns None when the slot has no display-name mapping or the API
    call fails. Network errors are swallowed — the caller treats a None
    as "this field is unknown; skip the read".
    """
    hardcoded = WRIKE_CUSTOM_FIELDS.get(slot, "")
    if hardcoded:
        return hardcoded

    if slot in _CUSTOM_FIELD_ID_CACHE:
        return _CUSTOM_FIELD_ID_CACHE[slot]

    display_name = WRIKE_CUSTOM_FIELD_DISPLAY_NAMES.get(slot)
    if not display_name:
        _CUSTOM_FIELD_ID_CACHE[slot] = None
        return None

    if access_token is None:
        try:
            access_token = load_wrike_config().access_token
        except WrikeError:
            _CUSTOM_FIELD_ID_CACHE[slot] = None
            return None

    try:
        resp = _wrike_get(
            f"{WRIKE_API_BASE_URL}/customfields",
            headers=_wrike_headers(access_token),
        )
        data = resp.json().get("data", [])
    except (WrikeError, requests.RequestException, ValueError) as exc:
        logger.warning(
            "Failed to resolve Wrike custom field id for %s: %s", slot, exc
        )
        _CUSTOM_FIELD_ID_CACHE[slot] = None
        return None

    target = display_name.casefold()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", "")).casefold()
        if title == target:
            field_id = str(entry.get("id", "")) or None
            _CUSTOM_FIELD_ID_CACHE[slot] = field_id
            return field_id

    logger.info(
        "Wrike custom field %r not found by display name %r", slot, display_name
    )
    _CUSTOM_FIELD_ID_CACHE[slot] = None
    return None


def _extract_string_custom_field(
    record: dict[str, Any],
    slot: str,
    *,
    access_token: str | None = None,
) -> str | None:
    """Generic string-valued custom-field reader keyed by slot name.

    Used for Phase 2 fields where the IDs aren't hardcoded yet. Returns
    a stripped string or None if the field is absent / blank / non-string.
    """
    field_id = _resolve_custom_field_id(slot, access_token=access_token)
    if not field_id:
        return None

    custom_fields = record.get("customFields", [])
    if not isinstance(custom_fields, list):
        return None

    for field in custom_fields:
        if not isinstance(field, dict):
            continue
        if field.get("id") != field_id:
            continue
        value = field.get("value")
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return None


def extract_school_feasibility_from_record(
    record: dict[str, Any],
    *,
    access_token: str | None = None,
) -> str | None:
    """Read W74 "School Feasibility" from a Wrike Site Record.

    Expected values: high / medium / low / unknown (data dictionary 4/24).
    Returns the raw lowercased string, or None when absent.
    """
    raw = _extract_string_custom_field(
        record, "school_feasibility", access_token=access_token
    )
    return raw.lower() if raw else None


def extract_timeline_confidence_from_record(
    record: dict[str, Any],
    *,
    access_token: str | None = None,
) -> str | None:
    """Read W81 "Timeline Confidence" from a Wrike Site Record.

    Expected values: high / medium / low / unknown (data dictionary 4/24).
    Returns the raw lowercased string, or None when absent.
    """
    raw = _extract_string_custom_field(
        record, "timeline_confidence", access_token=access_token
    )
    return raw.lower() if raw else None


def extract_total_building_sf_from_record(record: dict[str, Any]) -> int | None:
    """Extract total building square footage from Wrike custom fields."""
    custom_fields = record.get("customFields", [])
    if not isinstance(custom_fields, list):
        return None

    candidate_ids = (
        WRIKE_CUSTOM_FIELDS["square_footage_buildings"],
        WRIKE_CUSTOM_FIELDS["square_footage"],
    )
    for field_id in candidate_ids:
        for field in custom_fields:
            if not isinstance(field, dict):
                continue
            if field.get("id") != field_id:
                continue
            value = field.get("value", "")
            if isinstance(value, (int, float)):
                amount = int(value)
                return amount if amount > 0 else None
            if isinstance(value, str):
                digits = re.sub(r"[^\d]", "", value)
                if digits:
                    amount = int(digits)
                    return amount if amount > 0 else None
    return None


def get_contact_email(
    contact_id: str, *, cfg: WrikeConfig | None = None
) -> str | None:
    """Resolve a Wrike contact ID to an email address via the Contacts API."""
    profile = get_contact_profile(contact_id, cfg=cfg)
    return profile.get("email") if profile else None


def get_contact_profile(
    contact_id: str, *, cfg: WrikeConfig | None = None
) -> dict[str, str] | None:
    """Resolve a Wrike contact ID to a dict with 'name' and 'email'."""
    if cfg is None:
        cfg = load_wrike_config()

    try:
        resp = _wrike_get(
            f"https://www.wrike.com/api/v4/contacts/{contact_id}",
            headers=_wrike_headers(cfg.access_token),
            timeout=15,
        )
        data = resp.json().get("data", [])
        if data:
            contact = data[0]
            first = contact.get("firstName", "")
            last = contact.get("lastName", "")
            name = f"{first} {last}".strip() or None
            profiles = contact.get("profiles", [])
            email = profiles[0].get("email") if profiles else None
            if name or email:
                return {"name": name or "", "email": email or ""}
    except Exception as e:
        logger.warning("Failed to resolve contact %s: %s", contact_id, e)

    return None


def extract_p1_email_from_record(
    record: dict[str, Any], *, cfg: WrikeConfig | None = None
) -> str | None:
    """Extract the P1 Accountable person's email from a Wrike Site Record.

    Handles both string and list values for the User-type custom field
    (Wrike may return a single contact ID string or an array of IDs).
    """
    custom_fields = record.get("customFields", [])
    if not isinstance(custom_fields, list):
        return None

    p1_field_id = WRIKE_CUSTOM_FIELDS["p1_accountable"]

    for field in custom_fields:
        if not isinstance(field, dict):
            continue
        if field.get("id") == p1_field_id:
            value = field.get("value", "")
            logger.info(
                "P1 Accountable field found for '%s': value=%r (type=%s)",
                record.get("title", "?"), value, type(value).__name__,
            )

            # Wrike User fields can be arrays of contact IDs
            if isinstance(value, list):
                for cid in value:
                    if isinstance(cid, str) and cid.strip():
                        email = get_contact_email(cid.strip(), cfg=cfg)
                        if email:
                            logger.info("Resolved P1 contact %s -> %s", cid, email)
                            return email
            elif isinstance(value, str) and value.strip():
                email = get_contact_email(value.strip(), cfg=cfg)
                if email:
                    logger.info("Resolved P1 contact %s -> %s", value, email)
                    return email

            logger.warning(
                "P1 Accountable field present but could not resolve email for '%s'",
                record.get("title", "?"),
            )

    return None


def extract_p1_from_record(
    record: dict[str, Any], *, cfg: WrikeConfig | None = None
) -> dict[str, str] | None:
    """Return {'name': ..., 'email': ...} for the P1 Accountable person, or None."""
    custom_fields = record.get("customFields", [])
    if not isinstance(custom_fields, list):
        return None

    p1_field_id = WRIKE_CUSTOM_FIELDS["p1_accountable"]

    for field in custom_fields:
        if not isinstance(field, dict):
            continue
        if field.get("id") != p1_field_id:
            continue
        value = field.get("value", "")
        contact_id = None
        if isinstance(value, list):
            contact_id = next((v for v in value if isinstance(v, str) and v.strip()), None)
        elif isinstance(value, str) and value.strip():
            contact_id = value.strip()

        if contact_id:
            profile = get_contact_profile(contact_id, cfg=cfg)
            if profile:
                logger.info(
                    "Resolved P1 for '%s': %s <%s>",
                    record.get("title", "?"), profile.get("name"), profile.get("email"),
                )
                return profile

    return None


def _get_active_status_ids(*, access_token: str) -> set[str]:
    """Fetch all Wrike workflows and return the set of customStatusIds in the 'Active' group."""
    url = f"{WRIKE_API_BASE_URL}/workflows"
    logger.info("Fetching Wrike workflows to resolve active status IDs")

    resp = _wrike_get(
        url,
        headers=_wrike_headers(access_token),
        timeout=WRIKE_TIMEOUT_SECONDS,
    )

    payload: dict[str, Any] = resp.json()
    active_ids: set[str] = set()

    for workflow in payload.get("data", []):
        for status in workflow.get("customStatuses", []):
            if status.get("group") == "Active":
                status_id = status.get("id")
                if isinstance(status_id, str):
                    active_ids.add(status_id)

    logger.info("Found %d active status IDs across all workflows", len(active_ids))
    return active_ids


def is_record_active(record: dict[str, Any], active_status_ids: set[str]) -> bool:
    """Return True if the record's customStatusId belongs to the 'Active' status group.

    If the record has no customStatusId (field missing from API response),
    defaults to True so the record is not incorrectly filtered out.
    """
    status_id = record.get("customStatusId")
    if not isinstance(status_id, str):
        # No status on record — assume active to avoid false exclusions
        logger.debug(
            "Record '%s' has no customStatusId — treating as active",
            record.get("title", "?"),
        )
        return True
    return status_id in active_status_ids


def extract_stage_from_record(record: dict[str, Any]) -> str | None:
    """Extract overall_site_stage from Wrike Site Record."""
    custom_fields = record.get("customFields", [])
    if not isinstance(custom_fields, list):
        return None

    stage_field_id = WRIKE_CUSTOM_FIELDS["overall_site_stage"]

    for field in custom_fields:
        if not isinstance(field, dict):
            continue
        if field.get("id") == stage_field_id:
            value = field.get("value", "")
            if isinstance(value, str):
                return value

    return None


def get_site_record_by_id(
    *, record_id: str, cfg: WrikeConfig | None = None
) -> dict[str, Any]:
    """Get a Site Record by its Wrike ID."""
    if cfg is None:
        cfg = load_wrike_config()

    url = f"{WRIKE_API_BASE_URL}/folders/{record_id}"
    logger.info("Fetching site record: %s", record_id)

    resp = _wrike_get(
        url,
        headers=_wrike_headers(cfg.access_token),
        timeout=WRIKE_TIMEOUT_SECONDS,
    )

    payload: dict[str, Any] = resp.json()
    data = payload.get("data", [])

    if not data:
        raise WrikeError(f"Site record not found: {record_id}")

    record = data[0]
    logger.info("Site record fetched: %s", record.get("title"))
    return record  # type: ignore[no-any-return]


def resolve_permalink_to_id(*, permalink: str, cfg: WrikeConfig | None = None) -> str:
    """Resolve a Wrike permalink to a folder/record ID."""
    if cfg is None:
        cfg = load_wrike_config()

    url = f"{WRIKE_API_BASE_URL}/folders"
    logger.info("Resolving permalink to record ID: %s", permalink)

    resp = _wrike_get(
        url,
        headers=_wrike_headers(cfg.access_token),
        params={"permalink": permalink},
        timeout=WRIKE_TIMEOUT_SECONDS,
    )

    payload: dict[str, Any] = resp.json()
    data = payload.get("data", [])

    if not data:
        raise WrikeError(f"Could not resolve permalink: {permalink}")

    record = data[0]
    record_id = record.get("id")

    if not isinstance(record_id, str):
        raise WrikeError(f"Invalid record ID from permalink: {permalink}")

    logger.info("Resolved permalink to record ID: %s", record_id)
    return record_id


def _get_all_folder_ids(*, access_token: str) -> list[str]:
    """Get all folder IDs from the Wrike space."""
    url = f"{WRIKE_API_BASE_URL}/spaces/{WRIKE_SPACE_ID}/folders"
    logger.info("Fetching all folder IDs from space %s", WRIKE_SPACE_ID)

    resp = _wrike_get(
        url,
        headers=_wrike_headers(access_token),
        timeout=WRIKE_TIMEOUT_SECONDS,
    )

    payload: dict[str, Any] = resp.json()
    folder_ids: list[str] = []
    data = payload.get("data", [])
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                folder_id = item.get("id")
                if isinstance(folder_id, str):
                    folder_ids.append(folder_id)

    return folder_ids


def _get_all_site_records(*, cfg: WrikeConfig) -> list[dict[str, Any]]:
    """Get all Site Records from the Wrike space (all stages)."""
    folder_ids = _get_all_folder_ids(access_token=cfg.access_token)
    logger.info("Found %d folder IDs", len(folder_ids))

    batch_size = 100
    all_site_records: list[dict[str, Any]] = []

    for i in range(0, len(folder_ids), batch_size):
        batch = folder_ids[i : i + batch_size]
        ids_param = ",".join(batch)
        url = f"{WRIKE_API_BASE_URL}/folders/{ids_param}"

        logger.info(
            "Querying batch %d-%d of %d folders",
            i + 1,
            min(i + batch_size, len(folder_ids)),
            len(folder_ids),
        )

        resp = _wrike_get(
            url,
            headers=_wrike_headers(cfg.access_token),
            params={"fields": '["customItemTypeId"]'},
            timeout=WRIKE_TIMEOUT_SECONDS,
        )

        payload: dict[str, Any] = resp.json()
        data = payload.get("data", [])

        if not isinstance(data, list):
            continue

        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("customItemTypeId") == WRIKE_SITE_RECORD_TYPE_ID:
                all_site_records.append(item)

    logger.info("Found %d total Site Records", len(all_site_records))
    return all_site_records


def _match_site_with_llm(
    *, query: str, site_records: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Use LLM to match the provided query to the best Site Record by name or address."""
    if not site_records:
        logger.warning("No site records to match against")
        return None

    candidates: list[dict[str, Any]] = []
    for record in site_records:
        record_id = record.get("id")
        title = record.get("title", "")
        address = extract_address_from_record(record) or ""

        candidates.append({"id": record_id, "title": title, "address": address})

    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        logger.warning("OPENAI_API_KEY not found, falling back to exact title match")
        # Simple fallback: title contains query
        query_lower = query.lower().strip()
        for record in site_records:
            title = record.get("title", "")
            if isinstance(title, str) and query_lower in title.lower():
                return record
        return None

    client = OpenAI(api_key=openai_api_key, max_retries=2)

    system_prompt = (
        "You are a site record matching assistant. Given a search query (site name or address) "
        "and a list of candidate Site Records, identify which candidate best matches the query.\n\n"
        "Consider title similarity, address similarity, abbreviations, and common variations.\n\n"
        "Return ONLY a JSON object:\n"
        '{"matched_id": "the ID of the best matching record", "reasoning": "brief explanation"}\n\n'
        "If no good match is found, return:\n"
        '{"matched_id": null, "reasoning": "explanation"}'
    )

    user_prompt = (
        f"Search query: {query}\n\n"
        f"Candidate Site Records:\n{json.dumps(candidates, indent=2)}\n\n"
        "Which candidate best matches the search query?"
    )

    logger.info("Calling OpenAI to match site query: %s", query)

    settings = get_settings()
    response = client.chat.completions.create(
        model=settings.openai_site_match_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
    )

    result_text = response.choices[0].message.content
    if not result_text:
        logger.error("Empty response from OpenAI")
        return None

    result: dict[str, str | None] = json.loads(result_text)
    matched_id = result.get("matched_id")
    reasoning = result.get("reasoning", "")

    logger.info("LLM match result: matched_id=%s, reasoning=%s", matched_id, reasoning)

    if not matched_id:
        logger.warning("No matching Site Record found by LLM")
        return None

    for record in site_records:
        if record.get("id") == matched_id:
            return record

    logger.warning("Matched ID %s not found in site_records", matched_id)
    return None


def _looks_like_wrike_id(value: str) -> bool:
    """Return True if the value looks like a Wrike record ID (alphanumeric, 8-16 chars)."""
    return bool(re.fullmatch(r"[A-Z0-9]{8,16}", value.strip()))


def _looks_like_permalink(value: str) -> bool:
    """Return True if the value looks like a Wrike permalink URL."""
    return "wrike.com" in value.lower()


def find_site_record(
    *, site_name_or_id: str, cfg: WrikeConfig | None = None
) -> dict[str, Any] | None:
    """
    Find a Site Record by name or ID.

    - If it looks like a Wrike ID: fetch directly.
    - If it looks like a permalink: resolve then fetch.
    - Otherwise: search all Site Records and use LLM to find the best match.

    Returns the Site Record dict enriched with human-readable custom field names,
    or None if not found.
    """
    if cfg is None:
        cfg = load_wrike_config()

    query = site_name_or_id.strip()

    # Direct ID lookup
    if _looks_like_wrike_id(query):
        logger.info("Query looks like a Wrike ID, fetching directly: %s", query)
        try:
            record = get_site_record_by_id(record_id=query, cfg=cfg)
            return enrich_custom_fields_with_names(record)
        except WrikeError as e:
            logger.warning("Direct ID lookup failed (%s), falling back to name search", e)

    # Permalink lookup
    if _looks_like_permalink(query):
        logger.info("Query looks like a permalink, resolving: %s", query)
        try:
            record_id = resolve_permalink_to_id(permalink=query, cfg=cfg)
            record = get_site_record_by_id(record_id=record_id, cfg=cfg)
            return enrich_custom_fields_with_names(record)
        except WrikeError as e:
            logger.error("Permalink lookup failed: %s", e)
            return None

    # Name / fuzzy search
    logger.info("Searching for Site Record by name: %s", query)
    all_records = _get_all_site_records(cfg=cfg)
    matched = _match_site_with_llm(query=query, site_records=all_records)

    if matched:
        logger.info(
            "Found matching Site Record: %s (%s)",
            matched.get("title"),
            matched.get("id"),
        )
        return enrich_custom_fields_with_names(matched)

    logger.warning("No matching Site Record found for: %s", query)
    return None


def get_record_comments(
    *, record_id: str, cfg: WrikeConfig | None = None
) -> list[dict[str, Any]]:
    """Fetch comments on a Wrike folder/record, sorted newest-first.

    Returns list of ``{author, text, created_date}`` dicts.
    """
    if cfg is None:
        cfg = load_wrike_config()

    url = f"{WRIKE_API_BASE_URL}/folders/{record_id}/comments"
    logger.info("Fetching comments for record: %s", record_id)

    resp = _wrike_get(
        url,
        headers=_wrike_headers(cfg.access_token),
        timeout=WRIKE_TIMEOUT_SECONDS,
    )

    payload: dict[str, Any] = resp.json()
    raw_comments = payload.get("data", [])

    comments: list[dict[str, Any]] = []
    for c in raw_comments:
        if not isinstance(c, dict):
            continue
        text = c.get("text", "")
        # Strip HTML tags from comment text
        text = re.sub(r"<[^>]+>", "", text).strip()
        if not text:
            continue
        comments.append({
            "author": c.get("authorId", ""),
            "text": text,
            "created_date": c.get("createdDate", ""),
        })

    # Sort newest-first
    comments.sort(key=lambda x: x.get("created_date", ""), reverse=True)
    logger.info("Found %d comments for record %s", len(comments), record_id)
    return comments


# Keywords for classifying a comment to a report section
_COMMENT_SECTION_KEYWORDS: dict[str, list[str]] = {
    "q1": ["zoning", "permit", "ahj", "authority having jurisdiction", "variance",
           "conditional use", "special use", "cup", "sup", "pre-app", "pre-application",
           "meeting notes", "fire marshal", "code enforcement"],
    "q2": ["inspection", "building", "hvac", "sprinkler", "fire alarm", "structural",
           "roof", "plumbing", "electrical", "ada", "egress", "matterport", "floorplan"],
    "q3": ["cost", "budget", "estimate", "pricing", "quote", "bid", "expenditure"],
    "q4": ["timeline", "schedule", "milestone", "deadline", "target date", "opening"],
    "appendix": ["pre-app notes", "pre-application notes", "meeting minutes",
                  "attachment", "document link"],
}


def classify_comment_to_section(comment_text: str) -> str:
    """Map a comment's content to a report section using keyword matching.

    Returns a section key: ``"q1"``, ``"q2"``, ``"q3"``, ``"q4"``, ``"appendix"``,
    or ``"general"`` if no section matched.
    """
    text_lower = comment_text.lower()

    scores: dict[str, int] = {}
    for section, keywords in _COMMENT_SECTION_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[section] = score

    if not scores:
        return "general"

    return max(scores, key=lambda k: scores[k])


ACTIVE_DD_STAGES: set[str] = {
    "1. Looking for Sites",
    "2. Evaluating Potential Sites (LOI)",
}


def filter_active_site_records(
    records: list[dict[str, Any]],
    active_status_ids: set[str],
    *,
    allowed_stages: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter site records to only those with Active status and an allowed DD stage.

    Returns a new list; does not modify the input.
    """
    if allowed_stages is None:
        allowed_stages = ACTIVE_DD_STAGES

    filtered: list[dict[str, Any]] = []
    for record in records:
        title = record.get("title", "Unknown")

        if not is_record_active(record, active_status_ids):
            logger.debug("Filtering out '%s' — status group is not Active", title)
            continue

        stage = extract_stage_from_record(record)
        if stage not in allowed_stages:
            logger.debug("Filtering out '%s' — stage '%s' not in allowed stages", title, stage)
            continue

        filtered.append(record)

    logger.info(
        "Filtered site records: %d of %d are active with allowed stages",
        len(filtered), len(records),
    )
    return filtered


def build_site_summary(record: dict[str, Any]) -> dict[str, Any]:
    """
    Build a concise DD-relevant summary dict from a Wrike Site Record.

    Returns a flat dict of the most important fields for the DD workflow.
    """
    title = record.get("title", "")
    address = extract_address_from_record(record)
    school_type = extract_school_type_from_record(record)
    stage = extract_stage_from_record(record)
    drive_folder_url = extract_google_folder_from_record(record)
    total_building_sf = extract_total_building_sf_from_record(record)
    p1_profile = extract_p1_from_record(record)

    return {
        "id": record.get("id"),
        "title": title,
        "address": address,
        "school_type": school_type,
        "stage": stage,
        "drive_folder_url": drive_folder_url,
        "total_building_sf": total_building_sf,
        "p1_assignee_name": p1_profile.get("name") if p1_profile else None,
        "p1_assignee_email": p1_profile.get("email") if p1_profile else None,
        "custom_fields": record.get("customFields", []),
        "permalink": record.get("permalink"),
        "description": record.get("description", ""),
        # Wrike's own folder/task creation timestamp (ISO 8601 UTC).
        # This is the canonical "date created" for the site — when the
        # record was first added to Wrike — distinct from report_date
        # (when a DD report ran) or published_at (dashboard upsert time).
        "created_date": record.get("createdDate", ""),
    }
