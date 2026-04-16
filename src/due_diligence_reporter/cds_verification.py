"""CDS Verification Report Generator.

Transforms an AI-generated Site Investigation Report (SIR) into a CDS
Verification Report by:

1. Scanning all table rows for B/C confidence findings
2. Adding verification columns (CDS Verified Finding | CDS Source | CDS Confidence)
3. Embedding claim-id HTML comments for round-trip extraction
4. Building a Verification Task Summary grouped by authority
5. Prepending a cover sheet with instructions

The output is the full SIR with a verification overlay — CDS gets the
complete context for their phone calls, plus clear tasks for what to verify.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("cds_verification")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class VerificationItem:
    """A single B/C row that CDS needs to verify."""

    claim_id: str
    section: str
    item: str
    ai_finding: str
    confidence: str  # "B" or "C"
    authority_hint: str = ""  # e.g., "Planning Dept", "Fire Marshal"


@dataclass
class VerificationReport:
    """Result of generating a CDS verification overlay."""

    markdown: str
    bc_item_count: int
    sections_with_items: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Confidence tag patterns
# ---------------------------------------------------------------------------

# Matches confidence tags like [A], [B], [C], [D] or — [B], — [C] etc.
# Also handles tags at end of cell: "some value — [B]" or "some value [C]"
_CONFIDENCE_TAG_RE = re.compile(
    r"""
    (?:                     # Optional leading separator
        \s*[—–-]\s*         # em-dash, en-dash, or hyphen surrounded by whitespace
    )?
    \[([A-D])\]             # The confidence tag itself: [A], [B], [C], [D]
    """,
    re.VERBOSE,
)

# Matches markdown table rows: | cell | cell | ... |
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")

# Matches table separator rows: |---|---|
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")

# Matches markdown headers
_HEADER_RE = re.compile(r"^(#{1,4})\s+(.+)$")


# ---------------------------------------------------------------------------
# Authority inference from section names
# ---------------------------------------------------------------------------

_AUTHORITY_MAP = {
    "authority chain": "Multiple — see individual rows",
    "zoning": "Planning & Zoning",
    "planning": "Planning & Zoning",
    "permit": "Building Department",
    "building": "Building Department",
    "fire": "Fire Department / Fire Marshal",
    "health": "Health Department",
    "environmental": "Environmental / Geographic",
    "flood": "Environmental / Geographic",
    "infrastructure": "Public Works / Utilities",
    "sewer": "Public Works / Utilities",
    "water": "Public Works / Utilities",
    "internet": "Utilities / ISP",
    "feasibility": "Code Analysis",
    "code framework": "Building Department",
    "site": "Assessor / Property Records",
}


def _infer_authority(section_name: str) -> str:
    """Best-guess the relevant authority from the SIR section name."""
    lower = section_name.lower()
    for keyword, authority in _AUTHORITY_MAP.items():
        if keyword in lower:
            return authority
    return "General"


# ---------------------------------------------------------------------------
# Core: parse SIR text and find B/C rows
# ---------------------------------------------------------------------------


def _extract_confidence_from_cell(cell_text: str) -> str | None:
    """Extract the confidence letter (A/B/C/D) from a table cell, if present."""
    match = _CONFIDENCE_TAG_RE.search(cell_text)
    return match.group(1) if match else None


def _find_bc_items(sir_text: str) -> list[VerificationItem]:
    """Scan SIR text for table rows containing B or C confidence tags.

    Returns a list of VerificationItems with claim IDs assigned sequentially.
    """
    items: list[VerificationItem] = []
    current_section = "General"
    claim_counter = 0

    for line in sir_text.splitlines():
        # Track current section from markdown headers
        header_match = _HEADER_RE.match(line)
        if header_match:
            current_section = header_match.group(2).strip()
            continue

        # Skip separator rows
        if _TABLE_SEP_RE.match(line):
            continue

        # Check table rows for B/C confidence
        row_match = _TABLE_ROW_RE.match(line)
        if not row_match:
            continue

        cells = [c.strip() for c in row_match.group(1).split("|")]
        if len(cells) < 2:
            continue

        # Look for B or C confidence in any cell
        for cell in cells:
            conf = _extract_confidence_from_cell(cell)
            if conf in ("B", "C"):
                claim_counter += 1
                claim_id = f"R-{claim_counter:03d}"

                # First cell is usually the item/field name
                item_name = cells[0].strip()
                # The cell with the confidence tag has the finding
                ai_finding = cell.strip()

                items.append(
                    VerificationItem(
                        claim_id=claim_id,
                        section=current_section,
                        item=item_name,
                        ai_finding=ai_finding,
                        confidence=conf,
                        authority_hint=_infer_authority(current_section),
                    )
                )
                break  # One match per row is enough

    return items


# ---------------------------------------------------------------------------
# Build the verification overlay
# ---------------------------------------------------------------------------


def _build_task_summary_table(items: list[VerificationItem]) -> str:
    """Build the Verification Task Summary table grouped by authority."""
    if not items:
        return "*No B/C confidence items found — nothing for CDS to verify.*\n"

    lines = [
        "## Verification Task Summary",
        "",
        f"**{len(items)} items** require phone/email verification by CDS.",
        "",
        "| # | Claim ID | Section | Item | AI Finding | Confidence | Authority |",
        "|---|---|---|---|---|---|---|",
    ]

    for i, item in enumerate(items, 1):
        # Truncate long findings for the summary table
        finding = item.ai_finding
        if len(finding) > 80:
            finding = finding[:77] + "..."
        lines.append(
            f"| {i} | {item.claim_id} | {item.section} | {item.item} "
            f"| {finding} | {item.confidence} | {item.authority_hint} |"
        )

    lines.append("")
    return "\n".join(lines)


def _build_cover_sheet(site_name: str, bc_count: int) -> str:
    """Build the cover sheet prepended to the verification report."""
    return f"""---

# CDS Verification Report

**Site:** {site_name}
**Items requiring verification:** {bc_count}

---

**How to use this report**

This is the complete AI Site Investigation Report for this address with a
verification overlay. Every table row marked with **[B]** (high-confidence
inferred) or **[C]** (inferred/estimated) has three extra columns added:

- **CDS Verified Finding** — Write your verified answer here
- **CDS Source** — How you verified it (e.g., "staff call 4/16", "county website", "fee schedule PDF")
- **CDS Confidence** — Your confidence: A (verified from authority) or B (reliable secondary source)

**Rows marked [A]** are from authoritative sources and do not need
re-verification unless something looks wrong.

**Rows marked [D]** are field tasks for Worksmith — not CDS scope.

**Do not leave CDS Source blank** on any row you actively verified — that
field is how we distinguish "confirmed" from "carried forward."

---

"""


def _add_verification_columns_to_table(sir_text: str, items: list[VerificationItem]) -> str:
    """Rewrite the SIR text, adding verification columns to B/C rows.

    For each table that contains B/C rows:
    - Add three column headers: CDS Verified Finding | CDS Source | CDS Confidence
    - Add empty cells to the separator row
    - For B/C rows: add empty verification cells + claim-id comment
    - For A/D rows and non-matching rows: add empty cells to maintain table structure
    """
    if not items:
        return sir_text

    # Build a set of (section, item) pairs for fast lookup
    item_lookup: dict[tuple[str, str], VerificationItem] = {}
    for item in items:
        item_lookup[(item.section, item.item)] = item

    lines = sir_text.splitlines()
    result_lines: list[str] = []
    current_section = "General"
    in_bc_table = False
    table_has_bc = False
    pending_table_lines: list[str] = []

    def _flush_table() -> None:
        """Flush accumulated table lines, adding verification columns if needed."""
        nonlocal pending_table_lines, table_has_bc, in_bc_table
        if not pending_table_lines:
            return

        if table_has_bc:
            for tl in pending_table_lines:
                if _TABLE_SEP_RE.match(tl):
                    # Add separator columns
                    result_lines.append(tl.rstrip() + " --- | --- | --- |")
                elif _TABLE_ROW_RE.match(tl):
                    cells = [c.strip() for c in tl.strip().strip("|").split("|")]
                    item_name = cells[0].strip() if cells else ""
                    vi = item_lookup.get((current_section, item_name))

                    if vi:
                        # B/C row: add empty verification cells + claim-id
                        result_lines.append(
                            tl.rstrip() + "  |  |  |"
                        )
                        result_lines.append(f"<!-- claim-id: {vi.claim_id} -->")
                    elif cells and cells[0].strip() and not any(
                        c.strip().startswith("---") for c in cells
                    ):
                        # Check if this is the header row (first data row of table)
                        # Header rows typically contain words like "Field", "Item", "Finding"
                        first_cell_lower = cells[0].strip().lower()
                        if any(
                            kw in first_cell_lower
                            for kw in ("field", "item", "finding", "requirement", "parameter", "category")
                        ):
                            # This is a header row — add column headers
                            result_lines.append(
                                tl.rstrip() + " CDS Verified Finding | CDS Source | CDS Confidence |"
                            )
                        else:
                            # Non-BC data row — keep columns aligned
                            result_lines.append(tl.rstrip() + "  |  |  |")
                    else:
                        result_lines.append(tl)
                else:
                    result_lines.append(tl)
        else:
            result_lines.extend(pending_table_lines)

        pending_table_lines = []
        table_has_bc = False
        in_bc_table = False

    for line in lines:
        # Track section headers
        header_match = _HEADER_RE.match(line)
        if header_match:
            _flush_table()
            current_section = header_match.group(2).strip()
            result_lines.append(line)
            continue

        # Detect table rows
        is_table_row = bool(_TABLE_ROW_RE.match(line)) or bool(_TABLE_SEP_RE.match(line))

        if is_table_row:
            in_bc_table = True
            pending_table_lines.append(line)

            # Check if this row has a B/C item
            row_match = _TABLE_ROW_RE.match(line)
            if row_match and not _TABLE_SEP_RE.match(line):
                cells = [c.strip() for c in row_match.group(1).split("|")]
                item_name = cells[0].strip() if cells else ""
                if (current_section, item_name) in item_lookup:
                    table_has_bc = True
        else:
            if in_bc_table:
                _flush_table()
            result_lines.append(line)

    # Flush any remaining table
    _flush_table()

    return "\n".join(result_lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_cds_verification_report(
    sir_text: str,
    site_name: str = "Unknown Site",
) -> VerificationReport:
    """Generate a CDS Verification Report from an AI SIR.

    Args:
        sir_text: The full text of the AI-generated SIR (extracted from PDF or markdown).
        site_name: Human-readable site name for the cover sheet.

    Returns:
        VerificationReport with the full overlay markdown and metadata.
    """
    logger.info("Generating CDS verification report for '%s'", site_name)

    # Step 1: Find all B/C confidence items
    bc_items = _find_bc_items(sir_text)
    logger.info("Found %d B/C confidence items", len(bc_items))

    if not bc_items:
        logger.warning("No B/C items found in SIR for '%s' — generating report with notice", site_name)

    # Step 2: Build the cover sheet
    cover = _build_cover_sheet(site_name, len(bc_items))

    # Step 3: Build the task summary table
    task_summary = _build_task_summary_table(bc_items)

    # Step 4: Add verification columns to the SIR body
    annotated_sir = _add_verification_columns_to_table(sir_text, bc_items)

    # Step 5: Assemble the full report
    sections_with_items = sorted({item.section for item in bc_items})

    full_report = cover + task_summary + "\n\n---\n\n" + annotated_sir

    logger.info(
        "CDS verification report generated: %d B/C items across %d sections, %d chars",
        len(bc_items),
        len(sections_with_items),
        len(full_report),
    )

    return VerificationReport(
        markdown=full_report,
        bc_item_count=len(bc_items),
        sections_with_items=sections_with_items,
    )
