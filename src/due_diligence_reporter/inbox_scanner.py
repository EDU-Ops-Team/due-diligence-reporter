"""Inbox scanner for auto-filing DD documents from email to Google Drive.

Scans Gmail for emails sent to edu.ops@trilogy.com (to or cc) with PDF
attachments, classifies them by filename using the three-tier classifier,
and uploads to the correct shared Drive folder (SIR, Building Inspection,
or ISP). No site matching required — doc_type alone routes the file.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NotRequired, TypedDict

from .classifier import classify_by_content_llm, classify_document
from .config import Settings
from .google_client import GoogleClient
from .m1_lookup import (
    _list_m1_documents_by_type,
    _resolve_m1_folder,
)
from .utils import (
    escape_html_text,
    extract_city_from_address,
    extract_text_from_pdf_bytes,
    send_email,
)


class DDRepublishResult(TypedDict, total=False):
    """Normalized envelope returned by :func:`_maybe_fire_dd_republish`.

    Every code path returns this shape so callers can branch on
    ``status`` without keying on ``KeyError``-prone optional fields.
    """

    status: str  # "skipped" | "fired" | "failed"
    reason: str
    dd_report_republish: NotRequired[str]
    republish_reason: NotRequired[str]


# Regex to extract the bare email from a From header like "Display Name <user@domain.com>"
_EMAIL_RE = re.compile(r"<([^>]+)>")

logger = logging.getLogger("[inbox_scanner]")

# Confidence threshold — auto-file at or above this, flag below for review
AUTO_FILE_CONFIDENCE = 0.7

# Doc types we handle (others are skipped silently)
SUPPORTED_DOC_TYPES = {"sir", "building_inspection", "isp", "block_plan"}

# Legacy: doc_type -> Settings attr for the dedicated shared Drive folder.
# As of the M1-routing change, the live scanner uploads all supported doc
# types into the matched site's `M1` subfolder. This map is retained only
# so the one-shot migration script (`scripts/copy_legacy_docs_to_m1.py`)
# knows which shared folders to read from.
LEGACY_DOC_TYPE_FOLDER_MAP = {
    "sir": "sir_folder_id",
    "building_inspection": "building_inspection_folder_id",
    "isp": "isp_folder_id",
}
# Backwards-compatibility alias (older imports/tests).
DOC_TYPE_FOLDER_MAP = LEGACY_DOC_TYPE_FOLDER_MAP

# Filename templates per doc_type
DOC_TYPE_FILENAME_TEMPLATES = {
    "sir": "{date} - {site_title} SIR.pdf",
    "building_inspection": "{date} - {site_title} Building Inspection Report.pdf",
    "isp": "{date} - {site_title} ISP.pdf",
    "block_plan": "{date} - {site_title} Block Plan.pdf",
}


def _custom_field_value(record: dict[str, Any], names: set[str]) -> str:
    for field in record.get("customFields", []) or []:
        if not isinstance(field, dict):
            continue
        label = str(
            field.get("name")
            or field.get("title")
            or field.get("customFieldName")
            or ""
        ).strip().lower()
        if label in names:
            value = field.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _record_value(record: dict[str, Any], keys: tuple[str, ...], field_names: set[str]) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _custom_field_value(record, field_names)


def _record_address(record: dict[str, Any]) -> str:
    return _record_value(
        record,
        ("address", "site_address", "property_address"),
        {"address", "site address", "property address"},
    )


def _build_site_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(record.get("id") or record.get("site_id") or "").strip(),
        "title": str(record.get("title") or record.get("name") or "").strip(),
        "address": _record_address(record),
        "drive_folder_url": _record_value(
            record,
            ("drive_folder_url", "google_folder", "folder_url"),
            {"google folder", "drive folder", "drive folder url"},
        ),
        "total_building_sf": record.get("total_building_sf")
        or record.get("building_square_feet")
        or _custom_field_value(
            record,
            {"total building sf", "building square feet", "total building square feet"},
        ),
    }


@dataclass
class EmailMetadata:
    """Extracted metadata from a Gmail message."""

    message_id: str
    subject: str
    sender: str  # raw From: header (for display/logging)
    body_snippet: str
    label_ids: list[str]
    attachments: list[dict[str, Any]]  # [{filename, attachment_id?, body_data?, mime_type}]
    # X-Original-Sender header set by Google Groups when an email is rerouted
    # through a group. Holds the actual external sender's address. Empty when
    # the email did not pass through a Google Group.
    original_sender: str = ""

    @property
    def effective_sender(self) -> str:
        """Return the address that should be used for internal/external
        classification. Prefers X-Original-Sender when set, since Google
        Groups rewrite the visible From: to the group's domain (e.g.
        auth.permitting@trilogy.com), which would falsely classify
        external CDS / regulator / vendor mail as internal.
        """
        return self.original_sender.strip() or self.sender


@dataclass
class ProcessedAttachment:
    """Record of a successfully processed attachment."""

    filename: str
    doc_type: str
    site_title: str
    drive_file_id: str
    drive_file_name: str


# `_resolve_m1_folder`, `_list_m1_documents_by_type`, and
# `M1_RECOGNIZED_DOC_TYPES` are re-exported from `m1_lookup` above so that
# tests patching `due_diligence_reporter.inbox_scanner._resolve_m1_folder`
# and external scripts continue to work after the helpers were extracted to
# a shared module.


def _send_block_plan_failure_notification(
    settings: Settings,
    *,
    site_title: str,
    site_owner_email: str,
    filename: str,
    error_message: str,
) -> None:
    """Notify the site owner that Block Plan downstream processing failed."""
    if not settings.email_sender.strip() or not settings.email_app_password.strip():
        logger.warning(
            "Skipping site-owner failure email for %s: email credentials not configured",
            site_title,
        )
        return
    subject = f"Block Plan processing failed for {site_title}"
    body = (
        "<p>Block Plan downstream processing failed and needs attention.</p>"
        f"<p><strong>Site:</strong> {escape_html_text(site_title)}<br>"
        f"<strong>Attachment:</strong> {escape_html_text(filename)}<br>"
        f"<strong>Error:</strong> {escape_html_text(error_message)}</p>"
        "<p>The inbox scanner will retry automatically on the next run until the derived reports are created.</p>"
    )
    send_email(
        settings.email_sender,
        settings.email_app_password,
        [site_owner_email],
        subject,
        body,
        settings.global_email_cc,
    )


def _run_doc_arrival_folder_ping(
    gc: GoogleClient,
    *,
    site_summary: dict[str, Any],
    doc_type: str,
    drive_file: dict[str, Any] | None,
) -> dict[str, str]:
    """Tell RayCon a new doc landed in the site's Drive folder.

    Fired on every successful classified upload (CDS SIR, Worksmith
    inspection, ISP, Block Plan). RayCon walks the folder server-side
    using ``drive_folder_url`` and decides whether the document set is
    now complete enough to start computing scenarios. The ping is
    idempotent on RayCon's side, so duplicate fires are safe.

    Returns a status dict the caller can attach to the per-attachment
    ``uploaded`` row. Failures are surfaced in the dict (``status`` =
    ``error`` + ``error`` message) but never raise — a flaky RayCon
    must not block a successful Drive upload from being marked done.
    The cron-driven safety net in ``scripts/raycon_followup.py`` will
    re-fire any missed pings.
    """
    from .raycon_client import post_raycon_folder_ping

    site_id = str(site_summary.get("id", "")).strip()
    site_name = str(site_summary.get("title", "")).strip()
    site_address = str(site_summary.get("address", "")).strip()
    drive_folder_url = str(site_summary.get("drive_folder_url", "")).strip()

    if not (site_id and site_name and site_address and drive_folder_url):
        return {
            "status": "skipped",
            "reason": "missing site_id/title/address/drive_folder_url",
        }

    try:
        m1_folder_id, _ = _resolve_m1_folder(gc, drive_folder_url)
    except Exception as e:
        return {"status": "error", "error": f"resolve M1 failed: {e}"}
    if not m1_folder_id:
        return {"status": "skipped", "reason": "no M1 folder"}

    file_id = str((drive_file or {}).get("id", ""))
    file_url = str((drive_file or {}).get("webViewLink", ""))

    try:
        response = post_raycon_folder_ping(
            site_id=site_id,
            site_name=site_name,
            address=site_address,
            drive_folder_url=drive_folder_url,
            m1_folder_id=m1_folder_id,
            doc_type=doc_type,
            file_id=file_id,
            file_url=file_url,
        )
    except Exception as e:
        # Never break the upload on a flaky RayCon ping; cron safety net
        # in scripts/raycon_followup.py will re-fire missed dispatches.
        logger.warning(
            "RayCon folder ping failed for site=%s doc_type=%s file_id=%s: %s",
            site_name,
            doc_type,
            file_id or "(none)",
            e,
        )
        return {"status": "error", "error": str(e)}

    logger.info(
        "RayCon folder ping accepted for site=%s doc_type=%s file_id=%s status=%s",
        site_name,
        doc_type,
        file_id or "(none)",
        response.get("status", "accepted"),
    )
    return {
        "status": str(response.get("status", "accepted")),
        "doc_type": doc_type,
        "file_id": file_id,
    }


# Map of inbox doc_type → shared-helper reason code. Keep this narrow:
# ISP and Block Plan do NOT trigger a DD Report republish. ISP is not an
# authoritative DD input for the report content; Block Plan triggers the
# RayCon job dispatch which (when RayCon answers) routes through the
# scripts/raycon_followup.py republish hook instead.
_INBOX_DOC_TYPE_TO_REPUBLISH_REASON = {
    "sir": "vendor_sir",
    "building_inspection": "building_inspection",
}


def _maybe_fire_dd_republish(
    *,
    callback: Any,
    gc: GoogleClient,
    site_summary: dict[str, Any],
    doc_type: str,
    drive_file: dict[str, Any],
    dry_run: bool,
    m1_folder_id: str | None = None,
) -> DDRepublishResult:
    """Fire the shared DD Report republish callback for a vendor doc arrival.

    Builds the content fingerprint from the Drive file's
    ``id:modifiedTime`` so a re-upload of the same SIR (same Drive file
    id, refreshed modifiedTime) re-fires the republish, while a polled
    re-walk of the same file ID + same modifiedTime is a no-op.

    Failures inside the callback are swallowed and surfaced into the
    returned dict so the inbox scan never breaks on a flaky republish.

    All four return paths share the :class:`DDRepublishResult` envelope
    shape (``status`` + ``reason`` + optional callback fields) so
    callers can branch on ``status`` without keying on optional fields.
    """
    from .provenance import is_vendor_sourced  # local import to avoid cycles

    reason = _INBOX_DOC_TYPE_TO_REPUBLISH_REASON.get(doc_type)
    if not reason:
        return {
            "status": "skipped",
            "reason": f"doc_type {doc_type} not authoritative",
        }

    file_id = str(drive_file.get("id", "")).strip()
    modified_time = str(drive_file.get("modifiedTime", "")).strip()
    if not file_id:
        return {"status": "skipped", "reason": "missing drive file id"}

    # Provenance gate: only republish for vendor-sourced files. AI-named
    # uploads (Greg's "I'll just rename it" path) bypass the vendor
    # gate elsewhere but should never trigger a DD republish, since
    # they aren't trusted authoritative inputs.
    if not is_vendor_sourced(
        drive_file,
        gc=gc,
        m1_folder_id=m1_folder_id,
        doc_type=doc_type,
    ):
        return {"status": "skipped", "reason": "ai_named_skipped"}

    fingerprint = f"{file_id}:{modified_time}" if modified_time else file_id

    try:
        result = callback(
            gc=gc,
            site_summary=site_summary,
            reason=reason,
            fingerprint=fingerprint,
            dry_run=dry_run,
        )
    except Exception as e:
        logger.exception(
            "DD Report republish callback raised for site=%s doc_type=%s",
            site_summary.get("title"),
            doc_type,
        )
        return {"status": "failed", "reason": str(e)}

    # Preserve every key the callback returned (dd_report_republish,
    # republish_reason, site_id, content_fingerprint, doc_url, etc.)
    # while normalizing status/reason on top.
    fired: DDRepublishResult = {"status": "fired", "reason": "ok"}
    if isinstance(result, dict):
        fired.update(result)  # type: ignore[typeddict-item]
    return fired


def _run_block_plan_downstream(
    gc: GoogleClient,
    *,
    site_summary: dict[str, Any],
    block_plan_content: str,  # noqa: ARG001 — retained for caller compatibility
    block_plan_url: str,
    block_plan_file_id: str,
) -> list[dict[str, Any]]:
    """Hand the Block Plan off to RayCon's async ``/v1/jobs`` endpoint.

    Pre-cutover (April 2026) this function ran Capacity Brainlift +
    the synchronous ``/v1/chat`` call inline, which took up to ~10
    minutes per site. The new contract (per ``raycon_ddr_integration_spec.md``):

    1. DDR files the Block Plan into the site's M1 folder (already done
       by the caller at the time we run).
    2. We POST an HMAC-signed job ping to RayCon with the 11 spec fields.
       RayCon reads the Block Plan from Drive itself using
       ``block_plan_file_id``, derives rooms, computes scenarios, and
       writes ``raycon_scenario.json`` back into the same M1 folder.
    3. The ``raycon-followup`` workflow polls every 5 minutes, picks up
       the JSON when it lands, and publishes the RayCon Scenario
       Google Doc. (Implemented in scripts/raycon_followup.py.)

    No long inline wait. ``block_plan_content`` is retained so callers
    don't need to be touched, but RayCon now reads the PDF directly from
    Drive via ``block_plan_file_id``.
    """
    from .raycon_client import post_raycon_job

    site_id = str(site_summary.get("id", "")).strip()
    site_name = str(site_summary.get("title", "")).strip()
    site_address = str(site_summary.get("address", "")).strip()
    drive_folder_url = str(site_summary.get("drive_folder_url", "")).strip()

    if not (site_id and site_name and site_address and drive_folder_url):
        raise RuntimeError(
            "Block Plan downstream requires site_id, title, address, and "
            "drive_folder_url in the matched site metadata."
        )
    if not block_plan_file_id:
        raise RuntimeError(
            "Block Plan downstream requires block_plan_file_id (Drive file ID "
            "of the uploaded Block Plan); spec §1.2 uses it as the idempotency key."
        )
    if not block_plan_url:
        raise RuntimeError(
            "Block Plan downstream requires block_plan_url (Drive webViewLink); "
            "spec §1.2 requires it for the RayCon job body."
        )

    # m1_folder_id is the folder RayCon will write raycon_scenario.json into.
    m1_folder_id, _ = _resolve_m1_folder(gc, drive_folder_url)
    if not m1_folder_id:
        raise RuntimeError(
            f"Could not resolve M1 folder for drive_folder_url='{drive_folder_url}'; "
            "RayCon needs m1_folder_id to know where to write the result."
        )

    # total_building_sf comes from the matched site metadata (may be missing for
    # early-stage sites; the spec marks it required so post_raycon_job
    # sends 0 when truly unknown rather than dropping the field).
    total_building_sf_raw = site_summary.get("total_building_sf")
    try:
        total_building_sf = (
            int(total_building_sf_raw) if total_building_sf_raw is not None else None
        )
    except (TypeError, ValueError):
        total_building_sf = None

    response = post_raycon_job(
        site_id=site_id,
        site_name=site_name,
        address=site_address,
        drive_folder_url=drive_folder_url,
        m1_folder_id=m1_folder_id,
        block_plan_file_id=block_plan_file_id,
        block_plan_url=block_plan_url,
        total_building_sf=total_building_sf,
    )
    raycon_run_id = str(response.get("raycon_run_id", "") or "").strip()
    job_id = str(response.get("job_id", "")).strip()
    logger.info(
        "RayCon job dispatched for site=%s block_plan_file_id=%s job_id=%s run_id=%s",
        site_name,
        block_plan_file_id,
        job_id or "(unknown)",
        raycon_run_id or "(unknown)",
    )

    return [
        {
            "doc_type": "raycon_scenario_request",
            "block_plan_file_id": block_plan_file_id,
            "job_id": job_id,
            "idempotency_key": str(response.get("idempotency_key", "") or "").strip(),
            "raycon_run_id": raycon_run_id,
            "retry_after_seconds": str(response.get("retry_after_seconds", "") or ""),
            "status_url_present": bool(response.get("status_url")),
            "cached": str(response.get("cached", "") or ""),
            "status": str(response.get("status", "accepted")),
        }
    ]


def _parse_sender_email(from_header: str) -> str:
    """Extract the bare email address from a From header.

    Handles both ``user@domain.com`` and ``"Display Name" <user@domain.com>``.
    Returns the lowercase bare address, or the lowered raw string if parsing fails.
    """
    match = _EMAIL_RE.search(from_header)
    if match:
        return match.group(1).strip().lower()
    return from_header.strip().lower()


def _is_internal_sender(sender: str, settings: Settings) -> bool:
    """Return True when *sender* matches an internal domain or address.

    Prevents AI-generated documents (SIRs, CDS overlays, etc.) produced by
    internal processes from being re-filed into the shared vendor folders,
    which would create false readiness signals in the DD pipeline.
    """
    email = _parse_sender_email(sender)

    # Check explicit addresses first (service accounts, noreply, etc.)
    explicit = [
        addr.strip().lower()
        for addr in settings.inbox_internal_sender_addresses.split(",")
        if addr.strip()
    ]
    if email in explicit:
        return True

    # Check domain
    domains = [
        d.strip().lower() for d in settings.inbox_internal_sender_domains.split(",") if d.strip()
    ]
    _, _, domain = email.rpartition("@")
    return domain in domains


def _classify_inbox_attachment(
    gc: GoogleClient,
    message_id: str,
    attachment: dict[str, Any],
) -> tuple[str, float, bytes | None]:
    """Classify an inbox PDF, using first-page text when the filename is weak."""
    filename = attachment["filename"]
    doc_type, confidence = classify_document(filename)
    if doc_type in SUPPORTED_DOC_TYPES and confidence >= AUTO_FILE_CONFIDENCE:
        return doc_type, confidence, None
    if not filename.lower().endswith(".pdf"):
        return doc_type, confidence, None

    try:
        file_bytes = _get_attachment_bytes(gc, message_id, attachment)
        text = extract_text_from_pdf_bytes(file_bytes)
    except Exception as e:
        logger.warning("Inbox PDF content classification failed for '%s': %s", filename, e)
        return doc_type, confidence, None

    if not text.strip():
        return doc_type, confidence, file_bytes

    content_doc_type, content_confidence = classify_by_content_llm(
        text[:3000],
        filename,
    )
    if content_confidence > confidence:
        logger.info(
            "Inbox content classified '%s' as %s (%.2f), replacing filename result %s (%.2f)",
            filename,
            content_doc_type,
            content_confidence,
            doc_type,
            confidence,
        )
        return content_doc_type, content_confidence, file_bytes
    return doc_type, confidence, file_bytes


def scan_inbox(
    gc: GoogleClient,
    site_records: list[dict[str, Any]] | None,
    settings: Settings,
    *,
    dry_run: bool = False,
    dd_republish_callback: Any = None,
) -> dict[str, Any]:
    """Top-level orchestrator: scan Gmail, classify, upload, mark processed.

    ``dd_republish_callback`` (Rec. 3): when supplied, fires after each
    classified vendor SIR or Building Inspection upload to republish the
    DD Report if one already exists for the matched site. Signature:

        callback(*, gc, site_summary, reason, fingerprint, dry_run) -> dict

    The callback is responsible for loading the agent system prompt and
    Drive shared-folder cache lazily so the cost is only paid when at
    least one authoritative arrival actually fires republish. Failures
    inside the callback are caught here and surfaced into the per-upload
    row as ``dd_report_republish: failed``; they never break the scan.

    Returns a summary dict with counts and details.
    """
    logger.info("Starting inbox scan (dry_run=%s)", dry_run)
    site_records = site_records or []

    # Get or create the labels.
    # - DD-Processed: applied to emails the scanner has finished with (uploaded
    #   or had a real reason to skip an attachment).
    # - DD-Manual-Review: applied when human review is needed.
    # - DD-Internal-Skipped: applied to emails skipped by the internal-sender
    #   heuristic. Distinct from DD-Processed so heuristic bugs do not burn
    #   real DD deliveries (recoverable by clearing this one label).
    label_id = gc.gmail_get_or_create_label(settings.inbox_processed_label)
    review_label_id = gc.gmail_get_or_create_label(settings.inbox_manual_review_label)
    internal_skip_label_id = gc.gmail_get_or_create_label(settings.inbox_internal_skip_label)

    # Do not exclude DD-Processed here. Gmail labels are thread-visible in
    # search, so a processed kickoff thread can hide a later vendor reply with
    # a new SIR PDF. Idempotency is handled downstream by Drive file existence.
    query = settings.inbox_scan_query
    logger.info("Inbox scan query (resolved): %s", query)
    messages = gc.gmail_search(query, max_results=settings.inbox_scan_max_results)
    logger.info("Found %d unprocessed emails", len(messages))

    results: dict[str, Any] = {
        "emails_found": len(messages),
        "attachments_uploaded": 0,
        "attachments_skipped": 0,
        "internal_skipped": 0,
        "emails_processed": 0,
        "errors": [],
        "uploads": [],
        "low_confidence": [],
    }

    for msg_stub in messages:
        message_id = msg_stub["id"]
        try:
            email_result = process_email(
                gc,
                message_id,
                settings,
                label_id,
                review_label_id,
                site_records=site_records,
                dry_run=dry_run,
                internal_skip_label_id=internal_skip_label_id,
                dd_republish_callback=dd_republish_callback,
            )
            if email_result.get("internal_skipped"):
                results["internal_skipped"] += 1
                results["emails_processed"] += 1
                continue
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
        "Inbox scan complete: %d uploaded, %d skipped, %d internal, %d errors",
        results["attachments_uploaded"],
        results["attachments_skipped"],
        results["internal_skipped"],
        len(results["errors"]),
    )
    return results


def process_email(
    gc: GoogleClient,
    message_id: str,
    settings: Settings,
    label_id: str,
    review_label_id: str,
    *,
    site_records: list[dict[str, Any]] | None = None,
    internal_skip_label_id: str | None = None,
    dry_run: bool = False,
    dd_republish_callback: Any = None,
) -> dict[str, Any]:
    """Process a single email: classify attachments by filename, upload, mark done.

    ``dd_republish_callback`` (Rec. 3): see :func:`scan_inbox` docstring.
    Forwarded to the per-upload arrival path so vendor SIR + Building
    Inspection arrivals on a site that already has a DD Report trigger a
    republish. ``None`` (the default) means "don't fire republish" — the
    legacy first-generation path keeps owning the case where no DD
    Report exists yet.

    Returns a dict with keys: uploaded, skipped, low_confidence, errors, marked.
    """
    metadata = _extract_email_metadata(gc, message_id)
    logger.info(
        "Processing email: '%s' from %s (%d attachments)",
        metadata.subject,
        metadata.sender,
        len(metadata.attachments),
    )

    existing_label_ids = metadata.label_ids if isinstance(metadata.label_ids, list) else []

    # Use the effective sender for internal/external classification so that
    # Google-Group-routed mail (where the visible From: is the group address
    # but X-Original-Sender holds the real external sender) is not falsely
    # classified as internal. See EmailMetadata.effective_sender.
    if _is_internal_sender(metadata.effective_sender, settings):
        logger.info(
            "Skipping internal sender '%s' [effective='%s'] (subject: '%s') - "
            "would create false readiness if filed",
            metadata.sender,
            metadata.effective_sender,
            metadata.subject,
        )
        # Apply DD-Internal-Skipped (NOT DD-Processed) so future heuristic
        # bugs only require clearing this single label to recover. Falls back
        # to DD-Processed only if the new label id wasn't supplied (legacy
        # callers).
        if not dry_run:
            skip_label = internal_skip_label_id or label_id
            _mark_email_processed(gc, message_id, skip_label)
        return {"internal_skipped": True, "marked": not dry_run}

    uploaded: list[dict[str, Any]] = []
    skipped = 0
    low_confidence: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    all_succeeded = True
    review_needed = False
    keep_unprocessed = False
    failure_notification_sent = review_label_id in existing_label_ids
    site_records = site_records or []

    if not metadata.attachments:
        logger.warning(
            "No PDF attachments were extracted from email '%s' despite matching scan query",
            metadata.subject,
        )
        errors.append(
            {
                "message_id": message_id,
                "error": "No PDF attachments detected in Gmail payload",
                "email_subject": metadata.subject,
            }
        )
        review_needed = True

    for att in metadata.attachments:
        filename = att["filename"]
        doc_type, confidence, file_bytes = _classify_inbox_attachment(
            gc,
            message_id,
            att,
        )
        matched_record = _match_attachment_to_site(filename, metadata, site_records)
        site_title = matched_record.get("title") if matched_record else None
        matched_site_id = matched_record.get("id") if matched_record else None

        logger.info(
            "Classification for '%s': doc_type=%s, confidence=%.2f, site=%s",
            filename,
            doc_type,
            confidence,
            site_title or "unmatched",
        )

        if doc_type not in SUPPORTED_DOC_TYPES:
            logger.info("Skipping '%s' - unsupported doc_type: %s", filename, doc_type)
            skipped += 1
            continue

        if confidence < AUTO_FILE_CONFIDENCE:
            logger.warning(
                "Low confidence (%.2f) for '%s' - flagging for manual review",
                confidence,
                filename,
            )
            low_confidence.append(
                {
                    "filename": filename,
                    "doc_type": doc_type,
                    "confidence": confidence,
                    "email_subject": metadata.subject,
                    "site_title": site_title,
                }
            )
            skipped += 1
            review_needed = True
            continue

        if matched_record is None:
            logger.warning(
                "%s '%s' could not be matched to a site - flagging for manual review",
                doc_type,
                filename,
            )
            low_confidence.append(
                {
                    "filename": filename,
                    "doc_type": doc_type,
                    "confidence": confidence,
                    "email_subject": metadata.subject,
                    "site_title": site_title,
                }
            )
            skipped += 1
            review_needed = True
            continue

        site_summary = _build_site_summary(matched_record) if matched_record else {}
        # All supported doc types route to the matched site's M1 subfolder.
        # The legacy shared-folder targets (sir_folder_id, building_inspection_folder_id,
        # isp_folder_id) are no longer used by the live scanner — they remain
        # configured only so the migration script can read from them.
        drive_folder_url = str(site_summary.get("drive_folder_url", "")).strip()
        if not drive_folder_url:
            errors.append(
                {
                    "message_id": message_id,
                    "filename": filename,
                    "doc_type": doc_type,
                    "error": "Matched site has no Google Drive folder URL",
                }
            )
            all_succeeded = False
            review_needed = True
            continue
        try:
            target_folder_id, _target_folder_url = _resolve_m1_folder(gc, drive_folder_url)
        except Exception as e:
            errors.append(
                {
                    "message_id": message_id,
                    "filename": filename,
                    "doc_type": doc_type,
                    "error": f"Failed to resolve M1 folder: {e}",
                }
            )
            all_succeeded = False
            review_needed = True
            continue
        if not target_folder_id:
            errors.append(
                {
                    "message_id": message_id,
                    "filename": filename,
                    "doc_type": doc_type,
                    "error": "Could not resolve M1 folder ID",
                }
            )
            all_succeeded = False
            review_needed = True
            continue

        drive_filename = (
            _generate_drive_filename(site_title, doc_type)
            if site_title
            else _prefix_original_filename(filename)
        )

        # Plumb the matched site's address into the upload payload so
        # downstream readiness flips can resolve the canonical Rebl slug
        # (the publisher's slug source) instead of slugify(title).
        site_address = str(site_summary.get("address", "")).strip()

        if dry_run:
            logger.info(
                "[DRY RUN] Would upload '%s' to folder %s", drive_filename, target_folder_id
            )
            uploaded.append(
                {
                    "original_filename": filename,
                    "drive_filename": drive_filename,
                    "doc_type": doc_type,
                    "site_title": site_title,
                    "site_address": site_address,
                    "matched_site_id": matched_site_id,
                    "dry_run": True,
                }
            )
            continue

        drive_file: dict[str, Any] | None = None
        rerun_existing_block_plan = False
        if gc.file_exists_in_folder(target_folder_id, drive_filename):
            if doc_type != "block_plan":
                logger.info("File '%s' already exists in folder - skipping upload", drive_filename)
                skipped += 1
                continue
            existing_docs = _list_m1_documents_by_type(gc, target_folder_id)
            if "raycon_scenario_json" in existing_docs:
                logger.info(
                    "Block Plan '%s' already exists and RayCon scenario is published - skipping",
                    drive_filename,
                )
                skipped += 1
                continue
            drive_file = existing_docs.get("block_plan")
            rerun_existing_block_plan = True
            logger.info(
                "Block Plan '%s' already exists but RayCon scenario is missing - re-pinging RayCon",
                drive_filename,
            )
            if drive_file is None:
                errors.append(
                    {
                        "message_id": message_id,
                        "filename": filename,
                        "doc_type": doc_type,
                        "error": "Existing Block Plan PDF could not be found in M1",
                    }
                )
                all_succeeded = False
                review_needed = True
                keep_unprocessed = True
                continue

        try:
            if file_bytes is None:
                file_bytes = _get_attachment_bytes(gc, message_id, att)
            if not rerun_existing_block_plan:
                drive_file = gc.upload_file_to_folder(
                    folder_id=target_folder_id,
                    file_name=drive_filename,
                    file_bytes=file_bytes,
                )
                uploaded.append(
                    {
                        "original_filename": filename,
                        "drive_filename": drive_filename,
                        "doc_type": doc_type,
                        "site_title": site_title,
                        "site_address": site_address,
                        "matched_site_id": matched_site_id,
                        "drive_file_id": drive_file.get("id"),
                        "drive_link": drive_file.get("webViewLink"),
                    }
                )
                logger.info("Uploaded '%s' -> '%s'", filename, drive_filename)
            elif drive_file is not None:
                uploaded.append(
                    {
                        "original_filename": filename,
                        "drive_filename": drive_filename,
                        "doc_type": doc_type,
                        "site_title": site_title,
                        "site_address": site_address,
                        "matched_site_id": matched_site_id,
                        "drive_file_id": drive_file.get("id"),
                        "drive_link": drive_file.get("webViewLink"),
                        "retry_existing_upload": True,
                    }
                )

            # Per-doc folder ping: tell RayCon a new doc landed so it can
            # walk the folder and decide if the document set is complete
            # enough to start. Fired for ALL classified doc types
            # (CDS SIR, Worksmith inspection, ISP, Block Plan). Block
            # Plan also still fires the full job dispatch below.
            if drive_file is not None and uploaded:
                folder_ping = _run_doc_arrival_folder_ping(
                    gc,
                    site_summary=site_summary,
                    doc_type=doc_type,
                    drive_file=drive_file,
                )
                uploaded[-1]["raycon_folder_ping"] = folder_ping

                # Rec. 3 — generalized event-driven DD Report republish.
                # When a vendor SIR or Building Inspection lands on a
                # site that already has a DD Report, fire the shared
                # republish hook so the report picks up the new
                # authoritative input. RayCon arrivals continue to be
                # handled by scripts/raycon_followup.py since they fire
                # only when the scenario JSON is published, not when the
                # Block Plan is filed here.
                if dd_republish_callback is not None and doc_type in ("sir", "building_inspection"):
                    republish_result = _maybe_fire_dd_republish(
                        callback=dd_republish_callback,
                        gc=gc,
                        site_summary=site_summary,
                        doc_type=doc_type,
                        drive_file=drive_file,
                        dry_run=dry_run,
                        m1_folder_id=target_folder_id,
                    )
                    if republish_result:
                        uploaded[-1]["dd_report_republish"] = republish_result

            if doc_type == "block_plan" and drive_file is not None:
                block_plan_content = extract_text_from_pdf_bytes(file_bytes)
                try:
                    derived_docs = _run_block_plan_downstream(
                        gc,
                        site_summary=site_summary,
                        block_plan_content=block_plan_content,
                        block_plan_url=str(drive_file.get("webViewLink", "")),
                        block_plan_file_id=str(drive_file.get("id", "")),
                    )
                except Exception as e:
                    logger.warning(
                        "RayCon Block Plan dispatch failed after upload for '%s': %s",
                        filename,
                        e,
                    )
                    uploaded[-1]["raycon_dispatch_error"] = str(e)
                    uploaded[-1]["raycon_dispatch_status"] = "dispatch_failed"
                else:
                    uploaded[-1]["derived_documents"] = derived_docs
        except Exception as e:
            logger.error("Upload failed for '%s': %s", filename, e)
            errors.append(
                {
                    "message_id": message_id,
                    "filename": filename,
                    "doc_type": doc_type,
                    "error": str(e),
                }
            )
            all_succeeded = False
            review_needed = True
            if doc_type == "block_plan" and drive_file is not None:
                keep_unprocessed = True
                site_owner_email = str(site_summary.get("p1_assignee_email", "")).strip()
                if site_owner_email and not failure_notification_sent:
                    try:
                        _send_block_plan_failure_notification(
                            settings,
                            site_title=site_title or "Unknown site",
                            site_owner_email=site_owner_email,
                            filename=filename,
                            error_message=str(e),
                        )
                        failure_notification_sent = True
                    except Exception as notify_error:
                        logger.warning(
                            "Failed to send Block Plan failure notification for %s: %s",
                            site_title or filename,
                            notify_error,
                        )

    marked = False
    if review_needed and not dry_run:
        _mark_email_for_review(
            gc,
            message_id,
            label_id,
            review_label_id,
            include_processed_label=not keep_unprocessed,
        )
        marked = True
    elif all_succeeded and not dry_run and not low_confidence:
        _mark_email_processed(
            gc,
            message_id,
            label_id,
            remove_labels=["UNREAD", review_label_id],
        )
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


def _prefix_original_filename(filename: str) -> str:
    """Prefix the original filename with the current date."""
    date_str = datetime.now().strftime("%b %d %Y")
    return f"{date_str} - {filename}"


# _extract_city_from_address moved to utils.py (imported above)


def _site_match_score(filename: str, subject: str, record: dict[str, Any]) -> int:
    """Compute a deterministic match score between an attachment and a site record."""
    haystack = f"{filename} {subject}".lower()
    title = str(record.get("title") or record.get("name") or "").strip()
    if not title:
        return 0

    score = 0
    title_lower = title.lower()
    if title_lower in haystack:
        score += 100

    address = _record_address(record)
    city = extract_city_from_address(address)
    if city and city.lower() in haystack:
        score += 25

    stop_words = {"alpha", "school", "campus", "microschool"}
    for word in title_lower.replace("/", " ").split():
        token = word.strip(",.()")
        if len(token) < 3 or token in stop_words:
            continue
        if token in haystack:
            score += 12

    if address:
        zip_match = address.strip().split()[-1]
        if zip_match.isdigit() and zip_match in haystack:
            score += 10

    return score


def _match_attachment_to_site(
    filename: str,
    metadata: EmailMetadata,
    site_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Match an attachment to a site summary using deterministic rules."""
    if not site_records:
        return None

    scored: list[tuple[int, dict[str, Any]]] = []
    for record in site_records:
        score = _site_match_score(filename, metadata.subject, record)
        if score > 0:
            scored.append((score, record))
    scored.sort(key=lambda item: item[0], reverse=True)

    if scored:
        best_score, best_record = scored[0]
        next_score = scored[1][0] if len(scored) > 1 else -1
        if best_score >= 100 or (best_score >= 35 and best_score >= next_score + 15):
            return best_record

    return None


def _extract_email_metadata(gc: GoogleClient, message_id: str) -> EmailMetadata:
    """Fetch and parse email headers, snippet, and attachment info."""
    message = gc.gmail_get_message(message_id)

    headers = message.get("payload", {}).get("headers", [])
    header_map: dict[str, str] = {}
    for h in headers:
        name = h.get("name", "").lower()
        # Capture all headers we care about for sender classification.
        # X-Original-Sender is set by Google Groups when mail is rerouted
        # through a group; it preserves the actual external sender.
        if name in ("subject", "from", "to", "x-original-sender"):
            header_map[name] = h.get("value", "")

    subject = header_map.get("subject", "")
    sender = header_map.get("from", "")
    original_sender = header_map.get("x-original-sender", "")
    snippet = message.get("snippet", "")

    # Walk MIME parts to find PDF attachments
    attachments: list[dict[str, Any]] = []
    _walk_parts(message.get("payload", {}), attachments)

    return EmailMetadata(
        message_id=message_id,
        subject=subject,
        sender=sender,
        body_snippet=snippet,
        label_ids=list(message.get("labelIds", [])),
        attachments=attachments,
        original_sender=original_sender,
    )


def _walk_parts(part: dict[str, Any], attachments: list[dict[str, Any]]) -> None:
    """Recursively walk MIME parts to extract PDF attachment metadata."""
    filename = part.get("filename", "")
    mime_type = part.get("mimeType", "")
    body = part.get("body", {})
    attachment_id = body.get("attachmentId")
    body_data = body.get("data")
    is_pdf = mime_type == "application/pdf" or filename.lower().endswith(".pdf")

    if filename and is_pdf and (attachment_id or body_data):
        attachments.append(
            {
                "filename": filename,
                "attachment_id": attachment_id,
                "body_data": body_data,
                "mime_type": mime_type,
            }
        )

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


def _mark_email_processed(
    gc: GoogleClient,
    message_id: str,
    label_id: str,
    *,
    remove_labels: list[str] | None = None,
) -> None:
    """Add the DD-Processed label and remove UNREAD."""
    gc.gmail_modify_labels(
        message_id,
        add_labels=[label_id],
        remove_labels=remove_labels or ["UNREAD"],
    )
    logger.info("Marked email %s as processed", message_id)


def _mark_email_for_review(
    gc: GoogleClient,
    message_id: str,
    processed_label_id: str,
    review_label_id: str,
    *,
    include_processed_label: bool = True,
) -> None:
    """Mark an email as needing manual review while suppressing reprocessing."""
    add_labels = [review_label_id]
    if include_processed_label:
        add_labels.insert(0, processed_label_id)
    gc.gmail_modify_labels(
        message_id,
        add_labels=add_labels,
        remove_labels=[],
    )
    logger.info("Marked email %s for manual review", message_id)


def _get_attachment_bytes(
    gc: GoogleClient,
    message_id: str,
    attachment: dict[str, Any],
) -> bytes:
    """Return attachment bytes from either attachmentId or inline data."""
    attachment_id = attachment.get("attachment_id")
    if attachment_id:
        return gc.gmail_get_attachment(message_id, attachment_id)
    body_data = attachment.get("body_data", "")
    if isinstance(body_data, str) and body_data:
        return base64.urlsafe_b64decode(body_data)
    raise RuntimeError(f"Attachment '{attachment.get('filename', '')}' had no retrievable bytes")


def build_scan_summary(results: dict[str, Any]) -> str:
    """Build a human-readable summary for Google Chat notification."""
    lines = [
        "Inbox Scanner Summary",
        f"  Emails found: {results['emails_found']}",
        f"  Emails processed: {results['emails_processed']}",
        f"  Attachments uploaded: {results['attachments_uploaded']}",
        f"  Attachments skipped: {results['attachments_skipped']}",
    ]
    internal = results.get("internal_skipped", 0)
    if internal:
        lines.append(f"  Internal sender skipped: {internal}")

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
