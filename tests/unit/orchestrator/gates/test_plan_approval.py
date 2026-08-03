"""Unit tests for Plan approval gate."""

import pytest
from langgraph.graph import END

from forge.models.workflow import TicketType
from forge.workflow.feature.state import create_initial_feature_state as create_initial_state
from forge.workflow.gates import plan_approval_gate, provision_epics, route_plan_approval


class TestPlanApprovalGate:
    """Tests for plan_approval_gate node."""

    @pytest.fixture
    def plan_pending_state(self):
        """State with Plan pending approval."""
        state = create_initial_state(
            thread_id="test-thread",
            ticket_key="TEST-123",
            ticket_type=TicketType.FEATURE,
        )
        state["prd_content"] = "# PRD"
        state["spec_content"] = "# Spec"
        state["epic_keys"] = ["TEST-124", "TEST-125", "TEST-126"]
        state["current_node"] = "decompose_epics"
        return state

    def test_gate_pauses_workflow(self, plan_pending_state):
        """Gate sets is_paused=True and updates current_node."""
        result = plan_approval_gate(plan_pending_state)

        assert result["is_paused"] is True
        assert result["current_node"] == "plan_approval_gate"

    def test_gate_preserves_epic_keys(self, plan_pending_state):
        """Gate preserves existing epic keys."""
        result = plan_approval_gate(plan_pending_state)

        assert result["epic_keys"] == ["TEST-124", "TEST-125", "TEST-126"]

    def test_gate_pauses_workflow_with_zero_epics_in_non_yolo(self, plan_pending_state):
        """In non-YOLO mode, gate pauses even with zero epics."""
        plan_pending_state["epic_keys"] = []
        result = plan_approval_gate(plan_pending_state)

        assert result["is_paused"] is True
        assert result["current_node"] == "plan_approval_gate"

    def test_gate_routes_to_retry_with_zero_epics_in_yolo(self, plan_pending_state):
        """In YOLO mode, gate routes back to decompose_epics if empty."""
        plan_pending_state["epic_keys"] = []
        plan_pending_state["context"] = {"labels": ["forge:yolo"]}
        result = plan_approval_gate(plan_pending_state)

        assert result.get("is_paused") is not True
        assert result["current_node"] == "decompose_epics"
        assert result["retry_count"] == 1
        assert "No Epics generated" in result["last_error"]


class TestRoutePlanApproval:
    """Tests for route_plan_approval function."""

    @pytest.fixture
    def plan_pending_state(self):
        """State with Plan pending."""
        state = create_initial_state(
            thread_id="test-thread",
            ticket_key="TEST-123",
            ticket_type=TicketType.FEATURE,
        )
        state["prd_content"] = "# PRD"
        state["spec_content"] = "# Spec"
        state["epic_keys"] = ["TEST-124", "TEST-125"]
        state["current_node"] = "plan_approval_gate"
        state["is_paused"] = True
        return state

    @pytest.mark.asyncio
    async def test_routes_to_tasks_on_approval(self, plan_pending_state):
        """Approved Plan routes to task generation when not paused."""
        plan_pending_state["is_paused"] = False

        result = await route_plan_approval(plan_pending_state)

        assert result == "provision_epics"

    @pytest.mark.asyncio
    async def test_routes_to_regenerate_all_on_full_rejection(self, plan_pending_state):
        """Full plan rejection routes to regenerate all epics."""
        plan_pending_state["context"] = {
            "labels": ["forge:managed", "forge:plan-pending"],
            "rejection_scope": "feature",  # Full feature-level rejection
        }
        plan_pending_state["feedback_comment"] = "The epic breakdown doesn't make sense."
        plan_pending_state["revision_requested"] = True

        result = await route_plan_approval(plan_pending_state)

        assert result == "regenerate_all_epics"

    @pytest.mark.asyncio
    async def test_routes_to_update_single_on_epic_rejection(self, plan_pending_state):
        """Single epic rejection routes to update that epic."""
        plan_pending_state["context"] = {
            "labels": ["forge:managed", "forge:plan-pending"],
            "rejection_scope": "epic",
            "rejected_epic_key": "TEST-125",
        }
        plan_pending_state["current_epic_key"] = "TEST-125"
        plan_pending_state["feedback_comment"] = "Epic 2 needs more detail."
        plan_pending_state["revision_requested"] = True

        result = await route_plan_approval(plan_pending_state)

        assert result == "update_single_epic"

    @pytest.mark.asyncio
    async def test_routes_to_end_when_pending(self, plan_pending_state):
        """Pending Plan without feedback routes to END."""
        plan_pending_state["context"] = {
            "labels": ["forge:managed", "forge:plan-pending"],
        }

        result = await route_plan_approval(plan_pending_state)

        assert result == END


class TestPlanRevisionScenarios:
    """Tests for different plan revision scenarios."""

    @pytest.fixture
    def state_with_epics(self):
        """State with multiple epics."""
        state = create_initial_state(
            thread_id="test-thread",
            ticket_key="TEST-123",
            ticket_type=TicketType.FEATURE,
        )
        state["epic_keys"] = ["TEST-124", "TEST-125", "TEST-126"]
        return state

    @pytest.mark.asyncio
    async def test_full_regen_deletes_all_epics(self, state_with_epics):
        """Full regeneration affects all epics."""
        state_with_epics["context"] = {
            "labels": ["forge:managed", "forge:plan-pending"],
            "rejection_scope": "feature",
        }
        state_with_epics["feedback_comment"] = "Start over with a different approach."
        state_with_epics["revision_requested"] = True

        result = await route_plan_approval(state_with_epics)

        assert result == "regenerate_all_epics"

    @pytest.mark.asyncio
    async def test_single_epic_update_preserves_others(self, state_with_epics):
        """Single epic update preserves other epics."""
        state_with_epics["context"] = {
            "labels": ["forge:managed", "forge:plan-pending"],
            "rejection_scope": "epic",
            "rejected_epic_key": "TEST-125",
        }
        state_with_epics["current_epic_key"] = "TEST-125"
        state_with_epics["feedback_comment"] = "Just fix this one epic."
        state_with_epics["revision_requested"] = True

        result = await route_plan_approval(state_with_epics)

        assert result == "update_single_epic"
        # Other epics should remain in state
        assert "TEST-124" in state_with_epics["epic_keys"]
        assert "TEST-126" in state_with_epics["epic_keys"]

    @pytest.mark.asyncio
    async def test_partial_approval_scenario(self, state_with_epics):
        """Some epics approved, one needs revision."""
        # This tests the scenario where user approves some epics
        # but requests changes to one specific epic
        state_with_epics["context"] = {
            "labels": ["forge:managed", "forge:plan-pending"],
            "rejection_scope": "epic",
            "rejected_epic_key": "TEST-126",
            "approved_epics": ["TEST-124", "TEST-125"],
        }
        state_with_epics["current_epic_key"] = "TEST-126"
        state_with_epics["feedback_comment"] = "Epic 3 scope is too broad."
        state_with_epics["revision_requested"] = True

        result = await route_plan_approval(state_with_epics)

        assert result == "update_single_epic"


class TestPlanQuestionRouting:
    """Tests for Q&A routing in Plan approval gate."""

    @pytest.fixture
    def plan_pending_state(self):
        """State with Plan pending."""
        state = create_initial_state(
            thread_id="test-thread",
            ticket_key="TEST-123",
            ticket_type=TicketType.FEATURE,
        )
        state["prd_content"] = "# PRD"
        state["spec_content"] = "# Spec"
        state["epic_keys"] = ["TEST-124", "TEST-125"]
        state["current_node"] = "plan_approval_gate"
        state["is_paused"] = False
        return state

    @pytest.mark.asyncio
    async def test_routes_to_answer_question_when_is_question(self, plan_pending_state):
        """Questions route to answer_question node."""
        plan_pending_state["is_question"] = True
        plan_pending_state["feedback_comment"] = "?Why split into two epics?"

        result = await route_plan_approval(plan_pending_state)

        assert result == "answer_question"

    @pytest.mark.asyncio
    async def test_question_takes_priority_over_revision(self, plan_pending_state):
        """Question routing takes priority over revision routing."""
        plan_pending_state["is_question"] = True
        plan_pending_state["revision_requested"] = True
        plan_pending_state["feedback_comment"] = "?What's the dependency order?"

        result = await route_plan_approval(plan_pending_state)

        assert result == "answer_question"

    @pytest.mark.asyncio
    async def test_routes_to_regenerate_all_when_feedback_not_question(self, plan_pending_state):
        """Normal feedback routes to regenerate all epics."""
        plan_pending_state["is_question"] = False
        plan_pending_state["revision_requested"] = True
        plan_pending_state["feedback_comment"] = "Rethink the epic breakdown"

        result = await route_plan_approval(plan_pending_state)

        assert result == "regenerate_all_epics"

    @pytest.mark.asyncio
    async def test_question_without_feedback_does_not_route_to_answer(self, plan_pending_state):
        """is_question alone without feedback_comment doesn't route to answer."""
        plan_pending_state["is_question"] = True
        plan_pending_state["feedback_comment"] = ""

        result = await route_plan_approval(plan_pending_state)

        # Should proceed to provision_epics since not paused
        assert result == "provision_epics"


class TestPlanDraftProvisioning:
    """Tests for draft-based ticket provisioning in provision_epics."""

    @pytest.fixture
    def approved_plan_state(self):
        """Approved plan state waiting for ticket creation."""
        state = create_initial_state(
            thread_id="test-thread",
            ticket_key="TEST-123",
            ticket_type=TicketType.FEATURE,
        )
        state["is_paused"] = False
        state["epic_keys"] = []
        return state

    @pytest.mark.asyncio
    async def test_successful_draft_provisioning(self, approved_plan_state):
        """Verify successful download, parsing, skipping excluded items, and deletion on success."""
        from datetime import UTC, datetime
        from unittest.mock import AsyncMock, patch

        from forge.models.draft import DraftItem, ForgeDecompositionDraft

        draft_item_1 = DraftItem(
            id=1,
            summary="Epic One",
            description="Details of epic 1",
            repo="org/repo-1",
            acceptance_criteria=[],
            excluded=False,
        )
        draft_item_2 = DraftItem(
            id=2,
            summary="Epic Two",
            description="Details of epic 2",
            repo="org/repo-2",
            acceptance_criteria=[],
            excluded=True,  # Excluded!
        )
        draft = ForgeDecompositionDraft(
            parent_key="TEST-123",
            phase="stories",
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
            mock_jira.create_epic = AsyncMock(return_value="EPIC-101")
            mock_jira.search_issues = AsyncMock(return_value=[])  # Idempotency guard finds nothing

            MockDraftManager.get_draft_attachment = AsyncMock(return_value=draft)
            MockDraftManager.delete_draft_attachment = AsyncMock()

            result = await provision_epics(approved_plan_state)

            assert result["epic_keys"] == ["EPIC-101"]

            # Verify creations and exclusions
            mock_jira.create_epic.assert_called_once_with(
                project_key="TEST",
                summary="Epic One",
                description="Details of epic 1",
                parent_key="TEST-123",
                labels=["forge:managed", "forge:parent:TEST-123", "repo:org/repo-1"],
            )

            # Verify draft deleted
            MockDraftManager.delete_draft_attachment.assert_called_once()

    @pytest.mark.asyncio
    async def test_retains_draft_on_failure(self, approved_plan_state):
        """Verify that draft attachment is not deleted if epic creation fails midway."""
        from datetime import UTC, datetime
        from unittest.mock import AsyncMock, patch

        from forge.models.draft import DraftItem, ForgeDecompositionDraft

        draft_item_1 = DraftItem(
            id=1,
            summary="Epic One",
            description="Details of epic 1",
            repo="org/repo-1",
            acceptance_criteria=[],
            excluded=False,
        )
        draft = ForgeDecompositionDraft(
            parent_key="TEST-123",
            phase="stories",
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
            mock_jira.create_epic = AsyncMock(side_effect=Exception("Jira failure midway!"))
            mock_jira.search_issues = AsyncMock(return_value=[])  # Idempotency guard finds nothing

            MockDraftManager.get_draft_attachment = AsyncMock(return_value=draft)
            MockDraftManager.delete_draft_attachment = AsyncMock()

            with pytest.raises(Exception, match="Jira failure midway!"):
                await provision_epics(approved_plan_state)

            # Deletion should not have been called
            MockDraftManager.delete_draft_attachment.assert_not_called()
