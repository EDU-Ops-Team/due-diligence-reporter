from __future__ import annotations

from typing import Any

from due_diligence_reporter.rhodes import (
    RhodesError,
    list_rhodes_site_records,
    lookup_rhodes_site_owner,
)


class FakeRhodesClient:
    def __init__(
        self,
        site: dict[str, Any] | None = None,
        sites: list[dict[str, Any]] | None = None,
        drive_root: tuple[str, str] | Exception | None = None,
    ) -> None:
        self.site = site or {}
        self.sites = sites or []
        self.drive_root = drive_root
        self.calls: list[tuple[str, dict[str, str]]] = []

    def resolve_site(self, *, name: str = "", address: str = "") -> dict[str, Any] | None:
        self.calls.append(("resolve_site", {"name": name, "address": address}))
        return {"siteId": "SITE1", "name": name, "slug": "alpha-test"}

    def get_site(self, *, site_id: str | None = None, slug: str | None = None) -> dict[str, Any]:
        self.calls.append(("get_site", {"site_id": site_id or "", "slug": slug or ""}))
        return self.site

    def list_sites(self, *, status: str = "active") -> list[dict[str, Any]]:
        self.calls.append(("list_sites", {"status": status}))
        return self.sites

    def resolve_drive_root(self, *, site_id: str) -> tuple[str, str]:
        self.calls.append(("resolve_drive_root", {"site_id": site_id}))
        if isinstance(self.drive_root, Exception):
            raise self.drive_root
        return self.drive_root or (
            "drive-root-1",
            "https://drive.google.com/drive/folders/drive-root-1",
        )


def test_lookup_rhodes_site_owner_returns_p1_report_fields() -> None:
    client = FakeRhodesClient(
        {
            "_id": "SITE1",
            "name": "Alpha Los Angeles 5400 Beethoven St",
            "slug": "5400-beethoven-st-los-angeles-ca",
            "address": "5400 Beethoven St, Los Angeles, CA 90066",
            "createdDate": "2026-05-21",
            "p1Dri": {
                "name": "Devin Bates",
                "email": "devin.bates@trilogy.com",
                "userId": "USER1",
            },
            "driveFolderId": "drive-root-1",
        }
    )

    result = lookup_rhodes_site_owner(
        site_name="Alpha Los Angeles 5400 Beethoven St",
        site_address="5400 Beethoven St, Los Angeles, CA 90066",
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "found"
    assert result["site_id"] == "SITE1"
    assert result["p1_assignee_name"] == "Devin Bates"
    assert result["p1_assignee_email"] == "devin.bates@trilogy.com"
    assert result["drive_folder_status"] == "found"
    assert result["drive_folder_id"] == "drive-root-1"
    assert result["drive_folder_url"] == "https://drive.google.com/drive/folders/drive-root-1"
    assert result["report_data_fields"] == {
        "p1_assignee_name": "Devin Bates",
        "site.p1_assignee_name": "Devin Bates",
        "meta.prepared_by": "Devin Bates",
        "p1_assignee_email": "devin.bates@trilogy.com",
        "site.p1_assignee_email": "devin.bates@trilogy.com",
        "site.address": "5400 Beethoven St, Los Angeles, CA 90066",
        "site.site_address": "5400 Beethoven St, Los Angeles, CA 90066",
        "site_created_at": "2026-05-21",
        "meta.drive_folder_url": "https://drive.google.com/drive/folders/drive-root-1",
        "site.drive_folder_url": "https://drive.google.com/drive/folders/drive-root-1",
    }
    assert client.calls == [
        (
            "resolve_site",
            {
                "name": "Alpha Los Angeles 5400 Beethoven St",
                "address": "5400 Beethoven St, Los Angeles, CA 90066",
            },
        ),
        ("get_site", {"site_id": "SITE1", "slug": ""}),
    ]


def test_lookup_rhodes_site_owner_resolves_drive_root_when_not_on_site() -> None:
    client = FakeRhodesClient(
        {
            "_id": "SITE1",
            "name": "Alpha Test",
            "p1Dri": {"name": "Devin Bates"},
        },
        drive_root=(
            "drive-root-2",
            "https://drive.google.com/drive/folders/drive-root-2",
        ),
    )

    result = lookup_rhodes_site_owner(site_name="Alpha Test", client=client)  # type: ignore[arg-type]

    assert result["drive_folder_status"] == "found"
    assert result["drive_folder_url"] == "https://drive.google.com/drive/folders/drive-root-2"
    assert result["report_data_fields"]["meta.drive_folder_url"].endswith("/drive-root-2")
    assert client.calls[-1] == ("resolve_drive_root", {"site_id": "SITE1"})


def test_lookup_rhodes_site_owner_handles_missing_p1() -> None:
    client = FakeRhodesClient(
        {"_id": "SITE1", "name": "Alpha Test"},
        drive_root=RhodesError("Site has no Google Drive folder"),
    )

    result = lookup_rhodes_site_owner(site_name="Alpha Test", client=client)  # type: ignore[arg-type]

    assert result["status"] == "owner_missing"
    assert result["report_data_fields"] == {}
    assert "p1Dri is not assigned" in result["message"]


def test_lookup_rhodes_site_owner_reports_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("RHODES_API_KEY", raising=False)

    result = lookup_rhodes_site_owner(site_name="Alpha Test")

    assert result["status"] == "not_configured"
    assert result["report_data_fields"] == {}
    assert "RHODES_API_KEY" in result["message"]


def test_list_rhodes_site_records_returns_drive_ready_inbox_records() -> None:
    client = FakeRhodesClient(
        {
            "_id": "SITE1",
            "name": "Alpha Keller",
            "slug": "alpha-keller",
            "address": "123 Main St, Keller, TX 76248",
            "status": "active",
            "driveFolderId": "drive-root-1",
        },
        sites=[{"_id": "SITE1", "name": "Alpha Keller"}],
    )

    records = list_rhodes_site_records(client=client)  # type: ignore[arg-type]

    assert records == [
        {
            "id": "SITE1",
            "site_id": "SITE1",
            "title": "Alpha Keller",
            "name": "Alpha Keller",
            "slug": "alpha-keller",
            "address": "123 Main St, Keller, TX 76248",
            "drive_folder_id": "drive-root-1",
            "drive_folder_url": "https://drive.google.com/drive/folders/drive-root-1",
            "rhodes_status": "active",
        }
    ]
    assert client.calls == [
        ("list_sites", {"status": "active"}),
        ("get_site", {"site_id": "SITE1", "slug": ""}),
    ]
