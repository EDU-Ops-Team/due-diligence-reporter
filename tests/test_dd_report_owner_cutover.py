"""Tests for the retired DD_REPORT_OWNER cutover flag."""

from __future__ import annotations

from unittest.mock import patch

from due_diligence_reporter.report_pipeline import (
    PipelineResult,
    _run_pipeline_agent,
)


class TestDdReportOwnerCutover:
    """Legacy owner values must not yield M2 execution out of this repo."""

    @staticmethod
    def _run(env: dict[str, str]) -> tuple[
        dict[str, object] | None,
        PipelineResult | None,
        object,
    ]:
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

    def test_owner_pipeline_is_ignored_and_runs_agent(self) -> None:
        agent_result, pipeline_result, run_dd_mock = self._run(
            {"DD_REPORT_OWNER": "pipeline"},
        )
        assert pipeline_result is None
        assert agent_result is not None
        assert agent_result["doc_id"] == "doc-123"
        run_dd_mock.assert_called_once()

    def test_owner_pipeline_variants_are_ignored_and_run_agent(self) -> None:
        for value in ("PIPELINE", "Pipeline", "  pipeline  ", "pipeline\n"):
            agent_result, pipeline_result, run_dd_mock = self._run(
                {"DD_REPORT_OWNER": value},
            )
            assert pipeline_result is None
            assert agent_result is not None, f"value={value!r} should still run"
            run_dd_mock.assert_called_once()

    def test_owner_unset_runs_agent_normally(self) -> None:
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
        agent_result, pipeline_result, run_dd_mock = self._run(
            {"DD_REPORT_OWNER": "reporter"},
        )
        assert pipeline_result is None
        assert agent_result is not None
        assert agent_result["doc_id"] == "doc-123"
        run_dd_mock.assert_called_once()

    def test_unknown_owner_value_runs_agent_normally(self) -> None:
        agent_result, pipeline_result, run_dd_mock = self._run(
            {"DD_REPORT_OWNER": "pipline"},
        )
        assert pipeline_result is None
        assert agent_result is not None
        run_dd_mock.assert_called_once()
