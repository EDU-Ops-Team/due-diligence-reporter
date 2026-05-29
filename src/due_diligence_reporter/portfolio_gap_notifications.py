"""Google Chat notifications for portfolio automation gap snapshots."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .rhodes_events import post_google_chat_to_configured_webhooks

PostMessage = Callable[[str, str], None]


def format_portfolio_gap_chat_message(
    snapshot: dict[str, Any],
    *,
    run_url: str = "",
    max_sites: int = 5,
) -> str:
    """Return a compact operator-facing Chat message for a gap snapshot."""

    totals = _dict(snapshot.get("totals"))
    sites = [_dict(site) for site in _list(snapshot.get("sites"))]
    sites_with_gaps = _int(totals.get("sites_with_gaps"))
    total_sites = _int(totals.get("sites"))

    lines = [
        "Portfolio automation gaps need review",
        f"Sites with gaps: {sites_with_gaps} / {total_sites}",
        (
            "Counts: "
            f"missing P1 DRI={_int(totals.get('missing_p1_dri'))}; "
            f"missing Drive folder={_int(totals.get('missing_drive_folder'))}; "
            f"missing current-milestone docs={_int(totals.get('missing_required_documents'))}; "
            f"open automation failures={_int(totals.get('open_automation_failures'))}; "
            f"pending review tasks={_int(totals.get('pending_review_tasks'))}"
        ),
    ]
    if run_url.strip():
        lines.append(f"Run: {run_url.strip()}")

    gap_sites = [site for site in sites if _int(site.get("gap_count")) > 0 or site.get("errors")]
    if gap_sites:
        lines.append("Top sites:")
        for site in gap_sites[:max_sites]:
            reasons = ", ".join(_reason_label(str(reason)) for reason in _list(site.get("gap_reasons")))
            if not reasons:
                reasons = "snapshot_read_errors"
            lines.append(f"- {site.get('site_name') or 'Unknown site'}: {reasons}")
    return "\n".join(lines)


def post_portfolio_gap_chat_summary(
    snapshot: dict[str, Any],
    *,
    webhook_urls: str,
    run_url: str = "",
    max_sites: int = 5,
    post_message: PostMessage | None = None,
) -> dict[str, Any]:
    """Post a Chat summary when the snapshot contains portfolio gaps."""

    totals = _dict(snapshot.get("totals"))
    if _int(totals.get("sites_with_gaps")) <= 0:
        return {"status": "skipped", "reason": "no_gaps"}
    if not webhook_urls.strip():
        return {"status": "skipped", "reason": "missing_google_chat_webhook_url"}

    text = format_portfolio_gap_chat_message(
        snapshot,
        run_url=run_url,
        max_sites=max_sites,
    )
    kwargs: dict[str, Any] = {}
    if post_message is not None:
        kwargs["post_message"] = post_message
    result = post_google_chat_to_configured_webhooks(webhook_urls, text, **kwargs)
    result["sites_with_gaps"] = _int(totals.get("sites_with_gaps"))
    return result


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _reason_label(reason: str) -> str:
    labels = {
        "missing_p1_dri": "missing P1 DRI",
        "missing_drive_folder": "missing Drive folder",
        "missing_current_milestone_documents": "missing current-milestone docs",
        "open_automation_failures": "open automation failures",
        "pending_review_tasks": "pending review tasks",
        "snapshot_read_errors": "snapshot read errors",
    }
    return labels.get(reason, reason)
