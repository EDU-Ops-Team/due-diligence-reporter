"""Inbox scanner for auto-filing DD documents from email to Google Drive.

Scans Gmail for emails sent to edu.ops@trilogy.com (to or cc) with PDF
attachments, classifies them by filename using the three-tier classifier,
and uploads to the correct shared Drive folder (SIR, Building Inspection,
or ISP). No site matching required — doc_type alone routes the file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .classifier import classify_document
from .config import Settings
from .google_client import GoogleClient

logger = logging.getLogger("[inbox_scanner]")

# Confidence threshold — auto-file at or above this, flag below for review
AUTO_FILE_CONFIDENCE = 0.7

# Doc types we handle (others are skipped silently)
SUPPORTED_DOC_TYPES = {"sir", "building_inspection", "isp"}

# Map doc_type to the Settings field name for the target folder ID
DOC_TYPE_FOLDER_MAP = {
    "sir": "sir_folder_id",
    "building_inspection": "building_inspection_folder_id",
    "isp": "isp_folder_id",
}

# Filename templates per doc_type
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
                gc, message_id, settings, label_id, dry_run=dry_run,
            )
            if email_result.get("uploaded"):
                results["attachments_uploaded"] += len(email_result["uploaded"])
                results["uploads"].extend(email_result["uploaded"])
            if email_result.get("skipped"):
                results["attachments_skipped"] += email_result["skipped"]
            if email_result.get("low_confidence"):
                results["low_confidence"].extend(email_result["low_confidence"])
            if email_result.get("errors"):
                results["errors"].extend(email_result["errors"])
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
    settings: Settings,
    label_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Process a single email: classify attachments by filename, upload, mark done.

    Returns a dict with keys: uploaded, skipped, low_confidence, errors, marked.
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
    errors: list[dict[str, Any]] = []
    all_succeeded = True

    for att in metadata.attachments:
        filename = att["filename"]
        attachment_id = att["attachment_id"]

        # Classify by filename using the three-tier classifier
        doc_type, confidence = classify_document(filename)

        logger.info(
            "Classification for '%s': doc_type=%s, confidence=%.2f",
            filename, doc_type, confidence,
        )

        # Skip unsupported doc types
        if doc_type not in SUPPORTED_DOC_TYPES:
            logger.info("Skipping '%s' — unsupported doc_type: %s", filename, doc_type)
            skipped += 1
            continue

        # Flag low-confidence matches for manual review
        if confidence < AUTO_FILE_CONFIDENCE:
            logger.warning(
                "Low confidence (%.2f) for '%s' — flagging for manual review",
                confidence, filename,
            )
            low_confidence.append({
                "filename": filename,
                "doc_type": doc_type,
                "confidence": confidence,
                "email_subject": metadata.subject,
            })
            skipped += 1
            continue

        # Route to target folder by doc_type
        folder_attr = DOC_TYPE_FOLDER_MAP.get(doc_type)
        if not folder_attr:
            skipped += 1
            continue
        target_folder_id = getattr(settings, folder_attr, "")
        if not target_folder_id:
            logger.error("No folder ID configured for %s", doc_type)
            errors.append({
                "message_id": message_id,
                "filename": filename,
                "doc_type": doc_type,
                "error": f"No folder ID configured for {doc_type}",
            })
            all_succeeded = False
            continue

        # Use date-prefixed original filename
        date_str = datetime.now().strftime("%b %d %Y")
        drive_filename = f"{date_str} - {filename}"

        if dry_run:
            logger.info("[DRY RUN] Would upload '%s' to folder %s", drive_filename, target_folder_id)
            uploaded.append({
                "original_filename": filename,
                "drive_filename": drive_filename,
                "doc_type": doc_type,
                "site_title": None,
                "matched_site_id": None,
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
                "doc_type": doc_type,
                "site_title": None,
                "matched_site_id": None,
                "drive_file_id": drive_file.get("id"),
                "drive_link": drive_file.get("webViewLink"),
            })
            logger.info("Uploaded '%s' -> '%s'", filename, drive_filename)
        except Exception as e:
            logger.error("Upload failed for '%s': %s", filename, e)
            errors.append({
                "message_id": message_id,
                "filename": filename,
                "doc_type": doc_type,
                "error": str(e),
            })
            all_succeeded = False

    # Mark as processed if no exceptions and no low-confidence items.
    # Unknown doc types and duplicates are safely skipped and don't need
    # re-scanning. Only low-confidence items require human review and
    # should stay in the queue.
    marked = False
    if all_succeeded and not dry_run and not low_confidence:
        _mark_email_processed(gc, message_id, label_id)
        marked = True

    return {
        "uploaded": uploaded,
        "skipped": skipped,
        "low_confidence": low_confidence,
        "errors": errors,
        "marked": marked,
    }


def has_site_identity(uploads: list[dict[str, Any]]) -> bool:
    """Return True when at least one upload can be mapped to a site."""
    return any(u.get("site_title") or u.get("matched_site_id") for u in uploads)


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


def _generate_drive_filename(site_title: str, doc_type: str) -> str:
    """Generate a Drive filename using the standard naming pattern.

    Used by scan_inbox.py Phase 2 when a site match is available.
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
            lines.append(f"  {u['doc_type'].upper()} -> {u['drive_filename']}{dry}")

    if results.get("low_confidence"):
        lines.append("\nNeeds manual review:")
        for lc in results["low_confidence"]:
            lines.append(
                f"  '{lc['filename']}' — {lc['doc_type']} "
                f"(confidence: {lc['confidence']:.0%}, subject: {lc.get('email_subject', '')})"
            )

    if results.get("errors"):
        lines.append(f"\nErrors: {len(results['errors'])}")
        for err in results["errors"]:
            target = err.get("filename") or err.get("message_id", "unknown")
            lines.append(f"  {target}: {err['error']}")

    return "\n".join(lines)
