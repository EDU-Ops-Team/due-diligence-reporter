"""DD Report template token schema, agent key aliases, and normalization.

This module is the single source of truth for the {{token}} placeholders
in the V2 Google Doc DD report template.  It also provides an alias map that
translates commonly-observed agent key variations to their canonical template
token, and a ``normalize_report_data`` function used by ``create_dd_report``
to maximise the number of placeholders that get filled.
"""

from __future__ import annotations

import logging
from typing import Any

from .utils import flatten_report_data_for_replacement

logger = logging.getLogger(__name__)

ALLOWED_CAN_WE_ANSWERS: frozenset[str] = frozenset({
    "Yes",
    "Yes see notes",
    "No",
})

LEGACY_CAN_WE_ANSWER_ALIASES: dict[str, str] = {
    "yes": "Yes",
    "yes see notes": "Yes see notes",
    "no": "No",
    "conditional": "Yes see notes",
}

# ---------------------------------------------------------------------------
# Template tokens (match the {{token}} placeholders in the V2 template)
# ---------------------------------------------------------------------------

TEMPLATE_TOKENS: list[str] = [
    # ── meta ───────────────────────────────────────────────────────────────
    "meta.site_name",
    "meta.city_state_zip",
    "meta.school_type",
    "meta.marketing_name",
    "meta.report_date",
    "meta.prepared_by",
    "meta.drive_folder_url",
    # ── exec: "Can we do this?" card (pick-menu dimensions) ───────────────
    "exec.c_answer",
    "exec.c_edreg",
    "exec.c_occupancy",
    "exec.c_zoning",
    # ── exec: cost / capacity / timeline grid (bare values) ───────────────
    "exec.e_mvp_capacity",
    "exec.e_ideal_capacity",
    "exec.e_mvp_cost",
    "exec.e_ideal_cost",
    "exec.f_mvp_ready",
    "exec.f_ideal_ready",
    # ── exec: delta column (server-computed, NOT agent-filled) ──────────
    "exec.delta_capacity",
    "exec.delta_cost",
    "exec.delta_ready",
    # ── exec: conditions ──────────────────────────────────────────────────
    "exec.acquisition_conditions",
    "exec.risk_notes",
    # ── sources (document links — 6 rows) ─────────────────────────────────
    "sources.sir_link",
    "sources.inspection_link",
    "sources.isp_link",
    "sources.e_occupancy_link",
    "sources.school_approval_link",
    "sources.trace_link",
]

TEMPLATE_TOKEN_SET: frozenset[str] = frozenset(TEMPLATE_TOKENS)

# ---------------------------------------------------------------------------
# Token → document source mapping (which document provides each token's data)
# ---------------------------------------------------------------------------

TOKEN_SOURCES: dict[str, str] = {
    # ── meta ───────────────────────────────────────────────────────────────
    "meta.site_name":               "Wrike",
    "meta.city_state_zip":          "Wrike",
    "meta.school_type":             "Wrike",
    "meta.marketing_name":          "Wrike",
    "meta.report_date":             "System",
    "meta.prepared_by":             "System",
    # ── exec: "Can we do this?" ────────────────────────────────────────────
    "exec.c_answer":                "Agent",
    "exec.c_zoning":                "SIR",
    "exec.c_occupancy":             "E-Occupancy",
    "exec.c_edreg":                 "School Approval",
    # ── exec: cost / capacity / timeline ───────────────────────────────────
    "exec.e_mvp_capacity":          "ISP",
    "exec.e_ideal_capacity":        "ISP",
    "exec.e_mvp_cost":              "ISP",
    "exec.e_ideal_cost":            "ISP",
    "exec.f_mvp_ready":             "Agent",
    "exec.f_ideal_ready":           "Agent",
    # ── exec: deltas (server-computed) ─────────────────────────────────────
    "exec.delta_capacity":          "Computed",
    "exec.delta_cost":              "Computed",
    "exec.delta_ready":             "Computed",
    # ── exec: conditions ───────────────────────────────────────────────────
    "exec.acquisition_conditions":  "Agent",
    "exec.risk_notes":              "Agent",
}

# ---------------------------------------------------------------------------
# Link tokens — values that should render as clickable hyperlinks in the doc
# ---------------------------------------------------------------------------

LINK_TOKENS: frozenset[str] = frozenset({
    "sources.sir_link",
    "sources.inspection_link",
    "sources.isp_link",
    "sources.e_occupancy_link",
    "sources.school_approval_link",
    "sources.trace_link",
    "meta.drive_folder_url",
})

# Display labels for link tokens — shown in the doc instead of raw URLs.
# Tokens not listed here fall back to the raw URL as display text.

LINK_DISPLAY_LABELS: dict[str, str] = {
    "sources.sir_link":             "View SIR",
    "sources.inspection_link":      "View Inspection",
    "sources.isp_link":             "View ISP",
    "sources.e_occupancy_link":     "View E-Occupancy",
    "sources.school_approval_link": "View School Approval",
    "sources.trace_link":           "View Report Trace",
    "meta.drive_folder_url":        "View Site Folder",
}

# ---------------------------------------------------------------------------
# Agent key aliases → canonical template token
# ---------------------------------------------------------------------------
# Only semantically correct renames.  If the canonical token already has a
# value in the flattened data the alias is silently skipped (no overwrite).

AGENT_KEY_ALIASES: dict[str, str] = {
    # ── top-level / site.* → meta.* ──────────────────────────────────────
    "site_name":            "meta.site_name",
    "report_date":          "meta.report_date",
    "doc_url":              "meta.drive_folder_url",
    "site.name":            "meta.site_name",
    "site.city_state_zip":  "meta.city_state_zip",
    "site.address":         "meta.city_state_zip",
    "site.school_type":     "meta.school_type",
    "site.marketing_name":  "meta.marketing_name",
    "site.prepared_by":     "meta.prepared_by",
    "p1_assignee_name":     "meta.prepared_by",
    # ── exec_summary.* → exec.* (legacy agent output) ────────────────────
    "exec_summary.acquisition_conditions": "exec.acquisition_conditions",
    "exec_summary.risk_notes":             "exec.risk_notes",
    # ── backward compat: old token names → new names ─────────────────────
    "exec.c_permitting":                  "exec.c_edreg",
    "exec.f_ready_mm_yy":                 "exec.f_mvp_ready",
    # ── typo alias ───────────────────────────────────────────────────────
    "exec.e_ideal_capcity":               "exec.e_ideal_capacity",
    # ── appendix.* → sources.* ───────────────────────────────────────────
    "appendix.sir_link":                   "sources.sir_link",
    "appendix.inspection_link":            "sources.inspection_link",
    "appendix.building_inspection_link":   "sources.inspection_link",
    "appendix.floorplan_viability_link":   "sources.isp_link",
    "appendix.isp_link":                   "sources.isp_link",
}


# ---------------------------------------------------------------------------
# normalize_report_data
# ---------------------------------------------------------------------------

def normalize_report_data(
    report_data: dict[str, Any],
    site_name: str,
    report_date: str,
) -> tuple[dict[str, str], list[str], list[str], dict[str, str]]:
    """Normalize agent output into template-ready replacements.

    Steps:
        1. Flatten the nested ``report_data`` dict using dot-separated keys.
        2. Inject ``meta.site_name`` and ``meta.report_date`` defaults.
        3. Apply :data:`AGENT_KEY_ALIASES` — only when the canonical token
           does *not* already have a value (no overwrite).
        4. Filter to keys present in :data:`TEMPLATE_TOKEN_SET`.

    Returns:
        ``(replacements, unmatched_keys, unfilled_tokens, token_sources)``

        - *replacements* — ``{token: value}`` ready for ``replaceAllText``.
        - *unmatched_keys* — agent keys that matched no template token (even
          after aliasing).  Useful for diagnostics / future alias expansion.
        - *unfilled_tokens* — template tokens that received no value.
        - *token_sources* — ``{token: source}`` for every template token.
          Source values: ``"agent"``, ``"alias:{original_key}"``,
          ``"default"``, or ``"unfilled"``.  Deltas are marked ``"computed"``
          by the caller after :func:`compute_deltas`.
    """
    # 1. Flatten
    flat = flatten_report_data_for_replacement(report_data)
    token_sources: dict[str, str] = {}

    # Track which tokens the agent provided directly
    for key in flat:
        if key in TEMPLATE_TOKEN_SET:
            token_sources[key] = "agent"

    # 2. Inject defaults (only if agent didn't provide them)
    if "meta.site_name" not in flat:
        flat["meta.site_name"] = site_name.strip()
        token_sources["meta.site_name"] = "default"
    if "meta.report_date" not in flat:
        flat["meta.report_date"] = report_date
        token_sources["meta.report_date"] = "default"

    # 3. Apply aliases (skip when canonical already has a value)
    for alias, canonical in AGENT_KEY_ALIASES.items():
        if alias in flat and canonical not in flat:
            flat[canonical] = flat[alias]
            if canonical in TEMPLATE_TOKEN_SET:
                token_sources[canonical] = f"alias:{alias}"

    if "meta.prepared_by" not in flat:
        flat["meta.prepared_by"] = "EDU Ops Team"
        token_sources["meta.prepared_by"] = "default"

    can_we_answer = flat.get("exec.c_answer")
    if isinstance(can_we_answer, str):
        normalized_answer = normalize_can_we_answer(can_we_answer)
        if normalized_answer is None:
            logger.warning("Dropping invalid exec.c_answer value: %r", can_we_answer)
            flat.pop("exec.c_answer", None)
            token_sources.pop("exec.c_answer", None)
        else:
            flat["exec.c_answer"] = normalized_answer

    # 4. Filter → only template tokens
    replacements: dict[str, str] = {}
    unmatched_keys: list[str] = []

    for key, value in flat.items():
        if key in TEMPLATE_TOKEN_SET:
            replacements[key] = value
        else:
            unmatched_keys.append(key)

    unfilled_tokens = [t for t in TEMPLATE_TOKENS if t not in replacements]

    # Mark unfilled tokens in sources
    for token in unfilled_tokens:
        token_sources[token] = "unfilled"

    logger.info(
        "normalize_report_data: %d replacements, %d unmatched, %d unfilled",
        len(replacements),
        len(unmatched_keys),
        len(unfilled_tokens),
    )
    if unmatched_keys:
        logger.info("Unmatched agent keys: %s", sorted(unmatched_keys))
    if unfilled_tokens:
        logger.debug("Unfilled tokens: %s", sorted(unfilled_tokens))

    return replacements, sorted(unmatched_keys), sorted(unfilled_tokens), token_sources


def normalize_can_we_answer(value: str) -> str | None:
    """Normalize legacy or case-variant answers to the canonical allowed values."""
    return LEGACY_CAN_WE_ANSWER_ALIASES.get(value.strip().lower())


# ---------------------------------------------------------------------------
# Delta computation (server-side, not agent-filled)
# ---------------------------------------------------------------------------


def _parse_dollar(value: str) -> int | None:
    """Parse a dollar string like '$185,000' into an integer (185000)."""
    cleaned = value.replace("$", "").replace(",", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        try:
            return int(float(cleaned))
        except ValueError:
            return None


def _format_dollar(amount: int) -> str:
    """Format an integer as a dollar string like '$105,000'."""
    if amount < 0:
        return f"-${abs(amount):,}"
    return f"${amount:,}"


def _parse_mm_yy(value: str) -> tuple[int, int] | None:
    """Parse 'MM/YY' into (month, year_2digit). Returns None on failure."""
    parts = value.strip().split("/")
    if len(parts) != 2:
        return None
    try:
        month, year = int(parts[0]), int(parts[1])
        if 1 <= month <= 12 and 0 <= year <= 99:
            return month, year
        return None
    except ValueError:
        return None


def _month_diff(mvp: tuple[int, int], ideal: tuple[int, int]) -> int:
    """Compute month difference (ideal - mvp) from (month, year_2digit) tuples."""
    mvp_total = mvp[1] * 12 + mvp[0]
    ideal_total = ideal[1] * 12 + ideal[0]
    return ideal_total - mvp_total


def compute_deltas(replacements: dict[str, str]) -> None:
    """Compute delta column values from MVP/Ideal pairs and inject into *replacements*.

    Computes:
        - ``exec.delta_capacity`` = ideal_capacity - mvp_capacity (integer)
        - ``exec.delta_cost`` = ideal_cost - mvp_cost (dollar formatted)
        - ``exec.delta_ready`` = ideal_ready - mvp_ready (e.g. "+3 mo")

    Only sets each delta if both source values are present and parseable.
    Existing values are not overwritten.
    """
    # Capacity delta
    mvp_cap = replacements.get("exec.e_mvp_capacity", "").strip()
    ideal_cap = replacements.get("exec.e_ideal_capacity", "").strip()
    if mvp_cap and ideal_cap and "exec.delta_capacity" not in replacements:
        try:
            delta = int(ideal_cap) - int(mvp_cap)
            sign = "+" if delta > 0 else ""
            replacements["exec.delta_capacity"] = f"{sign}{delta}"
        except ValueError:
            logger.debug("Could not compute capacity delta: mvp=%s, ideal=%s", mvp_cap, ideal_cap)

    # Cost delta
    mvp_cost = replacements.get("exec.e_mvp_cost", "").strip()
    ideal_cost = replacements.get("exec.e_ideal_cost", "").strip()
    if mvp_cost and ideal_cost and "exec.delta_cost" not in replacements:
        mvp_val = _parse_dollar(mvp_cost)
        ideal_val = _parse_dollar(ideal_cost)
        if mvp_val is not None and ideal_val is not None:
            delta = ideal_val - mvp_val
            sign = "+" if delta > 0 else ""
            replacements["exec.delta_cost"] = f"{sign}{_format_dollar(delta)}"
        else:
            logger.debug("Could not parse cost values: mvp=%s, ideal=%s", mvp_cost, ideal_cost)

    # Timeline delta
    mvp_ready = replacements.get("exec.f_mvp_ready", "").strip()
    ideal_ready = replacements.get("exec.f_ideal_ready", "").strip()
    if mvp_ready and ideal_ready and "exec.delta_ready" not in replacements:
        mvp_parsed = _parse_mm_yy(mvp_ready)
        ideal_parsed = _parse_mm_yy(ideal_ready)
        if mvp_parsed and ideal_parsed:
            diff = _month_diff(mvp_parsed, ideal_parsed)
            sign = "+" if diff > 0 else ""
            replacements["exec.delta_ready"] = f"{sign}{diff} mo"
        else:
            logger.debug("Could not parse timeline values: mvp=%s, ideal=%s", mvp_ready, ideal_ready)
