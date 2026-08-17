# mypy: disallow-untyped-decorators=False
"""Tests for DraftManager utility class."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge.integrations.jira import JiraClient
from forge.models.draft import DraftItem, ForgeDecompositionDraft
from forge.workflow.utils.draft_manager import (
    FORGE_STORIES_DRAFT_FILENAME,
    FORGE_TASKS_DRAFT_FILENAME,
    DraftManager,
)


@pytest.fixture(
    params=[
        ("epics", FORGE_STORIES_DRAFT_FILENAME),
        ("tasks", FORGE_TASKS_DRAFT_FILENAME),
    ]
)
def draft_config(request: pytest.FixtureRequest) -> tuple[str, str]:
    """Return a tuple of (phase, filename) representing draft configurations."""
    val: tuple[str, str] = request.param
    return val


@pytest.fixture
def sample_draft(draft_config: tuple[str, str]) -> ForgeDecompositionDraft:
    """Return a valid ForgeDecompositionDraft instance matching the draft configuration."""
    phase, _ = draft_config
    now = datetime.now(UTC)
    return ForgeDecompositionDraft(
        parent_key="PROJ-123",
        phase=phase,
        items=[
            DraftItem(
                id=1,
                summary=f"{phase.capitalize()} 1",
                description="Desc 1",
                repo="repo-a",
                acceptance_criteria=["AC 1"],
            )
        ],
        version=1,
        created_at=now,
        updated_at=now,
    )


class TestDraftManager:
    """Test cases for DraftManager CRUD operations on Jira parent tickets."""

    @pytest.mark.asyncio
    async def test_delete_draft_attachment_success(self, draft_config: tuple[str, str]) -> None:
        """Should delete all matching attachments if found."""
        _, filename = draft_config
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.delete_attachments_by_name = AsyncMock(return_value=2)

        await DraftManager.delete_draft_attachment(mock_jira, "PROJ-123", filename)

        mock_jira.delete_attachments_by_name.assert_called_once_with("PROJ-123", filename)

    @pytest.mark.asyncio
    async def test_delete_draft_attachment_not_found(self, draft_config: tuple[str, str]) -> None:
        """Should do nothing and succeed if no matching attachment found."""
        _, filename = draft_config
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.delete_attachments_by_name = AsyncMock(return_value=0)

        await DraftManager.delete_draft_attachment(mock_jira, "PROJ-123", filename)

        mock_jira.delete_attachments_by_name.assert_called_once_with("PROJ-123", filename)

    @pytest.mark.asyncio
    async def test_delete_draft_attachment_failure(self, draft_config: tuple[str, str]) -> None:
        """Should propagate delete_attachments_by_name exception."""
        _, filename = draft_config
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.delete_attachments_by_name = AsyncMock(side_effect=Exception("Delete Error"))

        with pytest.raises(Exception, match="Delete Error"):
            await DraftManager.delete_draft_attachment(mock_jira, "PROJ-123", filename)

    def test_format_review_comment_escapes_pipes(self) -> None:
        """Should escape pipe characters in item summary and repo for draft review comment."""
        now = datetime.now(UTC)
        draft = ForgeDecompositionDraft(
            parent_key="PROJ-123",
            phase="tasks",
            items=[
                DraftItem(
                    id=1,
                    summary="Task with | pipe in summary",
                    description="Desc 1",
                    repo="repo|with|pipe",
                    acceptance_criteria=["AC 1"],
                )
            ],
            version=1,
            created_at=now,
            updated_at=now,
        )

        comment = DraftManager.format_review_comment(draft)

        # Verify that summary and repo are escaped in the markdown table
        assert "Task with \\| pipe in summary" in comment
        assert "repo\\|with\\|pipe" in comment

        # Also verify that the details section is not escaped
        assert "#### 1. Task with | pipe in summary (Repo: repo|with|pipe)" in comment

    def test_format_review_comment_visual_indicator_excluded(self) -> None:
        """Should apply strikethrough formatting and *(excluded)* text for excluded items."""
        now = datetime.now(UTC)
        draft = ForgeDecompositionDraft(
            parent_key="PROJ-123",
            phase="tasks",
            items=[
                DraftItem(
                    id=1,
                    summary="Active task",
                    description="Desc 1",
                    repo="repo1",
                    acceptance_criteria=["AC 1"],
                    excluded=False,
                ),
                DraftItem(
                    id=2,
                    summary="Excluded task",
                    description="Desc 2",
                    repo="repo2",
                    acceptance_criteria=["AC 2"],
                    excluded=True,
                ),
            ],
            version=1,
            created_at=now,
            updated_at=now,
        )

        comment = DraftManager.format_review_comment(draft)

        # Verify normal item is formatted normally
        assert "| 1 | Active task | repo1 |" in comment
        assert "#### 1. Active task (Repo: repo1)" in comment

        # Verify excluded item formatting in table
        assert "| 2 | ~~Excluded task~~ *(excluded)* | ~~repo2~~ |" in comment
        # Verify excluded item heading summary in detail blocks
        assert "#### 2. ~~Excluded task~~ *(excluded)* (Repo: repo2)" in comment

    def test_format_review_comment_condensed_exceeds_limit(self) -> None:
        """Should truncate table rows and use warning note when condensed comment exceeds custom limit."""
        now = datetime.now(UTC)
        draft = ForgeDecompositionDraft(
            parent_key="PROJ-123",
            phase="tasks",
            items=[
                DraftItem(
                    id=i,
                    summary=f"Task {i}",
                    repo=f"repo{i}",
                    description=f"Desc {i}",
                    acceptance_criteria=[f"AC {i}"],
                )
                for i in range(1, 11)
            ],
            version=1,
            created_at=now,
            updated_at=now,
        )

        # Set a limit that is large enough to contain headers + footer + some rows + warning note,
        # but too small to fit all 10 rows.
        comment = DraftManager.format_review_comment(draft, limit=1000)

        assert len(comment) <= 1000
        assert "⚠️ Showing first" in comment
        assert "items — see attached draft JSON for the full list." in comment
        assert "Task 1" in comment
        assert "## 🤖 Forge interaction options" in comment

        # Verify it is truncated when the limit is extremely small (e.g. 100)
        very_small_comment = DraftManager.format_review_comment(draft, limit=100)
        assert len(very_small_comment) <= 100
        assert very_small_comment.endswith(" [truncated]")

    def test_chunk_text_by_limit(self) -> None:
        """Verify chunk_text_by_limit splits text correctly."""
        text = "Line 1\nLine 2\nLine 3"
        # Split with small limit
        chunks = DraftManager.chunk_text_by_limit(text, limit=10)
        assert len(chunks) == 3
        assert chunks[0] == "Line 1"
        assert chunks[1] == "Line 2"
        assert chunks[2] == "Line 3"

    @pytest.mark.asyncio
    async def test_post_task_draft_review(self) -> None:
        """Verify post_task_draft_review slices tasks by Epic and posts comments to Epic and Feature tickets."""
        now = datetime.now(UTC)
        draft = ForgeDecompositionDraft(
            parent_key="FEATURE-1",
            phase="tasks",
            items=[
                DraftItem(
                    id=1,
                    summary="Task 1",
                    description="Desc 1",
                    repo="repo1",
                    acceptance_criteria=[],
                    epic_key="EPIC-101",
                ),
                DraftItem(
                    id=2,
                    summary="Task 2",
                    description="Desc 2",
                    repo="repo2",
                    acceptance_criteria=[],
                    epic_key="EPIC-102",
                ),
            ],
            version=1,
            created_at=now,
            updated_at=now,
        )

        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.add_comment = AsyncMock()

        await DraftManager.post_task_draft_review(mock_jira, "FEATURE-1", draft)

        # Should add comments to EPIC-101, EPIC-102, and FEATURE-1
        assert mock_jira.add_comment.call_count == 3

        # Verify Epic EPIC-101 comment
        epic_101_call = [
            call for call in mock_jira.add_comment.call_args_list if call[0][0] == "EPIC-101"
        ][0]
        assert "### 📋 Proposed Tasks Draft" in epic_101_call[0][1]
        assert "Task 1" in epic_101_call[0][1]

        # Verify Epic EPIC-102 comment
        epic_102_call = [
            call for call in mock_jira.add_comment.call_args_list if call[0][0] == "EPIC-102"
        ][0]
        assert "### 📋 Proposed Tasks Draft" in epic_102_call[0][1]
        assert "Task 2" in epic_102_call[0][1]

        # Verify Feature FEATURE-1 comment
        feature_call = [
            call for call in mock_jira.add_comment.call_args_list if call[0][0] == "FEATURE-1"
        ][0]
        assert "### 📋 Proposed Tasks Drafts by Epic" in feature_call[0][1]
        assert "EPIC-101" in feature_call[0][1]
        assert "EPIC-102" in feature_call[0][1]
