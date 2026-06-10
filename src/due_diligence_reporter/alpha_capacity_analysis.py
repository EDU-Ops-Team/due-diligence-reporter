"""Run hosted Alpha Capacity Analysis from Block Plan text."""

from __future__ import annotations

import json
import os
import re
from base64 import b64encode
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .config import get_settings
from .ops_skill_loader import OpsSkillLoadError, load_ops_skill_file

_VERSION_PATTERN = re.compile(r"(?m)^\s*version:\s*['\"]?([^'\"\s]+)['\"]?\s*$")
_THEME_ID_PATTERN = re.compile(r"(?m)^\s*themeId:\s*([^\s]+)\s*$")
_BLOCK_PLAN_TEXT_LIMIT = 30_000
_BLOCK_PLAN_FILE_BYTES_LIMIT = 25 * 1024 * 1024
_CAPACITY_JSON_MIME_TYPE = "application/json"
_EXPLICIT_STUDENT_PAIR_PATTERN = re.compile(
    r"(?i)(\d{1,4})\s*/\s*(\d{1,4})\s*students?"
)


@dataclass(frozen=True)
class AlphaCapacitySkill:
    """Hosted alpha-capacity-analysis skill and referenced rulesets."""

    version: str
    source: str
    scorecard_theme_id: str
    skill_text: str
    microschool_reference_source: str
    microschool_ruleset: str
    plus250_reference_source: str
    plus250_ruleset: str


class AlphaCapacityAnalysisError(RuntimeError):
    """Raised when the hosted alpha-capacity-analysis skill cannot be loaded."""


def load_alpha_capacity_skill() -> AlphaCapacitySkill:
    """Load hosted Ops-Skills Alpha Capacity Analysis instructions."""

    try:
        skill_file = load_ops_skill_file("alpha-capacity-analysis")
        microschool = load_ops_skill_file(
            "alpha-capacity-analysis",
            "references/microschool-ruleset.md",
        )
        plus250 = load_ops_skill_file(
            "alpha-capacity-analysis",
            "references/250plus-ruleset.md",
        )
    except OpsSkillLoadError as exc:
        raise AlphaCapacityAnalysisError(
            "Could not load Ops-Skills alpha-capacity-analysis skill and "
            "rulesets. Set OPS_SKILLS_REPO_PATH to the Ops-Skills repo root "
            "or install the Ops Skills Codex plugin cache."
        ) from exc

    version = _extract_optional(_VERSION_PATTERN, skill_file.text) or "unversioned"
    theme_id = _extract_optional(_THEME_ID_PATTERN, skill_file.text) or ""
    return AlphaCapacitySkill(
        version=version,
        source=skill_file.source,
        scorecard_theme_id=theme_id,
        skill_text=skill_file.text,
        microschool_reference_source=microschool.source,
        microschool_ruleset=microschool.text,
        plus250_reference_source=plus250.source,
        plus250_ruleset=plus250.text,
    )


def run_alpha_capacity_analysis(
    *,
    site_name: str,
    site_address: str,
    block_plan_content: str,
    total_building_sf: int | None = None,
    block_plan_file_id: str = "",
    block_plan_file_bytes: bytes | None = None,
    block_plan_file_name: str = "Block Plan.pdf",
    block_plan_mime_type: str = "application/pdf",
    client: Any | None = None,
    model: str | None = None,
    skill: AlphaCapacitySkill | None = None,
) -> dict[str, Any]:
    """Run Alpha Capacity Analysis and return a normalized JSON payload.

    This function does not invent capacity. If the hosted skill cannot produce
    both Strict/Fast Path and Max student counts from the supplied Block Plan,
    DDR may still use explicit Scenario 1 / Scenario 2 student-count schedules
    printed in that same Block Plan. Otherwise the result is
    ``insufficient_evidence`` and callers should avoid using it as RayCon's
    authoritative capacity source.
    """

    text = block_plan_content.strip()
    has_file = bool(block_plan_file_bytes)
    if not text and not has_file:
        return _error_result(
            "insufficient_evidence",
            "Missing Block Plan evidence",
            "Alpha Capacity Analysis requires readable Block Plan text or the Block Plan PDF.",
        )
    if block_plan_file_bytes and len(block_plan_file_bytes) > _BLOCK_PLAN_FILE_BYTES_LIMIT:
        return _error_result(
            "error",
            "Block Plan PDF too large",
            (
                "Block Plan PDF exceeds the inline Alpha Capacity Analysis limit "
                f"({_BLOCK_PLAN_FILE_BYTES_LIMIT} bytes)."
            ),
        )

    if client is None and not os.getenv("OPENAI_API_KEY"):
        return _error_result(
            "error",
            "OpenAI not configured",
            "OPENAI_API_KEY must be set to run Alpha Capacity Analysis.",
        )

    try:
        loaded_skill = skill or load_alpha_capacity_skill()
    except AlphaCapacityAnalysisError as exc:
        return _error_result(
            "error",
            "Ops-Skills alpha-capacity-analysis unavailable",
            str(exc),
        )

    settings = get_settings()
    selected_model = (model or settings.openai_capacity_model or "gpt-4o").strip() or "gpt-4o"
    try:
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=2)

        if block_plan_file_bytes:
            response = _create_capacity_response_with_file(
                client=client,
                model=selected_model,
                skill=loaded_skill,
                site_name=site_name,
                site_address=site_address,
                total_building_sf=total_building_sf,
                block_plan_file_id=block_plan_file_id,
                block_plan_content=text[:_BLOCK_PLAN_TEXT_LIMIT],
                block_plan_file_bytes=block_plan_file_bytes,
                block_plan_file_name=block_plan_file_name,
                block_plan_mime_type=block_plan_mime_type,
            )
        else:
            response = client.chat.completions.create(
                model=selected_model,
                messages=[
                    {"role": "system", "content": _capacity_system_prompt(loaded_skill)},
                    {
                        "role": "user",
                        "content": _capacity_user_prompt(
                            site_name=site_name,
                            site_address=site_address,
                            total_building_sf=total_building_sf,
                            block_plan_file_id=block_plan_file_id,
                            block_plan_content=text[:_BLOCK_PLAN_TEXT_LIMIT],
                            block_plan_file_attached=False,
                        ),
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
    except Exception as exc:
        return _error_result(
            "error",
            "Alpha Capacity Analysis model call failed",
            str(exc),
        )

    raw_text = _response_text(response)
    if not raw_text:
        return _error_result(
            "error",
            "Alpha Capacity Analysis returned no content",
            "The model response did not include a JSON object.",
        )
    try:
        raw_payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return _error_result(
            "error",
            "Alpha Capacity Analysis returned invalid JSON",
            str(exc),
        )
    if not isinstance(raw_payload, dict):
        return _error_result(
            "error",
            "Alpha Capacity Analysis returned invalid JSON shape",
            f"Expected object, got {type(raw_payload).__name__}.",
        )

    normalized = normalize_alpha_capacity_payload(
        raw_payload,
        skill=loaded_skill,
        site_name=site_name,
        site_address=site_address,
        total_building_sf=total_building_sf,
        block_plan_file_id=block_plan_file_id,
        model=selected_model,
    )
    if normalized.get("status") == "success":
        return normalized

    schedule_payload = _payload_from_explicit_capacity_schedule(
        text,
        skill=loaded_skill,
        site_name=site_name,
        site_address=site_address,
        total_building_sf=total_building_sf,
        block_plan_file_id=block_plan_file_id,
        model=selected_model,
        model_payload=raw_payload,
    )
    return schedule_payload or normalized


def generate_alpha_capacity_analysis_artifact(
    gc: Any,
    *,
    m1_folder_id: str,
    site_name: str,
    site_address: str,
    block_plan_content: str,
    total_building_sf: int | None = None,
    block_plan_file_id: str = "",
    block_plan_file_bytes: bytes | None = None,
    block_plan_file_name: str = "Block Plan.pdf",
    block_plan_mime_type: str = "application/pdf",
) -> dict[str, Any]:
    """Run capacity analysis and upload the JSON artifact into M1 on success."""

    result = run_alpha_capacity_analysis(
        site_name=site_name,
        site_address=site_address,
        block_plan_content=block_plan_content,
        total_building_sf=total_building_sf,
        block_plan_file_id=block_plan_file_id,
        block_plan_file_bytes=block_plan_file_bytes,
        block_plan_file_name=block_plan_file_name,
        block_plan_mime_type=block_plan_mime_type,
    )
    if result.get("status") != "success":
        return result

    capacity_analysis = _capacity_payload_for_artifact(result)
    file_name = alpha_capacity_analysis_filename(
        site_name=site_name,
        block_plan_file_id=block_plan_file_id,
    )
    uploaded = gc.upload_file_to_folder(
        folder_id=m1_folder_id,
        file_name=file_name,
        file_bytes=json.dumps(capacity_analysis, indent=2).encode("utf-8"),
        mime_type=_CAPACITY_JSON_MIME_TYPE,
    )
    result["capacity_analysis"] = capacity_analysis
    result["capacity_analysis_file_id"] = str(uploaded.get("id", "") or "")
    result["capacity_analysis_url"] = str(uploaded.get("webViewLink", "") or "")
    result["artifact_name"] = file_name
    return result


def normalize_alpha_capacity_payload(
    raw_payload: dict[str, Any],
    *,
    skill: AlphaCapacitySkill,
    site_name: str,
    site_address: str,
    total_building_sf: int | None,
    block_plan_file_id: str,
    model: str,
) -> dict[str, Any]:
    """Normalize model output to the RayCon-compatible capacity contract."""

    strict = _normalize_capacity_scenario(
        _first_dict(
            raw_payload,
            "strict",
            "fastest_open",
            "fastestOpen",
            "fast_path",
            "fastPath",
            "as_is",
            "asIs",
        ),
        scenario="strict",
    )
    max_capacity = _normalize_capacity_scenario(
        _first_dict(
            raw_payload,
            "max",
            "max_capacity",
            "maxCapacity",
            "maximum",
            "maximum_capacity",
            "maximumCapacity",
        ),
        scenario="max",
    )
    has_required_capacities = (
        strict.get("capacity_students") is not None
        and max_capacity.get("capacity_students") is not None
    )
    status = "success" if has_required_capacities else "insufficient_evidence"

    payload: dict[str, Any] = {
        "status": status,
        "source_system": "alpha_capacity_analysis",
        "source_label": "Alpha Capacity Analysis",
        "site_name": site_name,
        "site_address": site_address,
        "block_plan_file_id": block_plan_file_id,
        "total_building_sf": total_building_sf,
        "ruleset": _string_value(raw_payload.get("ruleset"))
        or _string_value(raw_payload.get("rules_applied"))
        or _ruleset_from_size(total_building_sf),
        "skill_version": skill.version,
        "skill_source": skill.source,
        "scorecard_theme_id": skill.scorecard_theme_id,
        "microschool_reference_source": skill.microschool_reference_source,
        "plus250_reference_source": skill.plus250_reference_source,
        "model": model,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strict": strict,
        "max": max_capacity,
        "fastest_open": strict,
        "max_capacity": max_capacity,
        "assumptions": _string_list(raw_payload.get("assumptions")),
        "warnings": _string_list(raw_payload.get("warnings")),
        "open_items": _string_list(raw_payload.get("open_items")),
    }

    for source_key, target_key in (
        ("room_inventory", "room_inventory"),
        ("raycon_rooms", "raycon_rooms"),
        ("support_space_check", "support_space_check"),
    ):
        value = raw_payload.get(source_key)
        if isinstance(value, list):
            payload[target_key] = value

    if has_required_capacities:
        payload["report_data_fields"] = {
            "exec.fastest_open_capacity": str(strict["capacity_students"]),
            "exec.max_capacity_capacity": str(max_capacity["capacity_students"]),
        }
        payload["message"] = (
            "Alpha Capacity Analysis produced Strict/Fast Path "
            f"{strict['capacity_students']} students and Max Capacity "
            f"{max_capacity['capacity_students']} students."
        )
    else:
        payload["message"] = (
            "Alpha Capacity Analysis could not produce both Strict/Fast Path "
            "and Max Capacity counts from the supplied Block Plan evidence."
        )
        if not payload["open_items"]:
            payload["open_items"] = [
                "Provide a Block Plan or room schedule with enough room sizes, "
                "natural-light/support-space facts, or stated capacity assumptions "
                "to calculate both Strict and Max capacity.",
            ]

    return payload


def _payload_from_explicit_capacity_schedule(
    block_plan_content: str,
    *,
    skill: AlphaCapacitySkill,
    site_name: str,
    site_address: str,
    total_building_sf: int | None,
    block_plan_file_id: str,
    model: str,
    model_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build a capacity payload from explicit Block Plan schedule counts."""

    pairs = _explicit_capacity_pairs(block_plan_content)
    if not pairs:
        return None

    strict_total = sum(pair[0] for pair in pairs)
    max_total = sum(pair[1] for pair in pairs)
    if strict_total <= 0 or max_total <= 0:
        return None

    ruleset = (
        _string_value((model_payload or {}).get("ruleset"))
        or _string_value((model_payload or {}).get("rules_applied"))
        or _ruleset_from_size(total_building_sf)
    )
    row_basis = ", ".join(f"{strict}/{max_}" for strict, max_ in pairs)
    raw_payload: dict[str, Any] = {
        "status": "success",
        "ruleset": ruleset,
        "strict": {
            "capacity_students": strict_total,
            "classroom_count": len(pairs),
            "basis": (
                "Block Plan schedule lists Scenario 1 / Scenario 2 student "
                f"counts by level or zone ({row_basis}); Strict/Fast Path uses "
                "the first value in each pair."
            ),
            "confidence": "medium",
            "warnings": [
                "Capacity came from explicit Block Plan schedule counts after "
                "the model requested more natural-light/building-SF detail.",
            ],
        },
        "max": {
            "capacity_students": max_total,
            "classroom_count": len(pairs),
            "basis": (
                "Block Plan schedule lists Scenario 1 / Scenario 2 student "
                f"counts by level or zone ({row_basis}); Max Capacity uses "
                "the second value in each pair."
            ),
            "confidence": "medium",
            "warnings": [
                "Capacity came from explicit Block Plan schedule counts after "
                "the model requested more natural-light/building-SF detail.",
            ],
        },
        "assumptions": [
            "The Block Plan's explicit student-count schedule is the source of "
            "record for Strict/Fast Path and Max Capacity counts.",
        ],
        "warnings": [
            "Verify room-level natural light and support-space assumptions before "
            "treating the counts as field-confirmed.",
        ],
        "open_items": _string_list((model_payload or {}).get("open_items")),
        "capacity_source_detail": {
            "source": "explicit_block_plan_schedule",
            "student_count_pairs": [
                {"strict": strict, "max": max_} for strict, max_ in pairs
            ],
        },
    }
    if isinstance((model_payload or {}).get("room_inventory"), list):
        raw_payload["room_inventory"] = (model_payload or {})["room_inventory"]

    normalized = normalize_alpha_capacity_payload(
        raw_payload,
        skill=skill,
        site_name=site_name,
        site_address=site_address,
        total_building_sf=total_building_sf,
        block_plan_file_id=block_plan_file_id,
        model=model,
    )
    normalized["capacity_source_detail"] = raw_payload["capacity_source_detail"]
    normalized["model_status_before_schedule_fallback"] = _string_value(
        (model_payload or {}).get("status")
    )
    return normalized


def _explicit_capacity_pairs(block_plan_content: str) -> list[tuple[int, int]]:
    """Return explicit ``Scenario 1 / Scenario 2`` student-count pairs."""

    pairs: list[tuple[int, int]] = []
    for match in _EXPLICIT_STUDENT_PAIR_PATTERN.finditer(block_plan_content):
        strict = _positive_int(match.group(1))
        max_capacity = _positive_int(match.group(2))
        if strict is None or max_capacity is None:
            continue
        if max_capacity < strict:
            continue
        pairs.append((strict, max_capacity))
    return _collapse_repeated_pair_sequence(pairs)


def _collapse_repeated_pair_sequence(
    pairs: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Collapse obvious repeated PDF extraction of the same full schedule."""

    pair_count = len(pairs)
    if pair_count < 4:
        return pairs
    for unit_length in range(2, (pair_count // 2) + 1):
        if pair_count % unit_length != 0:
            continue
        unit = pairs[:unit_length]
        if len(set(unit)) == 1:
            continue
        if unit * (pair_count // unit_length) == pairs:
            return unit
    return pairs


def alpha_capacity_analysis_filename(
    *,
    site_name: str,
    block_plan_file_id: str = "",
) -> str:
    """Return the Drive filename for a generated capacity JSON artifact."""

    safe_site = _safe_filename(site_name) or "Site"
    suffix = f" - {block_plan_file_id[:12]}" if block_plan_file_id else ""
    return f"Alpha Capacity Analysis - {safe_site}{suffix}.json"


def _capacity_payload_for_artifact(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key
        not in {
            "capacity_analysis",
            "capacity_analysis_file_id",
            "capacity_analysis_url",
            "artifact_name",
        }
    }


def _capacity_system_prompt(skill: AlphaCapacitySkill) -> str:
    return "\n\n".join(
        [
            "You are running ops-skills:alpha-capacity-analysis inside DDR.",
            "Follow the hosted skill and rulesets exactly. Use only the supplied "
            "site metadata, extracted Block Plan text, and attached Block Plan "
            "PDF page evidence. When the PDF is attached, read visible plan "
            "labels, room names, dimensions, areas, and scenario capacity notes "
            "from the pages directly; do not rely only on lossy text extraction. "
            "Do not invent rooms, square footages, windows, support spaces, or "
            "capacity counts.",
            "Return exactly one JSON object. Do not include markdown fences or prose.",
            "If both Strict/Fast Path and Max Capacity cannot be calculated from "
            "the evidence, return status='insufficient_evidence' and put the "
            "missing facts in open_items.",
            "Required JSON shape: status, ruleset, strict.capacity_students, "
            "strict.classroom_count, strict.basis, max.capacity_students, "
            "max.classroom_count, max.basis, assumptions, warnings, open_items, "
            "room_inventory.",
            "Hosted SKILL.md:",
            skill.skill_text,
            "Microschool ruleset:",
            skill.microschool_ruleset,
            "250+ ruleset:",
            skill.plus250_ruleset,
        ]
    )


def _capacity_user_prompt(
    *,
    site_name: str,
    site_address: str,
    total_building_sf: int | None,
    block_plan_file_id: str,
    block_plan_content: str,
    block_plan_file_attached: bool,
) -> str:
    return "\n\n".join(
        [
            f"Site name: {site_name}",
            f"Site address: {site_address}",
            f"Total building SF from DDR metadata: {total_building_sf or 'unknown'}",
            f"Block Plan Drive file ID: {block_plan_file_id or 'unknown'}",
            f"Block Plan PDF attached: {'yes' if block_plan_file_attached else 'no'}",
            "Block Plan extracted text:",
            block_plan_content or "[No text extracted from PDF. Use the attached PDF evidence.]",
        ]
    )


def _create_capacity_response_with_file(
    *,
    client: Any,
    model: str,
    skill: AlphaCapacitySkill,
    site_name: str,
    site_address: str,
    total_building_sf: int | None,
    block_plan_file_id: str,
    block_plan_content: str,
    block_plan_file_bytes: bytes,
    block_plan_file_name: str,
    block_plan_mime_type: str,
) -> Any:
    """Call Responses API with the Block Plan PDF as model evidence."""

    file_data = (
        f"data:{block_plan_mime_type};base64,"
        f"{b64encode(block_plan_file_bytes).decode('ascii')}"
    )
    user_prompt = _capacity_user_prompt(
        site_name=site_name,
        site_address=site_address,
        total_building_sf=total_building_sf,
        block_plan_file_id=block_plan_file_id,
        block_plan_content=block_plan_content,
        block_plan_file_attached=True,
    )
    return client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": [
                    {"type": "input_text", "text": _capacity_system_prompt(skill)}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user_prompt},
                    {
                        "type": "input_file",
                        "filename": block_plan_file_name or "Block Plan.pdf",
                        "file_data": file_data,
                        "detail": "high",
                    },
                ],
            },
        ],
        text={"format": {"type": "json_object"}},
        temperature=0,
        max_output_tokens=8192,
    )


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", "")
    if output_text:
        return str(output_text).strip()
    if isinstance(response, dict):
        output_text = response.get("output_text")
        if output_text:
            return str(output_text).strip()
        output = response.get("output")
        text = _text_from_response_output(output)
        if text:
            return text

    output = getattr(response, "output", None)
    text = _text_from_response_output(output)
    if text:
        return text

    choices = getattr(response, "choices", None)
    if not choices:
        return ""
    first_choice = choices[0]
    message = getattr(first_choice, "message", None)
    content = getattr(message, "content", "")
    return str(content or "").strip()


def _text_from_response_output(output: Any) -> str:
    parts: list[str] = []
    if not isinstance(output, list):
        return ""
    for item in output:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        if not isinstance(content, list):
            continue
        for content_item in content:
            item_type = (
                content_item.get("type")
                if isinstance(content_item, dict)
                else getattr(content_item, "type", "")
            )
            if item_type != "output_text":
                continue
            text = (
                content_item.get("text")
                if isinstance(content_item, dict)
                else getattr(content_item, "text", "")
            )
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()


def _first_dict(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    scenarios = payload.get("scenarios")
    if isinstance(scenarios, dict):
        for key in keys:
            value = scenarios.get(key)
            if isinstance(value, dict):
                return value
    return {}


def _normalize_capacity_scenario(raw: dict[str, Any], *, scenario: str) -> dict[str, Any]:
    capacity = _positive_int(raw.get("capacity_students"))
    if capacity is None:
        capacity = _positive_int(raw.get("capacity"))
    classroom_count = _positive_int(raw.get("classroom_count"))
    if classroom_count is None:
        classroom_count = _positive_int(raw.get("classroomCount"))
    return {
        "scenario": scenario,
        "capacity_students": capacity,
        "classroom_count": classroom_count,
        "basis": _string_value(raw.get("basis"))
        or _string_value(raw.get("binding_constraint"))
        or _string_value(raw.get("bindingConstraint")),
        "confidence": _string_value(raw.get("confidence")),
        "warnings": _string_list(raw.get("warnings")),
    }


def _positive_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _ruleset_from_size(total_building_sf: int | None) -> str:
    if total_building_sf is None or total_building_sf <= 0:
        return ""
    if total_building_sf > 10_000:
        return "250+"
    return "Microschool"


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _.-]+", "", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:120].strip(" .")


def _extract_optional(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _error_result(status: str, error: str, message: str) -> dict[str, Any]:
    return {
        "status": status,
        "error": error,
        "message": message,
    }
