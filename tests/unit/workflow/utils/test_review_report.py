"""Unit tests for review exhaustion reporting utility."""

from unittest.mock import MagicMock

import pytest

from forge.observability.review_poller import ReviewCycleData
from forge.sandbox.runner import ContainerResult
from forge.workflow.nodes.pr_creation import _format_review_exhaustion_section
from forge.workflow.utils.review_report import collect_review_exhaustion


class TestReviewExhausted:
    """Tests for ContainerResult.review_exhausted property."""

    def test_no_cycles_not_exhausted(self):
        result = ContainerResult(success=True, exit_code=0, stdout="", stderr="")
        assert result.review_exhausted is False

    def test_approved_not_exhausted(self):
        result = ContainerResult(
            success=True, exit_code=0, stdout="", stderr="",
            review_cycles=[
                ReviewCycleData(
                    cycle=1, max_cycles=3, verdict="approved",
                    feedback="", skill="test", elapsed_seconds=1.0, timestamp="",
                ),
            ],
        )
        assert result.review_exhausted is False

    def test_rejected_below_max_not_exhausted(self):
        result = ContainerResult(
            success=True, exit_code=0, stdout="", stderr="",
            review_cycles=[
                ReviewCycleData(
                    cycle=1, max_cycles=3, verdict="rejected",
                    feedback="fix it", skill="test", elapsed_seconds=1.0, timestamp="",
                ),
            ],
        )
        assert result.review_exhausted is False

    def test_rejected_at_max_is_exhausted(self):
        result = ContainerResult(
            success=True, exit_code=0, stdout="", stderr="",
            review_cycles=[
                ReviewCycleData(
                    cycle=1, max_cycles=2, verdict="rejected",
                    feedback="fix it", skill="test", elapsed_seconds=1.0, timestamp="",
                ),
                ReviewCycleData(
                    cycle=2, max_cycles=2, verdict="rejected",
                    feedback="still broken", skill="test", elapsed_seconds=1.0, timestamp="",
                ),
            ],
        )
        assert result.review_exhausted is True

    def test_approved_at_max_not_exhausted(self):
        result = ContainerResult(
            success=True, exit_code=0, stdout="", stderr="",
            review_cycles=[
                ReviewCycleData(
                    cycle=1, max_cycles=2, verdict="rejected",
                    feedback="fix it", skill="test", elapsed_seconds=1.0, timestamp="",
                ),
                ReviewCycleData(
                    cycle=2, max_cycles=2, verdict="approved",
                    feedback="", skill="test", elapsed_seconds=1.0, timestamp="",
                ),
            ],
        )
        assert result.review_exhausted is False


class TestCollectReviewExhaustion:
    """Tests for collect_review_exhaustion utility."""

    def test_returns_none_when_not_exhausted(self):
        result = ContainerResult(success=True, exit_code=0, stdout="", stderr="")
        assert collect_review_exhaustion(result, "TASK-1", "implement_task") is None

    def test_returns_none_when_approved(self):
        result = ContainerResult(
            success=True, exit_code=0, stdout="", stderr="",
            review_cycles=[
                ReviewCycleData(
                    cycle=1, max_cycles=3, verdict="approved",
                    feedback="", skill="implement-task",
                    elapsed_seconds=5.0, timestamp="",
                ),
            ],
        )
        assert collect_review_exhaustion(result, "TASK-1", "implement_task") is None

    def test_returns_dict_when_exhausted(self):
        result = ContainerResult(
            success=True, exit_code=0, stdout="", stderr="",
            review_cycles=[
                ReviewCycleData(
                    cycle=1, max_cycles=2, verdict="rejected",
                    feedback="missing tests", skill="implement-task",
                    elapsed_seconds=5.0, timestamp="",
                ),
                ReviewCycleData(
                    cycle=2, max_cycles=2, verdict="rejected",
                    feedback="still missing tests", skill="implement-task",
                    elapsed_seconds=3.0, timestamp="",
                ),
            ],
        )
        result_tuple = collect_review_exhaustion(result, "AISOS-2053", "implement_task")

        assert result_tuple is not None
        key, entry = result_tuple
        assert key == "AISOS-2053__implement_task"
        assert entry["task_key"] == "AISOS-2053"
        assert entry["step_name"] == "implement_task"
        assert entry["skill"] == "implement-task"
        assert entry["max_retries"] == 2
        assert entry["final_feedback"] == "still missing tests"
        assert len(entry["cycles"]) == 2
        assert entry["cycles"][0]["verdict"] == "rejected"
        assert entry["cycles"][1]["feedback"] == "still missing tests"


class TestFormatReviewExhaustionSection:
    """Tests for _format_review_exhaustion_section."""

    def test_empty_report_returns_empty(self):
        assert _format_review_exhaustion_section({}) == ""

    def test_single_entry(self):
        report = {
            "AISOS-2053__implement_task": {
                "task_key": "AISOS-2053",
                "step_name": "implement_task",
                "skill": "implement-task",
                "max_retries": 3,
                "final_feedback": "Missing test coverage",
                "cycles": [
                    {"cycle": 1, "verdict": "rejected", "feedback": "No tests"},
                    {"cycle": 2, "verdict": "rejected", "feedback": "Still no tests"},
                    {"cycle": 3, "verdict": "rejected", "feedback": "Missing test coverage"},
                ],
            },
        }
        section = _format_review_exhaustion_section(report)

        assert "Auto-Review Notes" in section
        assert "implement_task — AISOS-2053" in section
        assert "implement-task" in section
        assert "3/3 exhausted" in section
        assert "Missing test coverage" in section

    def test_multiple_entries(self):
        report = {
            "AISOS-2053__implement_task": {
                "task_key": "AISOS-2053",
                "step_name": "implement_task",
                "skill": "implement-task",
                "max_retries": 2,
                "final_feedback": "No docstrings",
                "cycles": [],
            },
            "AISOS-2055__implement_task": {
                "task_key": "AISOS-2055",
                "step_name": "implement_task",
                "skill": "implement-task",
                "max_retries": 2,
                "final_feedback": "Bare except",
                "cycles": [],
            },
        }
        section = _format_review_exhaustion_section(report)

        assert "AISOS-2053" in section
        assert "AISOS-2055" in section
        assert "No docstrings" in section
        assert "Bare except" in section

    def test_multiline_feedback(self):
        report = {
            "TASK-1__local_review": {
                "task_key": "TASK-1",
                "step_name": "local_review",
                "skill": "local-code-review",
                "max_retries": 1,
                "final_feedback": "Line 1\nLine 2\nLine 3",
                "cycles": [],
            },
        }
        section = _format_review_exhaustion_section(report)

        assert "> Line 1" in section
        assert "> Line 2" in section
        assert "> Line 3" in section
