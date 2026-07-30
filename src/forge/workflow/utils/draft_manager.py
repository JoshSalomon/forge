"""Utility for managing draft CRUD operations on Jira parent tickets as attachments."""

import copy
import logging
from typing import Any

from pydantic import ValidationError

from forge.integrations.jira import JiraClient
from forge.models.draft import ForgeDecompositionDraft

logger = logging.getLogger(__name__)

FORGE_STORIES_DRAFT_FILENAME = "forge-stories-draft.json"
FORGE_TASKS_DRAFT_FILENAME = "forge-tasks-draft.json"


class DraftManager:
    """Manages draft CRUD operations on Jira parent tickets as attachments."""

    @staticmethod
    def _validate_item_params(params: dict[str, Any]) -> None:
        """Validate the fields in draft item parameters strictly.

        Args:
            params: The parameters dictionary.

        Raises:
            ValueError: If a validation check fails.
        """
        allowed_fields = {"summary", "description", "repo", "acceptance_criteria", "excluded"}
        for k, v in params.items():
            if k not in allowed_fields:
                raise ValueError(f"Unknown field '{k}' for draft item.")
            if k in {"summary", "description", "repo"} and not isinstance(v, str):
                raise ValueError(f"Field '{k}' must be a string, got {type(v).__name__}.")
            if k == "acceptance_criteria" and (
                not isinstance(v, list) or not all(isinstance(x, str) for x in v)
            ):
                raise ValueError("Field 'acceptance_criteria' must be a list of strings.")
            if k == "excluded" and not isinstance(v, bool):
                raise ValueError("Field 'excluded' must be a boolean.")

    @staticmethod
    def apply_draft_modification(
        draft_json: list[dict[str, Any]],
        parsed_command: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Apply a direct mutation on a list of draft story or task JSON objects based on the command type.

        Args:
            draft_json: The current list of draft item dictionaries.
            parsed_command: The parsed comment command dictionary.

        Returns:
            The mutated list of draft item dictionaries.

        Raises:
            ValueError: If the command contains an error, the target ID is missing/not found,
                        or strict type validation fails.
        """
        if "error" in parsed_command:
            raise ValueError(f"Invalid command parameters: {parsed_command['error']}")

        command = parsed_command.get("command")
        if not command:
            raise ValueError("Command type is missing in parsed command.")

        mutated_list = copy.deepcopy(draft_json)

        if command == "remove":
            target_id = parsed_command.get("id")
            if target_id is None:
                raise ValueError("Missing ID for removal.")

            # Find and remove item
            found = False
            for i, item in enumerate(mutated_list):
                if item.get("id") == target_id:
                    mutated_list.pop(i)
                    found = True
                    break

            if not found:
                raise ValueError(f"Item with ID {target_id} not found for removal.")

            # Re-sequence remaining items
            for idx, item in enumerate(mutated_list):
                item["id"] = idx + 1

        elif command == "add":
            next_id = len(mutated_list) + 1
            params = parsed_command.get("params", {})

            # Strict type validation
            DraftManager._validate_item_params(params)

            # Build the new item using parsed parameters with defaults
            new_item = {
                "id": next_id,
                "summary": params.get("summary", ""),
                "description": params.get("description", ""),
                "repo": params.get("repo", ""),
                "acceptance_criteria": params.get("acceptance_criteria", []),
                "excluded": params.get("excluded", False),
            }

            mutated_list.append(new_item)

        elif command == "update":
            target_id = parsed_command.get("id")
            if target_id is None:
                raise ValueError("Missing ID for update.")

            # Find the item
            target_item = None
            for item in mutated_list:
                if item.get("id") == target_id:
                    target_item = item
                    break

            if not target_item:
                raise ValueError(f"Item with ID {target_id} not found for update.")

            params = parsed_command.get("params", {})

            # Strict type validation
            DraftManager._validate_item_params(params)

            # Apply updates
            for k, v in params.items():
                target_item[k] = v

        elif command == "exclude":
            target_id = parsed_command.get("id")
            if target_id is None:
                raise ValueError("Missing ID for exclude command.")

            # Find the item
            target_item = None
            for item in mutated_list:
                if item.get("id") == target_id:
                    target_item = item
                    break

            if not target_item:
                raise ValueError(f"Item with ID {target_id} not found for exclude.")

            # Flip the excluded boolean key
            target_item["excluded"] = not target_item.get("excluded", False)

        else:
            raise ValueError(f"Unsupported modification command type: '{command}'")

        return mutated_list

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
            attachments = await jira_client.get_attachments(issue_key)
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
