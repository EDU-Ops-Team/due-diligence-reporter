from __future__ import annotations

import json
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
                "milestones": {
                    "conductingDiligence": {"status": "active"},
                    "acquireProperty": {"status": "notStarted"},
                },
            },
            {
                "_id": "SITE2",
                "name": "Alpha Tulsa 6940 S Utica Ave",
                "slug": "alpha-tulsa",
                "stage": "diligence",
                "status": "active",
                "milestones": {
                    "conductingDiligence": {"status": "completed"},
                    "acquireProperty": {"status": "active"},
                },
            },
        ]
        self.missing_documents: dict[str, list[dict[str, Any]]] = {
            "SITE1": [
                {
                    "key": "conductingDiligence",
                    "label": "Conducting Diligence",
                    "missingRequired": [],
                    "presentRequired": [],
                    "presentRequiredCount": 0,
                    "requiredCount": 0,
                },
                {
                    "key": "acquireProperty",
                    "label": "Acquiring Property",
                    "missingRequired": [{"docType": "lease", "label": "Lease"}],
                    "presentRequired": [],
                    "presentRequiredCount": 0,
                    "requiredCount": 1,
                },
            ],
            "SITE2": [
                {
                    "key": "acquireProperty",
                    "label": "Acquiring Property",
                    "missingRequired": [
                        {
                            "docType": "propertyConditionAssessment",
                            "label": "Property Condition Assessment",
                        },
                        {"docType": "floorPlan", "label": "Floor Plan"},
                    ],
                    "presentRequired": [
                        {
                            "docType": "siteInvestigationReport",
                            "label": "Site Investigation Report",
                        }
                    ],
                    "presentRequiredCount": 1,
                    "requiredCount": 3,
                }
            ],
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

    def get_site(self, *, site_id: str) -> dict[str, Any]:
        return next(site for site in self.sites if site["_id"] == site_id)

    def get_missing_documents(self, *, site_id: str) -> dict[str, Any]:
        return {"milestones": self.missing_documents[site_id], "siteId": site_id}

    def list_notes(self, *, site_id: str = "", **_: Any) -> list[dict[str, Any]]:
        return self.notes[site_id]

    def list_tasks(self, *, site_id: str, **_: Any) -> list[dict[str, Any]]:
        return self.tasks[site_id]

    def resolve_drive_root(self, *, site_id: str) -> tuple[str, str]:
        if site_id == "SITE2":
            raise RhodesError("Rhodes site has no linked Google Drive folder")
        return "DRIVE1", "https://drive.google.com/drive/folders/DRIVE1"


class SnapshotReadErrorClient(FakeRhodesPortfolioClient):
    def list_notes(self, *, site_id: str = "", **_: Any) -> list[dict[str, Any]]:
        if site_id == "SITE2":
            raise RhodesError("notes failed for person@example.com at https://internal.example")
        return super().list_notes(site_id=site_id, **_)


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
    assert tulsa["required_documents"]["milestone"]["key"] == "acquireProperty"
    assert tulsa["open_automation_failures"][0]["kind"] == "raycon_followup_alert"
    assert tulsa["pending_review_tasks"][0]["task_id"] == "TASK1"
    actions = {action["gap_type"]: action for action in tulsa["remediation_actions"]}
    assert set(actions) == {
        "missing_p1_dri",
        "missing_drive_folder",
        "missing_current_milestone_documents",
        "open_automation_failures",
        "pending_review_tasks",
    }
    assert actions["missing_p1_dri"]["schema_version"] == "action_record.v1"
    assert actions["missing_p1_dri"]["owning_workflow"] == "aadp"
    assert actions["missing_p1_dri"]["status"] == "queued"
    assert "current P1 DRI" in actions["missing_p1_dri"]["evidence_summary"]
    assert actions["missing_drive_folder"]["owning_workflow"] == "aadp"
    assert "linked site Drive folder" in actions["missing_drive_folder"]["evidence_summary"]
    assert actions["missing_current_milestone_documents"]["owning_workflow"] == "ddr"
    assert (
        actions["missing_current_milestone_documents"]["workflow_owner"]
        == "drive-rhodes-reconciliation"
    )
    assert actions["missing_current_milestone_documents"]["status"] == "queued"
    assert "Drive Rhodes Reconciliation" in actions[
        "missing_current_milestone_documents"
    ]["action_requested"]
    assert (
        "no later Rhodes/Drive readback"
        in actions["missing_current_milestone_documents"]["evidence_summary"]
    )
    assert actions["missing_current_milestone_documents"]["retryable"] is True
    assert actions["open_automation_failures"]["owning_workflow"] == "ddr"
    assert actions["pending_review_tasks"]["owning_workflow"] == "rhodes"
    assert "propertyConditionAssessment" not in json.dumps(actions)
    assert "floorPlan" not in json.dumps(actions)

    austin = result["sites"][1]
    assert austin["required_documents"]["missing"] == []
    assert austin["required_documents"]["milestone"]["key"] == "conductingDiligence"
    assert austin["owner_routing_status"] == "owner_routed"
    assert austin["latest_ddr_status"]["status"] == "created"
    assert (
        austin["latest_source_event_fingerprint"]
        == "edu-ops-email-router:owner_added_to_thread:gmail-1"
    )
    assert "remediation_actions" not in austin


def test_portfolio_snapshot_can_filter_clean_sites() -> None:
    result = build_portfolio_automation_gap_snapshot(
        client=FakeRhodesPortfolioClient(),  # type: ignore[arg-type]
        include_clean=False,
    )

    assert [site["site_id"] for site in result["sites"]] == ["SITE2"]
    assert result["totals"]["sites"] == 1


def test_portfolio_snapshot_routes_snapshot_read_errors_without_sensitive_details() -> None:
    result = build_portfolio_automation_gap_snapshot(
        client=SnapshotReadErrorClient(),  # type: ignore[arg-type]
        include_clean=False,
    )

    tulsa = result["sites"][0]
    actions = {action["gap_type"]: action for action in tulsa["remediation_actions"]}

    assert "snapshot_read_errors" in tulsa["gap_reasons"]
    assert actions["snapshot_read_errors"]["schema_version"] == "action_record.v1"
    assert actions["snapshot_read_errors"]["owning_workflow"] == "rhodes"
    assert actions["snapshot_read_errors"]["status"] == "needs_review"
    assert "sanitized Rhodes snapshot read error" in actions["snapshot_read_errors"]["evidence_summary"]
    assert actions["snapshot_read_errors"]["retryable"] is True
    rendered = json.dumps(actions["snapshot_read_errors"])
    assert "person@example.com" not in rendered
    assert "internal.example" not in rendered
