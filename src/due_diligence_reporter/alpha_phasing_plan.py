"""Phase 1 Phase 2 workbook generation helpers."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from .ops_skill_loader import OpsSkillLoadError, load_ops_skill_file

_VERSION_PATTERN = re.compile(r"(?m)^\s*version:\s*['\"]?([^'\"\s]+)['\"]?\s*$")
_THEME_ID_PATTERN = re.compile(r"(?m)^\s*themeId:\s*([^\s]+)\s*$")

_WORKBOOK_TABS: tuple[str, ...] = (
    "Executive Summary",
    "Quality Bar Matrix",
    "Phase I Budget Schedule",
    "Phase II Budget Schedule",
    "Render Deck Inputs",
    "Source Notes",
)


@dataclass(frozen=True)
class AlphaPhasingSkill:
    """Loaded alpha-phasing skill metadata."""

    version: str
    source: str
    scorecard_theme_id: str


class AlphaPhasingPlanError(RuntimeError):
    """Raised when the Phase 1 Phase 2 workbook cannot be loaded or rendered."""


def load_alpha_phasing_skill() -> AlphaPhasingSkill:
    """Load hosted Ops-Skills metadata for alpha-phasing-plan."""

    try:
        loaded = load_ops_skill_file("alpha-phasing-plan")
    except OpsSkillLoadError as exc:
        raise AlphaPhasingPlanError(
            "Could not load Ops-Skills alpha-phasing-plan SKILL.md. Set "
            "OPS_SKILLS_REPO_PATH to the Ops-Skills repo root or install the "
            "Ops Skills Codex plugin cache."
        ) from exc

    version = _extract_optional(_VERSION_PATTERN, loaded.text) or "unversioned"
    theme_id = _extract_optional(_THEME_ID_PATTERN, loaded.text) or ""
    return AlphaPhasingSkill(
        version=version,
        source=loaded.source,
        scorecard_theme_id=theme_id,
    )


def missing_alpha_phasing_inputs(
    *,
    site_name: str,
    site_address: str,
    source_of_truth: str,
    quality_bar_target: str,
    opening_target_date: str,
    must_complete_before_opening: str,
    deferred_scopes: Any,
) -> list[str]:
    """Return missing minimum inputs from the alpha-phasing skill contract."""

    missing: list[str] = []
    if not site_name.strip():
        missing.append("site name")
    if not site_address.strip():
        missing.append("site address")
    if not source_of_truth.strip():
        missing.append("source of truth for phasing or budget tracker")
    if not quality_bar_target.strip():
        missing.append("quality bar target")
    if not opening_target_date.strip():
        missing.append("opening target date")
    if not must_complete_before_opening.strip():
        missing.append("Phase I scope required before opening")
    if not _normalize_list(deferred_scopes):
        missing.append("confirmed Phase II deferred scope")
    return missing


def alpha_phasing_open_items(missing_inputs: list[str]) -> str:
    """Format missing input names as concrete DDR verification open items."""

    return "\n".join(
        f"- Confirm Phase 1 Phase 2 workbook input: {item}."
        for item in missing_inputs
    )


def build_alpha_phasing_report_fields(
    *,
    workbook_url: str = "",
    phase_i_scope_summary: str = "",
    must_complete_before_opening: str = "",
    deferred_scopes: Any = None,
    phase_ii_budget_items: Any = None,
    phase_ii_total_allowance: str = "",
    recommended_timing: str = "",
    quality_bar_target: str = "",
) -> dict[str, str]:
    """Build DDR report_data_fields returned by the phasing publisher tool."""

    phase_i_scope = phase_i_scope_summary.strip() or must_complete_before_opening.strip()
    phase_ii_scope = _summarize_scopes(deferred_scopes)
    allowance = (
        phase_ii_total_allowance.strip()
        or _format_cost_k(_sum_costs(_normalize_line_items(phase_ii_budget_items)))
    )
    quality_status = ""
    if quality_bar_target.strip():
        gap_count = len(_normalize_list(deferred_scopes))
        plural = "" if gap_count == 1 else "s"
        quality_status = (
            f"{quality_bar_target.strip()} target with {gap_count} confirmed "
            f"Phase II gap{plural}."
        )

    fields: dict[str, str] = {}
    if workbook_url.strip():
        fields["sources.alpha_phasing_plan_link"] = workbook_url.strip()
    if phase_i_scope:
        fields["exec.alpha_phasing_phase_i_scope"] = phase_i_scope
    if phase_ii_scope:
        fields["exec.alpha_phasing_phase_ii_scope"] = phase_ii_scope
    if allowance:
        fields["exec.alpha_phasing_phase_ii_allowance"] = allowance
    if recommended_timing.strip():
        fields["exec.alpha_phasing_recommended_timing"] = recommended_timing.strip()
    if quality_status:
        fields["exec.alpha_phasing_quality_bar_status"] = quality_status
    return fields


def build_alpha_phasing_workbook(
    *,
    site_name: str,
    site_address: str,
    source_of_truth: str,
    quality_bar_target: str,
    opening_target_date: str,
    must_complete_before_opening: str,
    deferred_scopes: Any,
    phase_i_scope_summary: str = "",
    phase_i_budget_items: Any = None,
    phase_ii_budget_items: Any = None,
    phase_ii_total_allowance: str = "",
    recommended_timing: str = "",
    render_deck_inputs: Any = None,
    source_notes: Any = None,
    budget_tracker_url: str = "",
    skill: AlphaPhasingSkill | None = None,
) -> bytes:
    """Create a minimal valid XLSX workbook matching the skill tab contract."""

    phase_i_items = _normalize_line_items(phase_i_budget_items)
    phase_ii_items = _normalize_line_items(phase_ii_budget_items)
    if not phase_i_items:
        phase_i_items = [{
            "line_item": "Phase I opening scope",
            "rom_cost": "",
            "rom_duration": "",
            "scope_description": must_complete_before_opening,
            "estimating_basis": source_of_truth,
        }]
    if not phase_ii_items:
        phase_ii_items = [
            {
                "line_item": scope,
                "rom_cost": "",
                "rom_duration": "",
                "scope_description": scope,
                "estimating_basis": "Pending site-specific pricing",
            }
            for scope in _normalize_list(deferred_scopes)
        ]

    fields = build_alpha_phasing_report_fields(
        workbook_url="",
        phase_i_scope_summary=phase_i_scope_summary,
        must_complete_before_opening=must_complete_before_opening,
        deferred_scopes=deferred_scopes,
        phase_ii_budget_items=phase_ii_items,
        phase_ii_total_allowance=phase_ii_total_allowance,
        recommended_timing=recommended_timing,
        quality_bar_target=quality_bar_target,
    )

    phase_ii_allowance = (
        phase_ii_total_allowance.strip()
        or fields.get("exec.alpha_phasing_phase_ii_allowance", "")
        or "Pending pricing"
    )

    worksheets = [
        _executive_summary_rows(
            site_name=site_name,
            site_address=site_address,
            source_of_truth=source_of_truth,
            quality_bar_target=quality_bar_target,
            opening_target_date=opening_target_date,
            phase_i_scope=fields.get("exec.alpha_phasing_phase_i_scope", ""),
            phase_ii_scope=fields.get("exec.alpha_phasing_phase_ii_scope", ""),
            phase_ii_allowance=phase_ii_allowance,
            recommended_timing=recommended_timing,
            budget_tracker_url=budget_tracker_url,
            skill=skill,
        ),
        _quality_bar_rows(quality_bar_target, deferred_scopes),
        _budget_rows("Phase I", phase_i_items),
        _budget_rows("Phase II", phase_ii_items),
        _render_deck_rows(render_deck_inputs),
        _source_note_rows(source_of_truth, budget_tracker_url, source_notes, skill),
    ]

    return _write_xlsx(worksheets)


def _executive_summary_rows(
    *,
    site_name: str,
    site_address: str,
    source_of_truth: str,
    quality_bar_target: str,
    opening_target_date: str,
    phase_i_scope: str,
    phase_ii_scope: str,
    phase_ii_allowance: str,
    recommended_timing: str,
    budget_tracker_url: str,
    skill: AlphaPhasingSkill | None,
) -> list[list[Any]]:
    rows: list[list[Any]] = [
        ["Phase 1 Phase 2 workbook"],
        ["Site", site_name],
        ["Address", site_address],
        ["Quality Bar Target", quality_bar_target],
        ["Opening Target Date", opening_target_date],
        ["Phase I Opening Scope", phase_i_scope],
        ["Phase II Deferred Scope", phase_ii_scope],
        ["Phase II Total Allowance", phase_ii_allowance],
        ["Recommended Phase II Timing", recommended_timing or "Pending school calendar"],
        ["Source of Truth", source_of_truth],
        ["Budget Tracker", budget_tracker_url or "Pending tracker confirmation"],
    ]
    if skill is not None:
        rows.extend([
            ["Skill Version", skill.version],
            ["Skill Source", skill.source],
        ])
    return rows


def _quality_bar_rows(quality_bar_target: str, deferred_scopes: Any) -> list[list[Any]]:
    rows = [[
        "Quality Bar Element",
        "Target",
        "Phase I Status vs Quality Bar",
        "Phase II Status vs Quality Bar",
        "Notes",
    ]]
    scopes = _normalize_list(deferred_scopes)
    if not scopes:
        rows.append(["Pending", quality_bar_target, "Pending confirmation", "", ""])
        return rows
    for scope in scopes:
        rows.append([
            scope,
            quality_bar_target,
            "Gap remains after Phase I",
            "Planned for Phase II pending budget approval",
            "Confirmed deferred scope; pricing must stay tied to source notes.",
        ])
    return rows


def _budget_rows(phase_label: str, items: list[dict[str, str]]) -> list[list[Any]]:
    rows: list[list[Any]] = [[
        "Line Item",
        "ROM Cost",
        "ROM Duration",
        "Scope Description",
        "Estimating Basis",
    ]]
    for item in items:
        parsed_cost = _parse_cost(item["rom_cost"])
        rows.append([
            item["line_item"],
            parsed_cost if parsed_cost is not None else item["rom_cost"],
            item["rom_duration"],
            item["scope_description"],
            item["estimating_basis"],
        ])
    first_total_row = 2
    last_total_row = len(rows)
    rows.append([
        "Total",
        f"=SUM(B{first_total_row}:B{last_total_row})",
        "",
        "",
        f"{phase_label} total formula",
    ])
    return rows


def _render_deck_rows(render_deck_inputs: Any) -> list[list[Any]]:
    rows = [["Asset / View", "Purpose", "Phase", "Notes"]]
    normalized = _normalize_records(render_deck_inputs)
    if not normalized:
        rows.append(["Pending render inputs", "Explain Phase II end state", "Phase II", ""])
        return rows
    for item in normalized:
        rows.append([
            item.get("asset") or item.get("view") or item.get("name") or "Render input",
            item.get("purpose") or "",
            item.get("phase") or "",
            item.get("notes") or item.get("description") or "",
        ])
    return rows


def _source_note_rows(
    source_of_truth: str,
    budget_tracker_url: str,
    source_notes: Any,
    skill: AlphaPhasingSkill | None,
) -> list[list[Any]]:
    rows = [["Source", "Notes"]]
    rows.append(["Source of Truth", source_of_truth])
    rows.append(["Budget Tracker", budget_tracker_url or "Pending confirmation"])
    if skill is not None:
        rows.append(["Ops-Skills Source", skill.source])
        rows.append(["Ops-Skills Version", skill.version])
    notes = _normalize_list(source_notes)
    for note in notes:
        rows.append(["Note", note])
    if not notes:
        rows.append(["Open Item", "Confirm formulas, budget tracker, and pricing sources before final use."])
    return rows


def _write_xlsx(worksheets: list[list[list[Any]]]) -> bytes:
    buffer = BytesIO()
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types_xml(len(worksheets)))
        zf.writestr("_rels/.rels", _root_rels_xml())
        zf.writestr("docProps/core.xml", _core_xml(now))
        zf.writestr("docProps/app.xml", _app_xml(len(worksheets)))
        zf.writestr("xl/workbook.xml", _workbook_xml(_WORKBOOK_TABS[: len(worksheets)]))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml(len(worksheets)))
        zf.writestr("xl/styles.xml", _styles_xml())
        for idx, rows in enumerate(worksheets, start=1):
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", _worksheet_xml(rows))
    return buffer.getvalue()


def _worksheet_xml(rows: list[list[Any]]) -> str:
    row_xml: list[str] = []
    for row_idx, row in enumerate(rows, start=1):
        cells = []
        for col_idx, value in enumerate(row, start=1):
            ref = f"{_column_name(col_idx)}{row_idx}"
            cells.append(_cell_xml(ref, value, style="1" if row_idx == 1 else "0"))
        row_xml.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView showGridLines="0" workbookViewId="0">'
        '<pane xSplit="1" ySplit="5" topLeftCell="B6" activePane="bottomRight" state="frozen"/>'
        "</sheetView></sheetViews>"
        '<cols><col min="1" max="1" width="24" customWidth="1"/>'
        '<col min="2" max="9" width="28" customWidth="1"/></cols>'
        f"<sheetData>{''.join(row_xml)}</sheetData>"
        "</worksheet>"
    )


def _cell_xml(ref: str, value: Any, *, style: str) -> str:
    if isinstance(value, str) and value.startswith("="):
        return f'<c r="{ref}" s="{style}"><f>{escape(value[1:])}</f><v>0</v></c>'
    if isinstance(value, int | float):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(str(value or ""))
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t>{text}</t></is></c>'


def _content_types_xml(sheet_count: int) -> str:
    sheets = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        f"{sheets}</Types>"
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
        'Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" '
        'Target="docProps/app.xml"/>'
        "</Relationships>"
    )


def _workbook_xml(sheet_names: tuple[str, ...]) -> str:
    sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, name in enumerate(sheet_names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets></workbook>"
    )


def _workbook_rels_xml(sheet_count: int) -> str:
    rels = "".join(
        f'<Relationship Id="rId{i}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, sheet_count + 1)
    )
    rels += (
        f'<Relationship Id="rId{sheet_count + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels}</Relationships>"
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="12"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF014B50"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
        "</styleSheet>"
    )


def _core_xml(created: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:creator>Due Diligence Reporter</dc:creator>"
        "<dc:title>Phase 1 Phase 2 workbook</dc:title>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>'
        "</cp:coreProperties>"
    )


def _app_xml(sheet_count: int) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        "<Application>Due Diligence Reporter</Application>"
        f"<Worksheets>{sheet_count}</Worksheets></Properties>"
    )


def _normalize_line_items(value: Any) -> list[dict[str, str]]:
    records = _normalize_records(value)
    items: list[dict[str, str]] = []
    for record in records:
        line_item = (
            record.get("line_item")
            or record.get("item")
            or record.get("name")
            or record.get("scope")
            or ""
        )
        if not str(line_item).strip():
            continue
        items.append({
            "line_item": str(line_item).strip(),
            "rom_cost": record.get("rom_cost") or record.get("cost") or "",
            "rom_duration": record.get("rom_duration") or record.get("duration") or "",
            "scope_description": (
                record.get("scope_description")
                or record.get("description")
                or record.get("scope")
                or str(line_item)
            ),
            "estimating_basis": record.get("estimating_basis") or record.get("basis") or "",
        })
    return items


def _normalize_records(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    records: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            records.append({str(k): str(v) for k, v in item.items() if v is not None})
        elif str(item).strip():
            records.append({"name": str(item).strip()})
    return records


def _normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [
            item.strip(" -\t")
            for item in re.split(r"\n|;", value)
            if item.strip(" -\t")
        ]
    return []


def _summarize_scopes(value: Any) -> str:
    scopes = _normalize_list(value)
    if not scopes:
        return ""
    if len(scopes) <= 3:
        return "; ".join(scopes)
    return "; ".join(scopes[:3]) + f"; +{len(scopes) - 3} more"


def _sum_costs(items: list[dict[str, str]]) -> float | None:
    total = 0.0
    found = False
    for item in items:
        parsed = _parse_cost(item.get("rom_cost", ""))
        if parsed is None:
            continue
        total += parsed
        found = True
    return total if found else None


def _parse_cost(value: Any) -> float | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    match = re.search(r"([-+]?\d+(?:,\d{3})*(?:\.\d+)?)\s*([km]?)", text)
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    suffix = match.group(2)
    if suffix == "k":
        return number * 1000
    if suffix == "m":
        return number * 1_000_000
    return number


def _format_cost_k(value: float | None) -> str:
    if value is None:
        return ""
    return f"${round(value / 1000):,}k"


def _column_name(index: int) -> str:
    name = ""
    current = index
    while current:
        current, remainder = divmod(current - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _extract_optional(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip() if match else None
