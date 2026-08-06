"""Tests for forge:direct-mode direct ticket creation mode."""

from unittest.mock import MagicMock

import pytest

from forge.workflow.feature.state import create_initial_feature_state


class TestDirectModeDefaultsToFalse:
    def test_feature_state_direct_mode_defaults_false(self):
        state = create_initial_feature_state("TEST-1")
        assert state.get("direct_mode") is False

    def test_feature_state_direct_mode_can_be_set_true(self):
        state = create_initial_feature_state("TEST-1", direct_mode=True)
        assert state["direct_mode"] is True


class TestBuildInitialStateDirectMode:
    """Tests for direct_mode initialization from Jira payload."""

    def _make_worker(self):
        from forge.orchestrator.worker import OrchestratorWorker

        worker = OrchestratorWorker.__new__(OrchestratorWorker)
        worker.settings = MagicMock()
        worker.router = MagicMock()
        return worker

    def _make_message(self, labels: list):
        from forge.models.events import EventSource

        msg = MagicMock()
        msg.ticket_key = "TEST-1"
        msg.source = EventSource.JIRA
        msg.event_type = "jira:issue_updated"
        msg.event_id = "evt-1"
        msg.retry_count = 0
        msg.payload = {
            "issue": {
                "fields": {
                    "issuetype": {"name": "Feature"},
                    "labels": labels,
                }
            }
        }
        return msg

    def test_direct_mode_true_when_label_present(self):
        worker = self._make_worker()
        msg = self._make_message(["forge:managed", "forge:direct-mode"])
        state = worker._build_initial_state(msg)
        assert state["direct_mode"] is True

    def test_direct_mode_false_when_label_absent(self):
        worker = self._make_worker()
        msg = self._make_message(["forge:managed"])
        state = worker._build_initial_state(msg)
        assert state["direct_mode"] is False


class TestDirectModeApprovalGates:
    """Each approval gate routing function pauses when direct_mode=True and yolo_mode=False."""

    def _feature_state(self, current_node: str, extra: dict = None) -> dict:
        if extra is None:
            extra = {}
        state = create_initial_feature_state("TEST-1")
        state["current_node"] = current_node
        state["is_paused"] = True
        state["direct_mode"] = True
        state["yolo_mode"] = False
        state.update(extra)
        return state

    @pytest.mark.asyncio
    async def test_plan_route_pauses_in_direct_mode(self):
        from langgraph.graph import END

        from forge.workflow.gates.plan_approval import route_plan_approval

        state = self._feature_state("plan_approval_gate", {"epic_keys": ["EPIC-1"]})
        assert await route_plan_approval(state) == END

    @pytest.mark.asyncio
    async def test_task_route_pauses_in_direct_mode(self):
        from langgraph.graph import END

        from forge.workflow.gates.task_approval import route_task_approval

        state = self._feature_state("task_approval_gate", {"task_keys": ["TASK-1"]})
        assert await route_task_approval(state) == END
