"""Estimate ROM cost and timeline from Rhodes capacity counts."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

SOURCE_SYSTEM = "cost_and_timeline_estimate"
ESTIMATE_VERSION = "cost_and_timeline_estimate.v1"
CAPACITY_SOURCE_SYSTEM = "rhodes_locationos"

BREAKDOWN_ROWS: tuple[tuple[str, str], ...] = (
    ("demolition", "Demolition"),
    ("framing_doors", "Framing / Doors"),
    ("mep_fire_life_safety", "MEP / Fire / Life Safety"),
    ("plumbing_bathrooms", "Plumbing / Bathrooms"),
    ("finish_work", "Finish Work"),
    ("furniture", "Furniture"),
    ("tech_security_signage", "Tech / Security / Signage"),
    ("other_hard_costs", "Other Hard Costs"),
    ("soft_costs", "Soft Costs"),
    ("gc_fee", "GC Fee"),
    ("contingency", "Contingency"),
    ("grand_total", "Grand Total"),
)

HARD_COST_KEYS: tuple[str, ...] = tuple(key for key, _ in BREAKDOWN_ROWS[:8])
AGGREGATE_KEYS: tuple[str, ...] = tuple(key for key, _ in BREAKDOWN_ROWS[8:])
SF_CONSTRUCTION_KEYS: frozenset[str] = frozenset(
    {
        "demolition",
        "framing_doors",
        "mep_fire_life_safety",
        "finish_work",
        "other_hard_costs",
    }
)
BREAKDOWN_LABELS: dict[str, str] = dict(BREAKDOWN_ROWS)

COMPLEXITY_MULTIPLIERS: dict[str, float] = {
    "light": 0.85,
    "standard": 1.0,
    "heavy": 1.25,
}

LOCATION_MULTIPLIERS: tuple[tuple[str, float], ...] = (
    ("new york", 1.35),
    ("nyc", 1.35),
    ("san francisco", 1.3),
    ("bay area", 1.3),
    ("los angeles", 1.25),
    ("boston", 1.25),
    ("seattle", 1.18),
    ("washington", 1.15),
    ("chicago", 1.1),
    ("denver", 1.1),
    ("miami", 1.1),
    ("austin", 1.0),
    ("dallas", 1.0),
    ("houston", 1.0),
    ("tampa", 1.0),
    ("orlando", 1.0),
    ("phoenix", 1.0),
    ("charlotte", 0.95),
)

CAPACITY_FIELD_PATHS: dict[str, tuple[str, ...]] = {
    "fastest_open": (
        "dueDiligence.foCapacity",
        "due_diligence.fastest_open_capacity",
        "dueDiligence.fastestOpen.capacity",
        "dueDiligence.fastestOpenCapacity",
        "exec.fastest_open_capacity",
    ),
    "max_capacity": (
        "dueDiligence.maxCapCapacity",
        "due_diligence.max_capacity_capacity",
        "dueDiligence.maxCapacity.capacity",
        "dueDiligence.maxCapacityCapacity",
        "exec.max_capacity_capacity",
    ),
}

SCENARIO_DEFAULTS: dict[str, dict[str, Any]] = {
    "fastest_open": {
        "label": "Fastest Open",
        "report_suffix": "fastest_open",
        "capacity_keys": (
            "fastest_open_capacity",
            "fast_path_capacity",
            "strict_capacity",
            "foCapacity",
            "due_diligence.fastest_open_capacity",
            "dueDiligence.fastestOpen.capacity",
            "dueDiligence.fastestOpenCapacity",
            "exec.fastest_open_capacity",
        ),
        "complexity": "light",
        "sf_per_student": 55.0,
        "permit_weeks": 2,
        "mobilization_weeks": 0,
        "construction_weeks": 4,
        "closeout_weeks": 0,
        "restroom_delta": 0,
        "restroom_cost": 25_000.0,
        "soft_rate": 0.10,
        "gc_rate": 0.10,
        "contingency_rate": 0.12,
        "rates": {
            "demolition": 1.0,
            "framing_doors": 2.0,
            "mep_fire_life_safety": 6.0,
            "finish_work": 10.0,
            "other_hard_costs": 2.0,
            "furniture_per_student": 750.0,
            "tech_base": 15_000.0,
            "tech_per_student": 150.0,
        },
    },
    "max_capacity": {
        "label": "Max Capacity",
        "report_suffix": "max_capacity",
        "capacity_keys": (
            "max_capacity",
            "maximum_capacity",
            "max_capacity_capacity",
            "maxCapCapacity",
            "due_diligence.max_capacity_capacity",
            "dueDiligence.maxCapacity.capacity",
            "dueDiligence.maxCapacityCapacity",
            "exec.max_capacity_capacity",
        ),
        "complexity": "standard",
        "sf_per_student": 55.0,
        "permit_weeks": 6,
        "mobilization_weeks": 1,
        "construction_weeks": 10,
        "closeout_weeks": 0,
        "restroom_delta": 1,
        "restroom_cost": 30_000.0,
        "soft_rate": 0.12,
        "gc_rate": 0.12,
        "contingency_rate": 0.15,
        "rates": {
            "demolition": 4.0,
            "framing_doors": 14.0,
            "mep_fire_life_safety": 28.0,
            "finish_work": 14.0,
            "other_hard_costs": 4.0,
            "furniture_per_student": 900.0,
            "tech_base": 20_000.0,
            "tech_per_student": 180.0,
        },
    },
}


def estimate(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build a standalone Rhodes-sourced cost and timeline estimate."""

    warnings: list[str] = []
    scenarios: dict[str, Any] = {}
    report_data_fields: dict[str, str] = {}
    rhodes_capacity_read = _rhodes_capacity_read(payload)

    for scenario_key in ("fastest_open", "max_capacity"):
        capacity = _extract_capacity(payload, scenario_key, rhodes_capacity_read)
        if capacity is None:
            warnings.append(
                f"Missing Rhodes {scenario_key} capacity; scenario omitted."
            )
            continue
        if capacity <= 0:
            warnings.append(f"Rhodes {scenario_key} capacity must be positive; scenario omitted.")
            continue

        scenario = _estimate_scenario(payload, scenario_key, capacity, warnings)
        scenarios[scenario_key] = scenario
        report_data_fields.update(_report_fields_for_scenario(scenario_key, scenario))

    input_summary = _input_summary(payload, rhodes_capacity_read)
    downstream_inputs = _downstream_inputs(input_summary, scenarios, report_data_fields, warnings)

    return {
        "source_system": SOURCE_SYSTEM,
        "estimate_version": ESTIMATE_VERSION,
        "input_summary": input_summary,
        "rhodes_capacity_read": rhodes_capacity_read,
        "scenarios": scenarios,
        "report_data_fields": report_data_fields,
        "downstream_inputs": downstream_inputs,
        "warnings": warnings,
        "assumptions": {
            "capacity_quality_evaluated": False,
            "capacity_source": _capacity_source_summary(rhodes_capacity_read),
            "area_fallback_sf_per_student": 55,
            "cost_model": "ROM hard costs plus soft costs, GC fee, and contingency",
        },
    }


def _estimate_scenario(
    payload: Mapping[str, Any],
    scenario_key: str,
    capacity: int,
    warnings: list[str],
) -> dict[str, Any]:
    defaults = SCENARIO_DEFAULTS[scenario_key]
    overrides = _scenario_overrides(payload, scenario_key)
    label = str(defaults["label"])
    rates = cast(dict[str, float], defaults["rates"])

    area_sf, area_source = _planning_area_sf(payload, overrides, capacity, defaults, warnings)
    location_multiplier, location_source = _location_multiplier(payload, overrides)
    complexity = str(overrides.get("complexity") or defaults["complexity"]).strip().lower()
    complexity_multiplier = COMPLEXITY_MULTIPLIERS.get(complexity)
    if complexity_multiplier is None:
        warnings.append(
            f"Unknown {scenario_key} complexity '{complexity}'; using standard complexity."
        )
        complexity = "standard"
        complexity_multiplier = COMPLEXITY_MULTIPLIERS[complexity]

    breakdown = {
        "demolition": area_sf * rates["demolition"] * complexity_multiplier * location_multiplier,
        "framing_doors": area_sf
        * rates["framing_doors"]
        * complexity_multiplier
        * location_multiplier,
        "mep_fire_life_safety": area_sf
        * rates["mep_fire_life_safety"]
        * complexity_multiplier
        * location_multiplier,
        "plumbing_bathrooms": _overrideable_number(
            overrides,
            "restroom_delta",
            float(defaults["restroom_delta"]),
        )
        * _overrideable_number(overrides, "restroom_cost", float(defaults["restroom_cost"]))
        * location_multiplier,
        "finish_work": area_sf * rates["finish_work"] * complexity_multiplier * location_multiplier,
        "furniture": capacity * rates["furniture_per_student"],
        "tech_security_signage": rates["tech_base"] + (capacity * rates["tech_per_student"]),
        "other_hard_costs": area_sf
        * rates["other_hard_costs"]
        * complexity_multiplier
        * location_multiplier,
    }
    _apply_category_overrides(breakdown, overrides, HARD_COST_KEYS, scenario_key, warnings)
    _apply_additional_allowances(breakdown, overrides, HARD_COST_KEYS, scenario_key, warnings)

    rounded_breakdown: dict[str, int | float] = {
        key: _whole_dollars(value) for key, value in breakdown.items()
    }
    hard_subtotal = sum(rounded_breakdown[key] for key in HARD_COST_KEYS)
    soft_costs = _whole_dollars(hard_subtotal * _rate(overrides, "soft_rate", defaults))
    gc_fee = _whole_dollars(hard_subtotal * _rate(overrides, "gc_rate", defaults))
    contingency_base = hard_subtotal + soft_costs + gc_fee
    contingency = _whole_dollars(
        contingency_base * _rate(overrides, "contingency_rate", defaults)
    )

    rounded_breakdown["soft_costs"] = soft_costs
    rounded_breakdown["gc_fee"] = gc_fee
    rounded_breakdown["contingency"] = contingency
    _apply_category_overrides(rounded_breakdown, overrides, AGGREGATE_KEYS, scenario_key, warnings)
    _apply_additional_allowances(rounded_breakdown, overrides, AGGREGATE_KEYS, scenario_key, warnings)
    rounded_breakdown["grand_total"] = sum(
        rounded_breakdown[key]
        for key in (*HARD_COST_KEYS, "soft_costs", "gc_fee", "contingency")
    )
    _apply_category_overrides(
        rounded_breakdown,
        overrides,
        ("grand_total",),
        scenario_key,
        warnings,
    )

    timeline_weeks, timeline_detail = _timeline_weeks(defaults, overrides)
    projected_open_date = _projected_open_date(payload, timeline_weeks, warnings)

    return {
        "label": label,
        "source_system": SOURCE_SYSTEM,
        "capacity_students": capacity,
        "planning_area_sf": _round_number(area_sf, 2),
        "planning_area_source": area_source,
        "location_multiplier": location_multiplier,
        "location_multiplier_source": location_source,
        "complexity": complexity,
        "complexity_multiplier": complexity_multiplier,
        "timeline_weeks": timeline_weeks,
        "timeline_detail": timeline_detail,
        "projected_open_date": projected_open_date,
        "cost_breakdown": rounded_breakdown,
        "categories": [
            {
                "key": key,
                "category": BREAKDOWN_LABELS[key],
                "subtotal": rounded_breakdown[key],
            }
            for key in HARD_COST_KEYS
        ],
        "hard_cost_subtotal": hard_subtotal,
        "soft_costs": rounded_breakdown["soft_costs"],
        "gc_fee": rounded_breakdown["gc_fee"],
        "contingency": rounded_breakdown["contingency"],
        "grand_total": rounded_breakdown["grand_total"],
        "cost_per_sf": _round_number(rounded_breakdown["grand_total"] / area_sf, 2),
        "cost_per_student": _round_number(rounded_breakdown["grand_total"] / capacity, 2),
        "assumptions": {
            "soft_rate": _rate(overrides, "soft_rate", defaults),
            "gc_rate": _rate(overrides, "gc_rate", defaults),
            "contingency_rate": _rate(overrides, "contingency_rate", defaults),
        },
    }


def _rhodes_capacity_read(payload: Mapping[str, Any]) -> dict[str, Any]:
    site = _rhodes_site(payload)
    read: dict[str, Any] = {
        "source_system": CAPACITY_SOURCE_SYSTEM,
        "site": _site_summary(payload),
    }
    for scenario_key in ("fastest_open", "max_capacity"):
        field_path, raw_value = _first_path_value(site, CAPACITY_FIELD_PATHS[scenario_key])
        value = _coerce_float(raw_value)
        if value is not None:
            read[scenario_key] = {
                "value": _whole_number(value),
                "field": field_path,
                "source": "rhodes_site",
            }
            continue

        manual_value = _manual_capacity(payload, scenario_key)
        if manual_value is not None:
            read[scenario_key] = {
                "value": manual_value,
                "field": "manual_override",
                "source": "manual_override",
            }
        else:
            read[scenario_key] = {
                "value": None,
                "field": CAPACITY_FIELD_PATHS[scenario_key][0],
                "source": "missing",
            }
    return read


def _extract_capacity(
    payload: Mapping[str, Any],
    scenario_key: str,
    rhodes_capacity_read: Mapping[str, Any],
) -> int | None:
    read_value = rhodes_capacity_read.get(scenario_key)
    if isinstance(read_value, Mapping):
        capacity = _coerce_float(read_value.get("value"))
        if capacity is not None:
            return _whole_number(capacity)
    return _manual_capacity(payload, scenario_key)


def _manual_capacity(payload: Mapping[str, Any], scenario_key: str) -> int | None:
    defaults = SCENARIO_DEFAULTS[scenario_key]
    capacity_input = payload.get("capacity")
    if isinstance(capacity_input, Mapping):
        nested = _first_number(
            capacity_input,
            cast(tuple[str, ...], defaults["capacity_keys"]),
        )
        if nested is not None:
            return _whole_number(nested)

    scenario_input = payload.get(scenario_key)
    if isinstance(scenario_input, Mapping):
        nested = _first_number(
            scenario_input,
            ("capacity_students", "capacity", "students"),
        )
        if nested is not None:
            return _whole_number(nested)

    flat = _first_number(payload, cast(tuple[str, ...], defaults["capacity_keys"]))
    if flat is None:
        return None
    return _whole_number(flat)


def _input_summary(
    payload: Mapping[str, Any],
    rhodes_capacity_read: Mapping[str, Any],
) -> dict[str, Any]:
    site = _site_summary(payload)
    return {
        "site_name": site.get("name"),
        "site_slug": site.get("slug"),
        "site_id": site.get("id"),
        "site_address": site.get("address"),
        "market": _first_text(payload, ("market", "city")),
        "gross_sf": _planning_area_input(payload),
        "start_date": _string_or_none(payload.get("start_date")),
        "fastest_open_capacity": _capacity_read_value(rhodes_capacity_read, "fastest_open"),
        "max_capacity": _capacity_read_value(rhodes_capacity_read, "max_capacity"),
        "capacity_source": _capacity_source_summary(rhodes_capacity_read),
    }


def _downstream_inputs(
    input_summary: Mapping[str, Any],
    scenarios: Mapping[str, Any],
    report_data_fields: Mapping[str, str],
    warnings: Sequence[str],
) -> dict[str, Any]:
    return {
        "source_system": SOURCE_SYSTEM,
        "estimate_version": ESTIMATE_VERSION,
        "site": {
            "name": input_summary.get("site_name"),
            "slug": input_summary.get("site_slug"),
            "id": input_summary.get("site_id"),
            "address": input_summary.get("site_address"),
        },
        "capacity": {
            "source": input_summary.get("capacity_source"),
            "fastest_open_capacity": input_summary.get("fastest_open_capacity"),
            "max_capacity": input_summary.get("max_capacity"),
        },
        "scenarios": {
            scenario_key: _downstream_scenario(scenario)
            for scenario_key, scenario in scenarios.items()
            if isinstance(scenario, Mapping)
        },
        "report_data_fields": dict(report_data_fields),
        "warnings": list(warnings),
    }


def _downstream_scenario(scenario: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "capacity_students": scenario.get("capacity_students"),
        "grand_total": scenario.get("grand_total"),
        "timeline_weeks": scenario.get("timeline_weeks"),
        "projected_open_date": scenario.get("projected_open_date"),
        "planning_area_sf": scenario.get("planning_area_sf"),
        "cost_per_sf": scenario.get("cost_per_sf"),
        "cost_per_student": scenario.get("cost_per_student"),
        "cost_breakdown": dict(cast(Mapping[str, Any], scenario.get("cost_breakdown", {}))),
    }


def _capacity_read_value(
    rhodes_capacity_read: Mapping[str, Any],
    scenario_key: str,
) -> int | None:
    value = rhodes_capacity_read.get(scenario_key)
    if not isinstance(value, Mapping):
        return None
    capacity = _coerce_float(value.get("value"))
    if capacity is None:
        return None
    return _whole_number(capacity)


def _capacity_source_summary(rhodes_capacity_read: Mapping[str, Any]) -> str:
    sources = {
        str(value.get("source"))
        for key, value in rhodes_capacity_read.items()
        if key in {"fastest_open", "max_capacity"} and isinstance(value, Mapping)
    }
    if sources == {"rhodes_site"}:
        return CAPACITY_SOURCE_SYSTEM
    if "manual_override" in sources:
        return "manual_override"
    return "missing"


def _planning_area_sf(
    payload: Mapping[str, Any],
    overrides: Mapping[str, Any],
    capacity: int,
    defaults: Mapping[str, Any],
    warnings: list[str],
) -> tuple[float, str]:
    override_area = _first_number(overrides, ("gross_sf", "building_sf", "planning_area_sf"))
    if override_area is not None and override_area > 0:
        return override_area, "scenario_override"

    payload_area = _planning_area_input(payload)
    if payload_area is not None and payload_area > 0:
        return payload_area, "rhodes_or_input_gross_sf"

    area = capacity * float(defaults["sf_per_student"])
    warnings.append(
        f"No gross SF supplied for {defaults['label']}; using "
        f"{defaults['sf_per_student']} SF per student."
    )
    return area, "capacity_fallback"


def _scenario_overrides(payload: Mapping[str, Any], scenario_key: str) -> dict[str, Any]:
    combined: dict[str, Any] = {}
    overrides = payload.get("overrides")
    if isinstance(overrides, Mapping):
        common = overrides.get("common")
        if isinstance(common, Mapping):
            combined = _merge_mapping(combined, common)
        scenario = overrides.get(scenario_key)
        if isinstance(scenario, Mapping):
            combined = _merge_mapping(combined, scenario)

    direct = payload.get(f"{scenario_key}_overrides")
    if isinstance(direct, Mapping):
        combined = _merge_mapping(combined, direct)
    return combined


def _merge_mapping(
    base: Mapping[str, Any],
    extra: Mapping[Any, Any],
) -> dict[str, Any]:
    merged = dict(base)
    for raw_key, value in extra.items():
        key = str(raw_key)
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _merge_mapping(existing, value)
        else:
            merged[key] = value
    return merged


def _location_multiplier(
    payload: Mapping[str, Any],
    overrides: Mapping[str, Any],
) -> tuple[float, str]:
    explicit = _first_number(overrides, ("cost_multiplier", "city_multiplier"))
    if explicit is None:
        explicit = _first_number(payload, ("cost_multiplier", "city_multiplier"))
    if explicit is not None and explicit > 0:
        return explicit, "explicit_input"

    market_text = " ".join(
        str(value or "")
        for value in (
            _site_summary(payload).get("address"),
            payload.get("site_address"),
            payload.get("address"),
            payload.get("market"),
            payload.get("city"),
        )
    ).lower()
    for needle, multiplier in LOCATION_MULTIPLIERS:
        if needle in market_text:
            return multiplier, f"market_match:{needle}"
    return 1.0, "default"


def _rhodes_site(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    site = payload.get("rhodes_site")
    if isinstance(site, Mapping):
        return site
    site = payload.get("site")
    if isinstance(site, Mapping):
        return site
    return {}


def _site_summary(payload: Mapping[str, Any]) -> dict[str, str | None]:
    site = _rhodes_site(payload)
    return {
        "name": _first_text(site, ("name", "siteName", "title"))
        or _string_or_none(payload.get("site_name")),
        "slug": _first_text(site, ("slug", "siteSlug")) or _string_or_none(payload.get("site_slug")),
        "id": _first_text(site, ("id", "_id", "siteId")) or _string_or_none(payload.get("site_id")),
        "address": _first_text(site, ("address", "siteAddress", "fullAddress"))
        or _string_or_none(payload.get("site_address"))
        or _string_or_none(payload.get("address")),
    }


def _planning_area_input(payload: Mapping[str, Any]) -> float | None:
    site = _rhodes_site(payload)
    site_area = _first_path_number(
        site,
        (
            "gross_sf",
            "grossSf",
            "grossSF",
            "building_sf",
            "buildingSf",
            "buildingSF",
            "totalBuildingSf",
            "totalBuildingSF",
            "total_building_sf",
            "dueDiligence.buildingSf",
            "dueDiligence.grossSf",
        ),
    )
    if site_area is not None:
        return site_area
    return _first_number(payload, ("gross_sf", "building_sf", "total_building_sf"))


def _first_text(mapping: Mapping[Any, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = _path_value(mapping, key)
        text = _string_or_none(value)
        if text is not None:
            return text
    return None


def _first_path_number(mapping: Mapping[Any, Any], paths: Sequence[str]) -> float | None:
    _path, value = _first_path_value(mapping, paths)
    return _coerce_float(value)


def _first_path_value(
    mapping: Mapping[Any, Any],
    paths: Sequence[str],
) -> tuple[str | None, Any]:
    for path in paths:
        value = _path_value(mapping, path)
        if value not in (None, ""):
            return path, value
    return None, None


def _path_value(mapping: Mapping[Any, Any], path: str) -> Any:
    if path in mapping:
        return mapping[path]
    current: Any = mapping
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        if part not in current:
            return None
        current = current[part]
    return current


def _timeline_weeks(
    defaults: Mapping[str, Any],
    overrides: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    direct = _first_number(overrides, ("timeline_weeks", "total_weeks"))
    if direct is not None and direct > 0:
        weeks = _whole_number(direct)
        return weeks, {"source": "timeline_override", "total_weeks": weeks}

    permit_weeks = _whole_number(
        _overrideable_number(overrides, "permit_weeks", float(defaults["permit_weeks"]))
    )
    mobilization_weeks = _whole_number(
        _overrideable_number(
            overrides,
            "mobilization_weeks",
            float(defaults["mobilization_weeks"]),
        )
    )
    construction_weeks = _whole_number(
        _overrideable_number(
            overrides,
            "construction_weeks",
            float(defaults["construction_weeks"]),
        )
    )
    closeout_weeks = _whole_number(
        _overrideable_number(overrides, "closeout_weeks", float(defaults["closeout_weeks"]))
    )
    parallel = bool(overrides.get("parallel_permit_and_construction", False))
    added_weeks = _whole_number(_first_number(overrides, ("added_weeks", "delay_weeks")) or 0)

    if parallel:
        total = mobilization_weeks + max(permit_weeks, construction_weeks) + closeout_weeks
    else:
        total = mobilization_weeks + permit_weeks + construction_weeks + closeout_weeks
    total += added_weeks
    return total, {
        "source": "default_or_component_override",
        "permit_weeks": permit_weeks,
        "mobilization_weeks": mobilization_weeks,
        "construction_weeks": construction_weeks,
        "closeout_weeks": closeout_weeks,
        "added_weeks": added_weeks,
        "parallel_permit_and_construction": parallel,
        "total_weeks": total,
    }


def _projected_open_date(
    payload: Mapping[str, Any],
    timeline_weeks: int,
    warnings: list[str],
) -> str | None:
    raw_start = payload.get("start_date")
    if raw_start in (None, ""):
        warnings.append("No start_date supplied; returning timeline weeks without a calendar date.")
        return None
    try:
        start = date.fromisoformat(str(raw_start))
    except ValueError:
        warnings.append(f"Invalid start_date '{raw_start}'; expected YYYY-MM-DD.")
        return None
    return (start + timedelta(weeks=timeline_weeks)).strftime("%m/%d/%y")


def _report_fields_for_scenario(scenario_key: str, scenario: Mapping[str, Any]) -> dict[str, str]:
    suffix = str(SCENARIO_DEFAULTS[scenario_key]["report_suffix"])
    fields = {
        f"exec.{suffix}_capacity": str(scenario["capacity_students"]),
        f"exec.{suffix}_capex": _format_currency(scenario["grand_total"]),
        f"exec.{suffix}_open_date": str(
            scenario["projected_open_date"] or f"{scenario['timeline_weeks']} weeks"
        ),
    }
    breakdown = cast(Mapping[str, Any], scenario["cost_breakdown"])
    for key, _label in BREAKDOWN_ROWS:
        fields[f"exec.cost_{key}_{suffix}"] = _format_currency(breakdown[key])
    return fields


def _apply_category_overrides(
    breakdown: dict[str, int | float],
    overrides: Mapping[str, Any],
    allowed_keys: Sequence[str],
    scenario_key: str,
    warnings: list[str],
) -> None:
    category_overrides = overrides.get("category_overrides")
    if not isinstance(category_overrides, Mapping):
        return
    allowed = set(allowed_keys)
    for raw_key, raw_value in category_overrides.items():
        key = str(raw_key)
        amount = _coerce_float(raw_value)
        if key not in allowed:
            if key in BREAKDOWN_LABELS:
                continue
            warnings.append(f"Ignoring unknown {scenario_key} category override '{key}'.")
            continue
        if amount is None:
            warnings.append(f"Ignoring non-numeric {scenario_key} category override '{key}'.")
            continue
        breakdown[key] = _whole_dollars(amount)


def _apply_additional_allowances(
    breakdown: dict[str, int | float],
    overrides: Mapping[str, Any],
    allowed_keys: Sequence[str],
    scenario_key: str,
    warnings: list[str],
) -> None:
    allowances = overrides.get("additional_allowances")
    if not isinstance(allowances, Mapping):
        return
    allowed = set(allowed_keys)
    for raw_key, raw_value in allowances.items():
        key = str(raw_key)
        amount = _coerce_float(raw_value)
        if key not in allowed:
            if key in BREAKDOWN_LABELS:
                continue
            warnings.append(f"Ignoring unknown {scenario_key} additional allowance '{key}'.")
            continue
        if amount is None:
            warnings.append(f"Ignoring non-numeric {scenario_key} additional allowance '{key}'.")
            continue
        breakdown[key] = _whole_dollars(breakdown[key] + amount)


def _rate(
    overrides: Mapping[str, Any],
    key: str,
    defaults: Mapping[str, Any],
) -> float:
    return _overrideable_number(overrides, key, float(defaults[key]))


def _overrideable_number(
    overrides: Mapping[str, Any],
    key: str,
    default: float,
) -> float:
    value = _coerce_float(overrides.get(key))
    if value is None:
        return default
    return value


def _first_number(mapping: Mapping[Any, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        value = _coerce_float(mapping.get(key))
        if value is not None:
            return value
    return None


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _whole_dollars(value: int | float) -> int:
    return int(round(float(value)))


def _whole_number(value: int | float) -> int:
    return int(round(float(value)))


def _round_number(value: int | float, digits: int) -> float:
    return round(float(value), digits)


def _format_currency(value: Any) -> str:
    amount = _coerce_float(value)
    if amount is None:
        return ""
    return f"${_whole_dollars(amount):,}"


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Estimate cost and timeline from approved capacity counts.",
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="JSON payload path. Reads stdin when omitted or set to '-'.",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--output", help="Optional output JSON path.")
    args = parser.parse_args(argv)

    payload_text = sys.stdin.read()
    if args.input not in (None, "-"):
        payload_text = Path(str(args.input)).read_text(encoding="utf-8")

    payload_obj = json.loads(payload_text)
    if not isinstance(payload_obj, dict):
        raise SystemExit("Input JSON must be an object.")

    result = estimate(cast(dict[str, Any], payload_obj))
    output = json.dumps(result, indent=2 if args.pretty else None, sort_keys=True)
    if args.output:
        Path(str(args.output)).write_text(f"{output}\n", encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
