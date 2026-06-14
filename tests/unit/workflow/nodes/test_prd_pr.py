"""Tests for PRD PR creation and update helpers."""

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.models.workflow import TicketType
from forge.workflow.feature.state import create_initial_feature_state


class TestSlugify:
    def test_basic_slugify(self):
        from forge.workflow.nodes.prd_generation import _slugify

        assert _slugify("My Feature Title") == "my-feature-title"

    def test_removes_special_chars(self):
        from forge.workflow.nodes.prd_generation import _slugify

        assert _slugify("Define API for CaaS (v2)") == "define-api-for-caas-v2"

    def test_truncates_to_max_length(self):
        from forge.workflow.nodes.prd_generation import _slugify

        long_title = "a" * 100
        result = _slugify(long_title, max_length=60)
        assert len(result) <= 60

    def test_strips_trailing_hyphens(self):
        from forge.workflow.nodes.prd_generation import _slugify

        assert _slugify("trailing---") == "trailing"


class TestCreatePrdProposalPr:
    @pytest.mark.asyncio
    async def test_creates_branch_and_pr(self):
        from forge.workflow.nodes.prd_generation import _create_prd_proposal_pr

        mock_gh = MagicMock()
        mock_gh.create_branch = AsyncMock(return_value={"ref": "refs/heads/forge/prd/test-123"})
        mock_gh.create_or_update_file = AsyncMock(
            return_value={"content": {"sha": "filesha"}}
        )
        mock_gh.create_pull_request = AsyncMock(
            return_value={
                "number": 7,
                "html_url": "https://github.com/org/proposals/pull/7",
            }
        )
        mock_gh.close = AsyncMock()

        mock_jira = MagicMock()
        mock_jira.add_comment = AsyncMock()
        mock_jira.set_workflow_label = AsyncMock()
        mock_jira.close = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.prd_proposals_repo = "org/proposals"
        mock_settings.prd_proposals_path = "proposals"

        with (
            patch("forge.workflow.nodes.prd_generation.GitHubClient", return_value=mock_gh),
            patch("forge.workflow.nodes.prd_generation.JiraClient", return_value=mock_jira),
            patch("forge.workflow.nodes.prd_generation.get_settings", return_value=mock_settings),
            patch(
                "forge.workflow.nodes.prd_generation.set_pr_ticket_index",
                new_callable=AsyncMock,
            ) as mock_index,
        ):
            result = await _create_prd_proposal_pr(
                ticket_key="TEST-123",
                prd_content="# My PRD",
                summary="My Feature",
            )

        assert result["prd_pr_number"] == 7
        assert result["prd_pr_url"] == "https://github.com/org/proposals/pull/7"
        assert result["prd_pr_repo"] == "org/proposals"
        assert result["prd_pr_branch"] == "forge/prd/test-123"

        mock_gh.create_branch.assert_called_once_with("org", "proposals", "forge/prd/test-123")
        mock_gh.create_pull_request.assert_called_once()
        mock_jira.add_comment.assert_called_once()
        mock_jira.set_workflow_label.assert_called_once()
        mock_index.assert_called_once()


class TestUpdatePrdProposalPr:
    @pytest.mark.asyncio
    async def test_updates_file_on_branch(self):
        from forge.workflow.nodes.prd_generation import _update_prd_proposal_pr

        mock_gh = MagicMock()
        mock_gh.get_file_contents = AsyncMock(
            side_effect=[
                # First call: list proposals directory
                [{"name": "TEST-123-my-feature.md", "path": "proposals/TEST-123-my-feature.md"}],
                # Second call: get specific file metadata
                {"sha": "oldsha", "path": "proposals/TEST-123-my-feature.md"},
            ]
        )
        mock_gh.create_or_update_file = AsyncMock(
            return_value={"content": {"sha": "newsha"}}
        )
        mock_gh.create_issue_comment = AsyncMock()
        mock_gh.close = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.prd_proposals_repo = "org/proposals"
        mock_settings.prd_proposals_path = "proposals"

        state = create_initial_feature_state(
            ticket_key="TEST-123",
            ticket_type=TicketType.FEATURE,
            prd_pr_branch="forge/prd/test-123",
            prd_pr_repo="org/proposals",
            prd_pr_number=7,
            prd_pr_url="https://github.com/org/proposals/pull/7",
        )

        with (
            patch("forge.workflow.nodes.prd_generation.GitHubClient", return_value=mock_gh),
            patch("forge.workflow.nodes.prd_generation.get_settings", return_value=mock_settings),
        ):
            await _update_prd_proposal_pr(
                ticket_key="TEST-123",
                prd_content="# Revised PRD",
                state=state,
            )

        assert mock_gh.get_file_contents.call_count == 2
        mock_gh.create_or_update_file.assert_called_once()
        # Verify SHA was passed for update
        call_kwargs = mock_gh.create_or_update_file.call_args[1]
        assert call_kwargs["sha"] == "oldsha"
        mock_gh.create_issue_comment.assert_called_once()
