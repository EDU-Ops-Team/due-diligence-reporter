"""Load ease-of-conversion rating bands from the Ops-Skills hosted skill."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .ops_skill_loader import OpsSkillLoadError, load_ops_skill_file

_VERSION_PATTERN = re.compile(r"(?m)^version:\s*([^\s]+)\s*$")
_THEME_ID_PATTERN = re.compile(r"(?m)^\s*themeId:\s*([^\s]+)\s*$")
_BAND_PATTERN = re.compile(
    r"^\s*-\s*(GREEN|YELLOW|ORANGE|RED)\s*\((\d+)\s*-\s*(\d+)\):\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class EaseConversionBand:
    """One score band from the hosted ease-of-conversion reference."""

    label: str
    min_score: int
    max_score: int
    description: str

    def contains(self, score: int) -> bool:
        return self.min_score <= score <= self.max_score


@dataclass(frozen=True)
class EaseConversionSkill:
    """Loaded ease-of-conversion skill metadata and rating bands."""

    version: str
    source: str
    reference_source: str
    scorecard_theme_id: str
    bands: tuple[EaseConversionBand, ...]

    def band_for_score(self, score: int) -> str:
        for band in self.bands:
            if band.contains(score):
                return band.label
        raise EaseConversionSkillError(
            f"Ops-Skills ease-of-conversion bands do not cover score {score}."
        )


class EaseConversionSkillError(RuntimeError):
    """Raised when the hosted ease-of-conversion skill cannot be loaded."""


def load_ease_conversion_skill() -> EaseConversionSkill:
    """Load ease-of-conversion metadata and rating bands from Ops-Skills."""

    try:
        skill_file = load_ops_skill_file("ease-of-conversion")
        reference_file = load_ops_skill_file(
            "ease-of-conversion",
            "references/site-eval-brainlift.md",
        )
    except OpsSkillLoadError as exc:
        raise EaseConversionSkillError(
            "Could not load Ops-Skills ease-of-conversion skill and reference. "
            "Set OPS_SKILLS_REPO_PATH to the Ops-Skills repo root or install "
            "the Ops Skills Codex plugin cache."
        ) from exc

    version = _extract_optional(_VERSION_PATTERN, skill_file.text) or "unversioned"
    scorecard_theme_id = _extract_optional(_THEME_ID_PATTERN, skill_file.text) or ""
    bands = _parse_bands(reference_file.text, reference_file.source)

    return EaseConversionSkill(
        version=version,
        source=skill_file.source,
        reference_source=reference_file.source,
        scorecard_theme_id=scorecard_theme_id,
        bands=bands,
    )


def _extract_optional(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _parse_bands(text: str, source: str) -> tuple[EaseConversionBand, ...]:
    bands: list[EaseConversionBand] = []
    for match in _BAND_PATTERN.finditer(text):
        label, min_score, max_score, description = match.groups()
        bands.append(
            EaseConversionBand(
                label=label.upper(),
                min_score=int(min_score),
                max_score=int(max_score),
                description=description.strip(),
            )
        )

    if not bands:
        raise EaseConversionSkillError(
            f"Could not parse E-Occupancy Rating Bands from Ops-Skills reference: {source}"
        )

    covered_scores: set[int] = set()
    for band in bands:
        covered_scores.update(range(band.min_score, band.max_score + 1))
    missing_scores = [score for score in range(0, 101) if score not in covered_scores]
    if missing_scores:
        first_missing = missing_scores[0]
        raise EaseConversionSkillError(
            "Ops-Skills ease-of-conversion rating bands do not cover the full "
            f"0-100 score range; first missing score is {first_missing} in {source}"
        )

    return tuple(sorted(bands, key=lambda band: band.min_score, reverse=True))
