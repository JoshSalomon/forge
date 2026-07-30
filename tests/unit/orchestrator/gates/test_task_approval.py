"""Unit tests for Task approval gate."""

import pytest
from langgraph.graph import END

from forge.models.workflow import TicketType
from forge.workflow.feature.state import create_initial_feature_state as create_initial_state
from forge.workflow.gates import route_task_approval, task_approval_gate


class TestTaskApprovalGate:
    """Tests for task_approval_gate node."""

    @pytest.fixture
    def task_pending_state(self):
        """State with Tasks pending approval."""
        state = create_initial_state(
            thread_id="test-thread",
            ticket_key="TEST-123",
            ticket_type=TicketType.FEATURE,
        )
        state["prd_content"] = "# PRD"
        state["spec_content"] = "# Spec"
        state["epic_keys"] = ["TEST-124"]
        state["task_keys"] = ["TEST-130", "TEST-131", "TEST-132"]
        state["current_node"] = "generate_tasks"
        return state

    def test_gate_pauses_workflow(self, task_pending_state):
        """Gate sets is_paused=True and updates current_node."""
        result = task_approval_gate(task_pending_state)

        assert result["is_paused"] is True
        assert result["current_node"] == "task_approval_gate"

    def test_gate_preserves_task_keys(self, task_pending_state):
        """Gate preserves existing task keys."""
        result = task_approval_gate(task_pending_state)

        assert result["task_keys"] == ["TEST-130", "TEST-131", "TEST-132"]


class TestRouteTaskApproval:
    """Tests for route_task_approval function."""

    @pytest.fixture
    def task_pending_state(self):
        """State with Tasks pending."""
        state = create_initial_state(
            thread_id="test-thread",
            ticket_key="TEST-123",
            ticket_type=TicketType.FEATURE,
        )
        state["prd_content"] = "# PRD"
        state["spec_content"] = "# Spec"
        state["epic_keys"] = ["TEST-124"]
        state["task_keys"] = ["TEST-130", "TEST-131"]
        state["current_node"] = "task_approval_gate"
        state["is_paused"] = True
        return state

    @pytest.mark.asyncio
    async def test_routes_to_task_router_on_approval(self, task_pending_state):
        """Approved Tasks routes to task router when not paused."""
        task_pending_state["is_paused"] = False

        result = await route_task_approval(task_pending_state)

        assert result == "task_router"

    @pytest.mark.asyncio
    async def test_routes_to_regenerate_all_on_feature_rejection(self, task_pending_state):
        """Full task rejection routes to regenerate all tasks."""
        task_pending_state["feedback_comment"] = "The task breakdown is too coarse."
        task_pending_state["revision_requested"] = True

        result = await route_task_approval(task_pending_state)

        assert result == "regenerate_all_tasks"

    @pytest.mark.asyncio
    async def test_routes_to_update_single_on_task_rejection(self, task_pending_state):
        """Single task rejection routes to update that task."""
        task_pending_state["current_task_key"] = "TEST-131"
        task_pending_state["feedback_comment"] = "Task 2 needs more detail."
        task_pending_state["revision_requested"] = True

        result = await route_task_approval(task_pending_state)

        assert result == "update_single_task"

    @pytest.mark.asyncio
    async def test_routes_to_regenerate_all_on_epic_sourced_rejection(self, task_pending_state):
        """Epic-sourced task feedback routes to regenerate_epic_tasks, not all tasks."""
        task_pending_state["current_epic_key"] = "TEST-124"
        task_pending_state["feedback_comment"] = "Revise the tasks for this epic."
        task_pending_state["revision_requested"] = True

        result = await route_task_approval(task_pending_state)

        assert result == "regenerate_epic_tasks"

    @pytest.mark.asyncio
    async def test_feature_level_rejection_still_regenerates_all(self, task_pending_state):
        """Feature-level feedback (no epic key) still routes to regenerate_all_tasks."""
        task_pending_state["current_epic_key"] = None
        task_pending_state["feedback_comment"] = "The whole task breakdown is wrong."
        task_pending_state["revision_requested"] = True

        result = await route_task_approval(task_pending_state)

        assert result == "regenerate_all_tasks"

    @pytest.mark.asyncio
    async def test_epic_rejection_with_empty_body_routes_to_regenerate_epic_tasks(
        self, task_pending_state
    ):
        """Empty-body '!' on an Epic must not fall through to task_router (approval)."""
        task_pending_state["current_epic_key"] = "TEST-124"
        task_pending_state["feedback_comment"] = ""
        task_pending_state["revision_requested"] = True

        result = await route_task_approval(task_pending_state)

        assert result == "regenerate_epic_tasks"

    @pytest.mark.asyncio
    async def test_routes_to_end_when_pending(self, task_pending_state):
        """Pending Tasks without feedback routes to END."""
        result = await route_task_approval(task_pending_state)

        assert result == END


class TestTaskQuestionRouting:
    """Tests for Q&A routing in Task approval gate."""

    @pytest.fixture
    def task_pending_state(self):
        """State with Tasks pending."""
        state = create_initial_state(
            thread_id="test-thread",
            ticket_key="TEST-123",
            ticket_type=TicketType.FEATURE,
        )
        state["prd_content"] = "# PRD"
        state["spec_content"] = "# Spec"
        state["epic_keys"] = ["TEST-124"]
        state["task_keys"] = ["TEST-130", "TEST-131"]
        state["current_node"] = "task_approval_gate"
        state["is_paused"] = False
        return state

    @pytest.mark.asyncio
    async def test_routes_to_answer_question_when_is_question(self, task_pending_state):
        """Questions route to answer_question node."""
        task_pending_state["is_question"] = True
        task_pending_state["feedback_comment"] = "?Why are there two tasks for this?"

        result = await route_task_approval(task_pending_state)

        assert result == "answer_question"

    @pytest.mark.asyncio
    async def test_question_takes_priority_over_revision(self, task_pending_state):
        """Question routing takes priority over revision routing."""
        task_pending_state["is_question"] = True
        task_pending_state["revision_requested"] = True
        task_pending_state["feedback_comment"] = "?What's the testing strategy?"

        result = await route_task_approval(task_pending_state)

        assert result == "answer_question"

    @pytest.mark.asyncio
    async def test_routes_to_regenerate_when_feedback_not_question(self, task_pending_state):
        """Normal feedback routes to regenerate all tasks."""
        task_pending_state["is_question"] = False
        task_pending_state["revision_requested"] = True
        task_pending_state["feedback_comment"] = "Add more tasks for testing"

        result = await route_task_approval(task_pending_state)

        assert result == "regenerate_all_tasks"

    @pytest.mark.asyncio
    async def test_question_without_feedback_does_not_route_to_answer(self, task_pending_state):
        """is_question alone without feedback_comment doesn't route to answer."""
        task_pending_state["is_question"] = True
        task_pending_state["feedback_comment"] = ""

        result = await route_task_approval(task_pending_state)

        # Should proceed to task_router since not paused
        assert result == "task_router"


class TestTaskDraftProvisioning:
    """Tests for draft-based ticket provisioning in route_task_approval."""

    @pytest.fixture
    def approved_task_state(self):
        """Approved task state waiting for ticket creation."""
        state = create_initial_state(
            thread_id="test-thread",
            ticket_key="TEST-123",
            ticket_type=TicketType.FEATURE,
        )
        state["is_paused"] = False
        state["epic_keys"] = ["EPIC-124"]
        state["task_keys"] = []
        return state

    @pytest.mark.asyncio
    async def test_successful_draft_provisioning(self, approved_task_state):
        """Verify successful download, parsing, skipping excluded tasks, and deletion on success."""
        from datetime import UTC, datetime
        from unittest.mock import AsyncMock, patch

        from forge.models.draft import DraftItem, ForgeDecompositionDraft

        draft_item_1 = DraftItem(
            id=1,
            summary="Task One",
            description="Details of task 1",
            repo="org/repo-1",
            acceptance_criteria=[],
            excluded=False,
            epic_key="EPIC-124",
        )
        draft_item_2 = DraftItem(
            id=2,
            summary="Task Two",
            description="Details of task 2",
            repo="org/repo-2",
            acceptance_criteria=[],
            excluded=True,  # Excluded!
            epic_key="EPIC-124",
        )
        draft = ForgeDecompositionDraft(
            parent_key="TEST-123",
            phase="tasks",
            items=[draft_item_1, draft_item_2],
            version=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        with (
            patch("forge.integrations.jira.client.JiraClient") as MockJira,
            patch("forge.workflow.utils.draft_manager.DraftManager") as MockDraftManager,
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira

            mock_issue = AsyncMock()
            mock_issue.project_key = "TEST"
            mock_jira.get_issue = AsyncMock(return_value=mock_issue)
            mock_jira.create_task = AsyncMock(return_value="TASK-201")

            MockDraftManager.get_draft_attachment = AsyncMock(return_value=draft)
            MockDraftManager.delete_draft_attachment = AsyncMock()

            result = await route_task_approval(approved_task_state)

            assert result == "task_router"
            assert approved_task_state["task_keys"] == ["TASK-201"]
            assert approved_task_state["tasks_by_repo"] == {"org/repo-1": ["TASK-201"]}

            # Verify creations and exclusions
            mock_jira.create_task.assert_called_once_with(
                project_key="TEST",
                summary="Task One",
                description="Details of task 1",
                parent_key="EPIC-124",
                labels=["forge:managed", "forge:parent:TEST-123", "repo:org/repo-1"],
            )

            # Verify draft deleted
            MockDraftManager.delete_draft_attachment.assert_called_once()

    @pytest.mark.asyncio
    async def test_retains_draft_on_failure(self, approved_task_state):
        """Verify that draft attachment is not deleted if task creation fails midway."""
        from datetime import UTC, datetime
        from unittest.mock import AsyncMock, patch

        from forge.models.draft import DraftItem, ForgeDecompositionDraft

        draft_item_1 = DraftItem(
            id=1,
            summary="Task One",
            description="Details of task 1",
            repo="org/repo-1",
            acceptance_criteria=[],
            excluded=False,
            epic_key="EPIC-124",
        )
        draft = ForgeDecompositionDraft(
            parent_key="TEST-123",
            phase="tasks",
            items=[draft_item_1],
            version=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        with (
            patch("forge.integrations.jira.client.JiraClient") as MockJira,
            patch("forge.workflow.utils.draft_manager.DraftManager") as MockDraftManager,
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira

            mock_issue = AsyncMock()
            mock_issue.project_key = "TEST"
            mock_jira.get_issue = AsyncMock(return_value=mock_issue)
            mock_jira.create_task = AsyncMock(side_effect=Exception("Jira task failure!"))

            MockDraftManager.get_draft_attachment = AsyncMock(return_value=draft)
            MockDraftManager.delete_draft_attachment = AsyncMock()

            with pytest.raises(Exception, match="Jira task failure!"):
                await route_task_approval(approved_task_state)

            # Deletion should not have been called
            MockDraftManager.delete_draft_attachment.assert_not_called()
