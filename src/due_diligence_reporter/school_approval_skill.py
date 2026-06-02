"""Load school-approval baseline data from the Ops-Skills hosted skill."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .ops_skill_loader import OpsSkillLoadError, load_ops_skill_file

_STATE_NAME_TO_CODE: dict[str, str] = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "puerto rico": "PR",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
_STATE_CODES = frozenset(_STATE_NAME_TO_CODE.values())
_STATE_CODE_PATTERN = re.compile(r"\b(" + "|".join(sorted(_STATE_CODES)) + r")\b")
_STATE_CODE_WITH_ZIP_PATTERN = re.compile(
    r"(?:,|\s)\s*(" + "|".join(sorted(_STATE_CODES)) + r")\s+\d{5}(?:-\d{4})?\b"
)
_STATE_CODE_AFTER_COMMA_PATTERN = re.compile(
    r",\s*(" + "|".join(sorted(_STATE_CODES)) + r")\b"
)
_VERSION_PATTERN = re.compile(r"(?m)^version:\s*([^\s]+)\s*$")
_RULES_VERSION_PATTERN = re.compile(r'"rules_version"\s*:\s*"([^"]+)"')
_BASELINE_ROW_PATTERN = re.compile(
    r"^\|\s*([A-Z]{2}|PR)\s*\|\s*(\d+)\s*\|\s*([A-Z_]+)\s*\|"
    r"\s*([A-Z_]+)\s*\|\s*(Yes|No)\s*\|\s*(\d+)\s*\|",
    re.MULTILINE,
)


@dataclass(frozen=True)
class SchoolApprovalBaseline:
    """One state row from the hosted school-approval baseline table."""

    state: str
    score: int
    archetype: str
    approval_type: str
    gating: bool
    timeline_days: int


@dataclass(frozen=True)
class SchoolApprovalSkill:
    """Loaded school-approval skill metadata and parsed baseline rows."""

    version: str
    rules_version: str
    source: str
    baselines: dict[str, SchoolApprovalBaseline]


class SchoolApprovalSkillError(RuntimeError):
    """Raised when the hosted school-approval skill cannot be loaded."""


def normalize_school_approval_state(state: str = "", address: str = "") -> str:
    """Return a two-letter state code from a state field or address string."""

    direct = state.strip().upper()
    if direct in _STATE_CODES:
        return direct

    lower_state = state.strip().lower()
    if lower_state in _STATE_NAME_TO_CODE:
        return _STATE_NAME_TO_CODE[lower_state]

    text = address.strip()
    if text:
        upper_text = text.upper()
        for pattern in (_STATE_CODE_WITH_ZIP_PATTERN, _STATE_CODE_AFTER_COMMA_PATTERN):
            match = pattern.search(upper_text)
            if match:
                return match.group(1)
        lowered = text.lower()
        for state_name, state_code in _STATE_NAME_TO_CODE.items():
            if re.search(rf"\b{re.escape(state_name)}\b", lowered):
                return state_code
        match = _STATE_CODE_PATTERN.search(upper_text)
        if match:
            return match.group(1)

    return direct


def load_school_approval_skill() -> SchoolApprovalSkill:
    """Load the current hosted Ops-Skills school-approval skill."""

    try:
        loaded = load_ops_skill_file("school-approval")
    except OpsSkillLoadError as exc:
        raise SchoolApprovalSkillError(
            "Could not load Ops-Skills school-approval SKILL.md. Set "
            "OPS_SKILLS_REPO_PATH to the Ops-Skills repo root or install the "
            "Ops Skills Codex plugin cache."
        ) from exc

    source = loaded.source
    text = loaded.text
    version = _extract_required(_VERSION_PATTERN, text, "version", source)
    rules_version = _extract_optional(_RULES_VERSION_PATTERN, text) or version
    baselines = _parse_baselines(text, source)
    return SchoolApprovalSkill(
        version=version,
        rules_version=rules_version,
        source=source,
        baselines=baselines,
    )


def _extract_required(
    pattern: re.Pattern[str],
    text: str,
    label: str,
    source: str,
) -> str:
    match = pattern.search(text)
    if not match:
        raise SchoolApprovalSkillError(f"Ops-Skills school-approval {label} missing in {source}")
    return match.group(1).strip()


def _extract_optional(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _parse_baselines(text: str, source: str) -> dict[str, SchoolApprovalBaseline]:
    baselines: dict[str, SchoolApprovalBaseline] = {}
    for match in _BASELINE_ROW_PATTERN.finditer(text):
        state, score, archetype, approval_type, gating, timeline = match.groups()
        baselines[state] = SchoolApprovalBaseline(
            state=state,
            score=int(score),
            archetype=archetype,
            approval_type=approval_type,
            gating=gating == "Yes",
            timeline_days=int(timeline),
        )
    if not baselines:
        raise SchoolApprovalSkillError(
            f"Could not parse Baseline Score Table from Ops-Skills school-approval skill: {source}"
        )
    return baselines
