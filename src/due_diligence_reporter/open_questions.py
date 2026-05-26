"""Structured state for DDR open verification questions and source arrivals."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

SourceType = Literal[
    "vendor_sir",
    "building_inspection",
    "raycon_scenario",
    "e_occupancy_report",
    "school_approval_report",
]

OpenQuestionStatus = Literal["open", "closed"]

SOURCE_TYPE_VENDOR_SIR: SourceType = "vendor_sir"
SOURCE_TYPE_BUILDING_INSPECTION: SourceType = "building_inspection"
SOURCE_TYPE_RAYCON_SCENARIO: SourceType = "raycon_scenario"
SOURCE_TYPE_E_OCCUPANCY: SourceType = "e_occupancy_report"
SOURCE_TYPE_SCHOOL_APPROVAL: SourceType = "school_approval_report"

CORE_SOURCE_TYPES: frozenset[SourceType] = frozenset(
    {
        SOURCE_TYPE_VENDOR_SIR,
        SOURCE_TYPE_BUILDING_INSPECTION,
        SOURCE_TYPE_RAYCON_SCENARIO,
        SOURCE_TYPE_E_OCCUPANCY,
        SOURCE_TYPE_SCHOOL_APPROVAL,
    }
)

DOC_TYPE_TO_SOURCE_TYPE: dict[str, SourceType] = {
    "sir": SOURCE_TYPE_VENDOR_SIR,
    "building_inspection": SOURCE_TYPE_BUILDING_INSPECTION,
    "raycon_scenario_json": SOURCE_TYPE_RAYCON_SCENARIO,
    "raycon_scenario_report": SOURCE_TYPE_RAYCON_SCENARIO,
    "e_occupancy_report": SOURCE_TYPE_E_OCCUPANCY,
    "school_approval_report": SOURCE_TYPE_SCHOOL_APPROVAL,
}

REPORT_OPEN_ITEM_KEYS = (
    "verification.open_items",
    "_internal.verification_open_items",
    "open_items.verification",
    "verification_open_items",
)


@dataclass(frozen=True)
class SourceEvent:
    """One material DDR source document observation."""

    source_type: SourceType
    fingerprint: str
    doc_type: str = ""
    drive_file_id: str = ""
    drive_modified_time: str = ""
    file_name: str = ""
    drive_url: str = ""
    observed_at: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "source_type": self.source_type,
            "fingerprint": self.fingerprint,
            "doc_type": self.doc_type,
            "drive_file_id": self.drive_file_id,
            "drive_modified_time": self.drive_modified_time,
            "file_name": self.file_name,
            "drive_url": self.drive_url,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class OpenQuestion:
    """Structured representation of a rendered Open Items to Verify row."""

    open_question_id: str
    display_text: str
    affected_ddr_field: str
    expected_source_type: SourceType | str
    status: OpenQuestionStatus = "open"
    evidence_source: str = ""
    created_run: str = ""
    closed_run: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "open_question_id": self.open_question_id,
            "display_text": self.display_text,
            "affected_ddr_field": self.affected_ddr_field,
            "expected_source_type": str(self.expected_source_type),
            "status": self.status,
            "evidence_source": self.evidence_source,
            "created_run": self.created_run,
            "closed_run": self.closed_run,
        }


@dataclass(frozen=True)
class OpenQuestionClosure:
    """A previously open DDR question that disappeared after a validated rerun."""

    open_question_id: str
    display_text: str
    affected_ddr_field: str
    expected_source_type: SourceType | str
    evidence_source: str
    closed_run: str

    def to_dict(self) -> dict[str, str]:
        return {
            "open_question_id": self.open_question_id,
            "display_text": self.display_text,
            "affected_ddr_field": self.affected_ddr_field,
            "expected_source_type": str(self.expected_source_type),
            "evidence_source": self.evidence_source,
            "closed_run": self.closed_run,
        }


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def source_type_for_doc_type(doc_type: str) -> SourceType | None:
    return DOC_TYPE_TO_SOURCE_TYPE.get(doc_type)


def fingerprint_drive_file(file_info: dict[str, Any]) -> str:
    file_id = str(file_info.get("id") or "").strip()
    modified_time = str(file_info.get("modifiedTime") or "").strip()
    return f"{file_id}:{modified_time}" if file_id and modified_time else file_id


def source_event_from_drive_file(
    source_type: SourceType,
    file_info: dict[str, Any],
    *,
    doc_type: str = "",
    observed_at: str | None = None,
) -> SourceEvent:
    file_id = str(file_info.get("id") or "").strip()
    modified_time = str(file_info.get("modifiedTime") or "").strip()
    return SourceEvent(
        source_type=source_type,
        fingerprint=fingerprint_drive_file(file_info),
        doc_type=doc_type,
        drive_file_id=file_id,
        drive_modified_time=modified_time,
        file_name=str(file_info.get("name") or "").strip(),
        drive_url=str(file_info.get("webViewLink") or file_info.get("drive_url") or "").strip(),
        observed_at=observed_at or utc_now_iso(),
    )


def source_event_from_fingerprint(
    source_type: SourceType,
    fingerprint: str,
    *,
    doc_type: str = "",
    observed_at: str | None = None,
) -> SourceEvent:
    file_id, modified_time = _split_fingerprint(fingerprint)
    return SourceEvent(
        source_type=source_type,
        fingerprint=fingerprint.strip(),
        doc_type=doc_type,
        drive_file_id=file_id,
        drive_modified_time=modified_time,
        observed_at=observed_at or utc_now_iso(),
    )


def extract_open_questions_from_report_data(
    report_data: dict[str, Any] | None,
    *,
    created_run: str = "",
) -> list[OpenQuestion]:
    """Extract structured open questions from flat DDR report data."""
    if not isinstance(report_data, dict):
        return []
    raw = ""
    for key in REPORT_OPEN_ITEM_KEYS:
        value = report_data.get(key)
        if isinstance(value, str) and value.strip():
            raw = value
            break
        if isinstance(value, list):
            raw = "\n".join(str(item) for item in value if str(item).strip())
            break
    questions: list[OpenQuestion] = []
    seen: set[str] = set()
    for text in _split_open_items(raw):
        affected_field = _infer_affected_field(text)
        source_type = _infer_expected_source_type(text)
        qid = _question_id(text, affected_field, source_type)
        if qid in seen:
            continue
        seen.add(qid)
        questions.append(
            OpenQuestion(
                open_question_id=qid,
                display_text=text,
                affected_ddr_field=affected_field,
                expected_source_type=source_type,
                created_run=created_run,
            )
        )
    return questions


def serialize_open_questions(questions: list[OpenQuestion]) -> list[dict[str, str]]:
    return [question.to_dict() for question in questions]


def close_open_questions(
    previous_open_questions: list[dict[str, Any]] | list[OpenQuestion] | None,
    current_open_questions: list[dict[str, Any]] | list[OpenQuestion] | None,
    *,
    source_event: dict[str, Any] | SourceEvent | None,
    closed_run: str,
) -> list[OpenQuestionClosure]:
    """Return questions that were open before and are absent after a valid rerun."""
    previous = [_coerce_question(q) for q in previous_open_questions or []]
    current_ids = {
        _coerce_question(q).open_question_id
        for q in current_open_questions or []
        if _coerce_question(q).open_question_id
    }
    evidence_source = _evidence_source(source_event)
    closures: list[OpenQuestionClosure] = []
    for question in previous:
        if not question.open_question_id or question.open_question_id in current_ids:
            continue
        closures.append(
            OpenQuestionClosure(
                open_question_id=question.open_question_id,
                display_text=question.display_text,
                affected_ddr_field=question.affected_ddr_field,
                expected_source_type=question.expected_source_type,
                evidence_source=evidence_source,
                closed_run=closed_run,
            )
        )
    return closures


def load_latest_open_questions(
    manifest_root: Path,
    *,
    site_id: str = "",
    site_title: str = "",
) -> list[dict[str, Any]]:
    """Return open-question state from the latest manifest for a site."""
    candidates: list[tuple[str, list[dict[str, Any]]]] = []
    if not manifest_root.exists():
        return []
    for path in manifest_root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if not _manifest_matches_site(payload, site_id=site_id, site_title=site_title):
            continue
        questions = payload.get("open_questions")
        if not isinstance(questions, list):
            continue
        run_id = str(payload.get("run_id") or path.stem)
        candidates.append((run_id, [q for q in questions if isinstance(q, dict)]))
    if not candidates:
        return []
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def _split_open_items(raw: str) -> list[str]:
    items: list[str] = []
    for line in str(raw or "").splitlines():
        text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if not text:
            continue
        items.append(text)
    return items


def _question_id(text: str, affected_field: str, source_type: SourceType | str) -> str:
    normalized = " ".join(text.lower().split())
    digest = hashlib.sha1(
        f"{affected_field}|{source_type}|{normalized}".encode()
    ).hexdigest()
    return f"oq_{digest[:12]}"


def _infer_affected_field(text: str) -> str:
    lower = text.lower()
    if "zoning" in lower or "land use" in lower:
        return "Zoning"
    if "education" in lower or "private school" in lower or "regulatory" in lower:
        return "Education Regulatory Approval"
    if "occupancy" in lower or "egress" in lower or "fire/life" in lower:
        return "Occupancy path"
    if "permit" in lower or "ahj" in lower:
        return "Permit Timeline"
    if "construction" in lower or "buildout" in lower or "raycon" in lower:
        return "Construction Timeline"
    return "Open Items to Verify"


def _infer_expected_source_type(text: str) -> SourceType:
    lower = text.lower()
    if "school approval" in lower or "education" in lower or "regulatory" in lower:
        return SOURCE_TYPE_SCHOOL_APPROVAL
    if "e-occupancy" in lower or "occupancy" in lower or "egress" in lower:
        return SOURCE_TYPE_E_OCCUPANCY
    if "raycon" in lower or "scenario" in lower or "construction" in lower:
        return SOURCE_TYPE_RAYCON_SCENARIO
    if "inspection" in lower or "fire/life" in lower or "building condition" in lower:
        return SOURCE_TYPE_BUILDING_INSPECTION
    return SOURCE_TYPE_VENDOR_SIR


def _split_fingerprint(fingerprint: str) -> tuple[str, str]:
    clean = fingerprint.strip()
    if not clean:
        return "", ""
    file_id, sep, modified_time = clean.partition(":")
    if sep:
        return file_id, modified_time
    return clean, ""


def _coerce_question(value: dict[str, Any] | OpenQuestion) -> OpenQuestion:
    if isinstance(value, OpenQuestion):
        return value
    return OpenQuestion(
        open_question_id=str(value.get("open_question_id") or "").strip(),
        display_text=str(
            value.get("display_text") or value.get("text") or value.get("body") or ""
        ).strip(),
        affected_ddr_field=str(
            value.get("affected_ddr_field") or value.get("affected_field") or ""
        ).strip(),
        expected_source_type=str(value.get("expected_source_type") or "").strip(),
        status="closed" if str(value.get("status") or "") == "closed" else "open",
        evidence_source=str(value.get("evidence_source") or "").strip(),
        created_run=str(value.get("created_run") or "").strip(),
        closed_run=str(value.get("closed_run") or "").strip(),
    )


def _evidence_source(source_event: dict[str, Any] | SourceEvent | None) -> str:
    if source_event is None:
        return ""
    if isinstance(source_event, SourceEvent):
        data = source_event.to_dict()
    else:
        data = source_event
    return str(
        data.get("drive_url")
        or data.get("file_name")
        or data.get("drive_file_id")
        or data.get("source_type")
        or ""
    ).strip()


def _manifest_matches_site(payload: dict[str, Any], *, site_id: str, site_title: str) -> bool:
    wanted_id = site_id.strip()
    wanted_title = " ".join(site_title.lower().split())
    if wanted_id and str(payload.get("site_id") or "").strip() == wanted_id:
        return True
    if wanted_title and " ".join(str(payload.get("site_title") or "").lower().split()) == wanted_title:
        return True
    return False
