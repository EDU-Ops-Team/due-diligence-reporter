"""Tests for the report generation provenance trace."""

from __future__ import annotations

import json

from due_diligence_reporter.report_pipeline import (
    ReportTrace,
    TraceEvent,
    _sanitize_input,
    _summarize_tool_output,
)


class TestTraceEvent:
    """Basic TraceEvent construction."""

    def test_defaults(self):
        e = TraceEvent(timestamp="2026-03-19T12:00:00Z", event_type="tool_call")
        assert e.tool_name == ""
        assert e.input_summary == {}
        assert e.output_summary == {}
        assert e.duration_ms == 0
        assert e.error is None


class TestReportTrace:
    """ReportTrace serialization and event accumulation."""

    def test_to_dict_roundtrip(self):
        trace = ReportTrace(
            site_name="Alpha Keller",
            started_at="2026-03-19T12:00:00Z",
            prompt_version=2,
        )
        trace.add_event(TraceEvent(
            timestamp="2026-03-19T12:00:01Z",
            event_type="tool_call",
            tool_name="get_site_record",
            input_summary={"site_name_or_id": "Alpha Keller"},
            output_summary={"status": "success"},
            duration_ms=150,
        ))
        trace.ended_at = "2026-03-19T12:01:00Z"
        trace.total_duration_ms = 60000
        trace.final_status = "success"
        trace.doc_id = "abc123"
        trace.tokens_filled = 20
        trace.tokens_unfilled = 6

        d = trace.to_dict()

        # Roundtrip through JSON
        serialized = json.dumps(d)
        parsed = json.loads(serialized)

        assert parsed["site_name"] == "Alpha Keller"
        assert parsed["prompt_version"] == 2
        assert parsed["final_status"] == "success"
        assert parsed["doc_id"] == "abc123"
        assert parsed["tokens_filled"] == 20
        assert parsed["tokens_unfilled"] == 6
        assert parsed["event_count"] == 1
        assert len(parsed["events"]) == 1
        assert parsed["events"][0]["tool_name"] == "get_site_record"
        assert parsed["events"][0]["duration_ms"] == 150

    def test_empty_trace(self):
        trace = ReportTrace(site_name="Test", started_at="2026-01-01T00:00:00Z")
        d = trace.to_dict()
        assert d["events"] == []
        assert d["event_count"] == 0
        assert d["final_status"] == ""

    def test_multiple_events(self):
        trace = ReportTrace(site_name="Test", started_at="2026-01-01T00:00:00Z")
        for i in range(5):
            trace.add_event(TraceEvent(
                timestamp=f"2026-01-01T00:00:0{i}Z",
                event_type="tool_call",
                tool_name=f"tool_{i}",
            ))
        assert len(trace.events) == 5
        assert trace.to_dict()["event_count"] == 5


class TestSanitizeInput:
    """Input sanitization for trace logging."""

    def test_report_data_redacted(self):
        inp = {
            "site_name": "Alpha Keller",
            "report_data": {"meta": {}, "exec": {}, "sources": {}},
        }
        sanitized = _sanitize_input(inp)
        assert sanitized["site_name"] == "Alpha Keller"
        assert sanitized["report_data"] == "<3 top-level keys>"

    def test_long_content_truncated(self):
        inp = {"content": "x" * 500}
        sanitized = _sanitize_input(inp)
        assert len(sanitized["content"]) < 500
        assert "500 chars" in sanitized["content"]

    def test_short_values_preserved(self):
        inp = {"site_name": "Alpha Keller", "state": "TX"}
        sanitized = _sanitize_input(inp)
        assert sanitized == inp

    def test_long_string_truncated(self):
        inp = {"description": "a" * 1000}
        sanitized = _sanitize_input(inp)
        assert len(sanitized["description"]) < 1000
        assert sanitized["description"].endswith("...")


class TestSummarizeToolOutput:
    """Tool output summarization for trace logging."""

    def test_dict_with_status(self):
        result = {"status": "success", "message": "Done"}
        summary = _summarize_tool_output(result)
        assert summary["status"] == "success"
        assert summary["message"] == "Done"

    def test_dict_with_document(self):
        result = {
            "status": "success",
            "document": {"id": "abc", "url": "https://docs.google.com/abc"},
            "replacements_applied": 20,
            "unfilled_template_tokens": 6,
        }
        summary = _summarize_tool_output(result)
        assert summary["document"] == {"id": "abc", "url": "https://docs.google.com/abc"}
        assert summary["replacements_applied"] == 20
        assert summary["unfilled_template_tokens"] == 6

    def test_dict_with_files(self):
        result = {"status": "success", "files": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}
        summary = _summarize_tool_output(result)
        assert summary["file_count"] == 3

    def test_dict_with_content(self):
        result = {"status": "success", "content": "x" * 10000}
        summary = _summarize_tool_output(result)
        assert summary["content_length"] == 10000
        assert "content" not in summary or summary.get("content_length")

    def test_error_result(self):
        result = {"status": "error", "error": "Something broke"}
        summary = _summarize_tool_output(result)
        assert summary["error"] == "Something broke"

    def test_non_dict_result(self):
        summary = _summarize_tool_output("plain string result")
        assert summary["text"] == "plain string result"

    def test_long_message_truncated(self):
        result = {"status": "success", "message": "m" * 500}
        summary = _summarize_tool_output(result)
        assert len(summary["message"]) == 300

