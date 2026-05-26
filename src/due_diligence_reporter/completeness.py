"""Partial-on-purpose completeness metadata for DD reports.

Computes a structured ``completeness`` block that downstream consumers
(the Google Doc renderer and the ``check_site_readiness`` MCP tool) use
to decide whether a report is "complete" or
"partial-on-purpose -- waiting on a known input".

Known pending reasons include missing RayCon scenario data and first-round
reports that intentionally carry open vendor-verification items.
"""

from __future__ import annotations

from typing import Any

from .raycon_client import RAYCON_BREAKDOWN_ROWS

# ---------------------------------------------------------------------------
# Reason keys
# ---------------------------------------------------------------------------

RAYCON_PENDING_REASON = "raycon_scenario_pending"
RAYCON_PENDING_TRIGGER_FILE = "raycon_scenario.json"
VENDOR_VERIFICATION_PENDING_REASON = "vendor_verification_pending"
VENDOR_VERIFICATION_TRIGGER_FILE = "vendor source documents"
VERIFICATION_OPEN_ITEMS_TOKEN = "_internal.verification_open_items"

# Human-readable label for each reason key. Consumed by the banner
# renderer in the document builder. New reason keys must add an entry here
# or the banner falls back to the raw key.
REASON_DISPLAY_LABELS: dict[str, str] = {
    RAYCON_PENDING_REASON: "RayCon cost & capacity",
    VENDOR_VERIFICATION_PENDING_REASON: "Vendor verification",
}

# Trigger files keyed by reason — drives the ``auto_republish_on``
# array on the completeness block.
REASON_TRIGGER_FILES: dict[str, str] = {
    RAYCON_PENDING_REASON: RAYCON_PENDING_TRIGGER_FILE,
    VENDOR_VERIFICATION_PENDING_REASON: VENDOR_VERIFICATION_TRIGGER_FILE,
}


# ---------------------------------------------------------------------------
# Token classification
# ---------------------------------------------------------------------------


def raycon_token_paths() -> list[str]:
    """Return the canonical list of token paths sourced from RayCon.

    These are the tokens that go pending when ``raycon_scenario.json``
    has not yet landed. Per ``report_schema.TOKEN_SOURCES``, capacity
    summary tokens are sourced from the Capacity Brainlift (the agent),
    not RayCon, so they're excluded here.
    """
    paths: list[str] = []
    for scenario in ("fastest_open", "max_capacity"):
        paths.append(f"exec.{scenario}_capex")
        paths.append(f"exec.{scenario}_open_date")
        for row_key, _ in RAYCON_BREAKDOWN_ROWS:
            paths.append(f"exec.cost_{row_key}_{scenario}")
    return paths


_RAYCON_TOKEN_PATHS: frozenset[str] = frozenset(raycon_token_paths())


# Placeholder strings produced by ``server._fill_scenario_placeholders``.
# Anything that starts with one of these prefixes is a RayCon-pending
# placeholder, regardless of suffix.
_RAYCON_PENDING_PLACEHOLDER_PREFIXES: tuple[str, ...] = (
    "[Not found - Fastest Open scenario not extracted",
    "[Not found - Max Capacity scenario not extracted",
)


def is_raycon_pending_placeholder(value: Any) -> bool:
    """Return True if *value* is the placeholder server.py installs when
    a RayCon-derived field has no real data yet."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    return any(text.startswith(prefix) for prefix in _RAYCON_PENDING_PLACEHOLDER_PREFIXES)


def _is_filled(value: Any) -> bool:
    """Return True if *value* counts as a real value (not a placeholder)."""
    if not isinstance(value, str):
        return value is not None
    text = value.strip()
    if not text:
        return False
    if is_raycon_pending_placeholder(text):
        return False
    return True


# ---------------------------------------------------------------------------
# Completeness block
# ---------------------------------------------------------------------------


def compute_completeness_block(replacements: dict[str, str]) -> dict[str, Any]:
    """Compute the ``report_metadata.completeness`` block for a report.

    Walks the final populated token map and counts filled vs. pending
    tokens, grouping pending tokens by the reason they're pending.

    Args:
        replacements: The normalized token -> value map that the report
            Google Doc renderer will consume.

    Returns:
        A dict matching the schema documented in the partial-on-purpose
        plan (Rec. 5):

            {
              "stage": "partial" | "complete",
              "filled_token_count": int,
              "pending_token_count": int,
              "pending_reasons": {reason_key: [token_paths...]},
              "auto_republish_on": [trigger_file, ...]
            }
    """
    pending_by_reason: dict[str, list[str]] = {}
    filled = 0
    pending = 0

    for token, value in replacements.items():
        if token == VERIFICATION_OPEN_ITEMS_TOKEN and _is_filled(value):
            pending_by_reason.setdefault(
                VENDOR_VERIFICATION_PENDING_REASON, []
            ).append(token)
            pending += 1
        elif token in _RAYCON_TOKEN_PATHS and not _is_filled(value):
            pending_by_reason.setdefault(RAYCON_PENDING_REASON, []).append(token)
            pending += 1
        elif _is_filled(value):
            filled += 1

    for reason in pending_by_reason:
        pending_by_reason[reason].sort()

    pending_reasons_sorted = {
        reason: pending_by_reason[reason]
        for reason in sorted(pending_by_reason)
    }

    auto_republish_on = sorted({
        REASON_TRIGGER_FILES[reason]
        for reason in pending_reasons_sorted
        if reason in REASON_TRIGGER_FILES
    })

    stage = "partial" if pending > 0 else "complete"

    return {
        "stage": stage,
        "filled_token_count": filled,
        "pending_token_count": pending,
        "pending_reasons": pending_reasons_sorted,
        "auto_republish_on": auto_republish_on,
    }


def project_completeness_from_readiness(
    *,
    raycon_scenario_found: bool,
    verification_open_items_pending: bool = False,
) -> dict[str, Any]:
    """Project what the completeness block will look like *before* the
    pipeline has run, based on which gating inputs are available.

    Used by ``check_site_readiness`` to give the agent (and Greg) a
    preview: ship partial now, or wait for the missing input?

    RayCon is modeled directly from readiness. First-round vendor
    verification can also be projected when the caller already knows
    open items will be logged.
    """
    pending_reasons_unsorted: dict[str, list[str]] = {}
    if not raycon_scenario_found:
        pending_reasons_unsorted[RAYCON_PENDING_REASON] = sorted(_RAYCON_TOKEN_PATHS)
    if verification_open_items_pending:
        pending_reasons_unsorted[VENDOR_VERIFICATION_PENDING_REASON] = [
            VERIFICATION_OPEN_ITEMS_TOKEN
        ]

    pending_reasons = {
        reason: pending_reasons_unsorted[reason]
        for reason in sorted(pending_reasons_unsorted)
    }

    auto_republish_on = sorted({
        REASON_TRIGGER_FILES[reason]
        for reason in pending_reasons
        if reason in REASON_TRIGGER_FILES
    })

    pending_count = sum(len(paths) for paths in pending_reasons.values())
    stage = "partial" if pending_count > 0 else "complete"

    return {
        "stage": stage,
        # Filled count is unknown pre-generation — caller is asking what
        # the *shape* would look like. Surface the projected pending
        # count without inventing a filled count.
        "filled_token_count": None,
        "pending_token_count": pending_count,
        "pending_reasons": pending_reasons,
        "auto_republish_on": auto_republish_on,
    }
