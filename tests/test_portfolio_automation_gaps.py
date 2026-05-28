from __future__ import annotations

from typing import Any

from due_diligence_reporter.portfolio_automation_gaps import (
    build_portfolio_automation_gap_snapshot,
)
from due_diligence_reporter.rhodes import RhodesError


class FakeRhodesPortfolioClient:
    def __init__(self) -> None:
        self.sites = [
            {
                "_id": "SITE1",
                "name": "Alpha Austin 123 Main St",
                "slug": "alpha-austin",
                "stage": "diligence",
                "status": "active",
                "p1Dri": {"userId": "USER1", "name": "Owner One", "email": "owner@example.com"},
            },
            {
                "_id": "SITE2",
                "name": "Alpha Tulsa 6940 S Utica Ave",
                "slug": "alpha-tulsa",
                "stage": "diligence",
                "status": "active",
            },
        ]
        self.documents: dict[str, list[dict[str, Any]]] = {
            "SITE1": [
                {"_id": "DOC1", "title": "SIR", "docType": "siteInvestigationReport"},
                {"_id": "DOC2", "title": "BI", "docType": "propertyConditionAssessment"},
                {"_id": "DOC3", "title": "Plan", "docType": "floorPlan"},
            ],
            "SITE2": [{"_id": "DOC4", "title": "SIR", "docType": "siteInvestigationReport"}],
        }
        self.notes: dict[str, list[dict[str, Any]]] = {
            "SITE1": [
                {
                    "_id": "NOTE1",
                    "body": "\n".join(
                        [
                            "AutomationEvent v1",
                            "Source: due-diligence-reporter",
                            "Source ID: run-1",
                            "Kind: dd_report_created",
                            "Site: Alpha Austin 123 Main St",
                            "Site ID: SITE1",
                            "Decision required: no",
                            "Mutation status: created",
                            "Created at: 2026-05-28T15:00:00+00:00",
                        ]
                    ),
                },
                {
                    "_id": "NOTE2",
                    "body": "\n".join(
                        [
                            "AutomationEvent v1",
                            "Source: edu-ops-email-router",
                            "Source ID: gmail-1",
                            "Kind: owner_added_to_thread",
                            "Site: Alpha Austin 123 Main St",
                            "Site ID: SITE1",
                            "Decision required: no",
                            "Mutation status: success",
                            "Created at: 2026-05-28T15:05:00+00:00",
                        ]
                    ),
                },
            ],
            "SITE2": [
                {
                    "_id": "NOTE3",
                    "body": "\n".join(
                        [
                            "AutomationEvent v1",
                            "Source: due-diligence-reporter",
                            "Source ID: raycon-1",
                            "Kind: raycon_followup_alert",
                            "Site: Alpha Tulsa 6940 S Utica Ave",
                            "Site ID: SITE2",
                            "Decision required: yes",
                            "Requested decision: review RayCon follow-up alert",
                            "Mutation status: failed",
                            "Created at: 2026-05-28T16:00:00+00:00",
                        ]
                    ),
                }
            ],
        }
        self.tasks: dict[str, list[dict[str, Any]]] = {
            "SITE1": [],
            "SITE2": [
                {
                    "_id": "TASK1",
                    "title": "Assign P1 DRI for Alpha Tulsa",
                    "status": "new",
                    "tag": "rhodes_data_repair",
                    "description": "AutomationEvent v1\nKind: owner_missing",
                }
            ],
        }

    def list_sites(self, *, status: str | None = "active") -> list[dict[str, Any]]:
        assert status == "active"
        return self.sites

    def list_documents(self, *, site_id: str, **_: Any) -> list[dict[str, Any]]:
        return self.documents[site_id]

    def list_notes(self, *, site_id: str = "", **_: Any) -> list[dict[str, Any]]:
        return self.notes[site_id]

    def list_tasks(self, *, site_id: str, **_: Any) -> list[dict[str, Any]]:
        return self.tasks[site_id]

    def resolve_drive_root(self, *, site_id: str) -> tuple[str, str]:
        if site_id == "SITE2":
            raise RhodesError("Rhodes site has no linked Google Drive folder")
        return "DRIVE1", "https://drive.google.com/drive/folders/DRIVE1"


def test_portfolio_snapshot_rolls_up_automation_gaps() -> None:
    result = build_portfolio_automation_gap_snapshot(
        client=FakeRhodesPortfolioClient(),  # type: ignore[arg-type]
    )

    assert result["status"] == "success"
    assert result["system_of_record"] == "rhodes"
    assert result["totals"]["sites"] == 2
    assert result["totals"]["sites_with_gaps"] == 1
    assert result["totals"]["missing_p1_dri"] == 1
    assert result["totals"]["missing_drive_folder"] == 1
    assert result["totals"]["missing_required_documents"] == 1
    assert result["totals"]["open_automation_failures"] == 1
    assert result["totals"]["pending_review_tasks"] == 1

    tulsa = result["sites"][0]
    assert tulsa["site_id"] == "SITE2"
    assert tulsa["owner_routing_status"] == "missing_owner"
    assert tulsa["drive_folder"]["status"] == "missing"
    assert tulsa["required_documents"]["missing"] == [
        "propertyConditionAssessment",
        "floorPlan",
    ]
    assert tulsa["open_automation_failures"][0]["kind"] == "raycon_followup_alert"
    assert tulsa["pending_review_tasks"][0]["task_id"] == "TASK1"

    austin = result["sites"][1]
    assert austin["owner_routing_status"] == "owner_routed"
    assert austin["latest_ddr_status"]["status"] == "created"
    assert (
        austin["latest_source_event_fingerprint"]
        == "edu-ops-email-router:owner_added_to_thread:gmail-1"
    )


def test_portfolio_snapshot_can_filter_clean_sites() -> None:
    result = build_portfolio_automation_gap_snapshot(
        client=FakeRhodesPortfolioClient(),  # type: ignore[arg-type]
        include_clean=False,
    )

    assert [site["site_id"] for site in result["sites"]] == ["SITE2"]
    assert result["totals"]["sites"] == 1
