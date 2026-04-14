"""Utility functions for text extraction and URL parsing."""

from __future__ import annotations

import html
import logging
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

import requests as _requests

logger = logging.getLogger("[utils]")


def escape_html_text(value: str) -> str:
    """Escape a string for safe HTML rendering."""
    return html.escape(value, quote=True)


def sanitize_http_url(url: str) -> str | None:
    """Return a safe http/https URL or None when invalid."""
    trimmed = url.strip()
    if not trimmed:
        return None
    parsed = urlparse(trimmed)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return trimmed


def escape_drive_query_literal(value: str) -> str:
    """Escape a string for a single-quoted Google Drive query literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def extract_folder_id_from_url(url: str) -> str | None:
    """
    Extract the Google Drive folder ID from a Drive folder URL.

    Supports formats:
    - https://drive.google.com/drive/folders/FOLDER_ID
    - https://drive.google.com/drive/u/0/folders/FOLDER_ID
    - https://drive.google.com/open?id=FOLDER_ID
    - HTML anchor tags wrapping any of the above (from Wrike rich-text fields)

    Returns the folder ID string, or None if not parseable.
    """
    # If the value is an HTML anchor tag, extract the href first
    href_match = re.search(r'href="([^"]+)"', url)
    if href_match:
        url = href_match.group(1).replace("&amp;", "&")

    # Match /folders/<ID> anywhere in the URL
    match = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
    if match:
        folder_id = match.group(1)
        logger.debug("Extracted folder ID from URL: %s", folder_id)
        return folder_id

    # Match ?id=<ID> (from /open?id=... links)
    id_match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if id_match:
        folder_id = id_match.group(1)
        logger.debug("Extracted folder ID from ?id= param: %s", folder_id)
        return folder_id

    logger.warning("Could not extract folder ID from URL: %s", url)
    return None


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """
    Extract plain text from PDF bytes using pypdf.

    Returns extracted text (may be empty for image-only PDFs).
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.error("pypdf not installed; cannot extract PDF text")
        return ""

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        pages_text: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages_text.append(text)

        result = "\n\n".join(pages_text)
        logger.info(
            "Extracted %d characters from %d PDF pages", len(result), len(reader.pages)
        )
        return result
    except Exception as e:
        logger.error("Failed to extract text from PDF: %s", e)
        return ""


def _iter_paragraphs(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Yield all paragraph dicts from a Google Docs content tree.

    Recurses into tables (tableRows → tableCells → content) so that
    paragraphs inside table cells are included alongside top-level ones.
    """
    paragraphs: list[dict[str, Any]] = []
    for element in elements:
        if "paragraph" in element:
            paragraphs.append(element["paragraph"])
        elif "table" in element:
            for row in element["table"].get("tableRows", []):
                for cell in row.get("tableCells", []):
                    paragraphs.extend(_iter_paragraphs(cell.get("content", [])))
    return paragraphs


def find_text_index_in_doc(doc_body: dict[str, Any], search_text: str) -> int | None:
    """Find the start character index of ``search_text`` in a Google Docs body.

    Concatenates all textRun content within each paragraph before searching,
    so URLs or other strings split across multiple consecutive textRun
    elements are still found.  Recurses into tables so that text inside
    table cells is also searchable.  Returns the absolute start index or
    ``None``.
    """
    for paragraph in _iter_paragraphs(doc_body.get("content", [])):
        # Collect all textRun elements with their start indices
        runs: list[tuple[int, str]] = []
        for pe in paragraph.get("elements", []):
            text_run = pe.get("textRun")
            if text_run:
                runs.append((pe.get("startIndex", 0), text_run.get("content", "")))
        if not runs:
            continue
        # Concatenate and search the full paragraph text
        para_start = runs[0][0]
        full_text = "".join(content for _, content in runs)
        offset = full_text.find(search_text)
        if offset >= 0:
            return para_start + offset

    return None


def flatten_report_data_for_replacement(
    report_data: dict[str, Any], prefix: str = ""
) -> dict[str, str]:
    """
    Flatten a nested report_data dict into a mapping of {{PLACEHOLDER}} -> value.

    Nested keys are joined with dots: report_data["q1"]["rating"] -> {{q1.rating}}
    All values are converted to strings. None values become empty strings.
    Lists are joined with ", ".
    """
    result: dict[str, str] = {}

    for key, value in report_data.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if isinstance(value, dict):
            nested = flatten_report_data_for_replacement(value, prefix=full_key)
            result.update(nested)
        elif isinstance(value, list):
            result[full_key] = "\n".join(str(item) for item in value)
        elif value is None:
            result[full_key] = ""
        else:
            result[full_key] = str(value)

    return result


def send_email(
    sender: str,
    app_password: str,
    recipients: list[str],
    subject: str,
    html_body: str,
) -> None:
    """Send an HTML email via Gmail SMTP using an App Password.

    Args:
        sender: Gmail address to send from.
        app_password: Gmail App Password for the sender account.
        recipients: List of recipient email addresses.
        subject: Email subject line.
        html_body: HTML email body content.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html"))

    logger.info("Sending email to %d recipients: %s", len(recipients), subject)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, app_password)
        server.sendmail(sender, recipients, msg.as_string())
    logger.info("Email sent successfully")


def post_google_chat_message(webhook_url: str, text: str) -> None:
    """Post a message to a Google Chat space via incoming webhook.

    Args:
        webhook_url: Google Chat incoming webhook URL.
        text: Message text (supports basic markdown).
    """
    logger.info("Posting Google Chat message (%d chars)", len(text))
    resp = _requests.post(
        webhook_url,
        json={"text": text},
        timeout=10,
    )
    resp.raise_for_status()
    logger.info("Google Chat message posted successfully")


def build_replace_all_text_requests(
    replacements: dict[str, str],
) -> list[dict[str, Any]]:
    """
    Build a list of Google Docs API replaceAllText requests from a replacements mapping.

    Keys should NOT include the {{ }} delimiters — this function adds them.

    Args:
        replacements: dict of placeholder_key -> replacement_value

    Returns:
        List of replaceAllText request dicts for batchUpdate
    """
    requests_list: list[dict[str, Any]] = []

    for placeholder, value in replacements.items():
        requests_list.append(
            {
                "replaceAllText": {
                    "containsText": {
                        "text": f"{{{{{placeholder}}}}}",
                        "matchCase": True,
                    },
                    "replaceText": value,
                }
            }
        )

    return requests_list


class HyperlinkResult:
    """Result of building hyperlink requests, with per-token outcomes."""

    __slots__ = ("requests", "found_tokens", "not_found_tokens")

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.found_tokens: list[str] = []
        self.not_found_tokens: list[str] = []


def build_hyperlink_requests(
    doc_body: dict[str, Any],
    replacements: dict[str, str],
    link_tokens: frozenset[str],
    display_labels: dict[str, str] | None = None,
) -> HyperlinkResult:
    """Build ``updateTextStyle`` requests to hyperlink text in the doc.

    For each token in *link_tokens* whose replacement value starts with
    ``http``, finds the corresponding text in *doc_body* and returns an
    ``updateTextStyle`` request that sets ``link.url`` on that range.

    When *display_labels* is provided, searches for the label text instead
    of the raw URL (the label was already inserted by ``replaceAllText``).
    The URL from *replacements* is still used as the link target.

    Must be called **after** ``replaceAllText`` has been applied — pass a
    fresh ``doc_body`` from ``get_document()``.

    Returns a :class:`HyperlinkResult` with the requests list plus
    ``found_tokens`` and ``not_found_tokens`` for diagnostics.
    """
    result = HyperlinkResult()

    for token in link_tokens:
        url = replacements.get(token, "")
        if not url.startswith("http"):
            continue

        search_text = (display_labels or {}).get(token, url)
        start_idx = find_text_index_in_doc(doc_body, search_text)
        if start_idx is None:
            logger.warning(
                "Hyperlink: text for token '%s' not found in doc body "
                "(search=%r, url=%s)",
                token, search_text, url[:80],
            )
            result.not_found_tokens.append(token)
            continue

        logger.debug(
            "Hyperlink: found token '%s' at index %d (search=%r, url=%s)",
            token, start_idx, search_text, url[:80],
        )
        result.found_tokens.append(token)
        result.requests.append({
            "updateTextStyle": {
                "range": {
                    "startIndex": start_idx,
                    "endIndex": start_idx + len(search_text),
                },
                "textStyle": {
                    "link": {"url": url},
                },
                "fields": "link",
            }
        })

    return result
