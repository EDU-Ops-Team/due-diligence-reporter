"""Programmatic Google Doc builder for the DD report.

Constructs the entire DD report document structure from scratch using
Google Docs API batchUpdate requests, replacing the old template-copy-
and-replace flow.
"""

from __future__ import annotations

import logging
from typing import Any

from .report_schema import LINK_DISPLAY_LABELS, LINK_TOKENS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Style constants — reproduce the V3 template visual appearance
# ---------------------------------------------------------------------------

_DARK_BLUE: dict[str, float] = {"red": 0.102, "green": 0.235, "blue": 0.369}  # #1A3C5E
_WHITE: dict[str, float] = {"red": 1.0, "green": 1.0, "blue": 1.0}
_LIGHT_BLUE_BG: dict[str, float] = {"red": 0.937, "green": 0.961, "blue": 0.984}  # #EFF5FB
_LIGHT_BLUE_BORDER: dict[str, float] = {"red": 0.722, "green": 0.796, "blue": 0.878}  # #B8CBE0
_LIGHT_GRAY: dict[str, float] = {"red": 0.973, "green": 0.976, "blue": 0.980}  # #F8F9FA
_LINK_BLUE: dict[str, float] = {"red": 0.067, "green": 0.333, "blue": 0.800}  # #1155CC

# Google Docs API point units (1pt = 1 magnitude with UNIT=PT)
_PT = "PT"

# ---------------------------------------------------------------------------
# Header table rows: (label, token_key)
# ---------------------------------------------------------------------------

_HEADER_ROWS: list[tuple[str, str]] = [
    ("Site Name / Address", "meta.site_name"),
    ("Current Marketing Name", "meta.marketing_name"),
    ("City, State, Zip", "meta.city_state_zip"),
    ("School Type", "meta.school_type"),
    ("Report Date", "meta.report_date"),
    ("Prepared By", "meta.prepared_by"),
    ("Drive Folder", "meta.drive_folder_url"),
]

# ---------------------------------------------------------------------------
# Cost breakdown rows: (row_key, display_label)
# ---------------------------------------------------------------------------

_COST_BREAKDOWN_ROWS: list[tuple[str, str]] = [
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
]

# ---------------------------------------------------------------------------
# Source document rows: (label, token_key)
# ---------------------------------------------------------------------------

_SOURCE_DOC_ROWS: list[tuple[str, str]] = [
    ("Site Investigation Report (SIR)", "sources.sir_link"),
    ("Building Inspection", "sources.inspection_link"),
    ("Program Fit Analysis (ISP)", "sources.isp_link"),
    ("E-Occupancy Assessment", "sources.e_occupancy_link"),
    ("School Approval Assessment", "sources.school_approval_link"),
    ("Opening Plan", "sources.opening_plan_link"),
    ("Report Trace", "sources.trace_link"),
]

# ---------------------------------------------------------------------------
# Gap labels for missing token values
# ---------------------------------------------------------------------------

_LINK_GAP_LABELS: dict[str, str] = {
    "sources.sir_link": "[Not found - SIR]",
    "sources.inspection_link": "[Not found - Building Inspection]",
    "sources.isp_link": "[Not found - ISP]",
    "sources.e_occupancy_link": "[Not found - E-Occupancy Assessment]",
    "sources.school_approval_link": "[Not found - School Approval Assessment]",
    "sources.opening_plan_link": "[Not found - Opening Plan]",
    "sources.trace_link": "",
    "meta.drive_folder_url": "",
}

# ---------------------------------------------------------------------------
# Internal request builder helpers
# ---------------------------------------------------------------------------


class _DocBuilder:
    """Accumulates Google Docs API batchUpdate requests in document order.

    Tracks the current insertion index so callers insert content
    sequentially from top to bottom.  All text insertions shift the
    index forward automatically.
    """

    def __init__(self, start_index: int = 1) -> None:
        self._idx = start_index
        self.requests: list[dict[str, Any]] = []

    @property
    def index(self) -> int:
        return self._idx

    # -- primitives ----------------------------------------------------------

    def insert_text(self, text: str) -> tuple[int, int]:
        """Insert *text* at the current index.  Returns (start, end)."""
        start = self._idx
        self.requests.append({
            "insertText": {
                "location": {"index": start},
                "text": text,
            }
        })
        self._idx += len(text)
        return start, self._idx

    def style_text(
        self,
        start: int,
        end: int,
        *,
        bold: bool | None = None,
        font_size: float | None = None,
        font_family: str | None = None,
        foreground_color: dict[str, float] | None = None,
        link_url: str | None = None,
    ) -> None:
        """Apply text styling to the range [start, end)."""
        style: dict[str, Any] = {}
        fields: list[str] = []

        if bold is not None:
            style["bold"] = bold
            fields.append("bold")
        if font_size is not None:
            style["fontSize"] = {"magnitude": font_size, "unit": _PT}
            fields.append("fontSize")
        if font_family is not None:
            style["weightedFontFamily"] = {"fontFamily": font_family}
            fields.append("weightedFontFamily")
        if foreground_color is not None:
            style["foregroundColor"] = {"color": {"rgbColor": foreground_color}}
            fields.append("foregroundColor")
        if link_url is not None:
            style["link"] = {"url": link_url}
            fields.append("link")

        if not fields:
            return

        self.requests.append({
            "updateTextStyle": {
                "range": {"startIndex": start, "endIndex": end},
                "textStyle": style,
                "fields": ",".join(fields),
            }
        })

    def style_paragraph(
        self,
        start: int,
        end: int,
        *,
        named_style: str | None = None,
        alignment: str | None = None,
        space_above: float | None = None,
        space_below: float | None = None,
        border_bottom: dict[str, Any] | None = None,
    ) -> None:
        """Apply paragraph styling to the range [start, end)."""
        style: dict[str, Any] = {}
        fields: list[str] = []

        if named_style is not None:
            style["namedStyleType"] = named_style
            fields.append("namedStyleType")
        if alignment is not None:
            style["alignment"] = alignment
            fields.append("alignment")
        if space_above is not None:
            style["spaceAbove"] = {"magnitude": space_above, "unit": _PT}
            fields.append("spaceAbove")
        if space_below is not None:
            style["spaceBelow"] = {"magnitude": space_below, "unit": _PT}
            fields.append("spaceBelow")
        if border_bottom is not None:
            style["borderBottom"] = border_bottom
            fields.append("borderBottom")

        if not fields:
            return

        self.requests.append({
            "updateParagraphStyle": {
                "range": {"startIndex": start, "endIndex": end},
                "paragraphStyle": style,
                "fields": ",".join(fields),
            }
        })

    # -- table helpers -------------------------------------------------------

    def insert_table(self, rows: int, columns: int) -> int:
        """Insert a table at the current index.  Returns the index *before*
        the table so callers can locate its cells in the live document.

        IMPORTANT: after inserting a table the exact index offsets for
        cell contents are not known until the document is read back from
        the API.  Use ``insert_table`` as a "phase boundary": build all
        pre-table content first, flush requests, then read the document
        back and populate cells in a second pass.
        """
        table_start = self._idx
        self.requests.append({
            "insertTable": {
                "rows": rows,
                "columns": columns,
                "location": {"index": table_start},
            }
        })
        # We cannot know the exact character-length of the table skeleton
        # the API will insert, so we set _idx to a sentinel that forces
        # callers to re-read the document before inserting more text.
        self._idx = -1
        return table_start

    def insert_page_break(self) -> None:
        """Insert a page break at the current index."""
        # A page break is effectively \f — we insert a newline and
        # style it later if needed.  For the DD report we use section
        # headings rather than page breaks.
        pass

    # -- composite helpers ---------------------------------------------------

    def insert_heading(self, text: str, *, level: int = 1) -> tuple[int, int]:
        """Insert a heading line and style it as HEADING_{level}."""
        start, end = self.insert_text(text + "\n")
        named = f"HEADING_{level}"
        self.style_paragraph(start, end, named_style=named)
        return start, end

    def insert_paragraph(self, text: str) -> tuple[int, int]:
        """Insert a normal paragraph with a trailing newline."""
        return self.insert_text(text + "\n")


# ---------------------------------------------------------------------------
# Table cell population helpers
# ---------------------------------------------------------------------------


def _cell_index(table_element: dict[str, Any], row: int, col: int) -> int:
    """Return the content start index for cell (row, col) in a table element."""
    rows = table_element["table"]["tableRows"]
    cell = rows[row]["tableCells"][col]
    # Each cell has a content array; the first paragraph's first element
    # gives us the insertion point.
    content = cell["content"]
    if content:
        first_para = content[0]
        if "paragraph" in first_para:
            elements = first_para["paragraph"].get("elements", [])
            if elements:
                return int(elements[0].get("startIndex", 0))
            return int(first_para.get("startIndex", 0))
        return int(first_para.get("startIndex", 0))
    return 0


def _table_cell_range(table_element: dict[str, Any], row: int, col: int) -> tuple[int, int]:
    """Return (start, end) indices covering the full content of a cell."""
    rows = table_element["table"]["tableRows"]
    cell = rows[row]["tableCells"][col]
    content = cell["content"]
    if not content:
        return (0, 0)
    first = content[0]
    last = content[-1]
    start = first.get("startIndex", 0)
    end = last.get("endIndex", start + 1)
    return start, end


def _build_cell_style_requests(
    table_element: dict[str, Any],
    row: int,
    col: int,
    *,
    bg_color: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Build requests to style a table cell background."""
    cell_start, cell_end = _table_cell_range(table_element, row, col)
    requests: list[dict[str, Any]] = []

    if bg_color is not None:
        # tableCellStyle update — operates on cell location range
        requests.append({
            "updateTableCellStyle": {
                "tableRange": {
                    "tableCellLocation": {
                        "tableStartLocation": {"index": table_element.get("startIndex", 1)},
                        "rowIndex": row,
                        "columnIndex": col,
                    },
                    "rowSpan": 1,
                    "columnSpan": 1,
                },
                "tableCellStyle": {
                    "backgroundColor": {"color": {"rgbColor": bg_color}},
                },
                "fields": "backgroundColor",
            }
        })

    return requests


def _resolve_value(
    replacements: dict[str, str],
    token: str,
    gap_label: str = "",
) -> str:
    """Resolve a token value from replacements, returning gap_label if missing."""
    value = replacements.get(token, "")
    if value and value.strip():
        return value
    return gap_label


def _is_link_token(token: str) -> bool:
    """Return True if this token is a hyperlink token."""
    return token in LINK_TOKENS


def _resolve_link_value(
    replacements: dict[str, str],
    token: str,
) -> tuple[str, str | None]:
    """Return (display_text, url_or_none) for a link token.

    If the token value is a URL, returns the display label and URL.
    If empty/missing, returns a gap label and None.
    """
    value = replacements.get(token, "")
    if value.startswith("http"):
        display = LINK_DISPLAY_LABELS.get(token, value)
        return display, value
    if value and value.strip():
        return value, None
    gap = _LINK_GAP_LABELS.get(token, "")
    return gap, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_dd_report_doc(
    docs_service: Any,
    drive_service: Any,
    doc_id: str,
    replacements: dict[str, str],
    site_title: str,
) -> dict[str, Any]:
    """Construct the full DD report document structure in a blank Google Doc.

    This function populates the document using multiple batchUpdate
    passes.  The Google Docs API requires that table cell indices be read
    back from the live document after a table is inserted — we cannot
    predict the exact character offsets.  Therefore we use a multi-pass
    approach:

    1. Insert all text/heading content and tables (structure only).
    2. Read back the document to get actual element indices.
    3. Populate table cells and apply styling in subsequent passes.

    Args:
        docs_service: An authenticated ``docs`` API service object.
        drive_service: An authenticated ``drive`` API service object.
        doc_id: The ID of the blank document to populate.
        replacements: Normalized token→value mapping.
        site_title: The site name for the report title.

    Returns:
        A dict summarizing the build: hyperlinks_applied, etc.
    """
    hyperlink_trace: dict[str, Any] = {
        "applied": 0,
        "found_tokens": [],
        "not_found_tokens": [],
    }

    # ── Phase 1: Insert top-level structure ──────────────────────────────
    b = _DocBuilder()

    # Title
    title_start, title_end = b.insert_text("Site Due Diligence Report\n")
    b.style_text(
        title_start, title_end - 1,
        bold=True, font_size=24, font_family="Arial",
        foreground_color=_DARK_BLUE,
    )
    b.style_paragraph(title_start, title_end, alignment="CENTER")

    # Empty line before header table
    b.insert_text("\n")

    # Header table placeholder
    b.insert_table(len(_HEADER_ROWS), 2)

    # Flush phase 1
    _batch_update(docs_service, doc_id, b.requests)

    # ── Phase 2: Populate header table ───────────────────────────────────
    doc = docs_service.documents().get(documentId=doc_id).execute()
    body_content = doc.get("body", {}).get("content", [])

    header_table_el = _find_table(body_content, 0)
    if header_table_el is None:
        logger.error("Could not find header table in document")
        return hyperlink_trace

    phase2_requests: list[dict[str, Any]] = []

    # Populate header cells — work in reverse row order so index offsets
    # for earlier rows remain valid.
    for row_idx in range(len(_HEADER_ROWS) - 1, -1, -1):
        label, token = _HEADER_ROWS[row_idx]

        # Value column (col 1)
        val_idx = _cell_index(header_table_el, row_idx, 1)
        if _is_link_token(token):
            display, url = _resolve_link_value(replacements, token)
            if display:
                phase2_requests.append({
                    "insertText": {
                        "location": {"index": val_idx},
                        "text": display,
                    }
                })
                if url:
                    phase2_requests.append({
                        "updateTextStyle": {
                            "range": {"startIndex": val_idx, "endIndex": val_idx + len(display)},
                            "textStyle": {
                                "link": {"url": url},
                                "foregroundColor": {"color": {"rgbColor": _LINK_BLUE}},
                            },
                            "fields": "link,foregroundColor",
                        }
                    })
                    hyperlink_trace["applied"] += 1
                    hyperlink_trace["found_tokens"].append(token)
        else:
            value = _resolve_value(replacements, token)
            if value:
                phase2_requests.append({
                    "insertText": {
                        "location": {"index": val_idx},
                        "text": value,
                    }
                })

        # Label column (col 0)
        label_idx = _cell_index(header_table_el, row_idx, 0)
        phase2_requests.append({
            "insertText": {
                "location": {"index": label_idx},
                "text": label,
            }
        })

    # Style header table cells
    for row_idx in range(len(_HEADER_ROWS)):
        label, token = _HEADER_ROWS[row_idx]
        # Label cell: bold, 10pt, light blue bg
        label_start, label_end = _table_cell_range(header_table_el, row_idx, 0)
        # We'll apply text styling after insertion — use the label_idx we know
        phase2_requests.extend(
            _build_cell_style_requests(header_table_el, row_idx, 0, bg_color=_LIGHT_BLUE_BG)
        )
        # Value cell: white bg
        phase2_requests.extend(
            _build_cell_style_requests(header_table_el, row_idx, 1, bg_color=_WHITE)
        )

    if phase2_requests:
        _batch_update(docs_service, doc_id, phase2_requests)

    # Style header table text (read doc again after insertions)
    doc = docs_service.documents().get(documentId=doc_id).execute()
    body_content = doc.get("body", {}).get("content", [])
    header_table_el = _find_table(body_content, 0)

    if header_table_el:
        text_style_requests: list[dict[str, Any]] = []
        for row_idx in range(len(_HEADER_ROWS)):
            # Label column — bold
            cs, ce = _table_cell_range(header_table_el, row_idx, 0)
            text_style_requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": cs, "endIndex": ce},
                    "textStyle": {
                        "bold": True,
                        "fontSize": {"magnitude": 10, "unit": _PT},
                        "weightedFontFamily": {"fontFamily": "Arial"},
                    },
                    "fields": "bold,fontSize,weightedFontFamily",
                }
            })
            # Value column — normal
            vs, ve = _table_cell_range(header_table_el, row_idx, 1)
            text_style_requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": vs, "endIndex": ve},
                    "textStyle": {
                        "bold": False,
                        "fontSize": {"magnitude": 10, "unit": _PT},
                        "weightedFontFamily": {"fontFamily": "Arial"},
                    },
                    "fields": "bold,fontSize,weightedFontFamily",
                }
            })

        # Apply column widths via updateTableColumnProperties
        table_start = header_table_el.get("startIndex", 1)
        text_style_requests.append({
            "updateTableColumnProperties": {
                "tableStartLocation": {"index": table_start},
                "columnIndices": [0],
                "tableColumnProperties": {
                    "widthType": "FIXED_WIDTH",
                    "width": {"magnitude": 140, "unit": _PT},
                },
                "fields": "widthType,width",
            }
        })
        text_style_requests.append({
            "updateTableColumnProperties": {
                "tableStartLocation": {"index": table_start},
                "columnIndices": [1],
                "tableColumnProperties": {
                    "widthType": "FIXED_WIDTH",
                    "width": {"magnitude": 328, "unit": _PT},
                },
                "fields": "widthType,width",
            }
        })

        if text_style_requests:
            _batch_update(docs_service, doc_id, text_style_requests)

    # ── Phase 3: Executive Summary section ───────────────────────────────
    # Read document to find end index for appending after header table
    doc = docs_service.documents().get(documentId=doc_id).execute()
    body_content = doc.get("body", {}).get("content", [])
    end_idx = _doc_end_index(body_content)

    b3 = _DocBuilder(start_index=end_idx)

    # Horizontal divider
    div_start, div_end = b3.insert_text("\n")
    b3.style_paragraph(
        div_start, div_end,
        border_bottom={
            "color": {"color": {"rgbColor": _LIGHT_BLUE_BORDER}},
            "width": {"magnitude": 1, "unit": _PT},
            "padding": {"magnitude": 6, "unit": _PT},
            "dashStyle": "SOLID",
        },
    )

    # Executive Summary heading
    b3.insert_heading("Executive Summary", level=1)

    # "Can We Open?" card
    c_answer = _resolve_value(replacements, "exec.c_answer", "[Pending]")
    c_zoning = _resolve_value(replacements, "exec.c_zoning", "[Pending]")
    c_edreg = _resolve_value(replacements, "exec.c_edreg", "[Pending]")
    c_occupancy = _resolve_value(replacements, "exec.c_occupancy", "[Pending]")
    c_permit_timeline = _resolve_value(replacements, "exec.c_permit_timeline", "[Pending]")
    c_construction_timeline = _resolve_value(replacements, "exec.c_construction_timeline", "[Pending]")

    can_we_q = "Can this school be open in time for the current school year (8/12 or 9/8)?\n"
    q_start, q_end = b3.insert_text(can_we_q)
    b3.style_text(q_start, q_end - 1, bold=True, font_size=11, font_family="Arial")

    answer_text = f"{c_answer}, because:\n"
    a_start, a_end = b3.insert_text(answer_text)
    b3.style_text(a_start, a_end - 1, bold=True, font_size=12, font_family="Arial")

    checklist_text = (
        f"Zoning: {c_zoning}\n"
        f"Education Regulatory Approval: {c_edreg}\n"
        f"Occupancy path: {c_occupancy}\n"
        f"Permit Timeline: {c_permit_timeline}\n"
        f"Construction Timeline: {c_construction_timeline}\n"
    )
    cl_start, cl_end = b3.insert_text(checklist_text)
    b3.style_text(cl_start, cl_end - 1, font_size=10, font_family="Arial")

    # Buildout Analysis heading
    b3.insert_text("\n")
    b3.insert_heading("Buildout Analysis", level=2)

    # Build Scenarios summary table
    b3.insert_table(4, 3)

    _batch_update(docs_service, doc_id, b3.requests)

    # ── Phase 4: Populate Build Scenarios table ──────────────────────────
    doc = docs_service.documents().get(documentId=doc_id).execute()
    body_content = doc.get("body", {}).get("content", [])

    # Find the scenarios table (second table in the document)
    scenarios_table = _find_table(body_content, 1)
    if scenarios_table is None:
        logger.error("Could not find build scenarios table")
        return hyperlink_trace

    scenario_data = [
        ("", "Fastest Open", "Max Capacity"),
        ("Student Capacity",
         _resolve_value(replacements, "exec.fastest_open_capacity", "[Not found]"),
         _resolve_value(replacements, "exec.max_capacity_capacity", "[Not found]")),
        ("Target Open Date",
         _resolve_value(replacements, "exec.fastest_open_open_date", "[Not found]"),
         _resolve_value(replacements, "exec.max_capacity_open_date", "[Not found]")),
        ("Estimated CAPEX",
         _resolve_value(replacements, "exec.fastest_open_capex", "[Not found]"),
         _resolve_value(replacements, "exec.max_capacity_capex", "[Not found]")),
    ]

    phase4_requests: list[dict[str, Any]] = []
    # Populate in reverse order
    for row_idx in range(3, -1, -1):
        for col_idx in range(2, -1, -1):
            text = scenario_data[row_idx][col_idx]
            if text:
                cell_idx = _cell_index(scenarios_table, row_idx, col_idx)
                phase4_requests.append({
                    "insertText": {
                        "location": {"index": cell_idx},
                        "text": text,
                    }
                })

    if phase4_requests:
        _batch_update(docs_service, doc_id, phase4_requests)

    # Style the scenarios table
    doc = docs_service.documents().get(documentId=doc_id).execute()
    body_content = doc.get("body", {}).get("content", [])
    scenarios_table = _find_table(body_content, 1)

    if scenarios_table:
        style_requests: list[dict[str, Any]] = []
        # Header row: dark blue bg, white bold text
        for col_idx in range(3):
            style_requests.extend(
                _build_cell_style_requests(scenarios_table, 0, col_idx, bg_color=_DARK_BLUE)
            )
            cs, ce = _table_cell_range(scenarios_table, 0, col_idx)
            style_requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": cs, "endIndex": ce},
                    "textStyle": {
                        "bold": True,
                        "fontSize": {"magnitude": 10, "unit": _PT},
                        "weightedFontFamily": {"fontFamily": "Arial"},
                        "foregroundColor": {"color": {"rgbColor": _WHITE}},
                    },
                    "fields": "bold,fontSize,weightedFontFamily,foregroundColor",
                }
            })
        # Data rows: bold labels in col 0
        for row_idx in range(1, 4):
            cs, ce = _table_cell_range(scenarios_table, row_idx, 0)
            style_requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": cs, "endIndex": ce},
                    "textStyle": {
                        "bold": True,
                        "fontSize": {"magnitude": 10, "unit": _PT},
                        "weightedFontFamily": {"fontFamily": "Arial"},
                    },
                    "fields": "bold,fontSize,weightedFontFamily",
                }
            })
            for col_idx in range(1, 3):
                vs, ve = _table_cell_range(scenarios_table, row_idx, col_idx)
                style_requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": vs, "endIndex": ve},
                        "textStyle": {
                            "fontSize": {"magnitude": 10, "unit": _PT},
                            "weightedFontFamily": {"fontFamily": "Arial"},
                        },
                        "fields": "fontSize,weightedFontFamily",
                    }
                })
        if style_requests:
            _batch_update(docs_service, doc_id, style_requests)

    # ── Phase 5: Cost Breakdown section ──────────────────────────────────
    doc = docs_service.documents().get(documentId=doc_id).execute()
    body_content = doc.get("body", {}).get("content", [])
    end_idx = _doc_end_index(body_content)

    b5 = _DocBuilder(start_index=end_idx)
    b5.insert_text("\n")
    b5.insert_heading("Detailed Cost Breakdown", level=2)

    num_cost_rows = len(_COST_BREAKDOWN_ROWS) + 1  # +1 for header
    b5.insert_table(num_cost_rows, 3)

    _batch_update(docs_service, doc_id, b5.requests)

    # Populate cost table
    doc = docs_service.documents().get(documentId=doc_id).execute()
    body_content = doc.get("body", {}).get("content", [])
    cost_table = _find_table(body_content, 2)

    if cost_table is None:
        logger.error("Could not find cost breakdown table")
        return hyperlink_trace

    cost_header = ("Line Item", "Fastest Open", "Max Capacity")
    cost_rows_data: list[tuple[str, str, str]] = []
    for row_key, display_label in _COST_BREAKDOWN_ROWS:
        fo_val = _resolve_value(
            replacements,
            f"exec.cost_{row_key}_fastest_open",
            "[Not found]",
        )
        mc_val = _resolve_value(
            replacements,
            f"exec.cost_{row_key}_max_capacity",
            "[Not found]",
        )
        cost_rows_data.append((display_label, fo_val, mc_val))

    phase5_requests: list[dict[str, Any]] = []
    # Populate in reverse order
    all_cost_data = [cost_header] + cost_rows_data
    for row_idx in range(len(all_cost_data) - 1, -1, -1):
        for col_idx in range(2, -1, -1):
            text = all_cost_data[row_idx][col_idx]
            if text:
                cell_idx = _cell_index(cost_table, row_idx, col_idx)
                phase5_requests.append({
                    "insertText": {
                        "location": {"index": cell_idx},
                        "text": text,
                    }
                })

    if phase5_requests:
        _batch_update(docs_service, doc_id, phase5_requests)

    # Style cost table
    doc = docs_service.documents().get(documentId=doc_id).execute()
    body_content = doc.get("body", {}).get("content", [])
    cost_table = _find_table(body_content, 2)

    if cost_table:
        style_requests = []
        # Header row
        for col_idx in range(3):
            style_requests.extend(
                _build_cell_style_requests(cost_table, 0, col_idx, bg_color=_DARK_BLUE)
            )
            cs, ce = _table_cell_range(cost_table, 0, col_idx)
            style_requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": cs, "endIndex": ce},
                    "textStyle": {
                        "bold": True,
                        "fontSize": {"magnitude": 10, "unit": _PT},
                        "weightedFontFamily": {"fontFamily": "Arial"},
                        "foregroundColor": {"color": {"rgbColor": _WHITE}},
                    },
                    "fields": "bold,fontSize,weightedFontFamily,foregroundColor",
                }
            })

        # Data rows — alternating shading, bold labels, bold Grand Total
        for data_row_idx in range(len(_COST_BREAKDOWN_ROWS)):
            table_row_idx = data_row_idx + 1
            row_key, display_label = _COST_BREAKDOWN_ROWS[data_row_idx]
            is_grand_total = row_key == "grand_total"

            # Alternating row shading (even data rows = odd table rows)
            if data_row_idx % 2 == 1:
                for col_idx in range(3):
                    style_requests.extend(
                        _build_cell_style_requests(
                            cost_table, table_row_idx, col_idx, bg_color=_LIGHT_GRAY,
                        )
                    )

            # Label column — always bold
            cs, ce = _table_cell_range(cost_table, table_row_idx, 0)
            style_requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": cs, "endIndex": ce},
                    "textStyle": {
                        "bold": True,
                        "fontSize": {"magnitude": 10, "unit": _PT},
                        "weightedFontFamily": {"fontFamily": "Arial"},
                    },
                    "fields": "bold,fontSize,weightedFontFamily",
                }
            })

            # Value columns
            for col_idx in range(1, 3):
                vs, ve = _table_cell_range(cost_table, table_row_idx, col_idx)
                style_requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": vs, "endIndex": ve},
                        "textStyle": {
                            "bold": is_grand_total,
                            "fontSize": {"magnitude": 10, "unit": _PT},
                            "weightedFontFamily": {"fontFamily": "Arial"},
                        },
                        "fields": "bold,fontSize,weightedFontFamily",
                    }
                })

        if style_requests:
            _batch_update(docs_service, doc_id, style_requests)

    # ── Phase 6: Notes and Source Documents ───────────────────────────────
    doc = docs_service.documents().get(documentId=doc_id).execute()
    body_content = doc.get("body", {}).get("content", [])
    end_idx = _doc_end_index(body_content)

    b6 = _DocBuilder(start_index=end_idx)

    b6.insert_text("\n")
    b6.insert_heading("Supporting Documents", level=1)

    # Acquisition Conditions
    acq_label_start, acq_label_end = b6.insert_text("Acquisition Conditions\n")
    b6.style_text(acq_label_start, acq_label_end - 1, bold=True, font_size=11, font_family="Arial")

    acq_val = _resolve_value(replacements, "exec.acquisition_conditions", "[No acquisition conditions provided]")
    b6.insert_paragraph(acq_val)

    b6.insert_text("\n")

    # Risks to Note
    risk_label_start, risk_label_end = b6.insert_text("Risks to Note\n")
    b6.style_text(risk_label_start, risk_label_end - 1, bold=True, font_size=11, font_family="Arial")

    risk_val = _resolve_value(replacements, "exec.risk_notes", "[No risks noted]")
    b6.insert_paragraph(risk_val)

    b6.insert_text("\n")

    # Supporting Documents heading
    b6.insert_heading("Supporting Documents", level=2)

    # Source documents table
    b6.insert_table(len(_SOURCE_DOC_ROWS) + 1, 2)  # +1 for header

    _batch_update(docs_service, doc_id, b6.requests)

    # ── Phase 7: Populate source documents table ─────────────────────────
    doc = docs_service.documents().get(documentId=doc_id).execute()
    body_content = doc.get("body", {}).get("content", [])

    source_table = _find_table(body_content, 3)
    if source_table is None:
        logger.error("Could not find source documents table")
        return hyperlink_trace

    source_table_data: list[tuple[str, str, str | None]] = [
        ("Document", "Link", None),  # header
    ]
    for label, token in _SOURCE_DOC_ROWS:
        display, url = _resolve_link_value(replacements, token)
        source_table_data.append((label, display, url))

    phase7_requests: list[dict[str, Any]] = []
    # Populate in reverse
    for row_idx in range(len(source_table_data) - 1, -1, -1):
        row = source_table_data[row_idx]
        label_text = row[0]
        value_text = row[1]

        # Value column
        if value_text:
            val_idx = _cell_index(source_table, row_idx, 1)
            phase7_requests.append({
                "insertText": {
                    "location": {"index": val_idx},
                    "text": value_text,
                }
            })
            # Apply hyperlink if URL is present
            if len(row) > 2 and row[2] is not None:
                url = row[2]
                phase7_requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": val_idx, "endIndex": val_idx + len(value_text)},
                        "textStyle": {
                            "link": {"url": url},
                            "foregroundColor": {"color": {"rgbColor": _LINK_BLUE}},
                        },
                        "fields": "link,foregroundColor",
                    }
                })
                hyperlink_trace["applied"] += 1
                # Find original token for tracing
                if row_idx > 0:
                    token_key = _SOURCE_DOC_ROWS[row_idx - 1][1]
                    hyperlink_trace["found_tokens"].append(token_key)

        # Label column
        if label_text:
            label_idx = _cell_index(source_table, row_idx, 0)
            phase7_requests.append({
                "insertText": {
                    "location": {"index": label_idx},
                    "text": label_text,
                }
            })

    if phase7_requests:
        _batch_update(docs_service, doc_id, phase7_requests)

    # Style source documents table
    doc = docs_service.documents().get(documentId=doc_id).execute()
    body_content = doc.get("body", {}).get("content", [])
    source_table = _find_table(body_content, 3)

    if source_table:
        style_requests = []
        # Header row
        for col_idx in range(2):
            style_requests.extend(
                _build_cell_style_requests(source_table, 0, col_idx, bg_color=_DARK_BLUE)
            )
            cs, ce = _table_cell_range(source_table, 0, col_idx)
            style_requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": cs, "endIndex": ce},
                    "textStyle": {
                        "bold": True,
                        "fontSize": {"magnitude": 10, "unit": _PT},
                        "weightedFontFamily": {"fontFamily": "Arial"},
                        "foregroundColor": {"color": {"rgbColor": _WHITE}},
                    },
                    "fields": "bold,fontSize,weightedFontFamily,foregroundColor",
                }
            })
        # Data rows — bold label column
        for row_idx in range(1, len(_SOURCE_DOC_ROWS) + 1):
            cs, ce = _table_cell_range(source_table, row_idx, 0)
            style_requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": cs, "endIndex": ce},
                    "textStyle": {
                        "bold": True,
                        "fontSize": {"magnitude": 10, "unit": _PT},
                        "weightedFontFamily": {"fontFamily": "Arial"},
                    },
                    "fields": "bold,fontSize,weightedFontFamily",
                }
            })

        if style_requests:
            _batch_update(docs_service, doc_id, style_requests)

    # Track link tokens that were NOT found
    all_link_tokens_in_sources = {token for _, token in _SOURCE_DOC_ROWS}
    all_link_tokens_in_sources.add("meta.drive_folder_url")
    for token in all_link_tokens_in_sources:
        if token not in hyperlink_trace["found_tokens"]:
            value = replacements.get(token, "")
            if value.startswith("http"):
                # URL was provided but not hyperlinked — shouldn't happen in builder
                pass
            elif token != "sources.trace_link":
                # Token was missing or non-URL
                hyperlink_trace["not_found_tokens"].append(token)

    logger.info(
        "Document builder complete: %d hyperlinks applied, %d link tokens not found",
        hyperlink_trace["applied"],
        len(hyperlink_trace["not_found_tokens"]),
    )

    return hyperlink_trace


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _batch_update(
    docs_service: Any,
    doc_id: str,
    requests: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Execute a batchUpdate, returning the response."""
    if not requests:
        return None
    result: dict[str, Any] = (
        docs_service.documents()
        .batchUpdate(documentId=doc_id, body={"requests": requests})
        .execute()
    )
    return result


def _find_table(
    body_content: list[dict[str, Any]],
    table_index: int,
) -> dict[str, Any] | None:
    """Find the Nth table element (0-indexed) in the document body content."""
    count = 0
    for element in body_content:
        if "table" in element:
            if count == table_index:
                return element
            count += 1
    return None


def _doc_end_index(body_content: list[dict[str, Any]]) -> int:
    """Return the end index of the last element in the document body.

    Subtracts 1 because the document always has a trailing newline that
    we need to insert *before*.
    """
    if not body_content:
        return 1
    last = body_content[-1]
    end: int = last.get("endIndex", 1)
    return max(end - 1, 1)
