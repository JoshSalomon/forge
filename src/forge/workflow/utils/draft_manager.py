"""Utility for managing draft CRUD operations on Jira parent tickets as attachments."""

import logging

from pydantic import ValidationError

from forge.integrations.jira import JiraClient
from forge.models.draft import ForgeDecompositionDraft

logger = logging.getLogger(__name__)

FORGE_STORIES_DRAFT_FILENAME = "forge-stories-draft.json"
FORGE_TASKS_DRAFT_FILENAME = "forge-tasks-draft.json"


class DraftManager:
    """Manages draft CRUD operations on Jira parent tickets as attachments."""

    FORGE_STORIES_DRAFT_FILENAME = FORGE_STORIES_DRAFT_FILENAME
    FORGE_TASKS_DRAFT_FILENAME = FORGE_TASKS_DRAFT_FILENAME

    @staticmethod
    async def save_draft_attachment(
        jira_client: JiraClient,
        issue_key: str,
        draft: ForgeDecompositionDraft,
        filename: str,
    ) -> None:
        """Save a draft decomposition as an attachment on a Jira parent issue, enforcing the single-file constraint.

        Args:
            jira_client: The Jira client instance.
            issue_key: The Jira issue key.
            draft: The draft model to save.
            filename: The target filename.
        """
        # 1. Delete any matching filename to enforce the single-file constraint (BR-002/BR-004)
        try:
            await jira_client.delete_attachments_by_name(issue_key, filename)
        except Exception as e:
            logger.error(
                f"Failed to delete existing draft attachment '{filename}' on {issue_key} to enforce single-file constraint: {e}",
                exc_info=True,
            )
            raise

        # 2. Serialize and upload
        try:
            content_json = draft.model_dump_json()
            content_bytes = content_json.encode("utf-8")
        except Exception as e:
            logger.error(f"Failed to serialize draft for {issue_key}: {e}", exc_info=True)
            raise

        try:
            logger.info(f"Uploading new draft attachment '{filename}' to {issue_key}.")
            await jira_client.add_attachment(issue_key, filename, content_bytes)
        except Exception as e:
            logger.error(
                f"Failed to upload draft attachment '{filename}' to {issue_key}: {e}",
                exc_info=True,
            )
            raise

    @staticmethod
    async def get_draft_attachment(
        jira_client: JiraClient,
        issue_key: str,
        filename: str,
    ) -> ForgeDecompositionDraft | None:
        """Scan for attachment with matching filename on the issue, download and parse it.

        Args:
            jira_client: The Jira client instance.
            issue_key: The Jira issue key.
            filename: The target filename to retrieve.

        Returns:
            The parsed ForgeDecompositionDraft model instance, or None if not found or validation/parsing fails.
        """
        try:
            attachments = await jira_client.list_attachments(issue_key)
        except Exception as e:
            logger.error(f"Failed to list attachments for {issue_key}: {e}", exc_info=True)
            raise

        target_attachment = None
        for att in attachments:
            if att.get("filename") == filename:
                target_attachment = att
                break

        if not target_attachment:
            logger.debug(f"No attachment found with filename '{filename}' on {issue_key}")
            return None

        content_url = target_attachment.get("content_url") or target_attachment.get("content")
        if not content_url:
            logger.warning(
                f"Attachment '{filename}' found on {issue_key} but is missing a download URL."
            )
            return None

        try:
            content_bytes = await jira_client.download_attachment(content_url)
        except Exception as e:
            logger.error(
                f"Failed to download attachment '{filename}' from {content_url}: {e}",
                exc_info=True,
            )
            raise

        try:
            return ForgeDecompositionDraft.model_validate_json(content_bytes)
        except ValidationError as ve:
            if "json_invalid" in str(ve) or "Invalid JSON" in str(ve):
                logger.warning(
                    f"Failed to parse draft attachment '{filename}' on {issue_key}. Error: {ve}",
                    exc_info=True,
                )
            else:
                logger.warning(
                    f"Validation failed for draft attachment '{filename}' on {issue_key}. Error: {ve}",
                    exc_info=True,
                )
            return None
        except Exception as e:
            logger.warning(
                f"Failed to parse draft attachment '{filename}' on {issue_key}. Error: {e}",
                exc_info=True,
            )
            return None

    @staticmethod
    async def delete_draft_attachment(
        jira_client: JiraClient,
        issue_key: str,
        filename: str,
    ) -> None:
        """Scan for any attachment with matching filename and delete it.

        Args:
            jira_client: The Jira client instance.
            issue_key: The Jira issue key.
            filename: The target filename to delete.
        """
        try:
            await jira_client.delete_attachments_by_name(issue_key, filename)
        except Exception as e:
            logger.error(
                f"Failed to delete draft attachments named '{filename}' on {issue_key}: {e}",
                exc_info=True,
            )
            raise
