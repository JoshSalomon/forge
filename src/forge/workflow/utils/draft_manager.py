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
    def _validate_item_params(
        params: dict[str, Any], target_item: dict[str, Any] | None = None
    ) -> None:
        """Validate the fields in draft item parameters strictly.

        Args:
            params: The parameters dictionary.
            target_item: Optional target item dictionary to merge with (for update command).

        Raises:
            ValueError: If a validation check fails.
        """
        from forge.models.draft import DraftItem

        if target_item is not None:
            full_item = {**target_item, **params}
        else:
            defaults = {
                "id": 1,
                "summary": "",
                "description": "",
                "repo": "",
                "acceptance_criteria": [],
                "excluded": False,
                "epic_key": None,
            }
            full_item = {**defaults, **params}

        try:
            DraftItem.model_validate(full_item, strict=True)
        except ValidationError as e:
            for error in e.errors():
                loc = error["loc"]
                if not loc:
                    continue
                field = str(loc[0])
                error_type = error["type"]
                if error_type == "extra_forbidden":
                    raise ValueError(f"Unknown field '{field}'")
                elif field in {"summary", "description", "repo"}:
                    val = (
                        params.get(field)
                        if field in params
                        else (target_item.get(field) if target_item else None)
                    )
                    raise ValueError(
                        f"Field '{field}' must be a string, got {type(val).__name__ if val is not None else 'None'}."
                    )
                elif field == "acceptance_criteria":
                    raise ValueError("Field 'acceptance_criteria' must be a list of strings.")
                elif field == "excluded":
                    raise ValueError("Field 'excluded' must be a boolean.")
                elif field == "epic_key":
                    raise ValueError("Field 'epic_key' must be a string or None.")
            raise ValueError(str(e))

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
            DraftManager._validate_item_params(params, target_item)

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
            content_json = draft.model_dump_json(indent=2)
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

    @staticmethod
    def _truncate_to_jira_limit(text: str, limit: int = 32767) -> str:
        """Truncate text to fit within Jira's character limit and append a [truncated] suffix."""
        if len(text) <= limit:
            return text
        suffix = " [truncated]"
        if limit <= len(suffix):
            return text[:limit]
        return text[: limit - len(suffix)] + suffix

    @staticmethod
    def format_review_comment(draft: ForgeDecompositionDraft, limit: int = 32767) -> str:
        """Format a human-readable review comment for a draft."""
        from forge.models.workflow import ForgeLabel

        def _escape_cell(text: str) -> str:
            return text.replace("|", "\\|")

        items = draft.items
        if draft.phase == "stories":
            phase_title = "Epics"
            noun_plural = "epics"
            noun_singular = "epic"
            phase_action = "decomposition"
            item_label = "Plan"
            approval_label = ForgeLabel.PLAN_APPROVED.value
            filename = FORGE_STORIES_DRAFT_FILENAME
        else:
            phase_title = "Tasks"
            noun_plural = "tasks"
            noun_singular = "task"
            phase_action = "implementation"
            item_label = "Description"
            approval_label = ForgeLabel.TASK_APPROVED.value
            filename = FORGE_TASKS_DRAFT_FILENAME

        header = f"### 📋 Proposed {phase_title} Draft\n\nThe following {phase_title} have been proposed for {phase_action}:\n\n"
        table = "| ID | Summary | Target Repo |\n|----|---------|-------------|\n"
        for item in items:
            escaped_summary = _escape_cell(item.summary)
            escaped_repo = _escape_cell(item.repo or "unknown")
            if item.excluded:
                summary_cell = f"~~{escaped_summary}~~ *(excluded)*"
                repo_cell = f"~~{escaped_repo}~~"
            else:
                summary_cell = escaped_summary
                repo_cell = escaped_repo
            table += f"| {item.id} | {summary_cell} | {repo_cell} |\n"
        table += "\n---\n\n"

        details = ""
        for item in items:
            heading_summary = f"~~{item.summary}~~ *(excluded)*" if item.excluded else item.summary
            details += f"#### {item.id}. {heading_summary} (Repo: {item.repo or 'unknown'})\n"
            if item.description:
                details += f"**{item_label}:**\n{item.description}\n\n"
            else:
                details += "\n"

        footer = (
            "## 🤖 Forge interaction options\n\n"
            f"- ✅ **Approve:** comment `/forge approve` or add `{approval_label}` to continue.\n"
            f"- ♻️ **Revise all {noun_plural}:** add a comment starting with `!` on this ticket.\n"
            f"- 🔧 **Revise a single {noun_singular}:** add a comment starting with `!` on the {phase_title.rstrip('s')}.\n"
            "- ❓ **Ask a question:** add a Jira comment starting with `?`."
        )

        full_comment = header + table + details + footer

        if len(full_comment) > limit or len(items) > 15:
            condensed_header = (
                f"### 📋 Proposed {phase_title} Draft (Condensed)\n\n"
                "⚠️ **Warning:** The proposed plan exceeds character or size limits for detailed display in a comment. "
                f"Please refer to the attached `{filename}` for full implementation plan details.\n\n"
            )
            condensed_table_header = (
                "| ID | Summary | Target Repo |\n|----|---------|-------------|\n"
            )

            rows = []
            for item in items:
                escaped_summary = _escape_cell(item.summary)
                escaped_repo = _escape_cell(item.repo or "unknown")
                if item.excluded:
                    summary_cell = f"~~{escaped_summary}~~ *(excluded)*"
                    repo_cell = f"~~{escaped_repo}~~"
                else:
                    summary_cell = escaped_summary
                    repo_cell = escaped_repo
                rows.append(f"| {item.id} | {summary_cell} | {repo_cell} |\n")

            full_condensed_comment = (
                condensed_header + condensed_table_header + "".join(rows) + "\n" + footer
            )

            if len(full_condensed_comment) > limit:
                allowed_rows: list[str] = []
                for i, row in enumerate(rows, start=1):
                    temp_warning = f"\n⚠️ Showing first {i} items — see attached draft JSON for the full list.\n\n"
                    temp_comment = (
                        condensed_header
                        + condensed_table_header
                        + "".join(allowed_rows + [row])
                        + temp_warning
                        + footer
                    )
                    if len(temp_comment) <= limit:
                        allowed_rows.append(row)
                    else:
                        break

                count = len(allowed_rows)
                warning_note = f"\n⚠️ Showing first {count} items — see attached draft JSON for the full list.\n\n"
                condensed_comment = (
                    condensed_header
                    + condensed_table_header
                    + "".join(allowed_rows)
                    + warning_note
                    + footer
                )
            else:
                condensed_comment = full_condensed_comment

            return DraftManager._truncate_to_jira_limit(condensed_comment, limit)

        return DraftManager._truncate_to_jira_limit(full_comment, limit)

    @staticmethod
    def slice_task_draft(draft: ForgeDecompositionDraft) -> dict[str, ForgeDecompositionDraft]:
        """Slice the task draft by Epic key, producing a dict of epic_key -> slice_draft.

        Each slice draft will have items resequenced starting from 1.
        """
        slices: dict[str, list[Any]] = {}
        for item in draft.items:
            ek = item.epic_key
            if not ek:
                continue
            if ek not in slices:
                slices[ek] = []
            slices[ek].append(item)

        result: dict[str, ForgeDecompositionDraft] = {}
        for ek, items in slices.items():
            # Clone and re-sequence item IDs
            resequenced_items = []
            for idx, item in enumerate(items, start=1):
                cloned_item = item.model_copy()
                cloned_item.id = idx
                resequenced_items.append(cloned_item)

            result[ek] = ForgeDecompositionDraft(
                parent_key=ek,
                phase=draft.phase,
                items=resequenced_items,
                version=draft.version,
                created_at=draft.created_at,
                updated_at=draft.updated_at,
            )
        return result

    @staticmethod
    async def save_task_draft_with_slices(
        jira_client: JiraClient,
        feature_key: str,
        draft: ForgeDecompositionDraft,
    ) -> None:
        """Save the aggregate task draft on the Feature, and attach sliced drafts to each associated Epic."""
        # 1. Save main aggregate draft on Feature
        await DraftManager.save_draft_attachment(
            jira_client, feature_key, draft, FORGE_TASKS_DRAFT_FILENAME
        )

        # 2. Slice and attach per-Epic drafts
        slices = DraftManager.slice_task_draft(draft)
        for epic_key, slice_draft in slices.items():
            try:
                # Save slice with the same task draft filename on the Epic ticket
                await DraftManager.save_draft_attachment(
                    jira_client, epic_key, slice_draft, FORGE_TASKS_DRAFT_FILENAME
                )
            except Exception as e:
                logger.warning(f"Failed to save sliced draft to Epic {epic_key}: {e}")
