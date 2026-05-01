"""Tests for the rebl-migration recovery script's pure-logic helpers.

The HTTP / OAuth / Wrike paths are exercised end-to-end by manual workflow
runs; here we cover only the slug-matching and wipe-detection helpers so
the regression boundary is well-defined without hitting the network.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

import recover_migration_wiped_sites as recover  # noqa: E402


# ---------------------------------------------------------------------------
# _is_migration_wiped


class TestIsMigrationWiped:
    """Wipe signature: dd_status='complete' AND can_we_open blank.

    These two conditions together uniquely identify the rebl-slug-migration
    casualties — site_meta survived the round-trip but every report_data-
    derived field (can_we_open, scenarios, sources.*) blanked out. Healthy
    complete sites always carry a non-empty c_answer-derived can_we_open.
    """

    def test_complete_with_blank_can_we_open_is_wiped(self) -> None:
        site = {"dd_status": "complete", "can_we_open": ""}
        assert recover._is_migration_wiped(site) is True

    def test_complete_with_whitespace_only_can_we_open_is_wiped(self) -> None:
        site = {"dd_status": "complete", "can_we_open": "   "}
        assert recover._is_migration_wiped(site) is True

    def test_complete_with_populated_can_we_open_is_healthy(self) -> None:
        site = {"dd_status": "complete", "can_we_open": "Yes"}
        assert recover._is_migration_wiped(site) is False

    def test_not_ready_stub_is_not_wiped(self) -> None:
        # not_ready stubs from sync_site_roster always have blank
        # can_we_open — they never got a DD report. They aren't wipe
        # casualties; recovery should leave them alone.
        site = {"dd_status": "not_ready", "can_we_open": ""}
        assert recover._is_migration_wiped(site) is False

    def test_in_progress_is_not_wiped(self) -> None:
        site = {"dd_status": "in_progress", "can_we_open": ""}
        assert recover._is_migration_wiped(site) is False

    def test_missing_dd_status_is_not_wiped(self) -> None:
        # The 4 stuck-empty rows have no dd_status at all. Recovery must
        # not mistake them for migration casualties.
        site = {"can_we_open": ""}
        assert recover._is_migration_wiped(site) is False

    def test_dd_status_case_insensitive(self) -> None:
        site = {"dd_status": "COMPLETE", "can_we_open": ""}
        assert recover._is_migration_wiped(site) is True


# ---------------------------------------------------------------------------
# _match_wrike_to_broken_sites


class TestMatchWrikeToBrokenSites:
    """Match Wrike active records to broken dashboard slugs via Rebl.

    The dashboard slug after migration is the Rebl canonical id, so an
    address resolved through Rebl produces the same slug as the live
    record. Mismatch on Rebl resolution = no recovery candidate.
    """

    def _make_record(self, address: str, title: str = "Site") -> dict:
        # Minimal Wrike-record shape that extract_address_from_record can
        # parse. Real records carry Wrike's customFields blob; the helper
        # walks it to find the address. We stub the helper instead.
        return {"title": title, "_test_address": address}

    def test_matches_records_whose_rebl_slug_is_broken(self) -> None:
        broken = {
            "421-e-11th-st-tulsa-ok": {"slug": "421-e-11th-st-tulsa-ok"},
            "1726-whitley-ave-los-angeles-ca": {"slug": "1726-whitley-ave-los-angeles-ca"},
        }
        records = [
            self._make_record("421 E 11th St, Tulsa, OK"),
            self._make_record("1726 Whitley Ave, Los Angeles, CA"),
            self._make_record("999 Healthy Way, Austin, TX"),  # not broken
        ]

        def fake_extract_address(rec: dict) -> str:
            return rec["_test_address"]

        def fake_rebl(addr: str, fallback: str = "") -> str:
            return {
                "421 E 11th St, Tulsa, OK": "421-e-11th-st-tulsa-ok",
                "1726 Whitley Ave, Los Angeles, CA": "1726-whitley-ave-los-angeles-ca",
                "999 Healthy Way, Austin, TX": "999-healthy-way-austin-tx",
            }.get(addr, fallback)

        with patch.object(recover, "extract_address_from_record", side_effect=fake_extract_address), \
             patch.object(recover, "canonical_slug_for_address", side_effect=fake_rebl):
            pairs = recover._match_wrike_to_broken_sites(records, broken)

        assert len(pairs) == 2
        slugs = {slug for _, slug in pairs}
        assert slugs == {"421-e-11th-st-tulsa-ok", "1726-whitley-ave-los-angeles-ca"}

    def test_skips_records_with_blank_address(self) -> None:
        broken = {"any-slug": {"slug": "any-slug"}}
        records = [self._make_record("")]

        with patch.object(recover, "extract_address_from_record", return_value=""), \
             patch.object(recover, "canonical_slug_for_address", return_value="any-slug"):
            pairs = recover._match_wrike_to_broken_sites(records, broken)

        assert pairs == []

    def test_skips_records_rebl_cannot_resolve(self) -> None:
        # Rebl returns "" (the fallback) when it can't find the address.
        # Such a record can't be matched to any broken slug.
        broken = {"any-slug": {"slug": "any-slug"}}
        records = [self._make_record("Unknown Address")]

        with patch.object(recover, "extract_address_from_record", return_value="Unknown Address"), \
             patch.object(recover, "canonical_slug_for_address", return_value=""):
            pairs = recover._match_wrike_to_broken_sites(records, broken)

        assert pairs == []

    def test_returns_empty_when_no_broken_sites(self) -> None:
        records = [self._make_record("123 Main St, Austin, TX")]

        with patch.object(recover, "extract_address_from_record", return_value="123 Main St, Austin, TX"), \
             patch.object(recover, "canonical_slug_for_address", return_value="123-main-st-austin-tx"):
            pairs = recover._match_wrike_to_broken_sites(records, broken_by_slug={})

        assert pairs == []
