"""Publish DD report data to the DD Dashboard.

After `create_dd_report` successfully fills out a Google Doc, we also POST the
structured `report_data` to the dashboard's `/api/sites/:slug/publish`
endpoint. The endpoint converts flat pipeline tokens (exec.c_zoning,
exec.furniture_only_capex, sources.sir_link, etc.) into the dashboard's nested
site schema and commits it to `client/public/sites.json`.

Auth: shared bearer secret in the `DASHBOARD_PUBLISH_SECRET` env var.

Silent-fail policy: never raise. The pipeline's primary output is the Google
Doc; dashboard publishing is best-effort. Any failure is logged and
swallowed so a network hiccup doesn't block report delivery.

Environment:
    DASHBOARD_PUBLISH_URL     Base URL of the dashboard (default:
                              https://dd-dashboard-three.vercel.app)
    DASHBOARD_PUBLISH_SECRET  Bearer token (must match Vercel env var)
    DASHBOARD_PUBLISH_ENABLED Set to "0" to disable publishing entirely.
    DASHBOARD_PUBLISH_OWNER   Cutover flag for the DDR → DD Pipeline
                              migration (Phase A5). Values:
                                "reporter" — (default) reporter publishes
                                  as it always has.
                                "pipeline" — the alpha-dd-pipeline WU-15
                                  is the sole publisher; the reporter
                                  short-circuits its POST to avoid double
                                  writes during cutover.
                              The flag is read on every call so an operator
                              can flip ownership live without redeploying.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests

from .report_schema import (
    ALLOWED_SITE_SCORE_BANDS,
    DD_RECOMMENDATION_FROM_C_ANSWER,
    LEGACY_CAN_WE_ANSWER_ALIASES,
    site_score_band,
)
from .risk_flags import derive_risk_flags, normalize_caller_flags

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://dd-dashboard-three.vercel.app"
_DEFAULT_TIMEOUT_SEC = 15

# DD turn-time SLA: 14 days from Wrike record creation to report due.
# Drives the default dd_due_date when no explicit value is passed.
_DD_DUE_DATE_OFFSET_DAYS = 14

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def slugify(site_title: str) -> str:
    """Turn 'Palm Beach Gardens' into 'palm-beach-gardens'.

    Mirrors the dashboard's existing slugs — lowercase, hyphen-separated,
    alphanumeric only.
    """
    s = site_title.strip().lower()
    s = s.replace("&", "and")
    s = _SLUG_RE.sub("-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _parse_city_state_zip(address: str | None) -> tuple[str, str]:
    """Best-effort parse of 'Street, City, ST ZIP' → ('City, ST ZIP', 'ST')."""
    if not address:
        return "", ""
    parts = [p.strip() for p in address.split(",")]
    if len(parts) < 2:
        return "", ""
    tail = ", ".join(parts[1:]) if len(parts) > 1 else ""
    state = ""
    # Look for ' XX ' or ' XX,' two-letter USPS state.
    m = re.search(r"\b([A-Z]{2})\b(?:\s+\d{5})?", tail)
    if m:
        state = m.group(1)
    return tail, state


def _derive_dd_dates(
    *,
    explicit_commissioned: str | None,
    explicit_due: str | None,
    today: date | None = None,
) -> tuple[str | None, str | None]:
    """Derive (dd_commissioned_date, dd_due_date) for a publish call.

    Rules (per Greg, 4/25):
      - dd_commissioned_date == the date the DD report is first created.
        On every publish call we send today's date as a candidate. The
        dashboard's sticky-preserve transform locks the first non-empty
        value, so subsequent reruns cannot bump it forward.
      - dd_due_date == commissioned_date + 14 days (DD turn-time SLA).
      - An explicit caller-provided value always wins (back-dated
        manual rerun, custom due date).

    Returns (commissioned, due) as YYYY-MM-DD strings.

    Note on the sticky-preserve guarantee: even though we send today's
    date on every call, the dashboard side only writes it the first
    time. See `transformPipelineToSite` in dd-dashboard's
    api/_lib/transform.ts — dd_commissioned_date / dd_due_date
    fall through the `preserve()` helper.
    """
    today = today or date.today()

    # Caller-provided commissioned date short-circuits the today-default.
    commissioned = (explicit_commissioned or "").strip() or None
    if commissioned is None:
        commissioned = today.isoformat()

    # Caller-provided due date short-circuits the +14d default.
    due = (explicit_due or "").strip() or None
    if due is None:
        try:
            base = date.fromisoformat(commissioned)
            due = (base + timedelta(days=_DD_DUE_DATE_OFFSET_DAYS)).isoformat()
        except ValueError:
            # Malformed commissioned date — don't fabricate a due date.
            due = None

    return commissioned, due


def build_site_meta(
    site_title: str,
    *,
    address: str | None = None,
    school_type: str | None = None,
    drive_folder_url: str | None = None,
    dd_report_url: str | None = None,
    rebl_site_id: str | None = None,
    rebl_url: str | None = None,
    report_date: date | None = None,
    site_owner: str | None = None,
    # --- Phase 1 DD provenance fields (Rhodes data dictionary, 4/24) ---
    # All optional. Pipeline auto-runs leave them blank for now; explicit
    # callers (backfill scripts, future MCP integrations, manual reruns)
    # can populate them. Dashboard-side `transformPipelineToSite` uses a
    # sticky-preserve pattern so blanks here never overwrite a stored
    # value.
    dd_author: str | None = None,
    dd_owner: str | None = None,
    dd_version: str | None = None,
    dd_report_length: int | None = None,
    dd_commissioned_date: str | None = None,  # YYYY-MM-DD
    dd_due_date: str | None = None,  # YYYY-MM-DD
    # --- Phase 2 DD provenance fields (Rhodes data dictionary, 4/24) ---
    # All optional. Free-form strings on the wire so we can ship without
    # locking enum values; the data dictionary expects high/medium/low/
    # unknown for the two ratings and "in_progress"/"complete" for status.
    # Note: dd_recommendation here carries Go / No Go vocabulary derived from
    # the report's exec.c_answer (Yes → "go", No → "no_go"). It represents
    # "what the DD report concluded" and is distinct from the dashboard-side
    # decision override (approve/reject/info_req via the decision button),
    # which is layered on top in the UI by `effectiveDdStatusForSite`.
    school_feasibility: str | None = None,  # Wrike W74 (high/medium/low/unknown)
    timeline_confidence: str | None = None,  # Wrike W81 (high/medium/low/unknown)
    dd_status: str | None = None,  # in_progress | complete | follow_up
    dd_recommendation: str | None = None,  # "go" | "no_go" (derived from c_answer)
    # --- Phase 3 DD analytical fields (Rhodes data dictionary, 4/24) ---
    # dd_site_score is a 0–100 numeric derived from the E-Occupancy
    # rubric (`ease-of-conversion` skill, Phase 7). The publisher derives
    # it from `q2.e_occupancy_score` when not supplied. dd_site_score_band
    # is always derived from the score (green/yellow/orange/red) unless
    # explicitly overridden — keeping the two in sync.
    dd_site_score: int | None = None,
    dd_site_score_band: str | None = None,
    # --- Phase 4 DD analytical fields (Rhodes data dictionary, 4/25) ---
    # dd_risk_flags is a canonical, deduped list surfacing risk signals
    # from the four upstream archetypes (permit_history, e_occupancy IBC
    # gates, school_approval, SIR Risk Watch). When the caller does not
    # pass an explicit list, ``publish_to_dashboard`` derives it from the
    # report's token bag via ``derive_risk_flags``. Each entry has the
    # shape ``{category, severity, source, summary}`` — see
    # ``risk_flags.py`` for the canonical enums and severity rules.
    dd_risk_flags: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the `site_meta` payload from pipeline inputs.

    Everything optional falls back to sensible defaults.
    """
    slug = slugify(site_title)
    city_state_zip, state = _parse_city_state_zip(address)
    rd = (report_date or date.today()).isoformat()

    # Dashboard uses "K-8" / "Micro" / "250" / "1000"; pipeline normalizes to
    # "micro"/"250"/"1000". Map to dashboard-facing label.
    type_label_map = {
        "micro": "Microschool (25)",
        "250": "Growth (250)",
        "1000": "Flagship (1000)",
    }
    school_label = type_label_map.get(school_type or "", "K-8")

    payload: dict[str, Any] = {
        "slug": slug,
        "site_name": site_title,
        "marketing_name": f"Alpha School \u2014 {site_title}",
        "address": address or "",
        "city_state_zip": city_state_zip,
        "state": state,
        "school_type": school_label,
        "prepared_by": "DD Pipeline (auto)",
        "site_owner": (site_owner or "").strip(),
        "report_date": rd,
        "published_at": datetime.now(UTC).isoformat(),
        "drive_folder_url": drive_folder_url or "",
        "dd_report_url": dd_report_url or "",
        "rebl": {
            "site_id": rebl_site_id or "",
            "url": rebl_url or "",
        },
    }

    # Only include DD provenance keys when callers actually pass a value.
    # The dashboard's sticky-preserve logic treats omitted keys and blank
    # strings the same way, but omitting them keeps the wire payload tidy
    # and makes the diff in committed sites.json minimal during Phase 1
    # rollout (when most sites won't have these fields yet).
    if dd_author:
        payload["dd_author"] = dd_author.strip()
    if dd_owner:
        payload["dd_owner"] = dd_owner.strip()
    if dd_version:
        payload["dd_version"] = dd_version.strip()
    if isinstance(dd_report_length, int) and dd_report_length >= 0:
        payload["dd_report_length"] = dd_report_length
    if dd_commissioned_date:
        payload["dd_commissioned_date"] = dd_commissioned_date.strip()
    if dd_due_date:
        payload["dd_due_date"] = dd_due_date.strip()

    # Phase 2: same omit-when-unset semantics as Phase 1. Strip first so
    # whitespace-only inputs ("   ") are treated as unset and don't slip
    # blank values onto the dashboard's stored record.
    if school_feasibility and school_feasibility.strip():
        payload["school_feasibility"] = school_feasibility.strip()
    if timeline_confidence and timeline_confidence.strip():
        payload["timeline_confidence"] = timeline_confidence.strip()
    if dd_status and dd_status.strip():
        payload["dd_status"] = dd_status.strip()
    if dd_recommendation and dd_recommendation.strip():
        payload["dd_recommendation"] = dd_recommendation.strip().lower()

    # Phase 3: dd_site_score + band. Score is the source of truth; band is
    # derived from score unless caller supplies a *valid* explicit band,
    # in which case caller-wins (handy for backfill scripts setting band
    # only). Score is omitted when None or out of range. An invalid
    # caller-supplied band is silently dropped and the derived band is
    # used instead, keeping the payload self-consistent.
    explicit_band: str | None = None
    if dd_site_score_band and dd_site_score_band.strip():
        candidate_band = dd_site_score_band.strip().lower()
        if candidate_band in ALLOWED_SITE_SCORE_BANDS:
            explicit_band = candidate_band

    if isinstance(dd_site_score, (int, float)):
        score_int = int(round(float(dd_site_score)))
        if 0 <= score_int <= 100:
            payload["dd_site_score"] = score_int
            if explicit_band:
                payload["dd_site_score_band"] = explicit_band
            else:
                # site_score_band() accepts the raw float so we don't lose
                # precision at band boundaries (e.g. 79.6 → yellow,
                # 80.0 → green) — even though we stored the rounded int.
                derived_band = site_score_band(dd_site_score)
                if derived_band:
                    payload["dd_site_score_band"] = derived_band
    elif explicit_band:
        # Backfill case: human-classified band with no numeric score.
        payload["dd_site_score_band"] = explicit_band

    # Phase 4: dd_risk_flags. Caller-wins is enforced upstream in
    # ``publish_to_dashboard`` (it passes a normalized list here, or
    # derives one from report_data). Validate again as belt-and-
    # suspenders so direct ``build_site_meta`` callers get the same
    # invalid-entry-drop semantics. Empty list is omitted from the
    # payload to keep the dashboard's sticky-preserve transform from
    # treating "no flags this run" as "clear all flags".
    if dd_risk_flags:
        normalized = normalize_caller_flags(dd_risk_flags)
        if normalized:
            payload["dd_risk_flags"] = normalized

    return payload


def publish_to_dashboard(
    site_title: str,
    report_data: dict[str, Any],
    *,
    address: str | None = None,
    school_type: str | None = None,
    drive_folder_url: str | None = None,
    dd_report_url: str | None = None,
    rebl_site_id: str | None = None,
    rebl_url: str | None = None,
    report_date: date | None = None,
    site_owner: str | None = None,
    # Phase 1 DD provenance pass-through. See build_site_meta() for semantics.
    dd_author: str | None = None,
    dd_owner: str | None = None,
    dd_version: str | None = None,
    dd_report_length: int | None = None,
    dd_commissioned_date: str | None = None,
    dd_due_date: str | None = None,
    # Phase 2 DD provenance pass-through. See build_site_meta() for semantics.
    school_feasibility: str | None = None,
    timeline_confidence: str | None = None,
    dd_status: str | None = None,
    dd_recommendation: str | None = None,
    # Phase 3 DD analytical pass-through. See build_site_meta() for semantics.
    dd_site_score: int | None = None,
    dd_site_score_band: str | None = None,
    # Phase 4 DD analytical pass-through. See build_site_meta() for semantics.
    dd_risk_flags: list[dict[str, Any]] | None = None,
    base_url: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SEC,
) -> bool:
    """Publish one site's report_data to the dashboard.

    Returns True on HTTP 200, False otherwise (including disabled,
    missing-secret, or pipeline-owns-this-hop cutover cases).
    """
    # Phase A5 cutover: when DASHBOARD_PUBLISH_OWNER=pipeline, the
    # alpha-dd-pipeline WU-15 is the sole publisher. Short-circuit before
    # any other work so reruns and backfills both honor the flag uniformly,
    # and so the reporter's call sites don't need to know who's currently
    # in charge. Default "reporter" preserves legacy behavior until soak
    # passes; "pipeline" is the only value that disables the POST.
    owner = os.environ.get("DASHBOARD_PUBLISH_OWNER", "reporter").strip().lower()
    if owner == "pipeline":
        logger.info(
            "DASHBOARD_PUBLISH_OWNER=pipeline; reporter is yielding dashboard "
            "publish to alpha-dd-pipeline WU-15 for %s",
            site_title,
        )
        return False

    if os.environ.get("DASHBOARD_PUBLISH_ENABLED", "1") == "0":
        logger.info("Dashboard publish disabled via env; skipping %s", site_title)
        return False

    secret = os.environ.get("DASHBOARD_PUBLISH_SECRET")
    if not secret:
        logger.warning(
            "DASHBOARD_PUBLISH_SECRET not set; skipping dashboard publish for %s",
            site_title,
        )
        return False

    url_base = (base_url or os.environ.get("DASHBOARD_PUBLISH_URL") or _DEFAULT_BASE_URL).rstrip("/")

    # dd_commissioned_date == the date the DD report is first created.
    # We send today's date on every publish; the dashboard's sticky-
    # preserve transform locks the first non-empty value so reruns
    # cannot bump it forward. dd_due_date == commissioned + 14 days.
    inferred_commissioned, inferred_due = _derive_dd_dates(
        explicit_commissioned=dd_commissioned_date,
        explicit_due=dd_due_date,
    )

    # Auto-stamp dd_status="complete" when this publisher is reached without
    # an explicit value. Rationale: publish_to_dashboard() is only called
    # from `_publish_to_dashboard_best_effort` after a successful pipeline
    # run, so by definition a successful POST means the report is complete.
    # Callers (backfill scripts, partial republish flows) can still pass
    # "in_progress" or another label explicitly to override.
    effective_dd_status = (dd_status or "").strip() or "complete"

    # Derive dd_recommendation (Go / No Go) from the report's c_answer
    # (Yes / No) when the caller did not supply an explicit value. The
    # report card stays plain-English; the dashboard chip uses the
    # Go/No Go vocabulary. We normalize via the legacy alias map so that
    # historical payloads (Go, Yes see notes, Conditional, etc.) still
    # derive correctly. If the c_answer is missing or unrecognized,
    # dd_recommendation is left unset and the dashboard falls back to
    # its own logic.
    effective_dd_recommendation = (dd_recommendation or "").strip().lower() or None
    if not effective_dd_recommendation:
        raw_c_answer = str(report_data.get("exec.c_answer") or "").strip()
        if raw_c_answer:
            canonical = LEGACY_CAN_WE_ANSWER_ALIASES.get(
                raw_c_answer.rstrip(".,;:?!").lower()
            )
            if canonical is None and raw_c_answer in {"Yes", "No"}:
                canonical = raw_c_answer
            if canonical:
                effective_dd_recommendation = DD_RECOMMENDATION_FROM_C_ANSWER.get(canonical)

    # Derive dd_site_score from the report's q2.e_occupancy_score token
    # when the caller did not supply an explicit value. The E-Occupancy
    # tool (`apply_e_occupancy_skill`) emits this token as part of its
    # standard output; we promote it to a top-level publisher field so
    # the dashboard can render a sortable score column without parsing
    # report_data on every read. Caller-wins precedence; non-numeric or
    # out-of-range values are silently dropped (publisher omits the field
    # and the dashboard's sticky-preserve transform keeps the prior value).
    effective_dd_site_score: int | None = None
    if isinstance(dd_site_score, (int, float)):
        try:
            candidate = int(round(float(dd_site_score)))
        except (TypeError, ValueError):
            candidate = None
        if candidate is not None and 0 <= candidate <= 100:
            effective_dd_site_score = candidate
    if effective_dd_site_score is None:
        raw_score = report_data.get("q2.e_occupancy_score")
        if raw_score is not None and str(raw_score).strip():
            try:
                candidate = int(round(float(str(raw_score).strip())))
            except (TypeError, ValueError):
                candidate = None
            if candidate is not None and 0 <= candidate <= 100:
                effective_dd_site_score = candidate

    # Derive dd_risk_flags from the report's token bag when the caller
    # did not supply an explicit list. Caller-wins precedence with the
    # same invalid-entry-drop semantics as Phase 3 (band): explicit
    # input is normalized via ``normalize_caller_flags``; if that yields
    # nothing usable, fall back to the derivation path so we don't ship
    # an empty-but-meant-to-be-something list. See ``risk_flags.py``
    # for the canonical enums and severity rules.
    effective_dd_risk_flags: list[dict[str, Any]] = []
    if dd_risk_flags:
        effective_dd_risk_flags = normalize_caller_flags(dd_risk_flags)
    if not effective_dd_risk_flags:
        effective_dd_risk_flags = derive_risk_flags(report_data)

    meta = build_site_meta(
        site_title,
        address=address,
        school_type=school_type,
        drive_folder_url=drive_folder_url,
        dd_report_url=dd_report_url,
        rebl_site_id=rebl_site_id or str(report_data.get("meta.rebl_site_id") or ""),
        rebl_url=rebl_url or str(report_data.get("sources.rebl_link") or ""),
        report_date=report_date,
        site_owner=site_owner,
        dd_author=dd_author,
        dd_owner=dd_owner,
        dd_version=dd_version,
        dd_report_length=dd_report_length,
        dd_commissioned_date=inferred_commissioned,
        dd_due_date=inferred_due,
        school_feasibility=school_feasibility,
        timeline_confidence=timeline_confidence,
        dd_status=effective_dd_status,
        dd_recommendation=effective_dd_recommendation,
        dd_site_score=effective_dd_site_score,
        dd_site_score_band=dd_site_score_band,
        dd_risk_flags=effective_dd_risk_flags,
    )
    slug = meta["slug"]

    endpoint = f"{url_base}/api/sites/{slug}/publish"
    payload = {"site_meta": meta, "report_data": report_data}

    try:
        r = requests.post(
            endpoint,
            json=payload,
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
    except requests.RequestException as e:
        logger.warning("Dashboard publish failed for %s (network): %s", site_title, e)
        return False

    if r.status_code == 200:
        try:
            data = r.json()
            logger.info(
                "Dashboard publish OK for %s (%s) → slug=%s",
                site_title,
                data.get("action"),
                slug,
            )
        except ValueError:
            logger.info("Dashboard publish OK for %s → slug=%s", site_title, slug)
        return True

    logger.warning(
        "Dashboard publish HTTP %d for %s: %s",
        r.status_code,
        site_title,
        r.text[:200],
    )
    return False
