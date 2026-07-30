"""Tests for DraftManager utility class."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest import LogCaptureFixture

from forge.integrations.jira import JiraClient
from forge.models.draft import DraftItem, ForgeDecompositionDraft
from forge.workflow.utils.draft_manager import (
    FORGE_STORIES_DRAFT_FILENAME,
    DraftManager,
)


@pytest.fixture
def sample_draft() -> ForgeDecompositionDraft:
    """Return a valid ForgeDecompositionDraft instance."""
    now = datetime.now(UTC)
    return ForgeDecompositionDraft(
        parent_key="PROJ-123",
        phase="stories",
        items=[
            DraftItem(
                id=1,
                summary="Story 1",
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
    async def test_save_draft_attachment_success_no_existing(self, sample_draft: ForgeDecompositionDraft) -> None:
        """Should successfully upload draft when no prior matching attachment exists."""
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.list_attachments = AsyncMock(return_value=[])
        mock_jira.add_attachment = AsyncMock()
        mock_jira.delete_attachment = AsyncMock()

        filename = FORGE_STORIES_DRAFT_FILENAME
        await DraftManager.save_draft_attachment(mock_jira, "PROJ-123", sample_draft, filename)

        mock_jira.list_attachments.assert_called_once_with("PROJ-123")
        mock_jira.delete_attachment.assert_not_called()

        # Verify serialized content passed to add_attachment
        expected_bytes = sample_draft.model_dump_json().encode("utf-8")
        mock_jira.add_attachment.assert_called_once_with("PROJ-123", filename, expected_bytes)

    @pytest.mark.asyncio
    async def test_save_draft_attachment_success_with_existing(self, sample_draft: ForgeDecompositionDraft) -> None:
        """Should delete existing matching attachment before uploading new draft."""
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.list_attachments = AsyncMock(
            return_value=[
                {"id": "att-111", "filename": "unrelated.json", "content_url": "http://url1"},
                {
                    "id": "att-222",
                    "filename": FORGE_STORIES_DRAFT_FILENAME,
                    "content_url": "http://url2",
                },
                {
                    "id": "att-333",
                    "filename": FORGE_STORIES_DRAFT_FILENAME,
                    "content_url": "http://url3",
                },
            ]
        )
        mock_jira.delete_attachment = AsyncMock()
        mock_jira.add_attachment = AsyncMock()

        filename = FORGE_STORIES_DRAFT_FILENAME
        await DraftManager.save_draft_attachment(mock_jira, "PROJ-123", sample_draft, filename)

        mock_jira.list_attachments.assert_called_once_with("PROJ-123")
        # Ensure it deletes both matching attachments (single-file constraint)
        assert mock_jira.delete_attachment.call_count == 2
        mock_jira.delete_attachment.assert_any_call("att-222")
        mock_jira.delete_attachment.assert_any_call("att-333")

        expected_bytes = sample_draft.model_dump_json().encode("utf-8")
        mock_jira.add_attachment.assert_called_once_with("PROJ-123", filename, expected_bytes)

    @pytest.mark.asyncio
    async def test_save_draft_attachment_failure_to_list(self, sample_draft: ForgeDecompositionDraft) -> None:
        """Should propagate list_attachments exception."""
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.list_attachments = AsyncMock(side_effect=Exception("API Error"))

        with pytest.raises(Exception, match="API Error"):
            await DraftManager.save_draft_attachment(
                mock_jira, "PROJ-123", sample_draft, FORGE_STORIES_DRAFT_FILENAME
            )

    @pytest.mark.asyncio
    async def test_save_draft_attachment_failure_to_delete(self, sample_draft: ForgeDecompositionDraft) -> None:
        """Should propagate delete_attachment exception."""
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.list_attachments = AsyncMock(
            return_value=[{"id": "att-123", "filename": FORGE_STORIES_DRAFT_FILENAME}]
        )
        mock_jira.delete_attachment = AsyncMock(side_effect=Exception("Delete Error"))

        with pytest.raises(Exception, match="Delete Error"):
            await DraftManager.save_draft_attachment(
                mock_jira, "PROJ-123", sample_draft, FORGE_STORIES_DRAFT_FILENAME
            )

    @pytest.mark.asyncio
    async def test_save_draft_attachment_failure_to_upload(self, sample_draft: ForgeDecompositionDraft) -> None:
        """Should propagate add_attachment exception."""
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.list_attachments = AsyncMock(return_value=[])
        mock_jira.add_attachment = AsyncMock(side_effect=Exception("Upload Error"))

        with pytest.raises(Exception, match="Upload Error"):
            await DraftManager.save_draft_attachment(
                mock_jira, "PROJ-123", sample_draft, FORGE_STORIES_DRAFT_FILENAME
            )

    @pytest.mark.asyncio
    async def test_get_draft_attachment_success(self, sample_draft: ForgeDecompositionDraft) -> None:
        """Should download and successfully parse draft attachment if found."""
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.list_attachments = AsyncMock(
            return_value=[
                {
                    "id": "att-222",
                    "filename": FORGE_STORIES_DRAFT_FILENAME,
                    "content_url": "http://url2",
                },
            ]
        )
        serialized_bytes = sample_draft.model_dump_json().encode("utf-8")
        mock_jira.download_attachment = AsyncMock(return_value=serialized_bytes)

        res = await DraftManager.get_draft_attachment(
            mock_jira, "PROJ-123", FORGE_STORIES_DRAFT_FILENAME
        )

        assert res is not None
        assert res.parent_key == sample_draft.parent_key
        assert res.phase == sample_draft.phase
        assert len(res.items) == len(sample_draft.items)
        assert res.items[0].summary == sample_draft.items[0].summary
        mock_jira.list_attachments.assert_called_once_with("PROJ-123")
        mock_jira.download_attachment.assert_called_once_with("http://url2")

    @pytest.mark.asyncio
    async def test_get_draft_attachment_success_alternate_url_key(self, sample_draft: ForgeDecompositionDraft) -> None:
        """Should download using 'content' key if 'content_url' is missing."""
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.list_attachments = AsyncMock(
            return_value=[
                {
                    "id": "att-222",
                    "filename": FORGE_STORIES_DRAFT_FILENAME,
                    "content": "http://url2-alt",
                },
            ]
        )
        serialized_bytes = sample_draft.model_dump_json().encode("utf-8")
        mock_jira.download_attachment = AsyncMock(return_value=serialized_bytes)

        res = await DraftManager.get_draft_attachment(
            mock_jira, "PROJ-123", FORGE_STORIES_DRAFT_FILENAME
        )

        assert res is not None
        mock_jira.download_attachment.assert_called_once_with("http://url2-alt")

    @pytest.mark.asyncio
    async def test_get_draft_attachment_not_found(self) -> None:
        """Should return None if attachment does not exist."""
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.list_attachments = AsyncMock(return_value=[])

        res = await DraftManager.get_draft_attachment(
            mock_jira, "PROJ-123", FORGE_STORIES_DRAFT_FILENAME
        )

        assert res is None
        mock_jira.list_attachments.assert_called_once_with("PROJ-123")

    @pytest.mark.asyncio
    async def test_get_draft_attachment_missing_content_url(self) -> None:
        """Should log warning and return None if content URL is missing."""
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.list_attachments = AsyncMock(
            return_value=[
                {"id": "att-222", "filename": FORGE_STORIES_DRAFT_FILENAME},
            ]
        )

        res = await DraftManager.get_draft_attachment(
            mock_jira, "PROJ-123", FORGE_STORIES_DRAFT_FILENAME
        )

        assert res is None

    @pytest.mark.asyncio
    async def test_get_draft_attachment_validation_failure(self, caplog: LogCaptureFixture) -> None:
        """Should log a warning and return None if draft JSON is invalid according to model schema."""
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.list_attachments = AsyncMock(
            return_value=[
                {
                    "id": "att-222",
                    "filename": FORGE_STORIES_DRAFT_FILENAME,
                    "content_url": "http://url2",
                },
            ]
        )
        # Missing required fields like updated_at, parent_key, etc.
        invalid_bytes = b'{"parent_key": "PROJ-123", "phase": "invalid_phase"}'
        mock_jira.download_attachment = AsyncMock(return_value=invalid_bytes)

        res = await DraftManager.get_draft_attachment(
            mock_jira, "PROJ-123", FORGE_STORIES_DRAFT_FILENAME
        )

        assert res is None
        # Verify warning log was printed
        assert any(
            "Validation failed for draft attachment" in record.message
            and record.levelname == "WARNING"
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_get_draft_attachment_parsing_failure(self, caplog: LogCaptureFixture) -> None:
        """Should log a warning and return None if draft bytes are completely non-JSON."""
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.list_attachments = AsyncMock(
            return_value=[
                {
                    "id": "att-222",
                    "filename": FORGE_STORIES_DRAFT_FILENAME,
                    "content_url": "http://url2",
                },
            ]
        )
        mock_jira.download_attachment = AsyncMock(return_value=b"not json at all")

        res = await DraftManager.get_draft_attachment(
            mock_jira, "PROJ-123", FORGE_STORIES_DRAFT_FILENAME
        )

        assert res is None
        # Verify warning log was printed
        assert any(
            "Failed to parse draft attachment" in record.message and record.levelname == "WARNING"
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_get_draft_attachment_download_failure(self) -> None:
        """Should propagate download_attachment exceptions."""
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.list_attachments = AsyncMock(
            return_value=[
                {
                    "id": "att-222",
                    "filename": FORGE_STORIES_DRAFT_FILENAME,
                    "content_url": "http://url2",
                },
            ]
        )
        mock_jira.download_attachment = AsyncMock(side_effect=Exception("Network Timeout"))

        with pytest.raises(Exception, match="Network Timeout"):
            await DraftManager.get_draft_attachment(
                mock_jira, "PROJ-123", FORGE_STORIES_DRAFT_FILENAME
            )

    @pytest.mark.asyncio
    async def test_delete_draft_attachment_success(self) -> None:
        """Should delete all matching attachments if found."""
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.list_attachments = AsyncMock(
            return_value=[
                {
                    "id": "att-111",
                    "filename": FORGE_STORIES_DRAFT_FILENAME,
                    "content_url": "http://url1",
                },
                {"id": "att-222", "filename": "other.json", "content_url": "http://url2"},
                {
                    "id": "att-333",
                    "filename": FORGE_STORIES_DRAFT_FILENAME,
                    "content_url": "http://url3",
                },
            ]
        )
        mock_jira.delete_attachment = AsyncMock()

        await DraftManager.delete_draft_attachment(
            mock_jira, "PROJ-123", FORGE_STORIES_DRAFT_FILENAME
        )

        mock_jira.list_attachments.assert_called_once_with("PROJ-123")
        assert mock_jira.delete_attachment.call_count == 2
        mock_jira.delete_attachment.assert_any_call("att-111")
        mock_jira.delete_attachment.assert_any_call("att-333")

    @pytest.mark.asyncio
    async def test_delete_draft_attachment_not_found(self) -> None:
        """Should do nothing and succeed if no matching attachment found."""
        mock_jira = MagicMock(spec=JiraClient)
        mock_jira.list_attachments = AsyncMock(return_value=[])
        mock_jira.delete_attachment = AsyncMock()

        await DraftManager.delete_draft_attachment(
            mock_jira, "PROJ-123", FORGE_STORIES_DRAFT_FILENAME
        )

        mock_jira.list_attachments.assert_called_once_with("PROJ-123")
        mock_jira.delete_attachment.assert_not_called()
