from __future__ import annotations

from due_diligence_reporter.portfolio_gap_notifications import (
    format_portfolio_gap_chat_message,
    post_portfolio_gap_chat_summary,
)


def _snapshot(*, sites_with_gaps: int = 1) -> dict:
    return {
        "totals": {
            "sites": 2,
            "sites_with_gaps": sites_with_gaps,
            "missing_p1_dri": 1,
            "missing_drive_folder": 1,
            "missing_required_documents": 1,
            "open_automation_failures": 1,
            "pending_review_tasks": 1,
        },
        "sites": [
            {
                "site_name": "Alpha Tulsa 6940 S Utica Ave",
                "gap_count": sites_with_gaps,
                "gap_reasons": [
                    "missing_p1_dri",
                    "missing_drive_folder",
                    "missing_current_milestone_documents",
                ],
            }
        ]
        if sites_with_gaps
        else [],
    }


def test_format_portfolio_gap_chat_message_summarizes_counts_and_top_sites() -> None:
    message = format_portfolio_gap_chat_message(_snapshot(), run_url="https://actions/run/1")

    assert "Portfolio automation gaps need review" in message
    assert "Sites with gaps: 1 / 2" in message
    assert "missing P1 DRI=1" in message
    assert "missing current-milestone docs=1" in message
    assert "Run: https://actions/run/1" in message
    assert (
        "Alpha Tulsa 6940 S Utica Ave: missing P1 DRI, missing Drive folder, "
        "missing current-milestone docs"
    ) in message


def test_post_portfolio_gap_chat_summary_skips_when_clean() -> None:
    result = post_portfolio_gap_chat_summary(
        _snapshot(sites_with_gaps=0),
        webhook_urls="https://chat.example/hook",
    )

    assert result == {"status": "skipped", "reason": "no_gaps"}


def test_post_portfolio_gap_chat_summary_skips_missing_webhook() -> None:
    result = post_portfolio_gap_chat_summary(_snapshot(), webhook_urls="")

    assert result == {"status": "skipped", "reason": "missing_google_chat_webhook_url"}


def test_post_portfolio_gap_chat_summary_posts_to_configured_webhook() -> None:
    posts: list[tuple[str, str]] = []

    result = post_portfolio_gap_chat_summary(
        _snapshot(),
        webhook_urls="https://chat.example/hook",
        run_url="https://actions/run/1",
        post_message=lambda url, text: posts.append((url, text)),
    )

    assert result == {"status": "sent", "posted": 1, "sites_with_gaps": 1}
    assert posts[0][0] == "https://chat.example/hook"
    assert "Portfolio automation gaps need review" in posts[0][1]
