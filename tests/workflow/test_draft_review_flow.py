"""Integration tests for Draft Review Flow.

Covers YOLO bypass path, draft attachment creation/cleanup, BR-003 truncation
rules, excluded item skipping during ticket provisioning, and draft retention
on partial ticket provisioning failure.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.config import Settings
from forge.models.draft import DraftItem, ForgeDecompositionDraft
from forge.workflow.gates.plan_approval import provision_epics_from_draft, route_plan_approval
from forge.workflow.gates.task_approval import provision_tasks_from_draft, route_task_approval
from forge.workflow.nodes.epic_decomposition import decompose_epics
from forge.workflow.nodes.task_generation import generate_tasks
from forge.workflow.utils.draft_manager import DraftManager


@pytest.fixture
def mock_settings() -> Settings:
    """Create settings for tests."""
    return Settings(
        redis_url="redis://localhost:6379/0",
        jira_base_url="https://test.atlassian.net",
        jira_api_token="test-token",
        jira_user_email="test@example.com",
        jira_webhook_secret="test-webhook-secret",
        github_token="test-github-token",
        github_webhook_secret="test-github-webhook-secret",
        llm_backend="anthropic",
        llm_model="claude-sonnet-4-5-20250929",
        anthropic_api_key="test-anthropic-key",
        yolo_mode=False,
    )


@pytest.fixture
def base_epic_state() -> dict[str, Any]:
    """Base state for epic decomposition."""
    return {
        "ticket_key": "TEST-100",
        "spec_content": "Build feature x.",
        "qa_history": [],
        "retry_count": 0,
        "yolo_mode": False,
        "epic_keys": [],
    }


@pytest.fixture
def base_task_state() -> dict[str, Any]:
    """Base state for task generation."""
    return {
        "ticket_key": "TEST-100",
        "spec_content": "Build feature x.",
        "qa_history": [],
        "retry_count": 0,
        "yolo_mode": False,
        "epic_keys": ["TEST-101"],
        "task_keys": [],
        "tasks_by_repo": {},
    }


@pytest.fixture
def mock_parent_issue() -> Any:
    """Mock Jira parent issue."""
    issue = MagicMock()
    issue.project_key = "TEST"
    issue.summary = "Test Feature Summary"
    issue.description = "Test Feature Description"
    return issue


@pytest.fixture
def mock_epic_issue() -> Any:
    """Mock Jira Epic issue."""
    issue = MagicMock()
    issue.project_key = "TEST"
    issue.summary = "Test Epic Summary"
    issue.description = "Test Epic Plan Description"
    return issue


@pytest.fixture
def mock_epics_data() -> list[dict[str, Any]]:
    """Mock generated epics data from LLM agent."""
    return [
        {"summary": "Epic 1", "plan": "Plan for epic 1", "repo": "acme/repo1"},
        {"summary": "Epic 2", "plan": "Plan for epic 2", "repo": "acme/repo2"},
    ]


@pytest.fixture
def mock_tasks_data() -> list[dict[str, Any]]:
    """Mock generated tasks data from LLM agent."""
    return [
        {"summary": "Task 1", "description": "Desc for task 1", "repo": "acme/repo1"},
        {"summary": "Task 2", "description": "Desc for task 2", "repo": "acme/repo2"},
    ]


class TestYoloBypassPath:
    """Acceptance Criterion: Integration tests verify the YOLO bypass path."""

    @pytest.mark.asyncio
    async def test_epic_decomposition_yolo_bypass(
        self,
        base_epic_state: dict[str, Any],
        mock_parent_issue: Any,
        mock_epics_data: list[dict[str, Any]],
        mock_settings: Settings,
    ) -> None:
        """Verify decompose_epics provisions immediately and does NOT save draft attachments when YOLO is active."""
        state = {**base_epic_state, "yolo_mode": True}

        with (
            patch(
                "forge.workflow.nodes.epic_decomposition.get_settings", return_value=mock_settings
            ),
            patch("forge.workflow.nodes.epic_decomposition.JiraClient") as MockJira,
            patch("forge.workflow.nodes.epic_decomposition.ForgeAgent") as MockAgent,
            patch(
                "forge.workflow.nodes.epic_decomposition.DraftManager", wraps=DraftManager
            ) as MockDraftManager,
            patch("forge.workflow.nodes.epic_decomposition.post_qa_summary_if_needed"),
            patch("forge.workflow.nodes.epic_decomposition.post_status_comment"),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_parent_issue)
            mock_jira.get_labels = AsyncMock(return_value=["forge:managed"])
            mock_jira.get_project_repos = AsyncMock(return_value=["acme/repo1", "acme/repo2"])
            mock_jira.create_epic = AsyncMock(side_effect=["TEST-101", "TEST-102"])
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            mock_agent = AsyncMock()
            MockAgent.return_value = mock_agent
            mock_agent.generate_epics = AsyncMock(return_value=mock_epics_data)

            MockDraftManager.save_draft_attachment = AsyncMock()
            MockDraftManager.delete_draft_attachment = AsyncMock()

            result = await decompose_epics(state)

        # Verify immediate provisioning of Epics
        assert mock_jira.create_epic.call_count == 2
        mock_jira.create_epic.assert_any_call(
            project_key="TEST",
            summary="Epic 1",
            description="Plan for epic 1",
            parent_key="TEST-100",
            labels=["forge:managed", "forge:parent:TEST-100", "repo:acme/repo1"],
        )

        # Verify draft was NOT saved or cleaned up
        MockDraftManager.save_draft_attachment.assert_not_called()
        MockDraftManager.delete_draft_attachment.assert_not_called()

        # Verify workflow pauses state is not set, instead keys are returned and transitions
        assert result["epic_keys"] == ["TEST-101", "TEST-102"]
        assert result.get("is_paused") is not True
        assert result["current_node"] == "plan_approval_gate"

    @pytest.mark.asyncio
    async def test_task_generation_yolo_bypass(
        self,
        base_task_state: dict[str, Any],
        mock_parent_issue: Any,
        mock_epic_issue: Any,
        mock_tasks_data: list[dict[str, Any]],
        mock_settings: Settings,
    ) -> None:
        """Verify generate_tasks provisions immediately and does NOT save draft attachments when YOLO is active."""
        state = {**base_task_state, "yolo_mode": True}

        with (
            patch("forge.workflow.nodes.task_generation.get_settings", return_value=mock_settings),
            patch("forge.workflow.nodes.task_generation.JiraClient") as MockJira,
            patch("forge.workflow.nodes.task_generation.ForgeAgent") as MockAgent,
            patch(
                "forge.workflow.nodes.task_generation.DraftManager", wraps=DraftManager
            ) as MockDraftManager,
            patch("forge.workflow.nodes.task_generation.post_status_comment"),
            patch(
                "forge.workflow.nodes.task_generation._generate_tasks_for_epic",
                new_callable=AsyncMock,
                return_value=mock_tasks_data,
            ),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(side_effect=[mock_parent_issue, mock_epic_issue])
            mock_jira.get_labels = AsyncMock(return_value=["forge:managed"])
            mock_jira.create_task = AsyncMock(side_effect=["TEST-110", "TEST-111"])
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            mock_agent = AsyncMock()
            MockAgent.return_value = mock_agent

            MockDraftManager.save_draft_attachment = AsyncMock()
            MockDraftManager.delete_draft_attachment = AsyncMock()

            result = await generate_tasks(state)

        # Verify immediate provisioning of Tasks
        assert mock_jira.create_task.call_count == 2
        mock_jira.create_task.assert_any_call(
            project_key="TEST",
            summary="Task 1",
            description="Desc for task 1",
            parent_key="TEST-101",
            labels=["forge:managed", "forge:parent:TEST-100", "repo:acme/repo1"],
        )

        # Verify draft was NOT saved or cleaned up
        MockDraftManager.save_draft_attachment.assert_not_called()
        MockDraftManager.delete_draft_attachment.assert_not_called()

        # Verify result state
        assert result["task_keys"] == ["TEST-110", "TEST-111"]
        assert result["tasks_by_repo"] == {"acme/repo1": ["TEST-110"], "acme/repo2": ["TEST-111"]}
        assert result.get("is_paused") is not True
        assert result["current_node"] == "task_approval_gate"


class TestDraftAttachmentCreationAndCleanup:
    """Acceptance Criterion: Integration tests verify draft attachment creation and cleanup."""

    @pytest.mark.asyncio
    async def test_epic_decomposition_draft_review_flow(
        self,
        base_epic_state: dict[str, Any],
        mock_parent_issue: Any,
        mock_epics_data: list[dict[str, Any]],
        mock_settings: Settings,
    ) -> None:
        """Verify that in non-YOLO mode, decompose_epics cleans up old drafts, saves the new draft JSON, posts comments, and pauses."""
        state = {**base_epic_state, "yolo_mode": False}

        with (
            patch(
                "forge.workflow.nodes.epic_decomposition.get_settings", return_value=mock_settings
            ),
            patch("forge.workflow.nodes.epic_decomposition.JiraClient") as MockJira,
            patch("forge.workflow.nodes.epic_decomposition.ForgeAgent") as MockAgent,
            patch(
                "forge.workflow.nodes.epic_decomposition.DraftManager", wraps=DraftManager
            ) as MockDraftManager,
            patch("forge.workflow.nodes.epic_decomposition.post_qa_summary_if_needed"),
            patch("forge.workflow.nodes.epic_decomposition.post_status_comment"),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_parent_issue)
            mock_jira.get_labels = AsyncMock(return_value=["forge:managed"])
            mock_jira.get_project_repos = AsyncMock(return_value=["acme/repo1", "acme/repo2"])
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            mock_agent = AsyncMock()
            MockAgent.return_value = mock_agent
            mock_agent.generate_epics = AsyncMock(return_value=mock_epics_data)

            MockDraftManager.delete_draft_attachment = AsyncMock()
            MockDraftManager.save_draft_attachment = AsyncMock()

            result = await decompose_epics(state)

        # 1. Verify cleanup of any old drafts
        MockDraftManager.delete_draft_attachment.assert_called_once_with(
            mock_jira, "TEST-100", "forge-stories-draft.json"
        )

        # 2. Verify draft attachment saving
        MockDraftManager.save_draft_attachment.assert_called_once()
        saved_draft = MockDraftManager.save_draft_attachment.call_args[0][2]
        assert isinstance(saved_draft, ForgeDecompositionDraft)
        assert saved_draft.parent_key == "TEST-100"
        assert saved_draft.phase == "stories"
        assert len(saved_draft.items) == 2
        assert saved_draft.items[0].summary == "Epic 1"
        assert saved_draft.items[0].repo == "acme/repo1"

        # 3. Verify comments posted
        assert mock_jira.add_comment.call_count == 1
        comment_body = mock_jira.add_comment.call_args[0][1]
        assert "### 📋 Proposed Epics Draft" in comment_body
        assert "Epic 1" in comment_body
        assert "/forge approve" in comment_body

        # 4. Verify workflow state transitions and pauses
        assert result["is_paused"] is True
        assert result["current_node"] == "plan_approval_gate"
        assert result["epic_keys"] == []

    @pytest.mark.asyncio
    async def test_task_generation_draft_review_flow(
        self,
        base_task_state: dict[str, Any],
        mock_parent_issue: Any,
        mock_epic_issue: Any,
        mock_tasks_data: list[dict[str, Any]],
        mock_settings: Settings,
    ) -> None:
        """Verify that in non-YOLO mode, generate_tasks cleans up old drafts, saves the new draft JSON, posts comments, and pauses."""
        state = {**base_task_state, "yolo_mode": False}

        with (
            patch("forge.workflow.nodes.task_generation.get_settings", return_value=mock_settings),
            patch("forge.workflow.nodes.task_generation.JiraClient") as MockJira,
            patch("forge.workflow.nodes.task_generation.ForgeAgent") as MockAgent,
            patch(
                "forge.workflow.nodes.task_generation.DraftManager", wraps=DraftManager
            ) as MockDraftManager,
            patch("forge.workflow.nodes.task_generation.post_status_comment"),
            patch(
                "forge.workflow.nodes.task_generation._generate_tasks_for_epic",
                new_callable=AsyncMock,
                return_value=mock_tasks_data,
            ),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(side_effect=[mock_parent_issue, mock_epic_issue])
            mock_jira.get_labels = AsyncMock(return_value=["forge:managed"])
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            mock_agent = AsyncMock()
            MockAgent.return_value = mock_agent

            MockDraftManager.delete_draft_attachment = AsyncMock()
            MockDraftManager.save_draft_attachment = AsyncMock()

            result = await generate_tasks(state)

        # 1. Verify cleanup of any old drafts
        MockDraftManager.delete_draft_attachment.assert_called_once_with(
            mock_jira, "TEST-100", "forge-tasks-draft.json"
        )

        # 2. Verify draft attachment saving
        MockDraftManager.save_draft_attachment.assert_called_once()
        saved_draft = MockDraftManager.save_draft_attachment.call_args[0][2]
        assert isinstance(saved_draft, ForgeDecompositionDraft)
        assert saved_draft.parent_key == "TEST-100"
        assert saved_draft.phase == "tasks"
        assert len(saved_draft.items) == 2
        assert saved_draft.items[0].summary == "Task 1"
        assert saved_draft.items[0].repo == "acme/repo1"

        # 3. Verify comments posted
        assert mock_jira.add_comment.call_count == 1
        comment_body = mock_jira.add_comment.call_args[0][1]
        assert "### 📋 Proposed Tasks Draft" in comment_body
        assert "Task 1" in comment_body
        assert "/forge approve" in comment_body

        # 4. Verify workflow state transitions and pauses
        assert result["is_paused"] is True
        assert result["current_node"] == "task_approval_gate"
        assert result["task_keys"] == []


class TestTruncationFallbackBoundaries:
    """Acceptance Criterion: Integration tests verify character length and item count truncation fallback boundaries."""

    @pytest.mark.asyncio
    async def test_item_count_truncation_boundary_epics(
        self, base_epic_state: dict[str, Any], mock_parent_issue: Any, mock_settings: Settings
    ) -> None:
        """BR-003: Verify that when item count > 15, the review comment is formatted in the condensed table format."""
        state = {**base_epic_state, "yolo_mode": False}

        # Generate 16 epics
        many_epics = [
            {"summary": f"Epic {i}", "plan": f"Plan {i}", "repo": f"acme/repo{i}"}
            for i in range(1, 17)
        ]

        with (
            patch(
                "forge.workflow.nodes.epic_decomposition.get_settings", return_value=mock_settings
            ),
            patch("forge.workflow.nodes.epic_decomposition.JiraClient") as MockJira,
            patch("forge.workflow.nodes.epic_decomposition.ForgeAgent") as MockAgent,
            patch(
                "forge.workflow.nodes.epic_decomposition.DraftManager", wraps=DraftManager
            ) as MockDraftManager,
            patch("forge.workflow.nodes.epic_decomposition.post_qa_summary_if_needed"),
            patch("forge.workflow.nodes.epic_decomposition.post_status_comment"),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_parent_issue)
            mock_jira.get_labels = AsyncMock(return_value=["forge:managed"])
            mock_jira.get_project_repos = AsyncMock(return_value=["acme/repo1"])
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            mock_agent = AsyncMock()
            MockAgent.return_value = mock_agent
            mock_agent.generate_epics = AsyncMock(return_value=many_epics)

            MockDraftManager.delete_draft_attachment = AsyncMock()
            MockDraftManager.save_draft_attachment = AsyncMock()

            await decompose_epics(state)

        # Verify comment is condensed
        comment_body = mock_jira.add_comment.call_args[0][1]
        assert "### 📋 Proposed Epics Draft (Condensed)" in comment_body
        assert "Warning" in comment_body
        assert "exceeds character or size limits" in comment_body
        assert "forge-stories-draft.json" in comment_body
        # Detailed descriptions of items should not be present
        assert "#### 1. Epic 1" not in comment_body

    @pytest.mark.asyncio
    async def test_item_count_truncation_boundary_tasks(
        self,
        base_task_state: dict[str, Any],
        mock_parent_issue: Any,
        mock_epic_issue: Any,
        mock_settings: Settings,
    ) -> None:
        """BR-003: Verify that when item count > 15, the review comment is formatted in the condensed table format for tasks."""
        state = {**base_task_state, "yolo_mode": False}

        # Generate 16 tasks
        many_tasks = [
            {"summary": f"Task {i}", "description": f"Desc {i}", "repo": f"acme/repo{i}"}
            for i in range(1, 17)
        ]

        with (
            patch("forge.workflow.nodes.task_generation.get_settings", return_value=mock_settings),
            patch("forge.workflow.nodes.task_generation.JiraClient") as MockJira,
            patch("forge.workflow.nodes.task_generation.ForgeAgent") as MockAgent,
            patch(
                "forge.workflow.nodes.task_generation.DraftManager", wraps=DraftManager
            ) as MockDraftManager,
            patch("forge.workflow.nodes.task_generation.post_status_comment"),
            patch(
                "forge.workflow.nodes.task_generation._generate_tasks_for_epic",
                new_callable=AsyncMock,
                return_value=many_tasks,
            ),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(side_effect=[mock_parent_issue, mock_epic_issue])
            mock_jira.get_labels = AsyncMock(return_value=["forge:managed"])
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            mock_agent = AsyncMock()
            MockAgent.return_value = mock_agent

            MockDraftManager.delete_draft_attachment = AsyncMock()
            MockDraftManager.save_draft_attachment = AsyncMock()

            await generate_tasks(state)

        # Verify comment is condensed
        comment_body = mock_jira.add_comment.call_args[0][1]
        assert "### 📋 Proposed Tasks Draft (Condensed)" in comment_body
        assert "Warning" in comment_body
        assert "exceeds character or size limits" in comment_body
        assert "forge-tasks-draft.json" in comment_body
        assert "#### 1. Task 1" not in comment_body

    @pytest.mark.asyncio
    async def test_character_length_truncation_boundary_epics(
        self, base_epic_state: dict[str, Any], mock_parent_issue: Any, mock_settings: Settings
    ) -> None:
        """BR-003: Verify that when comment character length > 32,767 characters, it falls back to a condensed table."""
        state = {**base_epic_state, "yolo_mode": False}

        # Create 1 huge plan for an epic
        long_plan = "A" * 33000
        epics_with_long_plan = [
            {"summary": "Epic 1", "plan": long_plan, "repo": "acme/repo1"},
            {"summary": "Epic 2", "plan": "Short plan", "repo": "acme/repo2"},
        ]

        with (
            patch(
                "forge.workflow.nodes.epic_decomposition.get_settings", return_value=mock_settings
            ),
            patch("forge.workflow.nodes.epic_decomposition.JiraClient") as MockJira,
            patch("forge.workflow.nodes.epic_decomposition.ForgeAgent") as MockAgent,
            patch(
                "forge.workflow.nodes.epic_decomposition.DraftManager", wraps=DraftManager
            ) as MockDraftManager,
            patch("forge.workflow.nodes.epic_decomposition.post_qa_summary_if_needed"),
            patch("forge.workflow.nodes.epic_decomposition.post_status_comment"),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_parent_issue)
            mock_jira.get_labels = AsyncMock(return_value=["forge:managed"])
            mock_jira.get_project_repos = AsyncMock(return_value=["acme/repo1", "acme/repo2"])
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            mock_agent = AsyncMock()
            MockAgent.return_value = mock_agent
            mock_agent.generate_epics = AsyncMock(return_value=epics_with_long_plan)

            MockDraftManager.delete_draft_attachment = AsyncMock()
            MockDraftManager.save_draft_attachment = AsyncMock()

            await decompose_epics(state)

        # Verify comment is condensed due to length limit
        comment_body = mock_jira.add_comment.call_args[0][1]
        assert "### 📋 Proposed Epics Draft (Condensed)" in comment_body
        assert "Warning" in comment_body
        assert "exceeds character or size limits" in comment_body
        assert "forge-stories-draft.json" in comment_body
        # Detailed descriptions of items should not be present
        assert "#### 1. Epic 1" not in comment_body


class TestApprovalCommandAndSkippingExcludedItems:
    """Acceptance Criterion: Integration tests verify that excluded: true items are skipped during provisioning."""

    @pytest.mark.asyncio
    async def test_epics_provisioning_skips_excluded_items(
        self, mock_parent_issue: Any, mock_settings: Settings
    ) -> None:
        """Verify only non-excluded items are provisioned and the attachment is deleted upon success."""
        state = {
            "ticket_key": "TEST-100",
            "is_paused": False,
            "epic_keys": [],
        }

        # Create draft where item 2 is excluded
        draft = ForgeDecompositionDraft(
            parent_key="TEST-100",
            phase="stories",
            items=[
                DraftItem(
                    id=1,
                    summary="Epic 1",
                    description="Plan 1",
                    repo="acme/repo1",
                    excluded=False,
                    acceptance_criteria=[],
                ),
                DraftItem(
                    id=2,
                    summary="Epic 2",
                    description="Plan 2",
                    repo="acme/repo2",
                    excluded=True,
                    acceptance_criteria=[],
                ),
                DraftItem(
                    id=3,
                    summary="Epic 3",
                    description="Plan 3",
                    repo="acme/repo3",
                    excluded=False,
                    acceptance_criteria=[],
                ),
            ],
            version=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        with (
            patch("forge.config.get_settings", return_value=mock_settings),
            patch("forge.integrations.jira.client.JiraClient") as MockJira,
            patch("forge.workflow.utils.draft_manager.DraftManager") as MockDraftManager,
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_parent_issue)
            mock_jira.create_epic = AsyncMock(side_effect=["EPIC-1", "EPIC-3"])
            mock_jira.close = AsyncMock()

            MockDraftManager.get_draft_attachment = AsyncMock(return_value=draft)
            MockDraftManager.delete_draft_attachment = AsyncMock()

            result_keys = await provision_epics_from_draft(state, mock_jira)

        # Verify only Epic 1 and Epic 3 were created
        assert mock_jira.create_epic.call_count == 2
        mock_jira.create_epic.assert_any_call(
            project_key="TEST",
            summary="Epic 1",
            description="Plan 1",
            parent_key="TEST-100",
            labels=["forge:managed", "forge:parent:TEST-100", "repo:acme/repo1"],
        )
        mock_jira.create_epic.assert_any_call(
            project_key="TEST",
            summary="Epic 3",
            description="Plan 3",
            parent_key="TEST-100",
            labels=["forge:managed", "forge:parent:TEST-100", "repo:acme/repo3"],
        )

        # Verify Epic 2 was skipped
        for call in mock_jira.create_epic.call_args_list:
            assert "Epic 2" not in call[1]["summary"]

        # Verify draft was deleted after successful provisioning
        MockDraftManager.delete_draft_attachment.assert_called_once_with(
            mock_jira, "TEST-100", "forge-stories-draft.json"
        )
        assert result_keys == ["EPIC-1", "EPIC-3"]

    @pytest.mark.asyncio
    async def test_tasks_provisioning_skips_excluded_items(
        self, mock_parent_issue: Any, mock_settings: Settings
    ) -> None:
        """Verify only non-excluded tasks are provisioned and the attachment is deleted upon success."""
        state = {
            "ticket_key": "TEST-100",
            "is_paused": False,
            "task_keys": [],
            "epic_keys": ["EPIC-10"],
        }

        # Create draft where item 2 is excluded
        draft = ForgeDecompositionDraft(
            parent_key="TEST-100",
            phase="tasks",
            items=[
                DraftItem(
                    id=1,
                    summary="Task 1",
                    description="Desc 1",
                    repo="acme/repo1",
                    excluded=False,
                    epic_key="EPIC-10",
                    acceptance_criteria=[],
                ),
                DraftItem(
                    id=2,
                    summary="Task 2",
                    description="Desc 2",
                    repo="acme/repo2",
                    excluded=True,
                    epic_key="EPIC-10",
                    acceptance_criteria=[],
                ),
                DraftItem(
                    id=3,
                    summary="Task 3",
                    description="Desc 3",
                    repo="acme/repo3",
                    excluded=False,
                    epic_key="EPIC-10",
                    acceptance_criteria=[],
                ),
            ],
            version=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        with (
            patch("forge.config.get_settings", return_value=mock_settings),
            patch("forge.integrations.jira.client.JiraClient") as MockJira,
            patch("forge.workflow.utils.draft_manager.DraftManager") as MockDraftManager,
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_parent_issue)
            mock_jira.create_task = AsyncMock(side_effect=["TASK-1", "TASK-3"])
            mock_jira.close = AsyncMock()

            MockDraftManager.get_draft_attachment = AsyncMock(return_value=draft)
            MockDraftManager.delete_draft_attachment = AsyncMock()

            task_keys, tasks_by_repo = await provision_tasks_from_draft(state, mock_jira)

        # Verify only Task 1 and Task 3 were created
        assert mock_jira.create_task.call_count == 2
        mock_jira.create_task.assert_any_call(
            project_key="TEST",
            summary="Task 1",
            description="Desc 1",
            parent_key="EPIC-10",
            labels=["forge:managed", "forge:parent:TEST-100", "repo:acme/repo1"],
        )

        # Verify draft was deleted after successful provisioning
        MockDraftManager.delete_draft_attachment.assert_called_once_with(
            mock_jira, "TEST-100", "forge-tasks-draft.json"
        )
        assert task_keys == ["TASK-1", "TASK-3"]
        assert tasks_by_repo == {"acme/repo1": ["TASK-1"], "acme/repo3": ["TASK-3"]}


class TestDraftRetentionOnFailure:
    """Acceptance Criterion: Integration tests verify draft retention on partial ticket provisioning failure."""

    @pytest.mark.asyncio
    async def test_epics_provisioning_retains_draft_on_failure(
        self, mock_parent_issue: Any, mock_settings: Settings
    ) -> None:
        """Verify draft is retained (delete_draft_attachment is not called) if epic provisioning fails midway."""
        state = {
            "ticket_key": "TEST-100",
            "is_paused": False,
            "epic_keys": [],
        }

        # Create draft with 2 epics
        draft = ForgeDecompositionDraft(
            parent_key="TEST-100",
            phase="stories",
            items=[
                DraftItem(
                    id=1,
                    summary="Epic 1",
                    description="Plan 1",
                    repo="acme/repo1",
                    excluded=False,
                    acceptance_criteria=[],
                ),
                DraftItem(
                    id=2,
                    summary="Epic 2",
                    description="Plan 2",
                    repo="acme/repo2",
                    excluded=False,
                    acceptance_criteria=[],
                ),
            ],
            version=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        with (
            patch("forge.config.get_settings", return_value=mock_settings),
            patch("forge.integrations.jira.client.JiraClient") as MockJira,
            patch("forge.workflow.utils.draft_manager.DraftManager") as MockDraftManager,
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_parent_issue)
            # Epic 1 succeeds, Epic 2 fails with API error
            mock_jira.create_epic = AsyncMock(side_effect=["EPIC-1", Exception("Jira API Failure")])
            mock_jira.close = AsyncMock()

            MockDraftManager.get_draft_attachment = AsyncMock(return_value=draft)
            MockDraftManager.delete_draft_attachment = AsyncMock()

            with pytest.raises(Exception, match="Jira API Failure"):
                await route_plan_approval(state)

        # Verify delete_draft_attachment was NEVER called, thus retaining the draft
        MockDraftManager.delete_draft_attachment.assert_not_called()

    @pytest.mark.asyncio
    async def test_tasks_provisioning_retains_draft_on_failure(
        self, mock_parent_issue: Any, mock_settings: Settings
    ) -> None:
        """Verify draft is retained (delete_draft_attachment is not called) if task provisioning fails midway."""
        state = {
            "ticket_key": "TEST-100",
            "is_paused": False,
            "task_keys": [],
            "epic_keys": ["EPIC-10"],
        }

        # Create draft with 2 tasks
        draft = ForgeDecompositionDraft(
            parent_key="TEST-100",
            phase="tasks",
            items=[
                DraftItem(
                    id=1,
                    summary="Task 1",
                    description="Desc 1",
                    repo="acme/repo1",
                    excluded=False,
                    epic_key="EPIC-10",
                    acceptance_criteria=[],
                ),
                DraftItem(
                    id=2,
                    summary="Task 2",
                    description="Desc 2",
                    repo="acme/repo2",
                    excluded=False,
                    epic_key="EPIC-10",
                    acceptance_criteria=[],
                ),
            ],
            version=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        with (
            patch("forge.config.get_settings", return_value=mock_settings),
            patch("forge.integrations.jira.client.JiraClient") as MockJira,
            patch("forge.workflow.utils.draft_manager.DraftManager") as MockDraftManager,
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_parent_issue)
            # Task 1 succeeds, Task 2 fails with API error
            mock_jira.create_task = AsyncMock(side_effect=["TASK-1", Exception("Jira API Failure")])
            mock_jira.close = AsyncMock()

            MockDraftManager.get_draft_attachment = AsyncMock(return_value=draft)
            MockDraftManager.delete_draft_attachment = AsyncMock()

            with pytest.raises(Exception, match="Jira API Failure"):
                await route_task_approval(state)

        # Verify delete_draft_attachment was NEVER called, thus retaining the draft
        MockDraftManager.delete_draft_attachment.assert_not_called()
