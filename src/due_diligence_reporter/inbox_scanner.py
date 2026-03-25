"""Inbox scanner for auto-filing DD documents from email to Google Drive.

Scans Gmail for emails sent to edu.ops@trilogy.com with PDF attachments,
classifies them as SIR or Building Inspection using GPT-5.2, matches to a
Wrike site record, and uploads to the correct shared Drive folder.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from openai import OpenAI

from .config import Settings
from .google_client import GoogleClient

logger = logging.getLogger("[inbox_scanner]")

# Confidence threshold — auto-file at or above this, skip below
AUTO_FILE_CONFIDENCE = 0.7

# Doc types we handle (others are skipped)
SUPPORTED_DOC_TYPES = {"sir", "building_inspection", "isp"}

# Map doc_type to the Settings field name for the target folder ID
DOC_TYPE_FOLDER_MAP = {
    "sir": "sir_folder_id",
    "building_inspection": "building_inspection_folder_id",
    "isp": "isp_folder_id",
}

# Filename templates per doc_type — must match existing _classify_document_type() patterns
DOC_TYPE_FILENAME_TEMPLATES = {
    "sir": "{date} - {site_title} SIR.pdf",
    "building_inspection": "{date} - {site_title} Building Inspection Report.pdf",
    "isp": "{date} - {site_title} ISP.pdf",
}


@dataclass
class EmailMetadata:
    """Extracted metadata from a Gmail message."""

    message_id: str
    subject: str
    sender: str
    body_snippet: str
    attachments: list[dict[str, Any]]  # [{filename, attachment_id, mime_type}]


@dataclass
class ClassificationResult:
    """Result of LLM classification + site matching."""

    doc_type: str  # "sir", "building_inspection", or "unknown"
    matched_site_id: str | None
    matched_site_title: str | None
    confidence: float
    reasoning: str


@dataclass
class ProcessedAttachment:
    """Record of a successfully processed attachment."""

    filename: str
    doc_type: str
    site_title: str
    drive_file_id: str
    drive_file_name: str


def scan_inbox(
    gc: GoogleClient,
    site_records: list[dict[str, Any]],
    settings: Settings,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Top-level orchestrator: scan Gmail, classify, upload, mark processed.

    Returns a summary dict with counts and details.
    """
    logger.info("Starting inbox scan (dry_run=%s)", dry_run)

    # Get or create the DD-Processed label
    label_id = gc.gmail_get_or_create_label(settings.inbox_processed_label)

    # Exclude already-labeled messages from search
    query = f"{settings.inbox_scan_query} -label:{settings.inbox_processed_label}"
    messages = gc.gmail_search(query, max_results=settings.inbox_scan_max_results)
    logger.info("Found %d unprocessed emails", len(messages))

    results: dict[str, Any] = {
        "emails_found": len(messages),
        "attachments_uploaded": 0,
        "attachments_skipped": 0,
        "emails_processed": 0,
        "errors": [],
        "uploads": [],
        "low_confidence": [],
    }

    for msg_stub in messages:
        message_id = msg_stub["id"]
        try:
            email_result = process_email(
                gc, message_id, site_records, settings, label_id, dry_run=dry_run,
            )
            if email_result.get("uploaded"):
                results["attachments_uploaded"] += len(email_result["uploaded"])
                results["uploads"].extend(email_result["uploaded"])
            if email_result.get("skipped"):
                results["attachments_skipped"] += email_result["skipped"]
            if email_result.get("low_confidence"):
                results["low_confidence"].extend(email_result["low_confidence"])
            if email_result.get("marked"):
                results["emails_processed"] += 1
        except Exception as e:
            logger.error("Failed to process email %s: %s", message_id, e)
            results["errors"].append({"message_id": message_id, "error": str(e)})

    logger.info(
        "Inbox scan complete: %d uploaded, %d skipped, %d errors",
        results["attachments_uploaded"],
        results["attachments_skipped"],
        len(results["errors"]),
    )
    return results


def process_email(
    gc: GoogleClient,
    message_id: str,
    site_records: list[dict[str, Any]],
    settings: Settings,
    label_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Process a single email: classify attachments, upload, mark done.

    Returns a dict with keys: uploaded, skipped, low_confidence, marked.
    """
    metadata = _extract_email_metadata(gc, message_id)
    logger.info(
        "Processing email: '%s' from %s (%d attachments)",
        metadata.subject,
        metadata.sender,
        len(metadata.attachments),
    )

    uploaded: list[dict[str, Any]] = []
    skipped = 0
    low_confidence: list[dict[str, Any]] = []
    all_succeeded = True

    for att in metadata.attachments:
        filename = att["filename"]
        attachment_id = att["attachment_id"]

        # Classify and match
        classification = _classify_and_match_site(
            subject=metadata.subject,
            body_snippet=metadata.body_snippet,
            filename=filename,
            site_records=site_records,
        )

        logger.info(
            "Classification for '%s': doc_type=%s, site=%s, confidence=%.2f — %s",
            filename,
            classification.doc_type,
            classification.matched_site_title,
            classification.confidence,
            classification.reasoning,
        )

        # Skip unknown doc types
        if classification.doc_type not in SUPPORTED_DOC_TYPES:
            logger.info("Skipping '%s' — unsupported doc_type: %s", filename, classification.doc_type)
            skipped += 1
            continue

        # Check confidence threshold
        if classification.confidence < AUTO_FILE_CONFIDENCE:
            logger.warning(
                "Low confidence (%.2f) for '%s' — skipping for manual review",
                classification.confidence,
                filename,
            )
            low_confidence.append({
                "filename": filename,
                "doc_type": classification.doc_type,
                "matched_site": classification.matched_site_title,
                "confidence": classification.confidence,
                "reasoning": classification.reasoning,
                "email_subject": metadata.subject,
            })
            skipped += 1
            continue

        # Determine target folder — doc_type alone is sufficient to route the file
        folder_attr = DOC_TYPE_FOLDER_MAP.get(classification.doc_type)
        if not folder_attr:
            skipped += 1
            continue
        target_folder_id = getattr(settings, folder_attr, "")
        if not target_folder_id:
            logger.error("No folder ID configured for %s", classification.doc_type)
            all_succeeded = False
            continue

        # Generate filename — use site title when matched, original filename otherwise
        if classification.matched_site_title:
            drive_filename = _generate_drive_filename(
                classification.matched_site_title, classification.doc_type,
            )
        else:
            logger.info("No site match for '%s' — filing with original filename", filename)
            date_str = datetime.now().strftime("%b %d %Y")
            drive_filename = f"{date_str} - {filename}"

        if dry_run:
            logger.info("[DRY RUN] Would upload '%s' to folder %s", drive_filename, target_folder_id)
            uploaded.append({
                "original_filename": filename,
                "drive_filename": drive_filename,
                "doc_type": classification.doc_type,
                "site_title": classification.matched_site_title,
                "matched_site_id": classification.matched_site_id,
                "dry_run": True,
            })
            continue

        # Check for duplicates
        if gc.file_exists_in_folder(target_folder_id, drive_filename):
            logger.info("File '%s' already exists in folder — skipping upload", drive_filename)
            skipped += 1
            continue

        # Download attachment and upload to Drive
        try:
            file_bytes = gc.gmail_get_attachment(message_id, attachment_id)
            drive_file = gc.upload_file_to_folder(
                folder_id=target_folder_id,
                file_name=drive_filename,
                file_bytes=file_bytes,
            )
            uploaded.append({
                "original_filename": filename,
                "drive_filename": drive_filename,
                "doc_type": classification.doc_type,
                "site_title": classification.matched_site_title,
                "matched_site_id": classification.matched_site_id,
                "drive_file_id": drive_file.get("id"),
                "drive_link": drive_file.get("webViewLink"),
            })
            logger.info("Uploaded '%s' -> '%s'", filename, drive_filename)
        except Exception as e:
            logger.error("Upload failed for '%s': %s", filename, e)
            all_succeeded = False

    # Mark email as processed only if at least one attachment was uploaded
    # and none required manual review. Emails with low-confidence attachments
    # stay in the queue so they can be retried or reviewed.
    marked = False
    if all_succeeded and not dry_run and uploaded and not low_confidence:
        _mark_email_processed(gc, message_id, label_id)
        marked = True

    return {
        "uploaded": uploaded,
        "skipped": skipped,
        "low_confidence": low_confidence,
        "marked": marked,
    }


def _extract_email_metadata(gc: GoogleClient, message_id: str) -> EmailMetadata:
    """Fetch and parse email headers, snippet, and attachment info."""
    message = gc.gmail_get_message(message_id)

    headers = message.get("payload", {}).get("headers", [])
    header_map: dict[str, str] = {}
    for h in headers:
        name = h.get("name", "").lower()
        if name in ("subject", "from", "to"):
            header_map[name] = h.get("value", "")

    subject = header_map.get("subject", "")
    sender = header_map.get("from", "")
    snippet = message.get("snippet", "")

    # Walk MIME parts to find PDF attachments
    attachments: list[dict[str, Any]] = []
    _walk_parts(message.get("payload", {}), attachments)

    return EmailMetadata(
        message_id=message_id,
        subject=subject,
        sender=sender,
        body_snippet=snippet,
        attachments=attachments,
    )


def _walk_parts(part: dict[str, Any], attachments: list[dict[str, Any]]) -> None:
    """Recursively walk MIME parts to extract PDF attachment metadata."""
    filename = part.get("filename", "")
    mime_type = part.get("mimeType", "")
    body = part.get("body", {})
    attachment_id = body.get("attachmentId")

    if filename and attachment_id and mime_type == "application/pdf":
        attachments.append({
            "filename": filename,
            "attachment_id": attachment_id,
            "mime_type": mime_type,
        })

    for sub_part in part.get("parts", []):
        _walk_parts(sub_part, attachments)


def _classify_and_match_site(
    *,
    subject: str,
    body_snippet: str,
    filename: str,
    site_records: list[dict[str, Any]],
) -> ClassificationResult:
    """Use GPT-4o-mini to classify the attachment and match to a site record.

    Falls back to filename keyword classification if the OpenAI API is unavailable.
    """
    # Build candidate site list
    candidates: list[dict[str, str]] = []
    for record in site_records:
        title = record.get("title", "")
        record_id = record.get("id", "")
        if title and record_id:
            candidates.append({"id": record_id, "title": title})

    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        logger.warning("OPENAI_API_KEY not set — falling back to filename classification")
        return _fallback_classify(filename, candidates)

    client = OpenAI(api_key=openai_api_key)

    system_prompt = (
        "You classify email attachments for an Alpha School due diligence workflow.\n\n"
        "Given an email subject, body snippet, PDF filename, and a list of Alpha School site records, "
        "determine:\n"
        "1. **doc_type**: Is this PDF a 'sir' (Site Inspection Report), "
        "'building_inspection' (Building Inspection Report), "
        "'isp' (Instant School Plan / Program Fit Analysis), or 'unknown'?\n"
        "2. **matched_site_id**: Which site record does this document belong to?\n"
        "3. **confidence**: 0.0 to 1.0 — how confident are you in the classification and match?\n\n"
        "Clues:\n"
        "- SIR documents often mention 'site inspection', 'SIR', or 'site report' in the subject/filename.\n"
        "- Building Inspection reports are titled '[Brand] [City] Building Inspection Report' "
        "(e.g. 'Alpha Keller Building Inspection Report'). They may also mention 'inspection' or 'property condition'.\n"
        "- ISP documents mention 'Instant School Plan', 'ISP', 'Program Fit', or 'room assignment' "
        "in the subject/filename. They are program fit analysis PDFs generated by the Instant School Plan tool.\n"
        "- Site names follow the pattern 'Alpha {CityName}' (e.g. 'Alpha Keller', 'Alpha Boca Raton').\n"
        "- Look for city names, addresses, or site references in the subject, body, and filename.\n\n"
        "Return ONLY a JSON object:\n"
        "{\n"
        '  "doc_type": "sir" | "building_inspection" | "isp" | "unknown",\n'
        '  "matched_site_id": "the ID of the best matching site" | null,\n'
        '  "matched_site_title": "the title of the matched site" | null,\n'
        '  "confidence": 0.0-1.0,\n'
        '  "reasoning": "brief explanation"\n'
        "}"
    )

    user_prompt = (
        f"Email subject: {subject}\n"
        f"Body snippet: {body_snippet}\n"
        f"Attachment filename: {filename}\n\n"
        f"Site records:\n{json.dumps(candidates, indent=2)}\n\n"
        "Classify this attachment and match it to a site."
    )

    logger.info("Calling OpenAI to classify '%s'", filename)

    try:
        response = client.chat.completions.create(
            model="gpt-5.2",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )

        result_text = response.choices[0].message.content
        if not result_text:
            logger.error("Empty response from OpenAI")
            return _fallback_classify(filename, candidates)

        result: dict[str, Any] = json.loads(result_text)

        return ClassificationResult(
            doc_type=result.get("doc_type", "unknown"),
            matched_site_id=result.get("matched_site_id"),
            matched_site_title=result.get("matched_site_title"),
            confidence=float(result.get("confidence", 0.0)),
            reasoning=result.get("reasoning", ""),
        )

    except Exception as e:
        logger.error("OpenAI classification failed: %s — falling back to filename", e)
        return _fallback_classify(filename, candidates)


def _fallback_classify(
    filename: str, candidates: list[dict[str, str]],
) -> ClassificationResult:
    """Classify by filename keywords when LLM is unavailable.

    Uses the same keyword patterns as server._classify_document_type().
    Does not attempt site matching.
    """
    import re

    name = filename.lower()

    doc_type = "unknown"
    # Match SIR with word boundaries OR surrounded by underscores/hyphens/spaces
    if re.search(r"(?<![a-z])sir(?![a-z])", name):
        doc_type = "sir"
    elif "inspection" in name:
        doc_type = "building_inspection"
    elif re.search(r"(?<![a-z])isp(?![a-z])", name) or "program fit" in name:
        doc_type = "isp"

    return ClassificationResult(
        doc_type=doc_type,
        matched_site_id=None,
        matched_site_title=None,
        confidence=0.3 if doc_type != "unknown" else 0.0,
        reasoning="Filename keyword fallback (no LLM available)",
    )


def _generate_drive_filename(site_title: str, doc_type: str) -> str:
    """Generate a Drive filename that matches existing detection patterns.

    Examples:
        Mar 03 2026 - Alpha Keller SIR.pdf
        Mar 03 2026 - Alpha Boca Raton Building Inspection Report.pdf
    """
    template = DOC_TYPE_FILENAME_TEMPLATES.get(doc_type)
    if not template:
        return f"{site_title} - {doc_type}.pdf"

    date_str = datetime.now().strftime("%b %d %Y")
    return template.format(date=date_str, site_title=site_title)


def _mark_email_processed(gc: GoogleClient, message_id: str, label_id: str) -> None:
    """Add the DD-Processed label and remove UNREAD."""
    gc.gmail_modify_labels(
        message_id,
        add_labels=[label_id],
        remove_labels=["UNREAD"],
    )
    logger.info("Marked email %s as processed", message_id)


def build_scan_summary(results: dict[str, Any]) -> str:
    """Build a human-readable summary for Google Chat notification."""
    lines = [
        "Inbox Scanner Summary",
        f"  Emails found: {results['emails_found']}",
        f"  Emails processed: {results['emails_processed']}",
        f"  Attachments uploaded: {results['attachments_uploaded']}",
        f"  Attachments skipped: {results['attachments_skipped']}",
    ]

    if results.get("uploads"):
        lines.append("\nUploads:")
        for u in results["uploads"]:
            dry = " [DRY RUN]" if u.get("dry_run") else ""
            lines.append(f"  {u['doc_type'].upper()} -> {u['drive_filename']} ({u['site_title']}){dry}")

    if results.get("low_confidence"):
        lines.append("\nNeeds manual review:")
        for lc in results["low_confidence"]:
            lines.append(
                f"  '{lc['filename']}' — {lc['doc_type']} "
                f"(confidence: {lc['confidence']:.0%}, site: {lc.get('matched_site') or 'none'})"
            )

    if results.get("errors"):
        lines.append(f"\nErrors: {len(results['errors'])}")
        for err in results["errors"]:
            lines.append(f"  {err['message_id']}: {err['error']}")

    return "\n".join(lines)
