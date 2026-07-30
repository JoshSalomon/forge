"""Unit tests for epic decomposition node — repo resolution paths."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.integrations.jira.client import MissingProjectConfig
from forge.models.workflow import ForgeLabel
from forge.workflow.nodes.epic_decomposition import decompose_epics, regenerate_all_epics


@pytest.fixture
def base_state():
    return {
        "ticket_key": "MYPROJ-1",
        "spec_content": "Build a backend service.",
        "qa_history": [],
        "generation_context": {},
        "retry_count": 0,
        "yolo_mode": True,
    }


@pytest.fixture
def mock_issue():
    issue = MagicMock()
    issue.project_key = "MYPROJ"
    issue.summary = "Test Feature"
    return issue


@pytest.fixture
def mock_epics_data():
    return [{"summary": "Epic One", "plan": "Do stuff.", "repo": "acme/backend"}]


class TestDecomposeEpicsRepoResolution:
    """Tests for how decompose_epics resolves available repos."""

    @pytest.mark.asyncio
    async def test_uses_project_repos_from_jira_property(
        self, base_state, mock_issue, mock_epics_data
    ):
        """decompose_epics passes forge.repos project property to the agent context."""
        with (
            patch("forge.workflow.nodes.epic_decomposition.JiraClient") as MockJira,
            patch("forge.workflow.nodes.epic_decomposition.ForgeAgent") as MockAgent,
            patch("forge.workflow.nodes.epic_decomposition.post_qa_summary_if_needed"),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_issue)
            mock_jira.get_labels = AsyncMock(return_value=[])
            mock_jira.get_project_repos = AsyncMock(return_value=["acme/backend", "acme/frontend"])
            mock_jira.create_epic = AsyncMock(return_value="MYPROJ-100")
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            mock_agent = AsyncMock()
            MockAgent.return_value = mock_agent
            captured_context: dict = {}

            async def capture_generate_epics(_spec, context):
                captured_context.update(context)
                return mock_epics_data

            mock_agent.generate_epics = capture_generate_epics

            await decompose_epics(base_state)

        assert "acme/backend" in captured_context["available_repos"]
        assert "acme/frontend" in captured_context["available_repos"]

    @pytest.mark.asyncio
    async def test_also_includes_label_repos_alongside_project_repos(
        self, base_state, mock_issue, mock_epics_data
    ):
        """Repos from Feature labels are merged with forge.repos project property."""
        with (
            patch("forge.workflow.nodes.epic_decomposition.JiraClient") as MockJira,
            patch("forge.workflow.nodes.epic_decomposition.ForgeAgent") as MockAgent,
            patch("forge.workflow.nodes.epic_decomposition.post_qa_summary_if_needed"),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_issue)
            mock_jira.get_labels = AsyncMock(return_value=["repo:acme/infra"])
            mock_jira.get_project_repos = AsyncMock(return_value=["acme/backend"])
            mock_jira.create_epic = AsyncMock(return_value="MYPROJ-100")
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            mock_agent = AsyncMock()
            MockAgent.return_value = mock_agent
            captured_context: dict = {}

            async def capture_generate_epics(_spec, context):
                captured_context.update(context)
                return mock_epics_data

            mock_agent.generate_epics = capture_generate_epics

            await decompose_epics(base_state)

        repos = set(captured_context["available_repos"])
        assert "acme/infra" in repos
        assert "acme/backend" in repos

    @pytest.mark.asyncio
    async def test_blocks_and_comments_when_forge_repos_missing(self, base_state, mock_issue):
        """Posts blocking comment and sets forge:blocked when forge.repos is not set."""
        mock_settings = MagicMock()
        mock_settings.forge_require_project_config = True
        mock_settings.known_repos = []
        with (
            patch("forge.workflow.nodes.epic_decomposition.JiraClient") as MockJira,
            patch("forge.workflow.nodes.epic_decomposition.ForgeAgent") as MockAgent,
            patch("forge.workflow.nodes.epic_decomposition.post_qa_summary_if_needed"),
            patch(
                "forge.workflow.nodes.epic_decomposition.get_settings", return_value=mock_settings
            ),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_issue)
            mock_jira.get_labels = AsyncMock(return_value=[])
            mock_jira.get_project_repos = AsyncMock(
                side_effect=MissingProjectConfig("forge.repos not set for project MYPROJ")
            )
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            MockAgent.return_value = AsyncMock()

            result = await decompose_epics(base_state)

        mock_jira.add_comment.assert_called()
        comment_text = mock_jira.add_comment.call_args[0][1]
        assert "forge.repos" in comment_text
        assert "forge:retry" in comment_text

        mock_jira.set_workflow_label.assert_called_once_with("MYPROJ-1", ForgeLabel.BLOCKED)

        assert result["last_error"]
        assert result["current_node"] == "decompose_epics"

    @pytest.mark.asyncio
    async def test_blocks_and_comments_when_forge_repos_malformed(self, base_state, mock_issue):
        """Posts blocking comment and sets forge:blocked when forge.repos has invalid entries."""
        mock_settings = MagicMock()
        mock_settings.forge_require_project_config = True
        mock_settings.known_repos = []
        with (
            patch("forge.workflow.nodes.epic_decomposition.JiraClient") as MockJira,
            patch("forge.workflow.nodes.epic_decomposition.ForgeAgent") as MockAgent,
            patch("forge.workflow.nodes.epic_decomposition.post_qa_summary_if_needed"),
            patch(
                "forge.workflow.nodes.epic_decomposition.get_settings", return_value=mock_settings
            ),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_issue)
            mock_jira.get_labels = AsyncMock(return_value=[])
            mock_jira.get_project_repos = AsyncMock(
                side_effect=MissingProjectConfig(
                    "forge.repos for project MYPROJ is malformed: ['backend-only']"
                )
            )
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            MockAgent.return_value = AsyncMock()

            result = await decompose_epics(base_state)

        mock_jira.set_workflow_label.assert_called_once_with("MYPROJ-1", ForgeLabel.BLOCKED)
        assert result["last_error"]


class TestEpicRevisionState:
    """Tests for plan revision state cleanup."""

    @pytest.mark.asyncio
    async def test_decompose_epics_clears_revision_flags_on_success(
        self, base_state, mock_issue, mock_epics_data
    ):
        """Successful decomposition must not leave a pending revision at the plan gate."""
        state = {
            **base_state,
            "feedback_comment": "Split the authentication epic.",
            "revision_requested": True,
            "current_epic_key": "MYPROJ-99",
        }

        with (
            patch("forge.workflow.nodes.epic_decomposition.JiraClient") as MockJira,
            patch("forge.workflow.nodes.epic_decomposition.ForgeAgent") as MockAgent,
            patch("forge.workflow.nodes.epic_decomposition.post_qa_summary_if_needed"),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_issue)
            mock_jira.get_labels = AsyncMock(return_value=[])
            mock_jira.get_project_repos = AsyncMock(return_value=["acme/backend"])
            mock_jira.create_epic = AsyncMock(return_value="MYPROJ-100")
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            mock_agent = AsyncMock()
            MockAgent.return_value = mock_agent
            mock_agent.generate_epics = AsyncMock(return_value=mock_epics_data)

            result = await decompose_epics(state)

        assert result["current_node"] == "plan_approval_gate"
        assert result["revision_requested"] is False
        assert result["feedback_comment"] is None
        assert result["current_epic_key"] is None

    @pytest.mark.asyncio
    async def test_regenerate_all_epics_clears_revision_flags_after_new_epics(
        self, base_state, mock_issue, mock_epics_data
    ):
        """Full plan regeneration should return to the gate without looping."""
        state = {
            **base_state,
            "epic_keys": ["MYPROJ-10", "MYPROJ-11"],
            "feedback_comment": "Use smaller epics.",
            "revision_requested": True,
        }

        with (
            patch("forge.workflow.nodes.epic_decomposition.JiraClient") as MockJira,
            patch("forge.workflow.nodes.epic_decomposition.ForgeAgent") as MockAgent,
            patch("forge.workflow.nodes.epic_decomposition.post_qa_summary_if_needed"),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.archive_issue = AsyncMock()
            mock_jira.get_issue = AsyncMock(return_value=mock_issue)
            mock_jira.get_labels = AsyncMock(return_value=[])
            mock_jira.get_project_repos = AsyncMock(return_value=["acme/backend"])
            mock_jira.create_epic = AsyncMock(return_value="MYPROJ-100")
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            mock_agent = AsyncMock()
            MockAgent.return_value = mock_agent
            mock_agent.generate_epics = AsyncMock(return_value=mock_epics_data)

            result = await regenerate_all_epics(state)

        assert mock_jira.archive_issue.call_count == 2
        assert result["epic_keys"] == ["MYPROJ-100"]
        assert result["current_node"] == "plan_approval_gate"
        assert result["revision_requested"] is False
        assert result["feedback_comment"] is None


class TestDecomposeEpicsDraftReview:
    """Tests for the non-YOLO draft review gate flow in decompose_epics."""

    @pytest.mark.asyncio
    async def test_decompose_epics_draft_review_flow_success(
        self, base_state, mock_issue, mock_epics_data
    ):
        """When yolo_mode is False, decomposes epics into a draft JSON, deletes old attachments, saves new one, posts comment, and pauses."""
        state = {**base_state, "yolo_mode": False}

        with (
            patch("forge.workflow.nodes.epic_decomposition.JiraClient") as MockJira,
            patch("forge.workflow.nodes.epic_decomposition.ForgeAgent") as MockAgent,
            patch("forge.workflow.nodes.epic_decomposition.DraftManager") as MockDraftManager,
            patch("forge.workflow.nodes.epic_decomposition.post_qa_summary_if_needed"),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_issue)
            mock_jira.get_labels = AsyncMock(return_value=[])
            mock_jira.get_project_repos = AsyncMock(return_value=["acme/backend"])
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            mock_agent = AsyncMock()
            MockAgent.return_value = mock_agent
            mock_agent.generate_epics = AsyncMock(return_value=mock_epics_data)

            MockDraftManager.delete_draft_attachment = AsyncMock()
            MockDraftManager.save_draft_attachment = AsyncMock()

            result = await decompose_epics(state)

        # 1. Verify DraftManager deleted any existing forge-stories-draft.json first
        MockDraftManager.delete_draft_attachment.assert_called_once_with(
            mock_jira, "MYPROJ-1", "forge-stories-draft.json"
        )

        # 2. Verify DraftManager saved the new draft
        MockDraftManager.save_draft_attachment.assert_called_once()
        saved_draft = MockDraftManager.save_draft_attachment.call_args[0][2]
        assert saved_draft.parent_key == "MYPROJ-1"
        assert saved_draft.phase == "stories"
        assert len(saved_draft.items) == 1
        assert saved_draft.items[0].summary == "Epic One"
        assert saved_draft.items[0].description == "Do stuff."
        assert saved_draft.items[0].repo == "acme/backend"

        # 3. Verify formatted comment posted
        assert mock_jira.add_comment.call_count == 2
        comment_text = mock_jira.add_comment.call_args_list[1][0][1]
        assert "### 📋 Proposed Epics Draft" in comment_text
        assert "Epic One" in comment_text
        assert "acme/backend" in comment_text

        # 4. Verify workflow label updated to PLAN_PENDING
        mock_jira.set_workflow_label.assert_called_once_with("MYPROJ-1", ForgeLabel.PLAN_PENDING)

        # 5. Verify state transitions to plan_approval_gate and pauses
        assert result["current_node"] == "plan_approval_gate"
        assert result["is_paused"] is True
        assert result["epic_keys"] == []

    @pytest.mark.asyncio
    async def test_decompose_epics_draft_review_truncation_limits(self, base_state, mock_issue):
        """When the item list has > 15 elements, comment falls back to a condensed table."""
        state = {**base_state, "yolo_mode": False}

        # Mock 16 items
        many_epics_data = [
            {"summary": f"Epic {i}", "plan": f"Plan {i}", "repo": f"repo-{i}"} for i in range(1, 17)
        ]

        with (
            patch("forge.workflow.nodes.epic_decomposition.JiraClient") as MockJira,
            patch("forge.workflow.nodes.epic_decomposition.ForgeAgent") as MockAgent,
            patch("forge.workflow.nodes.epic_decomposition.DraftManager") as MockDraftManager,
            patch("forge.workflow.nodes.epic_decomposition.post_qa_summary_if_needed"),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_issue)
            mock_jira.get_labels = AsyncMock(return_value=[])
            mock_jira.get_project_repos = AsyncMock(return_value=["acme/backend"])
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            mock_agent = AsyncMock()
            MockAgent.return_value = mock_agent
            mock_agent.generate_epics = AsyncMock(return_value=many_epics_data)

            MockDraftManager.delete_draft_attachment = AsyncMock()
            MockDraftManager.save_draft_attachment = AsyncMock()

            await decompose_epics(state)

        # Verify comment is in condensed table format
        assert mock_jira.add_comment.call_count == 2
        comment_text = mock_jira.add_comment.call_args_list[1][0][1]
        assert "### 📋 Proposed Epics Draft (Condensed)" in comment_text
        assert "Warning" in comment_text
        assert "forge-stories-draft.json" in comment_text
        # Condensed table should only show IDs, summaries, and target repos
        # Detailed descriptions/plans (like Plan 1) should NOT be in the comment
        assert "Plan 1" not in comment_text
        assert "Epic 1" in comment_text
        assert "repo-1" in comment_text

    @pytest.mark.asyncio
    async def test_decompose_epics_draft_review_truncation_characters(self, base_state, mock_issue):
        """When comment exceeds 32,767 characters, comment falls back to a condensed table."""
        state = {**base_state, "yolo_mode": False}

        # Mock huge description to exceed character limit
        huge_epics_data = [{"summary": "Epic One", "plan": "A" * 35000, "repo": "acme/backend"}]

        with (
            patch("forge.workflow.nodes.epic_decomposition.JiraClient") as MockJira,
            patch("forge.workflow.nodes.epic_decomposition.ForgeAgent") as MockAgent,
            patch("forge.workflow.nodes.epic_decomposition.DraftManager") as MockDraftManager,
            patch("forge.workflow.nodes.epic_decomposition.post_qa_summary_if_needed"),
        ):
            mock_jira = AsyncMock()
            MockJira.return_value = mock_jira
            mock_jira.get_issue = AsyncMock(return_value=mock_issue)
            mock_jira.get_labels = AsyncMock(return_value=[])
            mock_jira.get_project_repos = AsyncMock(return_value=["acme/backend"])
            mock_jira.set_workflow_label = AsyncMock()
            mock_jira.add_comment = AsyncMock()

            mock_agent = AsyncMock()
            MockAgent.return_value = mock_agent
            mock_agent.generate_epics = AsyncMock(return_value=huge_epics_data)

            MockDraftManager.delete_draft_attachment = AsyncMock()
            MockDraftManager.save_draft_attachment = AsyncMock()

            await decompose_epics(state)

        # Verify comment is in condensed table format due to length
        assert mock_jira.add_comment.call_count == 2
        comment_text = mock_jira.add_comment.call_args_list[1][0][1]
        assert "### 📋 Proposed Epics Draft (Condensed)" in comment_text
        assert "Warning" in comment_text
        assert "forge-stories-draft.json" in comment_text
        assert "A" * 35000 not in comment_text
        assert "Epic One" in comment_text
        assert "acme/backend" in comment_text
