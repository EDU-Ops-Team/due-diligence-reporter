from __future__ import annotations

from typing import Any

from due_diligence_reporter.rhodes import (
    RhodesClient,
    RhodesError,
    add_rhodes_site_note,
    list_rhodes_site_records,
    lookup_rhodes_site_owner,
    map_ddr_doc_type_to_rhodes,
    register_rhodes_document_for_upload,
)


class FakeRhodesClient:
    def __init__(
        self,
        site: dict[str, Any] | None = None,
        resolved_site: dict[str, Any] | None = None,
        sites: list[dict[str, Any]] | None = None,
        drive_root: tuple[str, str] | Exception | None = None,
        note_response: dict[str, Any] | None = None,
        note_responses: list[dict[str, Any] | None] | None = None,
    ) -> None:
        self.site = site or {}
        self.resolved_site = resolved_site
        self.sites = sites or []
        self.drive_root = drive_root
        self.note_response = note_response
        self.note_responses = list(note_responses or [])
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
                return response
        if self.note_response is not None:
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
    assert alpha_phasing_mapping.doc_type == "other"
    assert alpha_phasing_mapping.milestone == "acquireProperty"


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

    result = register_rhodes_document_for_upload(
        site_id="SITE1",
        ddr_doc_type="sir",
        title="Alpha Keller SIR.pdf",
        drive_file_id="drive-file-1",
    )

    assert result["status"] == "failed"
    assert result["reason"] == "rhodes_error"
    assert "RHODES_API_KEY" in result["error"]


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
    assert client.calls == [
        (
            "add_site_note",
            {
                "site_id": "SITE1",
                "site_slug": "",
                "body": "AutomationEvent v1\nKind: raycon_followup_alert",
                "mentions": "OWNER1",
            },
        )
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


def test_add_rhodes_site_note_recovers_note_id_from_readback() -> None:
    body = "AutomationEvent v1\nKind: raycon_followup_alert"
    client = FakeRhodesClient(note_response={"text": "created"})
    client.notes = [{"_id": "NOTE-READBACK", "siteId": "SITE1", "body": body}]

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
