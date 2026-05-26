from __future__ import annotations

from typing import Any

from due_diligence_reporter.rhodes import lookup_rhodes_site_owner


class FakeRhodesClient:
    def __init__(self, site: dict[str, Any] | None = None) -> None:
        self.site = site or {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def resolve_site(self, *, name: str = "", address: str = "") -> dict[str, Any] | None:
        self.calls.append(("resolve_site", {"name": name, "address": address}))
        return {"siteId": "SITE1", "name": name, "slug": "alpha-test"}

    def get_site(self, *, site_id: str | None = None, slug: str | None = None) -> dict[str, Any]:
        self.calls.append(("get_site", {"site_id": site_id or "", "slug": slug or ""}))
        return self.site


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
    assert result["report_data_fields"] == {
        "p1_assignee_name": "Devin Bates",
        "site.p1_assignee_name": "Devin Bates",
        "meta.prepared_by": "Devin Bates",
        "p1_assignee_email": "devin.bates@trilogy.com",
        "site.p1_assignee_email": "devin.bates@trilogy.com",
        "site_created_at": "2026-05-21",
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


def test_lookup_rhodes_site_owner_handles_missing_p1() -> None:
    client = FakeRhodesClient({"_id": "SITE1", "name": "Alpha Test"})

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
