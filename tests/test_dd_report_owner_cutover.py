"""Tests for the DD_REPORT_OWNER cutover flag.

Phase B-PR3 of the DDR -> DD Pipeline migration. Mirrors the existing
``DASHBOARD_PUBLISH_OWNER`` flag (Phase A5) one-to-one — the reporter
must keep generating DD reports by default and only yield to the
alpha-dd-pipeline WU-13 when an operator explicitly flips ownership.
"""
from __future__ import annotations

from unittest.mock import patch

from due_diligence_reporter.report_pipeline import (
    PipelineResult,
    _run_pipeline_agent,
)


class TestDdReportOwnerCutover:
    """The flag is read on every call (not cached) so a flip is live."""

    @staticmethod
    def _run(env: dict[str, str]) -> tuple[
        dict[str, object] | None,
        PipelineResult | None,
        object,
    ]:
        """Run ``_run_pipeline_agent`` under ``env``.

        Returns ``(agent_result, pipeline_result, run_dd_mock)`` so callers
        can assert both the function's return value and whether the
        Anthropic-backed agent was invoked.
        """
        with patch.dict("os.environ", env, clear=False), patch(
            "due_diligence_reporter.report_pipeline.run_dd_report_agent",
        ) as run_dd_mock:
            run_dd_mock.return_value = {
                "success": True,
                "doc_id": "doc-123",
                "doc_url": "https://docs.google.com/document/d/doc-123",
                "trace": None,
            }
            settings = type("S", (), {"anthropic_report_model": "claude-test"})()
            agent_result, pipeline_result = _run_pipeline_agent(
                "Acme Boca Raton 2200",
                "system prompt",
                settings,  # type: ignore[arg-type]
            )
        return agent_result, pipeline_result, run_dd_mock

    def test_owner_pipeline_short_circuits_without_invoking_agent(self) -> None:
        """owner=pipeline → ``yielded_to_pipeline`` result, no agent call."""
        agent_result, pipeline_result, run_dd_mock = self._run(
            {"DD_REPORT_OWNER": "pipeline"},
        )
        assert agent_result is None
        assert pipeline_result is not None
        assert pipeline_result.status == "yielded_to_pipeline"
        assert pipeline_result.site_title == "Acme Boca Raton 2200"
        run_dd_mock.assert_not_called()

    def test_owner_pipeline_is_case_and_whitespace_tolerant(self) -> None:
        """Operators frequently mis-case or pad env values; tolerate it.

        Mirrors the DASHBOARD_PUBLISH_OWNER policy so cross-flag behavior
        is identical and operators can flip both flags from the same
        runbook without separate normalization rules.
        """
        for value in ("PIPELINE", "Pipeline", "  pipeline  ", "pipeline\n"):
            agent_result, pipeline_result, run_dd_mock = self._run(
                {"DD_REPORT_OWNER": value},
            )
            assert agent_result is None, f"value={value!r} should short-circuit"
            assert pipeline_result is not None
            assert pipeline_result.status == "yielded_to_pipeline", (
                f"value={value!r} should yield"
            )
            run_dd_mock.assert_not_called()

    def test_owner_unset_runs_agent_normally(self) -> None:
        """Default (env unset) must behave exactly like ``reporter``.

        Critical for rollout: deploying the cutover code WITHOUT setting
        the env var must not change current production behavior.
        """
        with patch.dict("os.environ", {}, clear=True), patch(
            "due_diligence_reporter.report_pipeline.run_dd_report_agent",
        ) as run_dd_mock:
            run_dd_mock.return_value = {
                "success": True,
                "doc_id": "doc-456",
                "doc_url": "https://docs.google.com/document/d/doc-456",
                "trace": None,
            }
            settings = type("S", (), {"anthropic_report_model": "claude-test"})()
            agent_result, pipeline_result = _run_pipeline_agent(
                "Acme Boca Raton 2200",
                "system prompt",
                settings,  # type: ignore[arg-type]
            )
        assert pipeline_result is None
        assert agent_result is not None
        assert agent_result["doc_id"] == "doc-456"
        run_dd_mock.assert_called_once()

    def test_owner_reporter_explicit_runs_agent_normally(self) -> None:
        """owner=reporter (explicit) preserves legacy behavior."""
        agent_result, pipeline_result, run_dd_mock = self._run(
            {"DD_REPORT_OWNER": "reporter"},
        )
        assert pipeline_result is None
        assert agent_result is not None
        assert agent_result["doc_id"] == "doc-123"
        run_dd_mock.assert_called_once()

    def test_unknown_owner_value_runs_agent_normally(self) -> None:
        """Unrecognized values are treated as ``reporter`` (fail-safe).

        We never want a typo on the env var to silently kill DD-report
        generation. Only the literal string ``pipeline`` (case-insensitive,
        whitespace-tolerant) yields.
        """
        agent_result, pipeline_result, run_dd_mock = self._run(
            {"DD_REPORT_OWNER": "pipline"},  # typo
        )
        assert pipeline_result is None
        assert agent_result is not None
        run_dd_mock.assert_called_once()
