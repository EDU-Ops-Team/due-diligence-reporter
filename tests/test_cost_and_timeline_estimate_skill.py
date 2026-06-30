"""Tests for the standalone cost and timeline estimate skill."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "docs" / "skills" / "cost-and-timeline-estimate" / "scripts" / "estimate.py"


def _load_estimator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cost_and_timeline_estimate", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ESTIMATOR = _load_estimator()


def test_estimates_cost_timeline_and_downstream_fields_from_rhodes_capacity() -> None:
    result = ESTIMATOR.estimate(
        {
            "rhodes_site": {
                "id": "site-1",
                "slug": "alpha-example",
                "name": "Alpha Example",
                "address": "Austin, TX",
                "dueDiligence": {"foCapacity": "80", "maxCapCapacity": "120"},
                "grossSf": 6000,
            },
            "start_date": "2026-07-01",
        }
    )

    assert result["source_system"] == "cost_and_timeline_estimate"
    assert result["estimate_version"] == "cost_and_timeline_estimate.v1"
    assert result["warnings"] == []
    assert result["rhodes_capacity_read"]["fastest_open"] == {
        "value": 80,
        "field": "dueDiligence.foCapacity",
        "source": "rhodes_site",
    }
    assert result["rhodes_capacity_read"]["max_capacity"] == {
        "value": 120,
        "field": "dueDiligence.maxCapCapacity",
        "source": "rhodes_site",
    }

    fastest = result["scenarios"]["fastest_open"]
    max_capacity = result["scenarios"]["max_capacity"]
    assert fastest["capacity_students"] == 80
    assert fastest["planning_area_sf"] == 6000.0
    assert fastest["timeline_weeks"] == 6
    assert fastest["projected_open_date"] == "08/12/26"
    assert fastest["grand_total"] == 260870
    assert max_capacity["capacity_students"] == 120
    assert max_capacity["timeline_weeks"] == 17
    assert max_capacity["projected_open_date"] == "10/28/26"
    assert max_capacity["grand_total"] == 803694

    expected_cost_keys = {
        "demolition",
        "framing_doors",
        "mep_fire_life_safety",
        "plumbing_bathrooms",
        "finish_work",
        "furniture",
        "tech_security_signage",
        "other_hard_costs",
        "soft_costs",
        "gc_fee",
        "contingency",
        "grand_total",
    }
    assert set(fastest["cost_breakdown"]) == expected_cost_keys

    fields = result["report_data_fields"]
    assert fields["exec.fastest_open_capacity"] == "80"
    assert fields["exec.fastest_open_capex"] == "$260,870"
    assert fields["exec.fastest_open_open_date"] == "08/12/26"
    assert fields["exec.cost_demolition_fastest_open"] == "$5,100"
    assert fields["exec.cost_grand_total_fastest_open"] == "$260,870"
    assert fields["exec.max_capacity_capacity"] == "120"
    assert fields["exec.max_capacity_capex"] == "$803,694"
    assert fields["exec.cost_mep_fire_life_safety_max_capacity"] == "$168,000"
    assert fields["exec.cost_grand_total_max_capacity"] == "$803,694"

    downstream = result["downstream_inputs"]
    assert downstream["site"] == {
        "name": "Alpha Example",
        "slug": "alpha-example",
        "id": "site-1",
        "address": "Austin, TX",
    }
    assert downstream["capacity"]["source"] == "rhodes_locationos"
    assert downstream["scenarios"]["fastest_open"]["grand_total"] == 260870
    assert downstream["report_data_fields"]["exec.max_capacity_capex"] == "$803,694"


def test_applies_category_allowance_and_timeline_overrides() -> None:
    result = ESTIMATOR.estimate(
        {
            "rhodes_site": {
                "dueDiligence": {"foCapacity": 50, "maxCapCapacity": 75},
            },
            "gross_sf": 4000,
            "start_date": "2026-07-01",
            "overrides": {
                "fastest_open": {
                    "category_overrides": {"mep_fire_life_safety": 50000},
                    "additional_allowances": {"other_hard_costs": 10000},
                    "timeline_weeks": 9,
                }
            },
        }
    )

    fastest = result["scenarios"]["fastest_open"]
    assert fastest["cost_breakdown"]["mep_fire_life_safety"] == 50000
    assert fastest["cost_breakdown"]["other_hard_costs"] == 16800
    assert fastest["timeline_weeks"] == 9
    assert fastest["projected_open_date"] == "09/02/26"
    assert fastest["grand_total"] == 229824
    assert result["report_data_fields"]["exec.fastest_open_open_date"] == "09/02/26"


def test_cli_reads_payload_and_writes_json(tmp_path: Path) -> None:
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps(
            {
                "rhodes_site": {
                    "name": "Alpha CLI",
                    "dueDiligence": {"foCapacity": 20, "maxCapCapacity": 35},
                },
                "gross_sf": 2500,
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(payload_path)],
        capture_output=True,
        check=True,
        text=True,
    )
    result: dict[str, Any] = json.loads(completed.stdout)

    assert result["source_system"] == "cost_and_timeline_estimate"
    assert result["scenarios"]["fastest_open"]["capacity_students"] == 20
    assert result["scenarios"]["max_capacity"]["capacity_students"] == 35
    assert result["report_data_fields"]["exec.fastest_open_open_date"] == "6 weeks"
    assert "No start_date supplied" in " ".join(result["warnings"])


def test_missing_rhodes_capacity_blocks_scenarios_without_deriving_counts() -> None:
    result = ESTIMATOR.estimate(
        {
            "rhodes_site": {
                "name": "Alpha Missing Capacity",
                "dueDiligence": {},
            },
            "gross_sf": 2500,
        }
    )

    assert result["scenarios"] == {}
    assert result["report_data_fields"] == {}
    assert result["rhodes_capacity_read"]["fastest_open"]["source"] == "missing"
    assert result["rhodes_capacity_read"]["max_capacity"]["source"] == "missing"
    assert result["assumptions"]["capacity_quality_evaluated"] is False
    assert "Missing Rhodes fastest_open capacity" in " ".join(result["warnings"])
