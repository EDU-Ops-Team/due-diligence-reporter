"""Tests for the rebl-migration recovery script's pure-logic helpers.

The HTTP / OAuth / Wrike paths are exercised end-to-end by manual workflow
runs; here we cover only the slug-matching and wipe-detection helpers so
the regression boundary is well-defined without hitting the network.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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

        slug_map = {
            "421 E 11th St, Tulsa, OK": "421-e-11th-st-tulsa-ok",
            "1726 Whitley Ave, Los Angeles, CA": "1726-whitley-ave-los-angeles-ca",
            "999 Healthy Way, Austin, TX": "999-healthy-way-austin-tx",
        }

        with patch.object(recover, "extract_address_from_record", side_effect=fake_extract_address), \
             patch.object(recover, "canonical_slugs_for_addresses", return_value=slug_map):
            pairs = recover._match_wrike_to_broken_sites(records, broken)

        assert len(pairs) == 2
        slugs = {slug for _, slug in pairs}
        assert slugs == {"421-e-11th-st-tulsa-ok", "1726-whitley-ave-los-angeles-ca"}

    def test_skips_records_with_blank_address(self) -> None:
        broken = {"any-slug": {"slug": "any-slug"}}
        records = [self._make_record("")]

        with patch.object(recover, "extract_address_from_record", return_value=""), \
             patch.object(recover, "canonical_slugs_for_addresses", return_value={}):
            pairs = recover._match_wrike_to_broken_sites(records, broken)

        assert pairs == []

    def test_skips_records_rebl_cannot_resolve(self) -> None:
        # Rebl drops unresolvable addresses from the returned mapping; an
        # address with no slug entry can't be matched to any broken slug.
        broken = {"any-slug": {"slug": "any-slug"}}
        records = [self._make_record("Unknown Address")]

        with patch.object(recover, "extract_address_from_record", return_value="Unknown Address"), \
             patch.object(recover, "canonical_slugs_for_addresses", return_value={}):
            pairs = recover._match_wrike_to_broken_sites(records, broken)

        assert pairs == []

    def test_returns_empty_when_no_broken_sites(self) -> None:
        records = [self._make_record("123 Main St, Austin, TX")]
        slug_map = {"123 Main St, Austin, TX": "123-main-st-austin-tx"}

        with patch.object(recover, "extract_address_from_record", return_value="123 Main St, Austin, TX"), \
             patch.object(recover, "canonical_slugs_for_addresses", return_value=slug_map):
            pairs = recover._match_wrike_to_broken_sites(records, broken_by_slug={})

        assert pairs == []


# ---------------------------------------------------------------------------
# _recover_one threads dashboard_slug -> backfill_one(force_slug=...)


class TestRecoverOneThreadsForceSlug:
    """The recovery path's whole reason for existing is to pin the slug.

    ``_recover_one`` already knows ``dashboard_slug`` -- the canonical Rebl
    slug pulled from the live ``sites.json`` -- and must forward it as
    ``force_slug`` to ``backfill_one`` so the publisher does not re-derive
    one from the legacy trace. Without this thread, recovery silently
    publishes under the reporter's slugify(title) fallback and creates
    phantom records (the bug fixed by cleanup_phantom_recovery_slugs.py).
    """

    def _make_record(self) -> dict:
        return {
            "title": "Alpha School Tulsa 421",
            "_test_address": "421 E 11th St, Tulsa, OK",
            "_test_drive": "https://drive.google.com/drive/folders/abc",
            "_test_school_type": "K-8",
            "_test_p1": {"name": "Greg"},
        }

    def test_recover_one_passes_dashboard_slug_as_force_slug(self) -> None:
        rec = self._make_record()
        canonical = "421-e-11th-st-tulsa-ok"
        captured: dict = {}

        def fake_backfill_one(gc, title, drive, addr, school_type, **kwargs):
            captured["title"] = title
            captured["kwargs"] = kwargs
            return True

        with patch.object(recover, "backfill_one", side_effect=fake_backfill_one), \
             patch.object(recover, "extract_address_from_record", return_value=rec["_test_address"]), \
             patch.object(recover, "extract_google_folder_from_record", return_value=rec["_test_drive"]), \
             patch.object(recover, "extract_school_type_from_record", return_value=rec["_test_school_type"]), \
             patch.object(recover, "extract_p1_from_record", return_value=rec["_test_p1"]):
            ok = recover._recover_one(
                gc=None,  # backfill_one is fully mocked, so gc is unused
                rec=rec,
                dashboard_slug=canonical,
                dry_run=False,
            )

        assert ok is True
        assert captured["kwargs"].get("force_slug") == canonical
        assert captured["kwargs"].get("site_owner") == "Greg"

    def test_recover_one_dry_run_does_not_call_backfill(self) -> None:
        """dry_run=True must short-circuit before reaching backfill_one.

        Sanity check on the existing dry-run gate -- ensures the new
        force_slug threading sits below the dry-run early return so that
        preview output stays cheap.
        """
        rec = self._make_record()

        with patch.object(recover, "backfill_one") as mock_backfill, \
             patch.object(recover, "extract_address_from_record", return_value=rec["_test_address"]), \
             patch.object(recover, "extract_google_folder_from_record", return_value=rec["_test_drive"]), \
             patch.object(recover, "extract_school_type_from_record", return_value="K-8"), \
             patch.object(recover, "extract_p1_from_record", return_value={"name": "Greg"}):
            ok = recover._recover_one(
                gc=None,
                rec=rec,
                dashboard_slug="any-slug",
                dry_run=True,
            )

        assert ok is True
        mock_backfill.assert_not_called()

    def test_recover_one_returns_false_on_empty_drive_url(self) -> None:
        """A Wrike record with no Drive folder cannot be recovered.

        ``_recover_one`` returns False without calling backfill_one. Without
        this guard the publisher would be invoked with an empty drive URL,
        crash deep inside the trace fetcher, and leave the run in an
        ambiguous "site present in matched pairs but no Update commit"
        state for the operator to untangle. The early False keeps the
        failed-list accounting honest.
        """
        rec = self._make_record()

        with patch.object(recover, "backfill_one") as mock_backfill, \
             patch.object(recover, "extract_address_from_record", return_value=rec["_test_address"]), \
             patch.object(recover, "extract_google_folder_from_record", return_value=""), \
             patch.object(recover, "extract_school_type_from_record", return_value="K-8"), \
             patch.object(recover, "extract_p1_from_record", return_value={"name": "Greg"}):
            ok = recover._recover_one(
                gc=None,
                rec=rec,
                dashboard_slug="any-slug",
                dry_run=False,
            )

        assert ok is False
        mock_backfill.assert_not_called()

    def test_recover_one_returns_false_when_backfill_raises(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """backfill_one exceptions must be caught, logged, and surfaced as False.

        The recovery loop processes ~26 records; one transient publisher
        error must not abort the whole run. ``_recover_one`` is responsible
        for catching the exception, logging it, and returning False so the
        loop can continue to the next site and the failed-list accounting
        stays accurate. If this guard regresses, the recover_migration
        workflow becomes "all-or-nothing" and a single 502 from the publish
        endpoint loses the rest of the batch.

        We also assert that:
          1. ``logger.exception`` (ERROR level + traceback) fires — the only
             operator signal that a site silently failed.
          2. The exception message text (which historically embeds raw
             addresses, e.g. "publish endpoint 502 for 123 Main St") is NOT
             %s-formatted into the top-level log line. The traceback may
             carry it, but the summary line must be slug-only so PII
             doesn't appear in the run summary that operators skim.
        """
        rec = self._make_record()
        leak_marker = "123-Main-St-PII-leak-marker"

        def raising_backfill(*args, **kwargs):
            raise RuntimeError(f"publish endpoint 502 for {leak_marker}")

        with patch.object(recover, "backfill_one", side_effect=raising_backfill), \
             patch.object(recover, "extract_address_from_record", return_value=rec["_test_address"]), \
             patch.object(recover, "extract_google_folder_from_record", return_value=rec["_test_drive"]), \
             patch.object(recover, "extract_school_type_from_record", return_value="K-8"), \
             patch.object(recover, "extract_p1_from_record", return_value={"name": "Greg"}), \
             caplog.at_level("ERROR", logger="recover_migration_wiped_sites"):
            ok = recover._recover_one(
                gc=None,
                rec=rec,
                dashboard_slug="any-slug",
                dry_run=False,
            )

        assert ok is False
        # Operator-visibility guarantee: logger.exception fired at ERROR level.
        error_records = [r for r in caplog.records if r.levelname == "ERROR"]
        assert error_records, "backfill failure must surface an ERROR log"
        assert any(r.exc_info for r in error_records), (
            "logger.exception must attach the traceback (exc_info)"
        )
        # PII guarantee: the formatted top-level message must not contain the
        # raw exception text. The traceback may; the summary line must not.
        summary_messages = [r.getMessage() for r in error_records]
        assert all(leak_marker not in msg for msg in summary_messages), (
            "exception message must not be %%s-formatted into the summary line; "
            f"got: {summary_messages!r}"
        )


# ---------------------------------------------------------------------------
# main() argparse contract
#
# The script uses a required mutually-exclusive group for --apply / --dry-run
# (see scripts/recover_migration_wiped_sites.py). A CI workflow that forgets
# to pass either flag must fail loudly rather than silently dry-run, since the
# old behavior (no flag = dry-run) was a footgun.
# ---------------------------------------------------------------------------


class TestMainArgparseContract:
    def test_main_exits_when_neither_apply_nor_dry_run_passed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Bare invocation must argparse-exit, not silently dry-run."""
        with pytest.raises(SystemExit) as exc_info:
            recover.main([])
        # argparse exits 2 on usage errors.
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "--apply" in captured.err
        assert "--dry-run" in captured.err

    def test_main_rejects_apply_and_dry_run_together(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--apply and --dry-run are mutually exclusive.

        We assert the argparse mutex error string "not allowed with"
        directly. ``--dry-run`` always appears in the usage line on any
        argparse error, so a fallback like ``"--dry-run" in captured.err``
        would silently survive a regression that removed the mutex group.
        """
        with pytest.raises(SystemExit) as exc_info:
            recover.main(["--apply", "--dry-run"])
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "not allowed with" in captured.err.lower(), (
            f"Expected argparse mutex error 'not allowed with' in stderr; got: {captured.err!r}"
        )
