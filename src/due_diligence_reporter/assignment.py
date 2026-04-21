"""P1 Accountable assignment engine.

Scoring (Rule 1 — flight-based):
  Same-day viable (depart ≤ 7am, return ≥ 8pm, ≤ 3hr each way)  +50
  Nonstop flight                                                   +30
  Strongly preferred airline available                             +15
  Strongly preferred airline NOT available                         -30
  Preferred airline available                                      +10
  Load penalty per existing assigned site                          -5

Rule 2: Contact in same state → fewest sites wins.
Rule 3: Nearest state (Haversine) → fewest sites tiebreaker.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import requests

from .config import Settings
from .wrike import extract_p1_from_record

logger = logging.getLogger("[assignment]")

# ---------------------------------------------------------------------------
# Auto-assign school types
# ---------------------------------------------------------------------------

GROWTH_FLAGSHIP_TYPES = {"250", "1000", "growth", "flagship"}
EXCLUDED_TYPES = {"jc fisher", "jc_fisher"}

AUTO_ASSIGN_EMAILS = ["thomas.barrow@trilogy.com", "israe.zizaoui@trilogy.com"]


def _parse_disabled_values(raw: str) -> set[str]:
    """Return lowercase CSV values with surrounding whitespace removed."""
    return {
        value.strip().lower()
        for value in raw.split(",")
        if value.strip()
    }


def _is_disabled_member(
    entry: dict[str, Any],
    disabled_names: set[str],
    disabled_emails: set[str],
) -> bool:
    """Return True when a team-config entry is explicitly excluded."""
    email = str(entry.get("email", "")).strip().lower()
    if email and email in disabled_emails:
        return True

    name = " ".join(str(entry.get("name", "")).strip().lower().split())
    if not name:
        return False
    return any(disabled_name in name for disabled_name in disabled_names)

# ---------------------------------------------------------------------------
# US state centroids (lat, lon) for Rule 3 Haversine
# ---------------------------------------------------------------------------

STATE_CENTROIDS: dict[str, tuple[float, float]] = {
    "AL": (32.806671, -86.791130), "AK": (61.370716, -152.404419),
    "AZ": (33.729759, -111.431221), "AR": (34.969704, -92.373123),
    "CA": (36.116203, -119.681564), "CO": (39.059811, -105.311104),
    "CT": (41.597782, -72.755371), "DE": (39.318523, -75.507141),
    "FL": (27.766279, -81.686783), "GA": (33.040619, -83.643074),
    "HI": (21.094318, -157.498337), "ID": (44.240459, -114.478828),
    "IL": (40.349457, -88.986137), "IN": (39.849426, -86.258278),
    "IA": (42.011539, -93.210526), "KS": (38.526600, -96.726486),
    "KY": (37.668140, -84.670067), "LA": (31.169960, -91.867805),
    "ME": (44.693947, -69.381927), "MD": (39.063946, -76.802101),
    "MA": (42.230171, -71.530106), "MI": (43.326618, -84.536095),
    "MN": (45.694454, -93.900192), "MS": (32.741646, -89.678696),
    "MO": (38.456085, -92.288368), "MT": (46.921925, -110.454353),
    "NE": (41.125370, -98.268082), "NV": (38.313515, -117.055374),
    "NH": (43.452492, -71.563896), "NJ": (40.298904, -74.521011),
    "NM": (34.840515, -106.248482), "NY": (42.165726, -74.948051),
    "NC": (35.630066, -79.806419), "ND": (47.528912, -99.784012),
    "OH": (40.388783, -82.764915), "OK": (35.565342, -96.928917),
    "OR": (44.572021, -122.070938), "PA": (40.590752, -77.209755),
    "RI": (41.680893, -71.511780), "SC": (33.856892, -80.945007),
    "SD": (44.299782, -99.438828), "TN": (35.747845, -86.692345),
    "TX": (31.054487, -97.563461), "UT": (40.150032, -111.862434),
    "VT": (44.045876, -72.710686), "VA": (37.769337, -78.169968),
    "WA": (47.400902, -121.490494), "WV": (38.491226, -80.954453),
    "WI": (44.268543, -89.616508), "WY": (42.755966, -107.302490),
    "DC": (38.897438, -77.026817),
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TeamMember:
    name: str
    email: str
    home_airport: str
    home_state: str
    preferred_airline: str = ""
    strongly_preferred_airline: str = ""


@dataclass
class AssignmentResult:
    assignee: TeamMember | None
    rule: str  # "auto", "rule1", "rule2", "rule3", "none"
    score: float
    reasoning: str
    co_assignee: TeamMember | None = None  # for Growth/Flagship auto-assign


# ---------------------------------------------------------------------------
# Team config loader
# ---------------------------------------------------------------------------


def load_team_members(settings: Settings) -> list[TeamMember]:
    """Parse P1_TEAM_CONFIG JSON into TeamMember list."""
    raw = (settings.p1_team_config or "").strip()
    if not raw:
        return []
    disabled_names = _parse_disabled_values(getattr(settings, "p1_disabled_names", "Andrea"))
    disabled_emails = _parse_disabled_values(getattr(settings, "p1_disabled_emails", ""))
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Invalid P1_TEAM_CONFIG JSON: %s", e)
        return []
    members = []
    for entry in entries:
        try:
            if _is_disabled_member(entry, disabled_names, disabled_emails):
                logger.info(
                    "Skipping disabled team member from assignment pool: %s <%s>",
                    entry.get("name", ""),
                    entry.get("email", ""),
                )
                continue
            members.append(
                TeamMember(
                    name=entry["name"],
                    email=entry["email"],
                    home_airport=entry["home_airport"],
                    home_state=entry.get("home_state", "").upper(),
                    preferred_airline=entry.get("preferred_airline", ""),
                    strongly_preferred_airline=entry.get("strongly_preferred_airline", ""),
                )
            )
        except KeyError as e:
            logger.warning("Skipping team member entry missing field %s: %s", e, entry)
    return members


# ---------------------------------------------------------------------------
# Wrike load counter
# ---------------------------------------------------------------------------


def build_site_counts(all_site_records: list[dict[str, Any]], cfg: Any) -> dict[str, int]:
    """Count active sites per P1 email from already-fetched Wrike records."""
    counts: dict[str, int] = {}
    for record in all_site_records:
        p1 = extract_p1_from_record(record, cfg=cfg)
        if p1 and p1.get("email"):
            key = p1["email"].lower()
            counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Flight search via SerpAPI
# ---------------------------------------------------------------------------


def _search_one_way(api_key: str, origin: str, dest: str, travel_date: str) -> list[dict[str, Any]]:
    """Search one-way flights via SerpAPI Google Flights engine."""
    resp = requests.get(
        "https://serpapi.com/search",
        params={
            "engine": "google_flights",
            "departure_id": origin,
            "arrival_id": dest,
            "outbound_date": travel_date,
            "type": "2",  # one-way
            "api_key": api_key,
            "hl": "en",
            "currency": "USD",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("best_flights", []) + data.get("other_flights", [])


def _departure_hour(flight_group: dict[str, Any]) -> float | None:
    """Extract departure hour (0-24) from SerpAPI flight group."""
    try:
        legs = flight_group.get("flights", [])
        if not legs:
            return None
        time_str = legs[0].get("departure_airport", {}).get("time", "")
        # Format: "2026-04-24 06:30"
        parts = time_str.split(" ")
        if len(parts) < 2:
            return None
        h, m = parts[1].split(":")[:2]
        return int(h) + int(m) / 60
    except Exception:
        return None


def _arrival_hour(flight_group: dict[str, Any]) -> float | None:
    """Extract arrival hour from the last leg of a SerpAPI flight group."""
    try:
        legs = flight_group.get("flights", [])
        if not legs:
            return None
        time_str = legs[-1].get("arrival_airport", {}).get("time", "")
        parts = time_str.split(" ")
        if len(parts) < 2:
            return None
        h, m = parts[1].split(":")[:2]
        return int(h) + int(m) / 60
    except Exception:
        return None


def _total_duration_min(flight_group: dict[str, Any]) -> int:
    """Return total flight duration in minutes from SerpAPI group."""
    return flight_group.get("total_duration", 9999)


def _airlines_in_group(flight_group: dict[str, Any]) -> set[str]:
    """Return lowercase set of airline names in a flight group."""
    names: set[str] = set()
    for leg in flight_group.get("flights", []):
        airline = leg.get("airline", "")
        if airline:
            names.add(airline.lower())
    return names


def check_same_day_viable(
    outbound: list[dict[str, Any]],
    return_: list[dict[str, Any]],
    max_leg_minutes: int = 180,
) -> bool:
    """Return True if there is a same-day viable outbound+return pair.

    Outbound: departs home ≤ 7:00am, ≤ 3hr flight.
    Return: departs site ≥ 20:00 (8pm), ≤ 3hr flight.
    """
    has_valid_out = any(
        (dep := _departure_hour(f)) is not None
        and dep <= 7.0
        and _total_duration_min(f) <= max_leg_minutes
        for f in outbound
    )
    has_valid_ret = any(
        (dep := _departure_hour(f)) is not None
        and dep >= 20.0
        and _total_duration_min(f) <= max_leg_minutes
        for f in return_
    )
    return has_valid_out and has_valid_ret


def score_member_flights(
    member: TeamMember,
    dest_city_state: str,
    site_count: int,
    serpapi_key: str,
    travel_date: str,
) -> tuple[float, str]:
    """Score a member using Rule 1. Returns (score, reasoning)."""
    score = float(-site_count * 5)
    notes: list[str] = [f"load penalty {site_count} sites → {-site_count * 5}"]

    try:
        outbound = _search_one_way(serpapi_key, member.home_airport, dest_city_state, travel_date)
        return_ = _search_one_way(serpapi_key, dest_city_state, member.home_airport, travel_date)
    except Exception as e:
        logger.warning("SerpAPI failed for %s (%s→%s): %s", member.name, member.home_airport, dest_city_state, e)
        return score, f"flight search failed: {e}"

    if not outbound:
        return score, "no outbound flights found"

    # Same-day viability
    if check_same_day_viable(outbound, return_):
        score += 50
        notes.append("same-day viable +50")

    # Nonstop
    all_flights = outbound + return_
    has_nonstop = any(len(fg.get("flights", [])) == 1 for fg in all_flights)
    if has_nonstop:
        score += 30
        notes.append("nonstop available +30")

    # Airline scoring
    all_airlines: set[str] = set()
    for fg in all_flights:
        all_airlines |= _airlines_in_group(fg)

    if member.strongly_preferred_airline:
        if member.strongly_preferred_airline.lower() in all_airlines:
            score += 15
            notes.append(f"strongly preferred airline ({member.strongly_preferred_airline}) available +15")
        else:
            score -= 30
            notes.append(f"strongly preferred airline ({member.strongly_preferred_airline}) not available -30")

    if member.preferred_airline:
        preferred_set = {a.strip().lower() for a in member.preferred_airline.split(",") if a.strip()}
        matched = preferred_set & all_airlines
        if matched:
            score += 10
            notes.append(f"preferred airline ({', '.join(sorted(matched))}) available +10")

    return score, "; ".join(notes)


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Assignment rules
# ---------------------------------------------------------------------------


def _pick_fewest_sites(
    candidates: list[TeamMember], counts: dict[str, int]
) -> TeamMember:
    """From candidates, return the one with fewest currently assigned sites."""
    return min(candidates, key=lambda m: counts.get(m.email.lower(), 0))


def rule1_assign(
    members: list[TeamMember],
    dest_city_state: str,
    counts: dict[str, int],
    serpapi_key: str,
) -> AssignmentResult | None:
    """Rule 1: Score all members by flight viability. Return winner if any score > -inf."""
    travel_date = (date.today() + timedelta(days=7)).isoformat()
    scores: list[tuple[TeamMember, float, str]] = []

    for member in members:
        score, reasoning = score_member_flights(
            member, dest_city_state, counts.get(member.email.lower(), 0), serpapi_key, travel_date
        )
        scores.append((member, score, reasoning))
        logger.info("Rule 1 score — %s: %.1f (%s)", member.name, score, reasoning)

    if not scores:
        return None

    best_member, best_score, best_reasoning = max(scores, key=lambda t: t[1])

    # Only assign if there's at least one viable option (score above pure-load-penalty floor)
    load_floor = -counts.get(best_member.email.lower(), 0) * 5
    if best_score <= load_floor and best_score < 0:
        logger.info("Rule 1 found no viable winner (best score %.1f)", best_score)
        return None

    return AssignmentResult(
        assignee=best_member,
        rule="rule1",
        score=best_score,
        reasoning=f"Rule 1 (flight score): {best_reasoning}",
    )


def rule2_assign(
    members: list[TeamMember],
    target_state: str,
    counts: dict[str, int],
) -> AssignmentResult | None:
    """Rule 2: Assign contact in same state with fewest sites."""
    state_upper = target_state.upper().strip()
    in_state = [m for m in members if m.home_state == state_upper]
    if not in_state:
        return None
    winner = _pick_fewest_sites(in_state, counts)
    site_count = counts.get(winner.email.lower(), 0)
    return AssignmentResult(
        assignee=winner,
        rule="rule2",
        score=float(-site_count * 5),
        reasoning=f"Rule 2 (same state {state_upper}): {winner.name} has {site_count} sites",
    )


def rule3_assign(
    members: list[TeamMember],
    target_state: str,
    counts: dict[str, int],
) -> AssignmentResult | None:
    """Rule 3: Nearest state by Haversine, fewest sites as tiebreaker."""
    dest_coords = STATE_CENTROIDS.get(target_state.upper().strip())
    if dest_coords is None:
        logger.warning("No centroid for state '%s' — Rule 3 cannot run", target_state)
        return None

    # Score each member by distance to target state
    scored: list[tuple[TeamMember, float]] = []
    for member in members:
        home_coords = STATE_CENTROIDS.get(member.home_state)
        if home_coords is None:
            logger.warning("No centroid for %s home state '%s' — skipping", member.name, member.home_state)
            continue
        dist = haversine_km(home_coords[0], home_coords[1], dest_coords[0], dest_coords[1])
        scored.append((member, dist))

    if not scored:
        return None

    min_dist = min(d for _, d in scored)
    # Allow up to 500km tie band → pick fewest sites within that band
    tie_threshold = 500.0
    near_members = [m for m, d in scored if d <= min_dist + tie_threshold]
    winner = _pick_fewest_sites(near_members, counts)
    site_count = counts.get(winner.email.lower(), 0)
    win_dist = next(d for m, d in scored if m.email == winner.email)

    return AssignmentResult(
        assignee=winner,
        rule="rule3",
        score=float(-site_count * 5),
        reasoning=(
            f"Rule 3 (nearest state): {winner.name} — "
            f"{win_dist:.0f}km from {target_state}, {site_count} sites"
        ),
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def assign_p1(
    school_type: str,
    city: str,
    state: str,
    settings: Settings,
    all_site_records: list[dict[str, Any]],
    wrike_cfg: Any,
) -> dict[str, Any]:
    """Run the P1 assignment engine and return the assignment decision.

    Returns dict with keys: assignee_name, assignee_email, co_assignee_name,
    co_assignee_email, rule, score, reasoning, status.
    """
    school_type_lower = school_type.lower().strip()

    # ── Auto-assign for Growth / Flagship ────────────────────────────────
    if school_type_lower in GROWTH_FLAGSHIP_TYPES:
        logger.info("Auto-assigning Growth/Flagship: %s", AUTO_ASSIGN_EMAILS)
        return {
            "status": "assigned",
            "rule": "auto",
            "assignee_email": AUTO_ASSIGN_EMAILS[0],
            "co_assignee_email": AUTO_ASSIGN_EMAILS[1],
            "reasoning": f"Growth/Flagship auto-assign: {', '.join(AUTO_ASSIGN_EMAILS)}",
        }

    # ── Excluded types ────────────────────────────────────────────────────
    if school_type_lower in EXCLUDED_TYPES:
        logger.info("School type '%s' excluded from P1 assignment", school_type)
        return {"status": "excluded", "rule": "none", "reasoning": f"School type '{school_type}' is excluded"}

    # ── Load team and site counts ─────────────────────────────────────────
    members = load_team_members(settings)
    if not members:
        return {
            "status": "error",
            "rule": "none",
            "reasoning": "P1_TEAM_CONFIG not set or empty — no eligible contacts",
        }

    counts = build_site_counts(all_site_records, wrike_cfg)

    # ── Rule 1: Flight scoring ────────────────────────────────────────────
    if city and settings.serpapi_key:
        dest = f"{city}, {state}"
        result = rule1_assign(members, dest, counts, settings.serpapi_key)
        if result and result.assignee:
            return _result_to_dict(result)

    # ── Rule 2: Same state ────────────────────────────────────────────────
    result = rule2_assign(members, state, counts)
    if result and result.assignee:
        return _result_to_dict(result)

    # ── Rule 3: Nearest state ─────────────────────────────────────────────
    result = rule3_assign(members, state, counts)
    if result and result.assignee:
        return _result_to_dict(result)

    return {"status": "no_match", "rule": "none", "reasoning": "No eligible contact found via any rule"}


def _result_to_dict(result: AssignmentResult) -> dict[str, Any]:
    return {
        "status": "assigned",
        "rule": result.rule,
        "assignee_name": result.assignee.name if result.assignee else None,
        "assignee_email": result.assignee.email if result.assignee else None,
        "score": result.score,
        "reasoning": result.reasoning,
    }
