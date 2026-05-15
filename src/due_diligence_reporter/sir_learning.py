"""SIR comparison learning-loop metadata.

The DDR pipeline should surface AI-vs-CDS SIR review opportunities without
letting an unreviewed comparison become source-of-truth for a DD report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .provenance import ProvenanceVerdict, classify_provenance

SirReviewStatus = Literal[
    "not_applicable",
    "waiting_for_ai_sir",
    "waiting_for_cds_sir",
    "ready_for_review",
]


@dataclass(frozen=True)
class SirReviewCandidate:
    """One SIR file selected for AI-vs-CDS comparison."""

    role: Literal["ai_sir", "cds_sir"]
    name: str
    file_id: str | None
    uri: str | None
    provenance_label: str
    provenance_confidence: float
    provenance_tier: str
    provenance_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "name": self.name,
            "file_id": self.file_id,
            "uri": self.uri,
            "provenance_label": self.provenance_label,
            "provenance_confidence": self.provenance_confidence,
            "provenance_tier": self.provenance_tier,
            "provenance_reason": self.provenance_reason,
        }


@dataclass(frozen=True)
class SirLearningReview:
    """Pipeline-visible review state for one site."""

    status: SirReviewStatus
    reason: str
    ai_sir: SirReviewCandidate | None = None
    cds_sir: SirReviewCandidate | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "ai_sir": self.ai_sir.to_dict() if self.ai_sir else None,
            "cds_sir": self.cds_sir.to_dict() if self.cds_sir else None,
            "review_dimensions": [
                "AI missed item",
                "CDS missed item",
                "AI unsupported claim",
                "CDS unsupported claim",
                "better wording needed",
                "template or prompt gap",
                "source retrieval gap",
            ],
            "learning_outputs": [
                "prompt update",
                "retrieval rule",
                "SIR template change",
                "DDR prompt or token mapping change",
                "QC checklist item",
            ],
        }


def build_sir_learning_review(
    files: list[dict[str, Any]],
    gc: Any | None,
    *,
    m1_folder_id: str | None = None,
    read_only: bool = False,
) -> SirLearningReview:
    """Classify SIR candidates and return the current comparison state."""
    candidates = _sir_files(files)
    if not candidates:
        return SirLearningReview("not_applicable", "no SIR candidates found")

    classified = [
        (_classify_sir(file_info, gc, m1_folder_id, read_only), file_info)
        for file_info in candidates
    ]
    ai_file = _pick_latest([
        (verdict, file_info)
        for verdict, file_info in classified
        if verdict.label == "ai_generated"
    ])
    cds_file = _pick_latest([
        (verdict, file_info)
        for verdict, file_info in classified
        if verdict.label != "ai_generated" and verdict.is_vendor
    ])

    if ai_file and cds_file:
        return SirLearningReview(
            "ready_for_review",
            "AI SIR and CDS/vendor SIR are both present",
            ai_sir=_candidate("ai_sir", ai_file[1], ai_file[0]),
            cds_sir=_candidate("cds_sir", cds_file[1], cds_file[0]),
        )
    if ai_file:
        return SirLearningReview(
            "waiting_for_cds_sir",
            "AI SIR present; CDS/vendor SIR not found yet",
            ai_sir=_candidate("ai_sir", ai_file[1], ai_file[0]),
        )
    if cds_file:
        return SirLearningReview(
            "waiting_for_ai_sir",
            "CDS/vendor SIR present; AI SIR not found yet",
            cds_sir=_candidate("cds_sir", cds_file[1], cds_file[0]),
        )
    return SirLearningReview("not_applicable", "SIR candidates could not be classified")


def _sir_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for file_info in files:
        if file_info.get("doc_type") != "sir":
            continue
        key = str(file_info.get("id") or file_info.get("name") or "")
        if key in seen:
            continue
        seen.add(key)
        result.append(file_info)
    return result


def _classify_sir(
    file_info: dict[str, Any],
    gc: Any | None,
    m1_folder_id: str | None,
    read_only: bool,
) -> ProvenanceVerdict:
    return classify_provenance(
        file_info,
        gc,
        m1_folder_id=m1_folder_id,
        doc_type="sir",
        read_only=read_only,
    )


def _pick_latest(
    items: list[tuple[ProvenanceVerdict, dict[str, Any]]],
) -> tuple[ProvenanceVerdict, dict[str, Any]] | None:
    if not items:
        return None
    return max(items, key=lambda item: str(item[1].get("modifiedTime", "")))


def _candidate(
    role: Literal["ai_sir", "cds_sir"],
    file_info: dict[str, Any],
    verdict: ProvenanceVerdict,
) -> SirReviewCandidate:
    return SirReviewCandidate(
        role=role,
        name=str(file_info.get("name", "")),
        file_id=str(file_info.get("id") or "") or None,
        uri=file_info.get("webViewLink") or file_info.get("uri"),
        provenance_label=verdict.label,
        provenance_confidence=verdict.confidence,
        provenance_tier=verdict.tier,
        provenance_reason=verdict.reason,
    )
