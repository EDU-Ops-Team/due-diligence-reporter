"""Tests for the P1 assignment engine (assignment.py)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from due_diligence_reporter.assignment import (
    AssignmentResult,
    TeamMember,
    assign_p1,
    check_same_day_viable,
    haversine_km,
    load_team_members,
    rule1_assign,
    rule2_assign,
    rule3_assign,
    build_site_counts,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _member(
    name: str = "Alice",
    email: str = "alice@trilogy.com",
    home_airport: str = "AUS",
    home_state: str = "TX",
    preferred_airline: str = "",
    strongly_preferred_airline: str = "",
) -> TeamMember:
    return TeamMember(
        name=name,
        email=email,
        home_airport=home_airport,
        home_state=home_state,
        preferred_airline=preferred_airline,
        strongly_preferred_airline=strongly_preferred_airline,
    )


def _settings(p1_team_config: str = "", serpapi_key: str = "") -> MagicMock:
    s = MagicMock()
    s.p1_team_config = p1_team_config
    s.serpapi_key = serpapi_key
    return s


# ---------------------------------------------------------------------------
# load_team_members
# ---------------------------------------------------------------------------


class TestLoadTeamMembers:
    def test_empty_config(self):
        assert load_team_members(_settings()) == []

    def test_parses_valid_json(self):
        cfg = '[{"name":"Bob","email":"bob@x.com","home_airport":"DEN","home_state":"CO"}]'
        members = load_team_members(_settings(p1_team_config=cfg))
        assert len(members) == 1
        assert members[0].name == "Bob"
        assert members[0].home_state == "CO"

    def test_optional_airline_fields_default_empty(self):
        cfg = '[{"name":"Bob","email":"bob@x.com","home_airport":"DEN","home_state":"CO"}]'
        members = load_team_members(_settings(p1_team_config=cfg))
        assert members[0].preferred_airline == ""
        assert members[0].strongly_preferred_airline == ""

    def test_invalid_json_returns_empty(self):
        members = load_team_members(_settings(p1_team_config="{bad json"))
        assert members == []

    def test_skips_entries_missing_required_fields(self):
        cfg = '[{"name":"Bad"}]'  # missing email, home_airport, home_state
        members = load_team_members(_settings(p1_team_config=cfg))
        assert members == []


# ---------------------------------------------------------------------------
# check_same_day_viable
# ---------------------------------------------------------------------------


def _make_flight(depart_time: str, total_min: int, airlines: list[str] = None) -> dict:
    """Build a minimal SerpAPI-shaped flight group."""
    legs = [{"departure_airport": {"time": depart_time}, "arrival_airport": {"time": ""}, "airline": a}
            for a in (airlines or ["United"])]
    return {"flights": legs, "total_duration": total_min}


class TestSameDayViable:
    def test_viable_when_early_out_and_late_return(self):
        outbound = [_make_flight("2026-04-24 06:30", 150)]
        return_ = [_make_flight("2026-04-24 20:30", 150)]
        assert check_same_day_viable(outbound, return_) is True

    def test_not_viable_late_outbound(self):
        outbound = [_make_flight("2026-04-24 10:00", 150)]
        return_ = [_make_flight("2026-04-24 20:30", 150)]
        assert check_same_day_viable(outbound, return_) is False

    def test_not_viable_early_return(self):
        outbound = [_make_flight("2026-04-24 06:30", 150)]
        return_ = [_make_flight("2026-04-24 17:00", 150)]
        assert check_same_day_viable(outbound, return_) is False

    def test_not_viable_long_flight(self):
        outbound = [_make_flight("2026-04-24 06:30", 240)]  # 4hr > 3hr max
        return_ = [_make_flight("2026-04-24 20:30", 150)]
        assert check_same_day_viable(outbound, return_) is False

    def test_exactly_7am_departure_is_viable(self):
        outbound = [_make_flight("2026-04-24 07:00", 150)]
        return_ = [_make_flight("2026-04-24 20:00", 150)]
        assert check_same_day_viable(outbound, return_) is True

    def test_empty_flights_not_viable(self):
        assert check_same_day_viable([], []) is False


# ---------------------------------------------------------------------------
# haversine_km
# ---------------------------------------------------------------------------


class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_km(30.0, -97.0, 30.0, -97.0) == pytest.approx(0.0, abs=1.0)

    def test_austin_to_dallas_approx_300km(self):
        # Austin TX (30.27, -97.74) → Dallas TX (32.78, -96.80)
        dist = haversine_km(30.27, -97.74, 32.78, -96.80)
        assert 280 < dist < 320

    def test_austin_to_miami_approx_2000km(self):
        dist = haversine_km(30.27, -97.74, 25.77, -80.19)
        assert 1700 < dist < 2200


# ---------------------------------------------------------------------------
# Rule 2 — same state
# ---------------------------------------------------------------------------


class TestRule2:
    def test_finds_member_in_state(self):
        alice = _member("Alice", "alice@t.com", home_state="TX")
        bob = _member("Bob", "bob@t.com", home_state="CO")
        result = rule2_assign([alice, bob], "TX", {})
        assert result is not None
        assert result.assignee.email == "alice@t.com"
        assert result.rule == "rule2"

    def test_no_match_returns_none(self):
        alice = _member("Alice", "alice@t.com", home_state="CA")
        result = rule2_assign([alice], "TX", {})
        assert result is None

    def test_fewest_sites_wins(self):
        alice = _member("Alice", "alice@t.com", home_state="TX")
        bob = _member("Bob", "bob@t.com", home_state="TX")
        counts = {"alice@t.com": 10, "bob@t.com": 2}
        result = rule2_assign([alice, bob], "TX", counts)
        assert result.assignee.email == "bob@t.com"

    def test_state_case_insensitive(self):
        alice = _member("Alice", "alice@t.com", home_state="TX")
        result = rule2_assign([alice], "tx", {})
        assert result is not None


# ---------------------------------------------------------------------------
# Rule 3 — nearest state
# ---------------------------------------------------------------------------


class TestRule3:
    def test_nearest_member_wins(self):
        # TX is adjacent to LA, far from WA
        la_member = _member("Alice", "alice@t.com", home_state="LA")
        wa_member = _member("Bob", "bob@t.com", home_state="WA")
        result = rule3_assign([la_member, wa_member], "TX", {})
        assert result is not None
        assert result.assignee.email == "alice@t.com"
        assert result.rule == "rule3"

    def test_returns_none_for_unknown_state(self):
        alice = _member("Alice", "alice@t.com", home_state="TX")
        result = rule3_assign([alice], "ZZ", {})
        assert result is None

    def test_fewest_sites_tiebreaker_within_band(self):
        # Target KS. NE (~250km) and MO (~370km) are both within 500km of min distance.
        ne = _member("Alice", "alice@t.com", home_state="NE")
        mo = _member("Bob", "bob@t.com", home_state="MO")
        counts = {"alice@t.com": 8, "bob@t.com": 1}
        result = rule3_assign([ne, mo], "KS", counts)
        # NE is closest but has 8 sites; MO within 500km band — Bob wins on load
        assert result.assignee.email == "bob@t.com"


# ---------------------------------------------------------------------------
# assign_p1 — top-level
# ---------------------------------------------------------------------------


class TestAssignP1:
    def _settings_with_team(self, serpapi_key: str = "") -> MagicMock:
        team = '[{"name":"Alice","email":"alice@t.com","home_airport":"AUS","home_state":"TX"}]'
        return _settings(p1_team_config=team, serpapi_key=serpapi_key)

    def test_growth_type_auto_assigns(self):
        result = assign_p1("250", "Austin", "TX", _settings(), [], MagicMock())
        assert result["rule"] == "auto"
        assert result["status"] == "assigned"

    def test_flagship_auto_assigns(self):
        result = assign_p1("flagship", "Austin", "TX", _settings(), [], MagicMock())
        assert result["rule"] == "auto"

    def test_jc_fisher_excluded(self):
        result = assign_p1("jc fisher", "Austin", "TX", _settings(), [], MagicMock())
        assert result["status"] == "excluded"

    def test_no_team_config_returns_error(self):
        result = assign_p1("micro", "Austin", "TX", _settings(), [], MagicMock())
        assert result["status"] == "error"

    def test_rule2_used_when_no_city(self):
        settings = self._settings_with_team()
        result = assign_p1("micro", "", "TX", settings, [], MagicMock())
        assert result["rule"] == "rule2"
        assert result["assignee_email"] == "alice@t.com"

    def test_rule3_used_when_no_state_match(self):
        settings = self._settings_with_team()
        # Alice is in TX; target is CO — no same-state match, falls to Rule 3
        result = assign_p1("micro", "", "CO", settings, [], MagicMock())
        assert result["rule"] == "rule3"
        assert result["assignee_email"] == "alice@t.com"

    def test_rule1_skipped_when_no_serpapi_key(self):
        settings = self._settings_with_team(serpapi_key="")
        with patch("due_diligence_reporter.assignment.rule1_assign") as mock_r1:
            assign_p1("micro", "Austin", "TX", settings, [], MagicMock())
            mock_r1.assert_not_called()

    def test_rule1_called_when_city_and_serpapi_key_set(self):
        settings = self._settings_with_team(serpapi_key="sk_test")
        with patch("due_diligence_reporter.assignment.rule1_assign", return_value=None) as mock_r1:
            assign_p1("micro", "Austin", "TX", settings, [], MagicMock())
            mock_r1.assert_called_once()


# ---------------------------------------------------------------------------
# build_site_counts
# ---------------------------------------------------------------------------


class TestBuildSiteCounts:
    def test_counts_p1_per_email(self):
        cfg = MagicMock()
        records = [{"id": "1"}, {"id": "2"}, {"id": "3"}]

        def fake_extract(record, *, cfg):
            mapping = {
                "1": {"name": "Alice", "email": "alice@t.com"},
                "2": {"name": "Alice", "email": "alice@t.com"},
                "3": {"name": "Bob", "email": "bob@t.com"},
            }
            return mapping.get(record["id"])

        with patch("due_diligence_reporter.assignment.extract_p1_from_record", side_effect=fake_extract, create=True):
            counts = build_site_counts(records, cfg)

        assert counts["alice@t.com"] == 2
        assert counts["bob@t.com"] == 1

    def test_skips_records_without_p1(self):
        cfg = MagicMock()
        records = [{"id": "1"}]
        with patch("due_diligence_reporter.assignment.extract_p1_from_record", return_value=None, create=True):
            counts = build_site_counts(records, cfg)
        assert counts == {}
