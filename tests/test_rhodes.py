from __future__ import annotations

from typing import Any

from due_diligence_reporter.rhodes import (
    RhodesError,
    list_rhodes_site_records,
    lookup_rhodes_site_owner,
    map_ddr_doc_type_to_rhodes,
    register_rhodes_document_for_upload,
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
        self.documents: list[dict[str, Any]] = []
        self.registered_documents: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, str]]] = []

    def resolve_site(self, *, name: str = "", address: str = "") -> dict[str, Any] | None:
        self.calls.append(("resolve_site", {"name": name, "address": address}))
        return {"siteId": "SITE1", "name": name, "slug": "alpha-test"}

    def get_site(self, *, site_id: str | None = None, slug: str | None = None) -> dict[str, Any]:
        self.calls.append(("get_site", {"site_id": site_id or "", "slug": slug or ""}))
        return self.site

    def list_sites(self, *, status: str | None = "active") -> list[dict[str, Any]]:
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
