from __future__ import annotations

import json
from typing import Any

from due_diligence_reporter.rhodes import (
    AerieApiConfig,
    AerieNotesClient,
    RhodesClient,
    RhodesError,
    add_rhodes_site_note,
    create_document_registration_handoff_for_uploads,
    list_rhodes_site_records,
    lookup_rhodes_site_owner,
    map_ddr_doc_type_to_rhodes,
    register_rhodes_document_for_upload,
    update_rhodes_due_diligence,
    verify_rhodes_due_diligence_fields,
)


def _fake_response_id(payload: Any, keys: tuple[str, ...] = ("noteId", "_id", "id")) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for nested_key in ("note", "record", "data"):
        nested_id = _fake_response_id(payload.get(nested_key), keys)
        if nested_id:
            return nested_id
    return ""


class FakeRhodesClient:
    def __init__(
        self,
        site: dict[str, Any] | None = None,
        resolved_site: dict[str, Any] | None = None,
        sites: list[dict[str, Any]] | None = None,
        drive_root: tuple[str, str] | Exception | None = None,
        note_response: dict[str, Any] | None = None,
        note_responses: list[dict[str, Any] | None] | None = None,
        due_diligence_response: dict[str, Any] | None = None,
        due_diligence_exception: Exception | None = None,
        registration_exception: Exception | None = None,
    ) -> None:
        self.site = site or {}
        self.resolved_site = resolved_site
        self.sites = sites or []
        self.drive_root = drive_root
        self.note_response = note_response
        self.note_responses = list(note_responses or [])
        self.due_diligence_response = due_diligence_response
        self.due_diligence_exception = due_diligence_exception
        self.registration_exception = registration_exception
        self.documents: list[dict[str, Any]] = []
        self.registered_documents: list[dict[str, Any]] = []
        self.notes: list[dict[str, Any]] = []
        self.users_by_email: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def resolve_site(self, *, name: str = "", address: str = "") -> dict[str, Any] | None:
        self.calls.append(("resolve_site", {"name": name, "address": address}))
        if self.resolved_site is not None:
            return self.resolved_site
        return {"siteId": "SITE1", "name": name, "slug": "alpha-test"}

    def get_site(self, *, site_id: str | None = None, slug: str | None = None) -> dict[str, Any]:
        self.calls.append(("get_site", {"site_id": site_id or "", "slug": slug or ""}))
        return self.site

    def list_sites(
        self,
        *,
        status: str | None = "active",
        location: str | None = None,
    ) -> list[dict[str, Any]]:
        call_args = {"status": status}
        if location is not None:
            call_args["location"] = location
        self.calls.append(("list_sites", call_args))
        return self.sites

    def resolve_drive_root(self, *, site_id: str) -> tuple[str, str]:
        self.calls.append(("resolve_drive_root", {"site_id": site_id}))
        if isinstance(self.drive_root, Exception):
            raise self.drive_root
        return self.drive_root or (
            "drive-root-1",
            "https://drive.google.com/drive/folders/drive-root-1",
        )

    def find_document_by_drive_file_id(
        self,
        *,
        site_id: str,
        drive_file_id: str,
        doc_type: str | None = None,
        milestone: str | None = None,
    ) -> dict[str, Any] | None:
        self.calls.append(
            (
                "find_document_by_drive_file_id",
                {
                    "site_id": site_id,
                    "drive_file_id": drive_file_id,
                    "doc_type": doc_type or "",
                    "milestone": milestone or "",
                },
            )
        )
        for document in self.documents:
            if document.get("driveFileId") == drive_file_id:
                return document
        return None

    def register_document(
        self,
        *,
        site_id: str,
        title: str,
        doc_type: str,
        drive_file_id: str,
        drive_url: str = "",
        mime_type: str = "",
        milestone: str | None = None,
        quality_bar: str | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        if self.registration_exception is not None:
            raise self.registration_exception
        self.calls.append(
            (
                "register_document",
                {
                    "site_id": site_id,
                    "title": title,
                    "doc_type": doc_type,
                    "drive_file_id": drive_file_id,
                    "milestone": milestone or "",
                    "quality_bar": quality_bar or "",
                },
            )
        )
        document = {
            "_id": f"DOC{len(self.registered_documents) + 1}",
            "siteId": site_id,
            "title": title,
            "docType": doc_type,
            "driveFileId": drive_file_id,
            "driveUrl": drive_url,
            "mimeType": mime_type,
            "milestone": milestone,
            "qualityBar": quality_bar,
            "notes": notes,
        }
        self.registered_documents.append(document)
        return document

    def update_due_diligence(
        self,
        *,
        site_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "update_due_diligence",
                {
                    "site_id": site_id,
                    "fields": ",".join(sorted(fields)),
                },
            )
        )
        if self.due_diligence_exception is not None:
            raise self.due_diligence_exception
        if self.due_diligence_response is None:
            due_diligence = self.site.setdefault("dueDiligence", {})
            if isinstance(due_diligence, dict):
                due_diligence.update(fields)
        return self.due_diligence_response or {"status": "ok", "siteId": site_id}

    def get_user(
        self,
        *,
        email: str = "",
        user_id: str = "",
    ) -> dict[str, Any] | None:
        self.calls.append(("get_user", {"email": email, "user_id": user_id}))
        if user_id:
            return {"_id": user_id, "email": email}
        return self.users_by_email.get(email)

    def add_site_note(
        self,
        *,
        site_id: str = "",
        site_slug: str = "",
        body: str,
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "add_site_note",
                {
                    "site_id": site_id,
                    "site_slug": site_slug,
                    "body": body,
                    "mentions": ",".join(mentions or []),
                },
            )
        )
        if self.note_responses:
            response = self.note_responses.pop(0)
            if response is not None:
                self._record_note_response(
                    response,
                    site_id=site_id,
                    site_slug=site_slug,
                    body=body,
                    mentions=mentions or [],
                )
                return response
        if self.note_response is not None:
            self._record_note_response(
                self.note_response,
                site_id=site_id,
                site_slug=site_slug,
                body=body,
                mentions=mentions or [],
            )
            return self.note_response
        note = {
            "_id": f"NOTE{len(self.notes) + 1}",
            "siteId": site_id,
            "siteSlug": site_slug,
            "body": body,
            "mentions": mentions or [],
        }
        self.notes.append(note)
        return note

    def _record_note_response(
        self,
        response: dict[str, Any],
        *,
        site_id: str,
        site_slug: str,
        body: str,
        mentions: list[str],
    ) -> None:
        note_id = _fake_response_id(response)
        if not note_id:
            return
        if any(_fake_response_id(note) == note_id for note in self.notes):
            return
        self.notes.append(
            {
                "_id": note_id,
                "siteId": site_id,
                "siteSlug": site_slug,
                "body": body,
                "mentions": mentions,
            }
        )

    def list_notes(
        self,
        *,
        site_id: str = "",
        site_slug: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            (
                "list_notes",
                {
                    "site_id": site_id,
                    "site_slug": site_slug,
                    "limit": str(limit),
                },
            )
        )
        return self.notes[:limit]

    def find_site_note_by_body(
        self,
        *,
        site_id: str = "",
        site_slug: str = "",
        body: str,
    ) -> dict[str, Any] | None:
        self.calls.append(
            (
                "find_site_note_by_body",
                {
                    "site_id": site_id,
                    "site_slug": site_slug,
                    "body": body,
                },
            )
        )
        for note in self.notes:
            if str(note.get("body") or "").strip() == body.strip():
                return note
        return None


class FakeAerieResponse:
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.headers: dict[str, str] = {}
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeAerieSession:
    def __init__(self, responses: list[FakeAerieResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeAerieResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"No fake Aerie response queued for {method} {url}")
        return self.responses.pop(0)


class RecordingRhodesClient(RhodesClient):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        return {"_id": "NOTE1"}


class SequencedToolRhodesClient(RhodesClient):
    def __init__(self, responses: list[Any]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses = list(responses)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if not self.responses:
            raise AssertionError(f"No response queued for {name}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_get_missing_documents_calls_rhodes_tool() -> None:
    client = RecordingRhodesClient()

    result = client.get_missing_documents(site_id="SITE1")

    assert result == {"_id": "NOTE1"}
    assert client.calls == [("getMissingDocuments", {"siteId": "SITE1"})]


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


def test_lookup_rhodes_site_owner_accepts_snake_case_site_id() -> None:
    client = FakeRhodesClient(
        {
            "site_id": "SITE1",
            "name": "Alpha Los Angeles 5401 Beethoven St",
            "slug": "5400-beethoven-st-los-angeles-ca",
            "address": "5401 Beethoven St, Los Angeles, CA",
            "p1Dri": {
                "name": "Devin Bates",
                "email": "devin.bates@trilogy.com",
                "userId": "USER1",
            },
            "driveFolderId": "drive-root-1",
        },
        resolved_site={
            "site_id": "SITE1",
            "name": "Alpha Los Angeles 5401 Beethoven St",
        },
    )

    result = lookup_rhodes_site_owner(
        site_name="Alpha Los Angeles 5400 Beethoven St",
        site_address="5400 Beethoven St, Los Angeles, CA 90066",
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "found"
    assert result["site_id"] == "SITE1"
    assert result["drive_folder_status"] == "found"
    assert result["drive_folder_url"].endswith("/drive-root-1")
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


def test_resolve_site_prefers_single_active_location_match_for_broad_name() -> None:
    client = SequencedToolRhodesClient(
        [
            {
                "sites": [
                    {
                        "_id": "ACTIVE-HOUSTON",
                        "name": "Alpha Houston 777 W 23rd St",
                        "status": "active",
                    }
                ]
            }
        ]
    )

    result = client.resolve_site(name="Houston")

    assert result is not None
    assert result["_id"] == "ACTIVE-HOUSTON"
    assert client.calls == [
        ("listSites", {"status": "active", "location": "Houston"}),
    ]


def test_resolve_site_prefers_unique_central_city_active_match() -> None:
    client = SequencedToolRhodesClient(
        [
            {
                "sites": [
                    {
                        "_id": "ACTIVE-WOODLANDS",
                        "name": "Alpha The Woodlands 2000 Woodlands Pkwy",
                        "marketId": "the-woodlands",
                        "metroId": "houston",
                        "region": "harris-tx",
                        "slug": "2000-woodlands-pkwy-the-woodlands-tx",
                    },
                    {
                        "_id": "ACTIVE-HOUSTON",
                        "name": "Alpha Houston 777 W 23rd St",
                        "marketId": "central-houston",
                        "metroId": "houston",
                        "region": "houston",
                        "slug": "777-west-23rd-st-houston-tx",
                    },
                ]
            }
        ]
    )

    result = client.resolve_site(name="Houston")

    assert result is not None
    assert result["_id"] == "ACTIVE-HOUSTON"
    assert client.calls == [
        ("listSites", {"status": "active", "location": "Houston"}),
    ]


def test_resolve_site_falls_back_when_broad_name_has_multiple_active_matches() -> None:
    client = SequencedToolRhodesClient(
        [
            {
                "sites": [
                    {"_id": "ACTIVE-A", "name": "Alpha Austin 121 W 6th St"},
                    {"_id": "ACTIVE-B", "name": "Alpha Austin 2611 Hillview Rd"},
                ]
            },
            {
                "_id": "RESOLVED",
                "name": "Alpha Austin 121 W 6th St",
            },
        ]
    )

    result = client.resolve_site(name="Austin")

    assert result is not None
    assert result["_id"] == "RESOLVED"
    assert client.calls == [
        ("listSites", {"status": "active", "location": "Austin"}),
        ("resolveSite", {"name": "Austin"}),
    ]


def test_lookup_rhodes_site_owner_uses_central_city_match_before_fallback() -> None:
    client = SequencedToolRhodesClient(
        [
            {
                "sites": [
                    {
                        "_id": "WOODLANDS",
                        "name": "Alpha The Woodlands 2000 Woodlands Pkwy",
                        "marketId": "the-woodlands",
                        "metroId": "houston",
                        "region": "harris-tx",
                        "slug": "2000-woodlands-pkwy-the-woodlands-tx",
                    },
                    {
                        "_id": "HOUSTON",
                        "name": "Alpha Houston 777 W 23rd St",
                        "marketId": "central-houston",
                        "metroId": "houston",
                        "region": "houston",
                        "slug": "777-west-23rd-st-houston-tx",
                    },
                ]
            },
            {
                "_id": "HOUSTON",
                "name": "Alpha Houston 777 W 23rd St",
                "slug": "777-west-23rd-st-houston-tx",
                "address": "777 W 23rd St, Houston, TX",
                "p1Dri": {
                    "name": "Brandon Gee",
                    "email": "brandon.gee@trilogy.com",
                    "userId": "USER1",
                },
                "driveFolderId": "drive-root-houston",
            },
        ]
    )

    result = lookup_rhodes_site_owner(site_name="Houston", client=client)

    assert result["status"] == "found"
    assert result["site_name"] == "Alpha Houston 777 W 23rd St"
    assert result["site_address"] == "777 W 23rd St, Houston, TX"
    assert result["drive_folder_url"].endswith("/drive-root-houston")
    assert result["report_data_fields"]["meta.prepared_by"] == "Brandon Gee"
    assert client.calls == [
        ("listSites", {"status": "active", "location": "Houston"}),
        ("getSite", {"siteId": "HOUSTON"}),
    ]


def test_lookup_rhodes_site_owner_hydrates_sparse_active_summary() -> None:
    client = FakeRhodesClient(
        {
            "_id": "SITE1",
            "name": "Alpha Houston 777 W 23rd St",
            "slug": "777-west-23rd-st-houston-tx",
            "address": "777 W 23rd St, Houston, TX",
            "p1Dri": {
                "name": "Brandon Gee",
                "email": "brandon.gee@trilogy.com",
                "userId": "USER1",
            },
            "driveFolderId": "drive-root-houston",
        },
        resolved_site={
            "_id": "SITE1",
            "name": "Alpha Houston 777 W 23rd St",
            "p1Dri": {
                "name": "Brandon Gee",
                "email": "brandon.gee@trilogy.com",
                "userId": "USER1",
            },
        },
    )

    result = lookup_rhodes_site_owner(site_name="Houston", client=client)  # type: ignore[arg-type]

    assert result["status"] == "found"
    assert result["site_name"] == "Alpha Houston 777 W 23rd St"
    assert result["site_address"] == "777 W 23rd St, Houston, TX"
    assert result["drive_folder_url"] == "https://drive.google.com/drive/folders/drive-root-houston"
    assert result["report_data_fields"]["meta.prepared_by"] == "Brandon Gee"
    assert result["report_data_fields"]["site.address"] == "777 W 23rd St, Houston, TX"
    assert client.calls == [
        ("resolve_site", {"name": "Houston", "address": ""}),
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
    monkeypatch.delenv("LOCATIONOS_MCP_API_KEY", raising=False)

    result = lookup_rhodes_site_owner(site_name="Alpha Test")

    assert result["status"] == "not_configured"
    assert result["report_data_fields"] == {}
    assert "LocationOS MCP bearer token" in result["message"]


def test_list_rhodes_site_records_returns_drive_ready_inbox_records() -> None:
    client = FakeRhodesClient(
        {
            "_id": "SITE1",
            "name": "Alpha Keller",
            "slug": "alpha-keller",
            "address": "123 Main St, Keller, TX 76248",
            "status": "active",
            "createdDate": "2026-05-20",
            "p1Dri": {"name": "Devin Bates", "email": "devin.bates@trilogy.com"},
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
            "p1_assignee_name": "Devin Bates",
            "p1_assignee_email": "devin.bates@trilogy.com",
            "p1_assignee_user_id": "",
            "created_date": "2026-05-20",
            "status": "active",
            "rhodes_status": "active",
            "customFields": [],
        }
    ]
    assert client.calls == [
        ("list_sites", {"status": "active"}),
        ("get_site", {"site_id": "SITE1", "slug": ""}),
    ]


def test_list_rhodes_site_records_skips_hydration_for_complete_summaries() -> None:
    client = FakeRhodesClient(
        sites=[
            {
                "_id": "SITE1",
                "name": "Alpha Keller",
                "slug": "alpha-keller",
                "address": "123 Main St, Keller, TX 76248",
                "status": "active",
                "createdDate": "2026-05-20",
                "p1Dri": {"name": "Devin Bates", "email": "devin.bates@trilogy.com"},
                "driveFolderUrl": "https://drive.google.com/drive/folders/drive-root-1",
            }
        ],
    )

    records = list_rhodes_site_records(client=client)  # type: ignore[arg-type]

    assert records[0]["id"] == "SITE1"
    assert records[0]["address"] == "123 Main St, Keller, TX 76248"
    assert records[0]["drive_folder_id"] == "drive-root-1"
    assert records[0]["drive_folder_url"].endswith("/drive-root-1")
    assert records[0]["p1_assignee_email"] == "devin.bates@trilogy.com"
    assert client.calls == [("list_sites", {"status": "active"})]


def test_list_rhodes_site_records_can_load_specific_site_ids_without_listing() -> None:
    client = FakeRhodesClient(
        {
            "_id": "SITE1",
            "name": "Alpha Keller",
            "slug": "alpha-keller",
            "address": "123 Main St, Keller, TX 76248",
            "status": "active",
            "createdDate": "2026-05-20",
            "p1Dri": {"name": "Devin Bates", "email": "devin.bates@trilogy.com"},
            "driveFolderId": "drive-root-1",
        },
    )

    records = list_rhodes_site_records(
        site_ids=["SITE1"],
        client=client,  # type: ignore[arg-type]
    )

    assert records[0]["id"] == "SITE1"
    assert records[0]["drive_folder_url"].endswith("/drive-root-1")
    assert client.calls == [("get_site", {"site_id": "SITE1", "slug": ""})]


def test_ddr_doc_type_mapping_covers_inbox_supported_docs() -> None:
    assert map_ddr_doc_type_to_rhodes("sir").doc_type == "siteInvestigationReport"  # type: ignore[union-attr]
    assert map_ddr_doc_type_to_rhodes("building_inspection").doc_type == (  # type: ignore[union-attr]
        "propertyConditionAssessment"
    )
    assert map_ddr_doc_type_to_rhodes("block_plan").doc_type == "floorPlan"  # type: ignore[union-attr]
    assert map_ddr_doc_type_to_rhodes("isp").doc_type == "other"  # type: ignore[union-attr]
    opening_plan_mapping = map_ddr_doc_type_to_rhodes("opening_plan_report")
    assert opening_plan_mapping is not None
    assert opening_plan_mapping.doc_type == "other"
    assert opening_plan_mapping.milestone == "acquireProperty"
    alpha_phasing_mapping = map_ddr_doc_type_to_rhodes("alpha_phasing_plan_report")
    assert alpha_phasing_mapping is not None
    assert alpha_phasing_mapping.doc_type == "phasing"
    assert alpha_phasing_mapping.milestone == "acquireProperty"
    alpha_capacity_mapping = map_ddr_doc_type_to_rhodes("alpha_capacity_analysis")
    assert alpha_capacity_mapping is not None
    assert alpha_capacity_mapping.doc_type == "capacityCalculation"
    cost_timeline_mapping = map_ddr_doc_type_to_rhodes("cost_timeline_estimate")
    assert cost_timeline_mapping is not None
    assert cost_timeline_mapping.doc_type == "initialCostEstimate"
    assert cost_timeline_mapping.milestone == "acquireProperty"
    outdoor_mapping = map_ddr_doc_type_to_rhodes("outdoor_play_space_report")
    assert outdoor_mapping is not None
    assert outdoor_mapping.doc_type == "other"
    assert outdoor_mapping.quality_bar == "outdoorRecreation"
    security_mapping = map_ddr_doc_type_to_rhodes("security_due_diligence_report")
    assert security_mapping is not None
    assert security_mapping.doc_type == "other"
    assert security_mapping.milestone == "acquireProperty"
    school_mapping = map_ddr_doc_type_to_rhodes("school_approval_report")
    assert school_mapping is not None
    assert school_mapping.doc_type == "regulatoryApproval"
    traffic_mapping = map_ddr_doc_type_to_rhodes("traffic_analysis")
    assert traffic_mapping is not None
    assert traffic_mapping.doc_type == "other"
    assert traffic_mapping.quality_bar == "transportation"
    assert (
        map_ddr_doc_type_to_rhodes("certificate_of_occupancy").doc_type
        == "certificateOfOccupancy"
    )
    assert map_ddr_doc_type_to_rhodes("permit_of_record").doc_type == "permit"
    assert map_ddr_doc_type_to_rhodes("measured_floor_plan").doc_type == "floorPlan"
    assert map_ddr_doc_type_to_rhodes("lidar").doc_type == "lidar"


def test_register_rhodes_document_for_upload_registers_mapped_drive_file() -> None:
    client = FakeRhodesClient()

    result = register_rhodes_document_for_upload(
        site_id="SITE1",
        ddr_doc_type="block_plan",
        title="May 26 2026 - Alpha Keller Block Plan.pdf",
        drive_file_id="drive-file-1",
        drive_url="https://drive.google.com/file/d/drive-file-1/view",
        original_filename="Block Plan.pdf",
        message_id="gmail-msg-1",
        attachment_id="att-1",
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "registered"
    assert result["rhodes_doc_type"] == "floorPlan"
    assert result["rhodes_milestone"] == "acquireProperty"
    assert result["rhodes_document_id"] == "DOC1"
    assert client.registered_documents[0]["docType"] == "floorPlan"
    assert client.registered_documents[0]["milestone"] == "acquireProperty"
    assert "DDR doc type: block_plan" in client.registered_documents[0]["notes"]
    assert "Gmail message ID: gmail-msg-1" in client.registered_documents[0]["notes"]


def test_register_rhodes_document_for_upload_skips_existing_drive_file() -> None:
    client = FakeRhodesClient()
    client.documents = [{"_id": "DOC_EXISTING", "driveFileId": "drive-file-1"}]

    result = register_rhodes_document_for_upload(
        site_id="SITE1",
        ddr_doc_type="isp",
        title="May 26 2026 - Alpha Keller ISP.pdf",
        drive_file_id="drive-file-1",
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "already_registered"
    assert result["rhodes_doc_type"] == "other"
    assert result["rhodes_document_id"] == "DOC_EXISTING"
    assert client.registered_documents == []


def test_register_rhodes_document_for_upload_handles_missing_config(monkeypatch) -> None:
    monkeypatch.delenv("RHODES_API_KEY", raising=False)
    monkeypatch.delenv("LOCATIONOS_MCP_API_KEY", raising=False)

    result = register_rhodes_document_for_upload(
        site_id="SITE1",
        ddr_doc_type="sir",
        title="Alpha Keller SIR.pdf",
        drive_file_id="drive-file-1",
    )

    assert result["status"] == "failed"
    assert result["reason"] == "rhodes_error"
    assert "LocationOS MCP bearer token" in result["error"]


def test_register_rhodes_document_for_upload_handoffs_approval_gated_registration() -> None:
    client = FakeRhodesClient(
        site={
            "_id": "SITE1",
            "name": "Alpha Test",
            "slug": "alpha-test",
            "address": "123 Main St, Denver, CO 80202",
            "p1Dri": {"email": "owner@example.com", "userId": "OWNER1"},
        },
        registration_exception=RhodesError("Action requires confirmation"),
    )

    result = register_rhodes_document_for_upload(
        site_id="SITE1",
        ddr_doc_type="sir",
        title="Alpha Test SIR.pdf",
        drive_file_id="drive-file-1",
        drive_url="https://drive.google.com/file/d/drive-file-1/view",
        original_filename="Alpha Test SIR source.pdf",
        message_id="gmail-msg-1",
        attachment_id="att-1",
        source="m2_executor",
        client=client,  # type: ignore[arg-type]
    )

    expected_body = (
        "Site: Alpha Test\n"
        "Address: 123 Main St, Denver, CO 80202\n"
        "Documents to register:\n"
        "Alpha Test SIR.pdf\n"
        "  Drive: https://drive.google.com/file/d/drive-file-1/view"
    )
    handoff = result["document_registration_handoff"]
    document = handoff["documents"][0]

    assert result["status"] == "pending_user_action"
    assert result["rhodes_registration_status"] == "pending_user_action"
    assert result["human_followup_required"] is True
    assert result["human_followup_type"] == "document_registration"
    assert result["remaining_work"] == []
    assert handoff["status"] == "created"
    assert handoff["note_body"] == expected_body
    assert handoff["note_readback_status"] == "verified"
    assert handoff["rhodes_note_id"] == "NOTE1"
    assert handoff["mentioned_owner_user_ids"] == ["OWNER1"]
    assert document["file_id"] == "drive-file-1"
    assert document["file_name"] == "Alpha Test SIR source.pdf"
    assert document["docType"] == "siteInvestigationReport"
    assert document["milestone"] == "acquireProperty"
    assert document["task_key"] == "m2_executor"
    assert document["registration_status"] == "pending_user_action"
    assert document["human_followup_required"] is True
    assert document["human_followup_type"] == "document_registration"
    assert "Drive file ID" not in handoff["note_body"]
    assert "siteInvestigationReport" not in handoff["note_body"]
    assert client.notes[0]["body"] == expected_body
    assert client.notes[0]["mentions"] == ["OWNER1"]
    assert client.registered_documents == []


def test_register_rhodes_document_for_upload_uses_greg_fallback_for_handoff() -> None:
    client = FakeRhodesClient(
        site={
            "_id": "SITE1",
            "name": "Alpha Test",
            "slug": "alpha-test",
            "address": "123 Main St, Denver, CO 80202",
        },
        registration_exception=RhodesError("Approval unavailable"),
    )
    client.users_by_email["greg.foote@trilogy.com"] = {
        "_id": "GREG1",
        "email": "greg.foote@trilogy.com",
    }

    result = register_rhodes_document_for_upload(
        site_id="SITE1",
        ddr_doc_type="school_approval_report",
        title="School Approval Report",
        drive_file_id="drive-file-1",
        drive_url="https://drive.google.com/file/d/drive-file-1/view",
        client=client,  # type: ignore[arg-type]
    )

    handoff = result["document_registration_handoff"]

    assert result["status"] == "pending_user_action"
    assert handoff["fallback_owner_used"] is True
    assert handoff["owner_email"] == "greg.foote@trilogy.com"
    assert handoff["owner_user_id"] == "GREG1"
    assert handoff["mentioned_owner_user_ids"] == ["GREG1"]
    assert client.notes[0]["mentions"] == ["GREG1"]


def test_register_rhodes_document_for_upload_blocks_when_handoff_note_fails() -> None:
    client = FakeRhodesClient(
        site={
            "_id": "SITE1",
            "name": "Alpha Test",
            "slug": "alpha-test",
            "address": "123 Main St, Denver, CO 80202",
            "p1Dri": {"email": "owner@example.com", "userId": "OWNER1"},
        },
        note_response={
            "status": "rejected",
            "rejectionReason": "Action requires confirmation",
        },
        registration_exception=RhodesError("Action requires confirmation"),
    )

    result = register_rhodes_document_for_upload(
        site_id="SITE1",
        ddr_doc_type="sir",
        title="Alpha Test SIR.pdf",
        drive_file_id="drive-file-1",
        drive_url="https://drive.google.com/file/d/drive-file-1/view",
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "failed"
    assert result["reason"] == "registration_handoff_failed"
    assert result["rhodes_registration_status"] == "failed"
    assert result["remaining_work"] == [
        {
            "type": "document_registration",
            "status": "blocked",
            "reason": "handoff_note_failed",
        }
    ]
    assert result["document_registration_handoff"]["status"] == "failed"
    assert result["document_registration_handoff"]["reason"] == "handoff_note_failed"


def test_create_document_registration_handoff_for_uploads_groups_documents() -> None:
    client = FakeRhodesClient(
        site={
            "_id": "SITE1",
            "name": "Alpha Test",
            "slug": "alpha-test",
            "address": "123 Main St, Denver, CO 80202",
            "p1Dri": {"email": "owner@example.com", "userId": "OWNER1"},
        }
    )

    result = create_document_registration_handoff_for_uploads(
        site_id="SITE1",
        documents=[
            {
                "status": "failed",
                "error": "Action requires confirmation",
                "ddr_doc_type": "outdoor_play_space_report",
                "title": "Outdoor Play Space Report - Alpha Test (Markdown)",
                "drive_file_id": "drive-md",
                "drive_url": "https://drive/md",
                "mime_type": "text/markdown",
                "original_filename": "play_space.md",
                "source": "apply_outdoor_play_space_skill",
            },
            {
                "status": "failed",
                "error": "Action requires confirmation",
                "ddr_doc_type": "outdoor_play_space_report",
                "title": "Outdoor Play Space Report - Alpha Test (HTML)",
                "drive_file_id": "drive-html",
                "drive_url": "https://drive/html",
                "mime_type": "text/html",
                "original_filename": "open_space_map.html",
                "source": "apply_outdoor_play_space_skill",
            },
        ],
        client=client,  # type: ignore[arg-type]
    )

    expected_body = (
        "Site: Alpha Test\n"
        "Address: 123 Main St, Denver, CO 80202\n"
        "Documents to register:\n"
        "Outdoor Play Space Report - Alpha Test (Markdown)\n"
        "  Drive: https://drive/md\n"
        "Outdoor Play Space Report - Alpha Test (HTML)\n"
        "  Drive: https://drive/html"
    )

    assert result["status"] == "created"
    assert result["document_count"] == 2
    assert result["note_body"] == expected_body
    assert result["rhodes_registration_status"] == "pending_user_action"
    assert result["human_followup_required"] is True
    assert result["human_followup_type"] == "document_registration"
    assert result["remaining_work"] == []
    assert [doc["file_id"] for doc in result["documents"]] == ["drive-md", "drive-html"]
    assert {doc["registration_status"] for doc in result["documents"]} == {
        "pending_user_action"
    }
    assert client.notes[0]["body"] == expected_body
    assert client.notes[0]["mentions"] == ["OWNER1"]


def test_rhodes_client_add_site_note_sends_explicit_site_anchor() -> None:
    client = RecordingRhodesClient()

    result = client.add_site_note(
        site_id=" SITE1 ",
        body=" Body ",
        mentions=[" USER1 ", ""],
    )

    assert result["_id"] == "NOTE1"
    assert client.calls == [
        (
            "addNote",
            {
                "anchorType": "site",
                "body": "Body",
                "siteId": "SITE1",
                "anchorId": "SITE1",
                "mentions": ["USER1"],
            },
        )
    ]


def test_rhodes_client_update_due_diligence_calls_locationos_tool() -> None:
    client = RecordingRhodesClient()

    result = client.update_due_diligence(
        site_id=" SITE1 ",
        fields={
            "status": " complete ",
            "ddReportLink": " https://docs.google.com/document/d/doc123 ",
            "foCapacity": "36",
            "blank": "",
        },
    )

    assert result["_id"] == "NOTE1"
    assert client.calls == [
        (
            "updateDueDiligence",
            {
                "siteId": "SITE1",
                "status": "complete",
                "ddReportLink": "https://docs.google.com/document/d/doc123",
                "foCapacity": "36",
            },
        )
    ]


def test_update_rhodes_due_diligence_reports_rejected_response() -> None:
    client = FakeRhodesClient(
        due_diligence_response={
            "status": "rejected",
            "rejectionReason": "Validation failed",
        }
    )

    result = update_rhodes_due_diligence(
        site_id="SITE1",
        fields={"status": "complete"},
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "failed"
    assert result["reason"] == "write_rejected"
    assert "Validation failed" in result["error"]
    assert result["write_request"] == {
        "server": "locationos",
        "tool": "updateDueDiligence",
        "arguments": {"siteId": "SITE1", "status": "complete"},
    }
    assert result["readback_request"] == {
        "server": "locationos",
        "tool": "getSite",
        "arguments": {"siteId": "SITE1"},
        "verify_fields": ["status"],
    }


def test_update_rhodes_due_diligence_handoffs_browser_approval_response() -> None:
    client = FakeRhodesClient(
        site={
            "_id": "SITE1",
            "name": "Alpha Test",
            "slug": "alpha-test",
            "address": "123 Main St, Denver, CO 80202",
            "p1Dri": {"email": "owner@example.com", "userId": "OWNER1"},
        },
        due_diligence_response={
            "status": "awaiting_browser_approval",
            "pendingMutationId": "MUT1",
            "approvalSessionId": "APPROVAL1",
            "reviewUrl": "https://locationos.example/review",
        },
    )

    result = update_rhodes_due_diligence(
        site_id="SITE1",
        fields={"maxCapCapacity": 54, "foCapacity": 36},
        client=client,  # type: ignore[arg-type]
    )

    expected_body = (
        "Site Name: Alpha Test\n"
        "Site Address: 123 Main St, Denver, CO 80202\n"
        "DDR submitted these due diligence field values to the LocationOS "
        "approval queue. Please review the pending change and approve or "
        "reject it:\n"
        "Due Diligence Fields proposed:\n"
        "foCapacity: 36\n"
        "maxCapCapacity: 54\n"
        "Review link: https://locationos.example/review"
    )
    handoff = result["due_diligence_update_handoff"]

    assert result["status"] == "proposal_submitted"
    assert result["reason"] == "approval_queue"
    assert result["rhodes_due_diligence_status"] == "proposal_submitted"
    assert result["human_followup_required"] is True
    assert result["human_followup_type"] == "due_diligence_approval"
    assert result["remaining_work"] == []
    assert result["approval"] == {
        "pending_mutation_id": "MUT1",
        "approval_session_id": "APPROVAL1",
        "review_url": "https://locationos.example/review",
    }
    assert result["response"]["status"] == "awaiting_browser_approval"
    assert result["response"]["pendingMutationId"] == "MUT1"
    assert "readback" not in result
    assert "error" not in result
    assert handoff["status"] == "created"
    assert handoff["note_body"] == expected_body
    assert handoff["note_readback_status"] == "verified"
    assert handoff["rhodes_note_id"] == "NOTE1"
    assert handoff["field_count"] == 2
    assert handoff["fields"] == [
        {"name": "foCapacity", "value": "36", "source": ""},
        {"name": "maxCapCapacity", "value": "54", "source": ""},
    ]
    assert client.notes[0]["body"] == expected_body
    assert client.notes[0]["mentions"] == ["OWNER1"]


def test_update_rhodes_due_diligence_handoff_note_includes_field_sources() -> None:
    client = FakeRhodesClient(
        site={
            "_id": "SITE1",
            "name": "Alpha Test",
            "slug": "alpha-test",
            "address": "123 Main St, Denver, CO 80202",
            "p1Dri": {"email": "owner@example.com", "userId": "OWNER1"},
        },
        due_diligence_response={
            "status": "awaiting_browser_approval",
            "pendingMutationId": "MUT1",
        },
    )

    result = update_rhodes_due_diligence(
        site_id="SITE1",
        fields={"foCapacity": 36, "status": "data-gathering"},
        client=client,  # type: ignore[arg-type]
        field_sources={"foCapacity": "Alpha Capacity Analysis - Alpha Test.json"},
    )

    handoff = result["due_diligence_update_handoff"]
    note_body = handoff["note_body"]
    assert "Supporting documents (registered on this site record):" in note_body
    assert "foCapacity: Alpha Capacity Analysis - Alpha Test.json" in note_body
    assert "status: workflow field - no source document" in note_body
    assert handoff["fields"] == [
        {
            "name": "foCapacity",
            "value": "36",
            "source": "Alpha Capacity Analysis - Alpha Test.json",
        },
        {"name": "status", "value": "data-gathering", "source": ""},
    ]
    assert client.notes[0]["body"] == note_body


def test_status_only_approval_response_falls_back_to_manual_handoff() -> None:
    client = FakeRhodesClient(
        site={
            "_id": "SITE1",
            "name": "Alpha Test",
            "slug": "alpha-test",
            "address": "123 Main St, Denver, CO 80202",
            "p1Dri": {"email": "owner@example.com", "userId": "OWNER1"},
        },
        due_diligence_response={"status": "awaiting_browser_approval"},
    )

    result = update_rhodes_due_diligence(
        site_id="SITE1",
        fields={"foCapacity": 36},
        client=client,  # type: ignore[arg-type]
    )

    handoff = result["due_diligence_update_handoff"]
    assert result["status"] == "pending_user_action"
    assert result["reason"] == "handoff_note_created"
    assert result["human_followup_type"] == "due_diligence_update"
    assert "approval" not in result
    assert "Due Diligence Fields to update:" in handoff["note_body"]
    assert "approval queue" not in handoff["note_body"]


def test_proposal_note_failure_reports_failed_submission() -> None:
    client = FakeRhodesClient(
        site={
            "_id": "SITE1",
            "name": "Alpha Test",
            "slug": "alpha-test",
            "address": "123 Main St, Denver, CO 80202",
            "p1Dri": {"email": "owner@example.com", "userId": "OWNER1"},
        },
        due_diligence_response={
            "status": "awaiting_browser_approval",
            "pendingMutationId": "MUT1",
        },
        note_response={"error": "note write rejected"},
    )

    result = update_rhodes_due_diligence(
        site_id="SITE1",
        fields={"foCapacity": 36},
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "failed"
    assert result["reason"] == "proposal_note_failed"
    assert result["rhodes_due_diligence_status"] == "failed"
    assert result["approval"]["pending_mutation_id"] == "MUT1"
    assert result["remaining_work"][0]["type"] == "due_diligence_approval"


def test_update_rhodes_due_diligence_handoffs_elicitation_exception() -> None:
    client = FakeRhodesClient(
        site={
            "_id": "SITE1",
            "name": "Alpha Test",
            "slug": "alpha-test",
            "address": "123 Main St, Denver, CO 80202",
            "p1Dri": {"email": "owner@example.com", "userId": "OWNER1"},
        },
        due_diligence_exception=RhodesError("Error: elicitation_unsupported"),
    )

    result = update_rhodes_due_diligence(
        site_id="SITE1",
        fields={"maxCapCapacity": 71},
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "pending_user_action"
    assert result["reason"] == "handoff_note_created"
    assert result["error"] == "Error: elicitation_unsupported"
    assert result["due_diligence_update_handoff"]["note_body"] == (
        "Site Name: Alpha Test\n"
        "Site Address: 123 Main St, Denver, CO 80202\n"
        "Copy/paste these field names and values into the LocationOS due diligence record:\n"
        "Due Diligence Fields to update:\n"
        "maxCapCapacity: 71"
    )


def test_update_rhodes_due_diligence_preserves_failed_write_request_and_readback() -> None:
    client = FakeRhodesClient(
        site={"_id": "SITE1", "dueDiligence": {"status": "data-gathering"}},
        due_diligence_exception=RhodesError(
            "Rhodes tool returned error: {'content': [{'type': 'text', "
            "'text': 'Error: [Request ID: 9ec066c68ad1bdb6] Server Error\\n"
            "Request ID: 93946545-f2fb-43ea-a2ab-705c4aa4f61d'}], "
            "'isError': True}"
        ),
    )

    result = update_rhodes_due_diligence(
        site_id="SITE1",
        fields={"status": "complete", "foCapacity": 36},
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "failed"
    assert result["reason"] == "rhodes_error"
    assert result["error_summary"] == (
        "LocationOS updateDueDiligence returned a server error. Request IDs: "
        "9ec066c68ad1bdb6, 93946545-f2fb-43ea-a2ab-705c4aa4f61d"
    )
    assert result["write_request"] == {
        "server": "locationos",
        "tool": "updateDueDiligence",
        "arguments": {"siteId": "SITE1", "status": "complete", "foCapacity": 36},
    }
    assert result["readback_request"] == {
        "server": "locationos",
        "tool": "getSite",
        "arguments": {"siteId": "SITE1"},
        "verify_fields": ["foCapacity", "status"],
    }
    assert result["readback"]["status"] == "failed"
    assert result["readback"]["reason"] == "field_mismatch"
    assert result["readback"]["mismatches"] == [
        {"field": "status", "expected": "complete", "actual": "data-gathering"},
        {"field": "foCapacity", "expected": "36", "actual": "None"},
    ]
    assert client.calls == [
        (
            "update_due_diligence",
            {
                "site_id": "SITE1",
                "fields": "foCapacity,status",
            },
        ),
        ("get_site", {"site_id": "SITE1", "slug": ""}),
    ]


def test_update_rhodes_due_diligence_verifies_readback() -> None:
    client = FakeRhodesClient(site={"_id": "SITE1", "name": "Alpha Test"})

    result = update_rhodes_due_diligence(
        site_id="SITE1",
        fields={"status": "complete", "foCapacity": "36"},
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "updated"
    assert result["readback"] == {
        "status": "verified",
        "verified_fields": ["foCapacity", "status"],
    }
    assert client.calls == [
        (
            "update_due_diligence",
            {
                "site_id": "SITE1",
                "fields": "foCapacity,status",
            },
        ),
        ("get_site", {"site_id": "SITE1", "slug": ""}),
    ]


def test_verify_rhodes_due_diligence_fields_uses_readback_without_write() -> None:
    client = FakeRhodesClient(
        site={"_id": "SITE1", "dueDiligence": {"status": "complete", "foCapacity": "36"}}
    )

    result = verify_rhodes_due_diligence_fields(
        site_id="SITE1",
        fields={"status": "complete", "foCapacity": "36"},
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "verified"
    assert result["readback"] == {
        "status": "verified",
        "verified_fields": ["foCapacity", "status"],
    }
    assert client.calls == [("get_site", {"site_id": "SITE1", "slug": ""})]


def test_update_rhodes_due_diligence_fails_when_readback_mismatches() -> None:
    client = FakeRhodesClient(
        site={"_id": "SITE1", "dueDiligence": {"status": "data-gathering"}},
        due_diligence_response={"status": "ok", "siteId": "SITE1"},
    )

    result = update_rhodes_due_diligence(
        site_id="SITE1",
        fields={"status": "complete"},
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "failed"
    assert result["reason"] == "readback_failed"
    assert result["error"] == "LocationOS readback mismatch for status"
    assert result["readback"]["mismatches"] == [
        {
            "field": "status",
            "expected": "complete",
            "actual": "data-gathering",
        }
    ]


def test_add_rhodes_site_note_mentions_owner_user_id() -> None:
    client = FakeRhodesClient()

    result = add_rhodes_site_note(
        site_id="SITE1",
        body="AutomationEvent v1\nKind: document_registration_failed",
        owner_user_id="USER1",
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "created"
    assert result["rhodes_note_id"] == "NOTE1"
    assert result["owner_notification"] == "mentioned"
    assert client.notes[0]["mentions"] == ["USER1"]
    assert result["readback"] == {
        "status": "verified",
        "rhodes_note_id": "NOTE1",
        "matched_by": "note_id",
        "mentioned_user_ids": ["USER1"],
    }


def test_add_rhodes_site_note_mentions_owner_and_extra_users() -> None:
    client = FakeRhodesClient()

    result = add_rhodes_site_note(
        site_id="SITE1",
        body="AutomationEvent v1\nKind: raycon_followup_alert",
        owner_user_id="OWNER1",
        extra_mention_user_ids=["GREG1", "OWNER1", ""],
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "created"
    assert result["owner_notification"] == "mentioned"
    assert result["mentioned_user_ids"] == ["OWNER1", "GREG1"]
    assert client.notes[0]["mentions"] == ["OWNER1", "GREG1"]


def test_add_rhodes_site_note_accepts_nested_note_id_response() -> None:
    client = FakeRhodesClient(note_response={"note": {"id": "NOTE-NESTED"}})

    result = add_rhodes_site_note(
        site_id="SITE1",
        body="AutomationEvent v1\nKind: raycon_followup_alert",
        owner_user_id="OWNER1",
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "created"
    assert result["rhodes_note_id"] == "NOTE-NESTED"
    assert result["owner_notification"] == "mentioned"
    assert result["readback"] == {
        "status": "verified",
        "rhodes_note_id": "NOTE-NESTED",
        "matched_by": "note_id",
        "mentioned_user_ids": ["OWNER1"],
    }
    assert client.calls == [
        (
            "add_site_note",
            {
                "site_id": "SITE1",
                "site_slug": "",
                "body": "AutomationEvent v1\nKind: raycon_followup_alert",
                "mentions": "OWNER1",
            },
        ),
        (
            "list_notes",
            {
                "site_id": "SITE1",
                "site_slug": "",
                "limit": "50",
            },
        ),
    ]


def test_add_rhodes_site_note_requires_returned_note_id() -> None:
    client = FakeRhodesClient(
        note_response={
            "status": "rejected",
            "rejectionReason": "Action requires confirmation",
            "text": "created",
        }
    )

    result = add_rhodes_site_note(
        site_id="SITE1",
        body="AutomationEvent v1\nKind: raycon_followup_alert",
        owner_user_id="OWNER1",
        extra_mention_user_ids=["GREG1"],
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "failed"
    assert result["reason"] == "missing_note_id"
    assert result["owner_notification"] == "none"
    assert result["rhodes_note_id"] == ""
    assert result["mentioned_user_ids"] == ["OWNER1", "GREG1"]
    assert result["note_response_summaries"] == [
        {
            "attempt": "site_id",
            "type": "dict",
            "has_note_id": False,
            "keys": ["rejectionReason", "status", "text"],
            "status": "rejected",
            "rejectionReason": "Action requires confirmation",
            "text_prefix": "created",
        }
    ]


def test_add_rhodes_site_note_fails_when_readback_missing() -> None:
    body = "AutomationEvent v1\nKind: raycon_followup_alert"
    client = FakeRhodesClient(note_response={"_id": "NOTE-MISSING"})
    client.notes = []

    def empty_list_notes(**kwargs):
        client.calls.append(
            (
                "list_notes",
                {
                    "site_id": kwargs.get("site_id", ""),
                    "site_slug": kwargs.get("site_slug", ""),
                    "limit": str(kwargs.get("limit", "")),
                },
            )
        )
        return []

    client.list_notes = empty_list_notes  # type: ignore[method-assign]

    result = add_rhodes_site_note(
        site_id="SITE1",
        body=body,
        owner_user_id="OWNER1",
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "failed"
    assert result["reason"] == "note_readback_failed"
    assert result["rhodes_note_id"] == "NOTE-MISSING"
    assert result["readback"] == {
        "status": "failed",
        "reason": "note_not_found",
        "rhodes_note_id": "NOTE-MISSING",
    }


def test_add_rhodes_site_note_recovers_note_id_from_readback() -> None:
    body = "AutomationEvent v1\nKind: raycon_followup_alert"
    client = FakeRhodesClient(note_response={"text": "created"})
    client.notes = [
        {"_id": "NOTE-READBACK", "siteId": "SITE1", "body": body, "mentions": ["OWNER1"]}
    ]

    result = add_rhodes_site_note(
        site_id="SITE1",
        body=body,
        owner_user_id="OWNER1",
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "created"
    assert result["rhodes_note_id"] == "NOTE-READBACK"
    assert result["owner_notification"] == "mentioned"
    assert (
        "find_site_note_by_body",
        {
            "site_id": "SITE1",
            "site_slug": "",
            "body": body,
        },
    ) in client.calls


def test_add_rhodes_site_note_retries_by_slug_when_site_id_returns_no_id() -> None:
    client = FakeRhodesClient(note_responses=[{"text": "created"}, None])

    result = add_rhodes_site_note(
        site_id="SITE1",
        site_slug="alpha-test",
        body="AutomationEvent v1\nKind: raycon_followup_alert",
        owner_user_id="OWNER1",
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "created"
    assert result["rhodes_note_id"] == "NOTE1"
    assert client.calls[0] == (
        "add_site_note",
        {
            "site_id": "SITE1",
            "site_slug": "alpha-test",
            "body": "AutomationEvent v1\nKind: raycon_followup_alert",
            "mentions": "OWNER1",
        },
    )
    assert result.get("note_response_summaries") is None
    assert client.calls[2] == (
        "add_site_note",
        {
            "site_id": "",
            "site_slug": "alpha-test",
            "body": "AutomationEvent v1\nKind: raycon_followup_alert",
            "mentions": "OWNER1",
        },
    )


def test_add_rhodes_site_note_resolves_owner_email() -> None:
    client = FakeRhodesClient()
    client.users_by_email["owner@example.com"] = {
        "_id": "USER2",
        "email": "owner@example.com",
    }

    result = add_rhodes_site_note(
        site_id="SITE1",
        body="AutomationEvent v1\nKind: document_registration_failed",
        owner_email="owner@example.com",
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "created"
    assert result["owner_user_id"] == "USER2"
    assert result["owner_resolution"] == "resolved_from_email"
    assert client.notes[0]["mentions"] == ["USER2"]


def test_add_rhodes_site_note_resolves_nested_owner_user_id() -> None:
    client = FakeRhodesClient()
    client.users_by_email["owner@example.com"] = {
        "result": {"userId": "USER-NESTED"},
    }

    result = add_rhodes_site_note(
        site_id="SITE1",
        body="AutomationEvent v1\nKind: document_registration_failed",
        owner_email="owner@example.com",
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "created"
    assert result["owner_user_id"] == "USER-NESTED"
    assert result["owner_resolution"] == "resolved_from_email"
    assert client.notes[0]["mentions"] == ["USER-NESTED"]


def _aerie_client(responses: list[FakeAerieResponse]) -> tuple[AerieNotesClient, FakeAerieSession]:
    session = FakeAerieSession(responses)
    return (
        AerieNotesClient(
            cfg=AerieApiConfig(base_url="https://aerie.example/api", api_key="test-key"),
            session=session,  # type: ignore[arg-type]
        ),
        session,
    )


def test_add_rhodes_site_note_uses_headless_aerie_api_with_owner_mention() -> None:
    body = "AutomationEvent v1\nKind: dd_report_created"
    notes_client, session = _aerie_client(
        [
            FakeAerieResponse({"data": []}),
            FakeAerieResponse(
                {
                    "data": {
                        "noteId": "NOTE1",
                        "siteId": "SITE1",
                        "body": body,
                        "mentionedUserIds": ["OWNER1"],
                    }
                },
                status_code=201,
            ),
            FakeAerieResponse(
                {
                    "data": [
                        {
                            "noteId": "NOTE1",
                            "siteId": "SITE1",
                            "body": body,
                            "mentionedUserIds": ["OWNER1"],
                        }
                    ]
                }
            ),
        ]
    )

    result = add_rhodes_site_note(
        site_id="SITE1",
        body=body,
        owner_user_id="OWNER1",
        owner_email="owner@example.com",
        notes_client=notes_client,
        automation_source="ddr-test",
    )

    assert result["status"] == "created"
    assert result["rhodes_note_id"] == "NOTE1"
    assert result["owner_notification"] == "mentioned"
    assert result["mentioned_user_ids"] == ["OWNER1"]
    assert result["write_path"] == "aerie_notes_api"
    post_request = session.requests[1]
    assert post_request["method"] == "POST"
    assert post_request["json"] == {
        "anchorType": "site",
        "body": body,
        "siteId": "SITE1",
        "anchorId": "SITE1",
        "mentions": ["OWNER1"],
        "automationSource": "ddr-test",
        "decisionmakerUserId": "OWNER1",
    }


def test_add_rhodes_site_note_requires_owner_user_id_for_headless_path() -> None:
    result = add_rhodes_site_note(
        site_id="SITE1",
        body="AutomationEvent v1\nKind: dd_report_created",
    )

    assert result["status"] == "failed"
    assert result["reason"] == "missing_owner_user_id"
    assert result["write_path"] == "aerie_notes_api"


def test_add_rhodes_site_note_dedupes_existing_aerie_note_with_owner_mention() -> None:
    body = "AutomationEvent v1\nKind: dd_report_created"
    notes_client, session = _aerie_client(
        [
            FakeAerieResponse(
                {
                    "data": [
                        {
                            "noteId": "NOTE-EXISTING",
                            "siteId": "SITE1",
                            "body": body,
                            "mentionedUserIds": ["OWNER1"],
                        }
                    ]
                }
            )
        ]
    )

    result = add_rhodes_site_note(
        site_id="SITE1",
        body=body,
        owner_user_id="OWNER1",
        notes_client=notes_client,
    )

    assert result["status"] == "created"
    assert result["reason"] == "already_exists"
    assert result["rhodes_note_id"] == "NOTE-EXISTING"
    assert result["idempotency_status"] == "matched_existing"
    assert [request["method"] for request in session.requests] == ["GET"]


def test_add_rhodes_site_note_fails_when_aerie_readback_lacks_owner_mention() -> None:
    body = "AutomationEvent v1\nKind: dd_report_created"
    notes_client, _session = _aerie_client(
        [
            FakeAerieResponse({"data": []}),
            FakeAerieResponse(
                {
                    "data": {
                        "noteId": "NOTE1",
                        "siteId": "SITE1",
                        "body": body,
                        "mentionedUserIds": [],
                    }
                },
                status_code=201,
            ),
            FakeAerieResponse(
                {
                    "data": [
                        {
                            "noteId": "NOTE1",
                            "siteId": "SITE1",
                            "body": body,
                            "mentionedUserIds": [],
                        }
                    ]
                }
            ),
        ]
    )

    result = add_rhodes_site_note(
        site_id="SITE1",
        body=body,
        owner_user_id="OWNER1",
        notes_client=notes_client,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "note_readback_failed"
    assert result["readback"]["reason"] == "note_mentions_missing"
    assert result["readback"]["missing_user_ids"] == ["OWNER1"]


def test_notify_rhodes_phasing_review_mentions_p2_dri() -> None:
    from due_diligence_reporter.rhodes import notify_rhodes_phasing_review

    client = FakeRhodesClient(
        site={
            "_id": "SITE1",
            "name": "Alpha Armonk 355 Main St",
            "slug": "alpha-armonk",
            "p1Dri": {"email": "p1@example.com", "userId": "P1USER"},
            "p2Dri": {"email": "p2@example.com", "userId": "P2USER"},
        }
    )

    result = notify_rhodes_phasing_review(
        site_id="SITE1",
        workbook_name="Phase 1 Phase 2 Workbook - Alpha Armonk - 2026-07-08.xlsx",
        workbook_url="https://drive/phasing",
        auto_accepted_inputs=["Phase II deferred scope: Quality-bar completion items"],
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "created"
    assert result["mentioned_owner_user_id"] == "P2USER"
    assert result["p2_dri_found"] is True
    assert result["fallback_owner_used"] is False
    note = client.notes[0]
    assert note["mentions"] == ["P2USER"]
    assert note["body"].splitlines()[0] == "Phase 1 Phase 2 workbook review"
    assert "Action needed: Review the completed Phase 1 Phase 2 workbook." in note["body"]
    assert "Workbook: https://drive/phasing" in note["body"]
    assert "Auto-accepted inputs to scrutinize:" in note["body"]
    assert "- Phase II deferred scope: Quality-bar completion items" in note["body"]


def test_notify_rhodes_phasing_review_falls_back_to_p1_dri() -> None:
    from due_diligence_reporter.rhodes import notify_rhodes_phasing_review

    client = FakeRhodesClient(
        site={
            "_id": "SITE1",
            "name": "Alpha Test",
            "slug": "alpha-test",
            "p1Dri": {"email": "p1@example.com", "userId": "P1USER"},
        }
    )

    result = notify_rhodes_phasing_review(
        site_id="SITE1",
        workbook_name="Workbook.xlsx",
        workbook_url="https://drive/phasing",
        client=client,  # type: ignore[arg-type]
    )

    assert result["status"] == "created"
    assert result["mentioned_owner_user_id"] == "P1USER"
    assert result["p2_dri_found"] is False
    assert client.notes[0]["mentions"] == ["P1USER"]
    assert "Auto-accepted inputs" not in client.notes[0]["body"]
