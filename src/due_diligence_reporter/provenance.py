"""Vendor-vs-AI provenance detection for source documents.

The DD pipeline only treats a site as ``ready`` when it has *vendor-sourced*
SIR + Building Inspection. Files our own workflow generated (and dropped in
the same M1 folder) must not flip those readiness gates.

Detection strategy — two tiers, cheapest first:

* **Tier 1 — filename heuristic.** Our pipeline writes AI artifacts with a
  deterministic name: ``<address-slug>_<YYYY-MM-DD>_<artifact-type>.docx``.
  Vendor files come from email attachments and humans, so their names are
  free-form (``Alpha School - Santa Barbara CA (27 E Cota St) - SIR UPDATE
  6.18.25.pdf``). A filename matching the AI pattern is conclusively AI.
  A filename that does *not* match is *probably* vendor — confirm with Tier 2
  on first read.

* **Tier 2 — content LLM check.** Pull the first ~3 KB of text and ask
  GPT-4o-mini to label the file as ``vendor`` or ``ai_generated``. Vendors
  have firm letterhead, signed sections, professional report covers; AI
  outputs reproduce our pipeline's predictable token-driven structure.

Results are cached per file (``file_id`` + ``modifiedTime`` → verdict) in a
``provenance.json`` file inside the site's M1 folder, so repeat runs are
free. The cache is invalidated automatically when ``modifiedTime`` changes.

Public API:

    is_vendor_sourced(file_info, gc, *, m1_folder_id=None) -> bool
    classify_provenance(file_info, gc, *, m1_folder_id=None) -> ProvenanceVerdict

Both swallow upstream errors and default to ``vendor`` on uncertainty so we
never silently hide a real vendor doc; the gate's failure mode is to miss
an AI doc, not to drop a vendor one.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# AI-generated artifact filename pattern from our pipeline. Matches:
#   6940-s-utica-ave-tulsa-ok_2026-04-29_SIR.docx
#   alpha-school-santa-clara-2340_2026-04-15_cds-packet.docx
#   foo-bar_2026-04-29_school-approval.docx
# The slug is at least one ``[a-z0-9-]`` token, the date is ISO ``YYYY-MM-DD``,
# the suffix is a known AI artifact label (case-insensitive). We deliberately
# anchor with ``$`` on the extension so vendor files containing similar
# substrings don't get mis-flagged.
_AI_FILENAME_RE = re.compile(
    r"^[a-z0-9][a-z0-9-]*_\d{4}-\d{2}-\d{2}_(?:"
    r"sir|cds-packet|school-approval|e-occupancy|opening-plan|"
    r"capacity-brainlift|raycon-scenario|dd-report"
    r")\.(?:docx|pdf)$",
    re.IGNORECASE,
)

# Files whose existence in M1 is *only* ever AI-produced. These never need
# to be checked — they're AI by definition.
_ALWAYS_AI_DOC_TYPES = frozenset({
    "dd_report",
    "e_occupancy_report",
    "school_approval_report",
    "opening_plan_report",
    "capacity_brainlift_report",
    "raycon_scenario_report",
    "report_trace",
})

# Source doc types where vendor-vs-AI distinction matters for the gate.
_GATEABLE_DOC_TYPES = frozenset({"sir", "building_inspection", "isp"})

_PROVENANCE_CACHE_FILENAME = "provenance.json"
_DEFAULT_CONTENT_PROBE_BYTES = 3000

_VENDOR = "vendor"
_AI = "ai_generated"
_UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProvenanceVerdict:
    """Structured outcome of a provenance check."""

    label: str  # "vendor" | "ai_generated" | "unknown"
    confidence: float
    tier: str  # "filename" | "content" | "cached" | "trivial" | "error"
    reason: str = ""
    # Set True only when classify_provenance itself raised and we fell
    # through to the safe default. Lets callers distinguish "classifier
    # said this isn't vendor" from "classifier crashed and we don't
    # actually know" — the latter must NOT open the vendor gate.
    provenance_classification_failed: bool = False

    @property
    def is_vendor(self) -> bool:
        # On classifier error we deliberately return False (not vendor) so
        # AI-generated SIRs cannot slip past the vendor gate when the
        # classifier itself crashes. This is the Tulsa-class failure mode
        # that the recommendations doc (Rec. 6) calls out.
        if self.provenance_classification_failed:
            return False
        # Default to vendor on UNKNOWN — better to surface a doc the gate
        # then rejects on completeness than to silently hide a real vendor doc.
        return self.label != _AI


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1 — filename heuristic
# ─────────────────────────────────────────────────────────────────────────────


def looks_ai_generated_by_filename(filename: str) -> bool:
    """Return True iff the filename matches our deterministic AI pattern."""
    if not filename:
        return False
    base = filename.rsplit("/", 1)[-1].strip()
    return bool(_AI_FILENAME_RE.match(base))


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2 — LLM content classification
# ─────────────────────────────────────────────────────────────────────────────

_CONTENT_PROMPT = """\
You classify Alpha School DD source documents as one of:
- vendor: produced by a human / outside firm. Signs: signed report covers,
  professional letterhead, firm names, dated signatures, photographs of
  the property, narrative tone.
- ai_generated: produced by Alpha's automated DD pipeline. Signs:
  predictable token-driven structure, headings like "Executive Summary",
  numbered sections matching a template, references to "AI-generated SIR"
  or pipeline artifact labels, no signatures or letterhead.

You are given a filename and the first ~3000 chars of extracted text.
Return ONLY JSON: {"label": "vendor" | "ai_generated", "confidence": 0.0-1.0,
"reason": "<one short sentence>"}.
Default to "vendor" when uncertain — false negatives are safer than false positives.
"""


def _classify_by_content(filename: str, text: str) -> ProvenanceVerdict:
    """Ask GPT-4o-mini to classify text as vendor vs AI."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return ProvenanceVerdict(_UNKNOWN, 0.0, "content", "OPENAI_API_KEY not set")

    try:
        from openai import OpenAI

        from .config import get_settings

        settings = get_settings()
        client = OpenAI(api_key=api_key, max_retries=2)
        snippet = (text or "")[:_DEFAULT_CONTENT_PROBE_BYTES]
        if not snippet.strip():
            return ProvenanceVerdict(_UNKNOWN, 0.0, "content", "empty content")

        response = client.chat.completions.create(
            model=settings.openai_filename_model,  # gpt-4o-mini sized model
            messages=[
                {"role": "system", "content": _CONTENT_PROMPT},
                {"role": "user", "content": f"Filename: {filename}\n\nText:\n{snippet}"},
            ],
            response_format={"type": "json_object"},
        )
        body = response.choices[0].message.content or "{}"
        data = json.loads(body)
        label = (data.get("label") or "").strip().lower()
        if label not in (_VENDOR, _AI):
            label = _UNKNOWN
        confidence = float(data.get("confidence", 0.0))
        reason = str(data.get("reason", ""))[:200]
        return ProvenanceVerdict(label, confidence, "content", reason)
    except Exception as e:  # pragma: no cover — network/credentials failure
        logger.warning("Content provenance check failed for %s: %s", filename, e)
        return ProvenanceVerdict(_UNKNOWN, 0.0, "content", f"error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Per-site cache
# ─────────────────────────────────────────────────────────────────────────────


def _load_cache(gc: Any, m1_folder_id: str | None) -> dict[str, dict[str, Any]]:
    """Return cache keyed by file_id → {modifiedTime, label, confidence, tier, reason}."""
    if not gc or not m1_folder_id:
        return {}
    try:
        for f in gc.list_files_in_folder(m1_folder_id):
            if str(f.get("name", "")).strip() == _PROVENANCE_CACHE_FILENAME:
                blob = gc.download_file_bytes(f.get("id"))
                return json.loads(blob.decode("utf-8"))
    except Exception as e:
        logger.debug("provenance cache load failed: %s", e)
    return {}


def _save_cache(
    gc: Any, m1_folder_id: str | None, cache: dict[str, dict[str, Any]]
) -> None:
    if not gc or not m1_folder_id:
        return
    try:
        body = json.dumps(cache, indent=2, sort_keys=True).encode("utf-8")
        # Replace existing file or upload new.
        existing_id = None
        for f in gc.list_files_in_folder(m1_folder_id):
            if str(f.get("name", "")).strip() == _PROVENANCE_CACHE_FILENAME:
                existing_id = f.get("id")
                break
        if existing_id and hasattr(gc, "update_file_content"):
            gc.update_file_content(existing_id, body, mime_type="application/json")
        else:
            gc.upload_file_to_folder(
                folder_id=m1_folder_id,
                file_name=_PROVENANCE_CACHE_FILENAME,
                file_bytes=body,
                mime_type="application/json",
            )
    except Exception as e:
        logger.debug("provenance cache save failed: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def classify_provenance(
    file_info: dict[str, Any],
    gc: Any | None = None,
    *,
    m1_folder_id: str | None = None,
    doc_type: str | None = None,
    read_only: bool = False,
) -> ProvenanceVerdict:
    """Classify a Drive file as vendor- or AI-sourced.

    Args:
        file_info: ``{"id": ..., "name": ..., "modifiedTime": ...}``-shaped
            dict from ``GoogleClient.list_files_*``.
        gc: optional GoogleClient. Required for Tier 2 content fetch and
            cache I/O. When None, only Tier 1 (filename) runs.
        m1_folder_id: Drive folder ID where the per-site provenance cache
            lives. When None, results are not cached.
        doc_type: optional pre-computed classifier result. When passed and
            the doc_type is exclusively AI-produced (e.g. ``dd_report``),
            short-circuit before any I/O.
        read_only: when True, never write the provenance cache back to
            Drive even on a Tier 2 miss. Used by read-only callers (e.g.
            the diagnose tool). The cache is still *read* — only the
            ``_save_cache`` side effect is suppressed.

    Returns a ``ProvenanceVerdict``. On any internal exception, returns a
    verdict with ``provenance_classification_failed=True`` and
    ``is_vendor=False`` so the vendor gate fails closed instead of letting
    AI-generated SIRs through (Rec. 6).
    """
    try:
        return _classify_provenance_inner(
            file_info,
            gc,
            m1_folder_id=m1_folder_id,
            doc_type=doc_type,
            read_only=read_only,
        )
    except Exception as e:
        name = ""
        try:
            if isinstance(file_info, dict):
                name = str(file_info.get("name", ""))
        except Exception:
            name = ""
        logger.error(
            "Provenance classification failed for %s (%s): %s",
            name or "<unknown>",
            type(e).__name__,
            e,
        )
        return ProvenanceVerdict(
            label=_UNKNOWN,
            confidence=0.0,
            tier="error",
            reason=f"classifier raised {type(e).__name__}: {e}"[:200],
            provenance_classification_failed=True,
        )


def _classify_provenance_inner(
    file_info: dict[str, Any],
    gc: Any | None = None,
    *,
    m1_folder_id: str | None = None,
    doc_type: str | None = None,
    read_only: bool = False,
) -> ProvenanceVerdict:
    """Real classify_provenance body. Wrapped by ``classify_provenance``."""
    if not isinstance(file_info, dict):
        return ProvenanceVerdict(_UNKNOWN, 0.0, "trivial", "no file_info")

    name = str(file_info.get("name", "")).strip()
    file_id = str(file_info.get("id", "")).strip()
    modified = str(file_info.get("modifiedTime", "")).strip()

    # Trivial — types only ever produced by AI never need a vendor check.
    if doc_type in _ALWAYS_AI_DOC_TYPES:
        return ProvenanceVerdict(_AI, 1.0, "trivial", f"doc_type={doc_type} is AI-only")

    # Tier 1 — filename heuristic. Hits Tulsa-style files immediately.
    if looks_ai_generated_by_filename(name):
        return ProvenanceVerdict(
            _AI, 0.95, "filename", "matches AI artifact naming pattern"
        )

    # Cache hit?
    cache = _load_cache(gc, m1_folder_id) if (gc and m1_folder_id) else {}
    cached = cache.get(file_id) if file_id else None
    if cached and cached.get("modifiedTime") == modified:
        label = str(cached.get("label", _UNKNOWN))
        return ProvenanceVerdict(
            label,
            float(cached.get("confidence", 0.0)),
            "cached",
            str(cached.get("reason", "")),
        )

    # Tier 2 — content LLM. Read first page only.
    text = ""
    if gc and file_id:
        try:
            from .utils import extract_text_from_pdf_bytes

            blob = gc.download_file_bytes(file_id)
            if name.lower().endswith(".pdf"):
                text = extract_text_from_pdf_bytes(blob)
            else:
                # docx/text best-effort decode. Real docx parsing is in the
                # report agent's read_drive_document tool; for provenance the
                # raw bytes' UTF-8 head usually contains enough metadata
                # (creator, application) to discriminate.
                try:
                    text = blob.decode("utf-8", errors="ignore")
                except Exception:
                    text = ""
        except Exception as e:
            logger.debug("provenance content fetch failed for %s: %s", name, e)

    verdict = _classify_by_content(name, text) if text else ProvenanceVerdict(
        _UNKNOWN, 0.0, "content", "no text available"
    )

    # Default-to-vendor policy: if Tier 2 is unsure and Tier 1 didn't flag
    # this as AI, treat as vendor. The gate only opens on explicit AI hits.
    if verdict.label == _UNKNOWN:
        verdict = ProvenanceVerdict(_VENDOR, 0.5, verdict.tier, "default-to-vendor")

    # Persist. Skipped for read-only callers (e.g. the diagnose tool).
    if gc and m1_folder_id and file_id and not read_only:
        cache[file_id] = {
            "modifiedTime": modified,
            "label": verdict.label,
            "confidence": verdict.confidence,
            "tier": verdict.tier,
            "reason": verdict.reason,
            "name": name,
        }
        _save_cache(gc, m1_folder_id, cache)

    return verdict


def is_vendor_sourced(
    file_info: dict[str, Any],
    gc: Any | None = None,
    *,
    m1_folder_id: str | None = None,
    doc_type: str | None = None,
    read_only: bool = False,
) -> bool:
    """Return True iff the file is vendor-sourced (or unknown — see policy).

    ``read_only`` propagates through to :func:`classify_provenance` so
    callers in observability/diagnostic contexts can probe provenance
    without mutating the on-disk cache.
    """
    return classify_provenance(
        file_info,
        gc,
        m1_folder_id=m1_folder_id,
        doc_type=doc_type,
        read_only=read_only,
    ).is_vendor


def is_gateable_doc_type(doc_type: str | None) -> bool:
    """True iff a doc_type is in the vendor-gating allow-list (sir/bi/isp)."""
    return (doc_type or "") in _GATEABLE_DOC_TYPES
