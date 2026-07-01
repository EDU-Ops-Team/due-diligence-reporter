"""M2 source-packet contract for direct due-diligence field writes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
from typing import Any

from .source_types import canonical_source_type

REGISTERED_DOCUMENT_STATUSES = frozenset({"registered", "already_registered"})

SCHEMA_GAP_CONFIRMATION_FIELDS = frozenset(
    {
        "fast_open_mode_confirmed",
        "fast_open_occupancy_type_confirmed",
        "max_plan_occupancy_type_confirmed",
        "current_occupancy_confirmed",
        "zoning_status_confirmed",
        "site_square_footage_confirmed",
    }
)


@dataclass(frozen=True)
class RequiredSource:
    """One required source slot for a DD field.

    ``any_of`` supports equivalent source records, such as a CO or permit of
    record for current occupancy evidence.
    """

    label: str
    any_of: tuple[str, ...]


@dataclass(frozen=True)
class M2FieldSpec:
    """M2 field ownership, writable LocationOS key, and evidence contract."""

    field: str
    report_data_key: str | None
    locationos_key: str | None
    writer: str
    required_sources: tuple[RequiredSource, ...]
    hold_reason: str = ""


@dataclass(frozen=True)
class SourceDocumentRef:
    """Registered or pending support document for M2 field evidence."""

    source_type: str
    title: str
    drive_url: str = ""
    drive_file_id: str = ""
    rhodes_doc_type: str = ""
    quality_bar: str = ""
    registration_status: str = ""
    fields_supported: tuple[str, ...] = dataclass_field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DDFieldUpdate:
    """One direct DD-field write intent plus evidence/readback state."""

    field: str
    locationos_key: str | None
    value: Any
    writer: str
    required_source_docs: tuple[str, ...]
    write_status: str
    readback_status: str
    hold_reason: str = ""
    source_titles: tuple[str, ...] = dataclass_field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _source(label: str, *source_types: str) -> RequiredSource:
    return RequiredSource(label=label, any_of=tuple(source_types))


_M2_FIELD_MATRIX: tuple[M2FieldSpec, ...] = (
    M2FieldSpec(
        field="fast_open_capacity",
        report_data_key="exec.fastest_open_capacity",
        locationos_key="foCapacity",
        writer="alpha_capacity_analysis",
        required_sources=(_source("Alpha Capacity Analysis", "alpha_capacity_analysis"),),
    ),
    M2FieldSpec(
        field="max_plan_capacity",
        report_data_key="exec.max_capacity_capacity",
        locationos_key="maxCapCapacity",
        writer="alpha_capacity_analysis",
        required_sources=(_source("Alpha Capacity Analysis", "alpha_capacity_analysis"),),
    ),
    M2FieldSpec(
        field="fast_open_date",
        report_data_key="exec.fastest_open_open_date",
        locationos_key="foDate",
        writer="opening_plan",
        required_sources=(
            _source("Opening Plan", "opening_plan_report"),
            _source("Cost/Timeline Estimate", "cost_timeline_estimate"),
        ),
    ),
    M2FieldSpec(
        field="max_plan_date",
        report_data_key="exec.max_capacity_open_date",
        locationos_key="maxCapProjOpenDate",
        writer="opening_plan",
        required_sources=(
            _source("Opening Plan", "opening_plan_report"),
            _source("Cost/Timeline Estimate", "cost_timeline_estimate"),
        ),
    ),
    M2FieldSpec(
        field="fast_open_capex",
        report_data_key="exec.fastest_open_capex",
        locationos_key="foCapEx",
        writer="alpha_phasing_plan",
        required_sources=(
            _source("Alpha Phasing Plan", "alpha_phasing_plan_report"),
            _source("Cost/Timeline Estimate", "cost_timeline_estimate"),
        ),
    ),
    M2FieldSpec(
        field="max_plan_capex",
        report_data_key="exec.max_capacity_capex",
        locationos_key="maxCapCapEx",
        writer="alpha_phasing_plan",
        required_sources=(
            _source("Alpha Phasing Plan", "alpha_phasing_plan_report"),
            _source("Cost/Timeline Estimate", "cost_timeline_estimate"),
        ),
    ),
    M2FieldSpec(
        field="building_score",
        report_data_key="exec.building_score",
        locationos_key="buildingScore",
        writer="alpha_phasing_plan",
        required_sources=(_source("Alpha Phasing Plan", "alpha_phasing_plan_report"),),
    ),
    M2FieldSpec(
        field="building_comment",
        report_data_key="exec.building_comment",
        locationos_key="buildingComment",
        writer="alpha_phasing_plan",
        required_sources=(_source("Alpha Phasing Plan", "alpha_phasing_plan_report"),),
    ),
    M2FieldSpec(
        field="play_area_score",
        report_data_key="exec.play_area_score",
        locationos_key="playAreaScore",
        writer="outdoor_play_space",
        required_sources=(_source("Outdoor Play Space Report", "outdoor_play_space_report"),),
    ),
    M2FieldSpec(
        field="play_area_comment",
        report_data_key="exec.play_area_comment",
        locationos_key="playAreaComment",
        writer="outdoor_play_space",
        required_sources=(_source("Outdoor Play Space Report", "outdoor_play_space_report"),),
    ),
    M2FieldSpec(
        field="regulatory_score",
        report_data_key="exec.regulatory_score",
        locationos_key="regulatoryScore",
        writer="regulatory_resolver",
        required_sources=(
            _source("SIR zoning evidence", "sir"),
            _source("School Approval Report", "school_approval_report"),
            _source("Opening Plan", "opening_plan_report"),
        ),
    ),
    M2FieldSpec(
        field="regulatory_comment",
        report_data_key="exec.regulatory_comment",
        locationos_key="regulatoryComment",
        writer="regulatory_resolver",
        required_sources=(
            _source("SIR zoning evidence", "sir"),
            _source("School Approval Report", "school_approval_report"),
            _source("Opening Plan", "opening_plan_report"),
        ),
    ),
    M2FieldSpec(
        field="school_ops_score",
        report_data_key="exec.school_ops_score",
        locationos_key="schoolOperationsScore",
        writer="school_ops_resolver",
        required_sources=(_source("KH traffic analysis", "traffic_analysis"),),
    ),
    M2FieldSpec(
        field="school_ops_comment",
        report_data_key="exec.school_ops_comment",
        locationos_key="schoolOperationsComment",
        writer="school_ops_resolver",
        required_sources=(_source("KH traffic analysis", "traffic_analysis"),),
    ),
    M2FieldSpec(
        field="fast_open_mode_confirmed",
        report_data_key=None,
        locationos_key=None,
        writer="opening_plan",
        required_sources=(_source("Opening Plan", "opening_plan_report"),),
        hold_reason="locationos_schema_gap",
    ),
    M2FieldSpec(
        field="fast_open_occupancy_type_confirmed",
        report_data_key=None,
        locationos_key=None,
        writer="opening_plan",
        required_sources=(_source("Opening Plan", "opening_plan_report"),),
        hold_reason="locationos_schema_gap",
    ),
    M2FieldSpec(
        field="max_plan_occupancy_type_confirmed",
        report_data_key=None,
        locationos_key=None,
        writer="opening_plan",
        required_sources=(_source("Opening Plan", "opening_plan_report"),),
        hold_reason="locationos_schema_gap",
    ),
    M2FieldSpec(
        field="current_occupancy_confirmed",
        report_data_key=None,
        locationos_key=None,
        writer="occupancy_resolver",
        required_sources=(
            _source(
                "CO / permit of record",
                "certificate_of_occupancy",
                "permit_of_record",
            ),
        ),
        hold_reason="locationos_schema_gap",
    ),
    M2FieldSpec(
        field="zoning_status_confirmed",
        report_data_key=None,
        locationos_key=None,
        writer="regulatory_resolver",
        required_sources=(
            _source("SIR zoning evidence", "sir"),
            _source("School Approval Report", "school_approval_report"),
            _source("Opening Plan", "opening_plan_report"),
        ),
        hold_reason="locationos_schema_gap",
    ),
    M2FieldSpec(
        field="site_square_footage_confirmed",
        report_data_key=None,
        locationos_key=None,
        writer="space_resolver",
        required_sources=(
            _source(
                "Measured floor plan / BIM",
                "measured_floor_plan",
                "floor_plan",
                "lidar",
            ),
        ),
        hold_reason="locationos_schema_gap",
    ),
)


def m2_field_matrix() -> tuple[M2FieldSpec, ...]:
    return _M2_FIELD_MATRIX


def build_m2_source_packet(
    *,
    values: dict[str, Any],
    supporting_documents: Sequence[SourceDocumentRef | dict[str, Any]],
) -> dict[str, Any]:
    docs = [_coerce_source_document(doc) for doc in supporting_documents]
    updates = build_dd_field_updates(values=values, supporting_documents=docs)
    completion = source_packet_completion(
        supporting_documents=docs,
        dd_field_updates=updates,
    )
    note_lines = source_packet_note_lines(
        supporting_documents=docs,
        dd_field_updates=updates,
    )
    return {
        "status": completion["status"],
        "m2_source_packet_complete": completion["m2_source_packet_complete"],
        "supporting_documents": [doc.to_dict() for doc in docs],
        "dd_field_updates": [update.to_dict() for update in updates],
        "source_note_lines": note_lines,
        "open_items": completion["open_items"],
    }


def build_dd_field_updates(
    *,
    values: dict[str, Any],
    supporting_documents: Sequence[SourceDocumentRef | dict[str, Any]],
) -> list[DDFieldUpdate]:
    docs = [_coerce_source_document(doc) for doc in supporting_documents]
    updates: list[DDFieldUpdate] = []
    for spec in _M2_FIELD_MATRIX:
        value = _field_value(spec, values)
        source_titles, missing_sources = _source_titles_for_spec(spec, docs)
        required_labels = tuple(source.label for source in spec.required_sources)
        if missing_sources:
            missing_text = ", ".join(missing_sources)
            updates.append(
                DDFieldUpdate(
                    field=spec.field,
                    locationos_key=spec.locationos_key,
                    value=value,
                    writer=spec.writer,
                    required_source_docs=required_labels,
                    write_status="blocked",
                    readback_status="not_started",
                    hold_reason=f"required_source_not_registered: {missing_text}",
                    source_titles=source_titles,
                )
            )
            continue
        if value is None:
            updates.append(
                DDFieldUpdate(
                    field=spec.field,
                    locationos_key=spec.locationos_key,
                    value=None,
                    writer=spec.writer,
                    required_source_docs=required_labels,
                    write_status="held",
                    readback_status="held",
                    hold_reason="missing_value",
                    source_titles=source_titles,
                )
            )
            continue
        if spec.hold_reason:
            updates.append(
                DDFieldUpdate(
                    field=spec.field,
                    locationos_key=spec.locationos_key,
                    value=value,
                    writer=spec.writer,
                    required_source_docs=required_labels,
                    write_status="held",
                    readback_status="held",
                    hold_reason=spec.hold_reason,
                    source_titles=source_titles,
                )
            )
            continue
        updates.append(
            DDFieldUpdate(
                field=spec.field,
                locationos_key=spec.locationos_key,
                value=value,
                writer=spec.writer,
                required_source_docs=required_labels,
                write_status="pending",
                readback_status="pending",
                source_titles=source_titles,
            )
        )
    return updates


def source_packet_completion(
    *,
    supporting_documents: Sequence[SourceDocumentRef | dict[str, Any]],
    dd_field_updates: Sequence[DDFieldUpdate | dict[str, Any]],
) -> dict[str, Any]:
    docs = [_coerce_source_document(doc) for doc in supporting_documents]
    updates = [_coerce_field_update(update) for update in dd_field_updates]
    open_items: list[str] = []

    for doc in docs:
        if doc.registration_status not in REGISTERED_DOCUMENT_STATUSES:
            open_items.append(f"Register source document: {doc.title}")
        elif not doc.rhodes_doc_type.strip():
            open_items.append(f"Map source document: {doc.title}")

    for update in updates:
        if update.write_status == "blocked":
            open_items.append(f"{update.field}: {update.hold_reason or 'blocked'}")
            continue
        if update.locationos_key:
            if update.write_status not in {"written", "updated"}:
                open_items.append(f"{update.field}: write not completed")
            if update.readback_status != "verified":
                open_items.append(f"{update.field}: readback not verified")
            continue
        if update.hold_reason != "locationos_schema_gap":
            open_items.append(f"{update.field}: {update.hold_reason or 'held'}")

    return {
        "status": "complete" if not open_items else "blocked",
        "m2_source_packet_complete": not open_items,
        "open_items": _dedupe(open_items),
    }


def source_packet_note_lines(
    *,
    supporting_documents: Sequence[SourceDocumentRef | dict[str, Any]],
    dd_field_updates: Sequence[DDFieldUpdate | dict[str, Any]],
    limit: int = 14,
) -> list[str]:
    _ = [_coerce_source_document(doc) for doc in supporting_documents]
    updates = [_coerce_field_update(update) for update in dd_field_updates]
    lines: list[str] = []
    for update in updates:
        if update.value is None:
            continue
        if update.write_status == "blocked":
            value = f"held: {update.hold_reason}"
        elif update.hold_reason == "locationos_schema_gap":
            value = f"{_short_text(update.value)} (held: LocationOS schema gap)"
        else:
            value = _short_text(update.value)
        source_title = ", ".join(_safe_title(title) for title in update.source_titles)
        if not source_title:
            source_title = ", ".join(update.required_source_docs)
        lines.append(f"{update.field} -> {value} -> {source_title}")
        if len(lines) >= limit:
            break
    return lines


def translate_outdoor_play_score(result: dict[str, Any]) -> dict[str, Any]:
    """Translate outdoor-play-space output into LocationOS 1/2/3 score semantics."""

    on_site = str(result.get("on_site_verdict") or "").strip().lower()
    off_site = str(result.get("off_site_verdict") or "").strip().lower()
    confidence = str(result.get("confidence") or "").strip().upper()
    safety_flags = _string_list(result.get("safety_flags"))
    warnings = _string_list(result.get("warnings"))
    no_candidate_reason = str(result.get("no_candidate_reason") or "").strip()
    required_sf = result.get("required_outdoor_sf")

    if confidence in {"A", "B"} and not safety_flags and (on_site == "pass" or off_site == "pass"):
        if on_site == "pass":
            comment = "On-site outdoor play option passes."
        else:
            comment = "Off-site outdoor play option passes within the walk limit."
        if required_sf:
            comment += f" Required area: {required_sf} SF."
        return {"score": 1, "comment": comment}

    if (
        confidence == "C"
        or on_site == "needs_manual_review"
        or off_site == "needs_manual_review"
        or safety_flags
        or any("manual review" in warning.lower() for warning in warnings)
    ):
        reasons = safety_flags if safety_flags else ([no_candidate_reason] if no_candidate_reason else [])
        reason_text = f" ({', '.join(reasons)})" if reasons else ""
        return {
            "score": 2,
            "comment": (
                "Plausible outdoor play option exists but needs manual review"
                f"{reason_text}."
            ),
        }

    return {
        "score": 3,
        "comment": (
            "No viable outdoor play option was confirmed"
            + (f" ({no_candidate_reason})." if no_candidate_reason else ".")
        ),
    }


def mark_written_fields_from_update_result(
    *,
    source_packet: dict[str, Any],
    update_result: dict[str, Any],
) -> dict[str, Any]:
    """Return source packet metadata with write/readback statuses updated."""

    packet = dict(source_packet)
    raw_updates = packet.get("dd_field_updates")
    if not isinstance(raw_updates, list):
        return packet
    status = str(update_result.get("status") or "").strip().lower()
    updated_fields = {
        str(field).strip()
        for field in update_result.get("updated_fields", [])
        if str(field).strip()
    }
    next_updates: list[dict[str, Any]] = []
    for raw_update in raw_updates:
        update = _coerce_field_update(raw_update)
        row = update.to_dict()
        if update.locationos_key and update.locationos_key in updated_fields:
            if status == "updated":
                row["write_status"] = "written"
                row["readback_status"] = "verified"
            elif status == "failed":
                row["write_status"] = "failed"
                row["readback_status"] = "failed"
                row["hold_reason"] = str(
                    update_result.get("error_summary")
                    or update_result.get("error")
                    or update_result.get("reason")
                    or "write_failed"
                )
        next_updates.append(row)
    packet["dd_field_updates"] = next_updates
    completion = source_packet_completion(
        supporting_documents=packet.get("supporting_documents", []),
        dd_field_updates=next_updates,
    )
    packet["status"] = completion["status"]
    packet["m2_source_packet_complete"] = completion["m2_source_packet_complete"]
    packet["open_items"] = completion["open_items"]
    packet["source_note_lines"] = source_packet_note_lines(
        supporting_documents=packet.get("supporting_documents", []),
        dd_field_updates=next_updates,
    )
    return packet


def locationos_fields_allowed_by_source_packet(
    fields: dict[str, Any],
    source_packet: dict[str, Any] | None,
) -> dict[str, Any]:
    """Filter writable LocationOS fields to packet-approved field updates."""

    if not source_packet:
        return dict(fields)
    raw_updates = source_packet.get("dd_field_updates")
    field_updates = raw_updates if isinstance(raw_updates, list) else []
    allowed = {
        update.locationos_key
        for update in (_coerce_field_update(raw) for raw in field_updates)
        if update.locationos_key and update.write_status == "pending"
    }
    system_fields = {"status", "recommendation"}
    if source_packet_is_complete(source_packet):
        system_fields.update({"dateCompleted", "ddReportLink"})
    filtered = {
        key: value
        for key, value in fields.items()
        if key in allowed or key in system_fields
    }
    if not source_packet_is_complete(source_packet) and filtered.get("status") == "complete":
        filtered["status"] = "data-gathering"
    return filtered


def source_packet_is_complete(source_packet: dict[str, Any]) -> bool:
    """Return True only for an explicitly complete M2 source packet."""

    if source_packet.get("m2_source_packet_complete") is not True:
        return False
    if str(source_packet.get("status") or "").strip().lower() != "complete":
        return False
    open_items = source_packet.get("open_items")
    return not (isinstance(open_items, list) and open_items)


def _field_value(spec: M2FieldSpec, values: dict[str, Any]) -> Any:
    for key in (spec.field, spec.report_data_key):
        if not key:
            continue
        if key in values:
            return _clean_value(values[key])
    return None


def _source_titles_for_spec(
    spec: M2FieldSpec,
    docs: list[SourceDocumentRef],
) -> tuple[tuple[str, ...], list[str]]:
    titles: list[str] = []
    missing: list[str] = []
    for required in spec.required_sources:
        matched = [
            doc
            for doc in docs
            if doc.source_type in required.any_of
            and _source_doc_is_registered_and_mapped(doc)
        ]
        if not matched:
            missing.append(required.label)
            continue
        titles.append(matched[0].title)
    return tuple(titles), missing


def _source_doc_is_registered_and_mapped(doc: SourceDocumentRef) -> bool:
    return (
        doc.registration_status in REGISTERED_DOCUMENT_STATUSES
        and bool(doc.rhodes_doc_type.strip())
    )


def _coerce_source_document(raw: SourceDocumentRef | dict[str, Any]) -> SourceDocumentRef:
    if isinstance(raw, SourceDocumentRef):
        data = raw.to_dict()
        data["source_type"] = canonical_source_type(raw.source_type)
        return SourceDocumentRef(**data)
    return SourceDocumentRef(
        source_type=canonical_source_type(
            str(raw.get("source_type") or raw.get("doc_type") or "").strip()
        ),
        title=str(raw.get("title") or raw.get("name") or "").strip(),
        drive_url=str(raw.get("drive_url") or raw.get("doc_url") or "").strip(),
        drive_file_id=str(raw.get("drive_file_id") or raw.get("doc_id") or "").strip(),
        rhodes_doc_type=str(raw.get("rhodes_doc_type") or "").strip(),
        quality_bar=str(raw.get("quality_bar") or "").strip(),
        registration_status=str(raw.get("registration_status") or raw.get("status") or "").strip(),
        fields_supported=_string_tuple(
            raw.get("fields_supported") or raw.get("fields_supported_by_doc")
        ),
    )


def _coerce_field_update(raw: DDFieldUpdate | dict[str, Any]) -> DDFieldUpdate:
    if isinstance(raw, DDFieldUpdate):
        return raw
    return DDFieldUpdate(
        field=str(raw.get("field") or "").strip(),
        locationos_key=(str(raw.get("locationos_key") or "").strip() or None),
        value=raw.get("value"),
        writer=str(raw.get("writer") or "").strip(),
        required_source_docs=_string_tuple(raw.get("required_source_docs")),
        write_status=str(raw.get("write_status") or "").strip(),
        readback_status=str(raw.get("readback_status") or "").strip(),
        hold_reason=str(raw.get("hold_reason") or "").strip(),
        source_titles=_string_tuple(raw.get("source_titles")),
    )


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith("{{") or text.lower().startswith("[not found"):
            return None
        return text
    return value


def _short_text(value: Any, limit: int = 120) -> str:
    text = str(value).strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _safe_title(title: str) -> str:
    text = title.strip()
    if not text:
        return "source document"
    return text.replace("\n", " ")


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
