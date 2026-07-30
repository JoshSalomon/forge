"""Task approval gate for human-in-the-loop review before implementation.

The task approval workflow uses labels:
- forge:task-pending  - Tasks awaiting approval before implementation
- forge:task-approved - Tasks approved (triggers implementation)

To approve: Change label from forge:task-pending to forge:task-approved
To request revision: Add a comment starting with ! (keeps forge:task-pending)
"""

import logging
from typing import Any, cast

from langgraph.graph import END

from forge.api.routes.metrics import record_approval, record_revision_requested
from forge.workflow.feature.state import FeatureState as WorkflowState
from forge.workflow.utils import set_paused

logger = logging.getLogger(__name__)


def task_approval_gate(state: WorkflowState) -> WorkflowState:
    """Pause workflow for human to review generated Tasks before implementation.

    This gate pauses the workflow after task generation, allowing humans to:
    - Review the generated tasks for accuracy and completeness
    - Modify tasks manually in Jira if needed
    - Approve when ready for AI implementation

    The workflow resumes when:
    - Label changes to forge:task-approved -> proceed to implementation
    - Comment starting with ! -> regenerate tasks

    Args:
        state: Current workflow state.

    Returns:
        State with is_paused=True, or error state if no tasks.
    """
    ticket_key = state["ticket_key"]
    task_keys = state.get("task_keys", [])
    task_count = len(task_keys)

    is_yolo = state.get("yolo_mode") or "forge:yolo" in state.get("context", {}).get("labels", [])

    # Validate that we actually have tasks to approve
    if task_count == 0 and is_yolo:
        logger.error(
            f"Task approval gate reached with 0 Tasks for {ticket_key}. "
            "This indicates task generation failed. Routing back to retry."
        )
        return {
            **state,
            "last_error": "No Tasks generated - task generation may have failed",
            "current_node": "generate_tasks",
            "retry_count": state.get("retry_count", 0) + 1,
        }

    logger.info(
        f"Task approval gate: pausing workflow for {ticket_key} "
        f"({task_count} Tasks pending implementation approval)"
    )

    return cast(WorkflowState, set_paused(cast(dict[str, Any], state), "task_approval_gate"))


async def route_task_approval(state: WorkflowState) -> str:
    """Route based on task approval status.

    Routing logic:
    - Question (Q&A mode) -> answer_question
    - YOLO mode enabled -> auto-approve without human input
    - ! comment on specific Task ticket -> update_single_task
    - ! comment on Feature ticket -> regenerate_all_tasks
    - Label changed to approved -> task_router
    - Still paused -> END (wait for webhook)

    Args:
        state: Current workflow state.

    Returns:
        Next node name or END.
    """
    ticket_key = state["ticket_key"]

    # Check if this is a question (Q&A mode) - check FIRST
    if state.get("is_question") and state.get("feedback_comment"):
        logger.info(f"Q&A mode: routing to answer_question for {ticket_key}")
        return "answer_question"

    # YOLO mode: auto-approve without human input
    if state.get("yolo_mode"):
        logger.info(f"YOLO mode: auto-approving tasks for {ticket_key}")
        record_approval("task")
        return "task_router"

    # Check if revision requested (! feedback comment added)
    if state.get("revision_requested"):
        feedback = state.get("feedback_comment", "")
        current_task = state.get("current_task_key")
        current_epic = state.get("current_epic_key")

        if current_task:
            # Single Task update - comment was on a specific Task
            logger.info(f"Single Task revision requested for {current_task}")
            record_revision_requested("task")
            return "update_single_task"
        elif current_epic:
            # Epic-level regeneration - comment was on a specific Epic
            logger.info(f"Epic Task regeneration requested for {current_epic} on {ticket_key}")
            record_revision_requested("task")
            return "regenerate_epic_tasks"
        elif feedback:
            # Feature-level regeneration - comment was on Feature
            logger.info(f"Full Task regeneration requested for {ticket_key}: {feedback[:100]}...")
            record_revision_requested("task")
            return "regenerate_all_tasks"

    # Check if still paused - END and wait for approval webhook
    if state.get("is_paused"):
        logger.info(
            f"Task approval gate: workflow paused for {ticket_key}, "
            "waiting for forge:task-approved label"
        )
        return END

    # Handle standard (non-YOLO) approval draft ticket provisioning
    if not state.get("task_keys"):
        from forge.config import get_settings
        from forge.integrations.jira.client import JiraClient, MissingProjectConfig
        from forge.models.workflow import ForgeLabel
        from forge.workflow.utils.draft_manager import FORGE_TASKS_DRAFT_FILENAME, DraftManager

        settings = get_settings()
        jira = JiraClient()
        try:
            logger.info(f"Downloading task draft for {ticket_key}")
            draft = await DraftManager.get_draft_attachment(
                jira, ticket_key, FORGE_TASKS_DRAFT_FILENAME
            )
            if not draft:
                raise ValueError(
                    f"Approved draft {FORGE_TASKS_DRAFT_FILENAME} not found on {ticket_key}"
                )

            parent_issue = await jira.get_issue(ticket_key)
            project_key = parent_issue.project_key

            task_keys: list[str] = []
            tasks_by_repo: dict[str, list[str]] = {}
            for item in draft.items:
                if item.excluded:
                    logger.info(f"Skipping excluded task item {item.id}: {item.summary}")
                    continue

                # Fallback repository logic (mimics task_generation.py)
                repo = item.repo
                if not repo or repo == "unknown" or "/" not in repo:
                    try:
                        repo = await jira.get_project_default_repo(project_key)
                    except MissingProjectConfig:
                        repo = (
                            settings.github_default_repo
                            if not settings.forge_require_project_config
                            else ""
                        )

                if not repo or "/" not in repo:
                    logger.warning(
                        f"Task '{item.summary}' has no valid repo. "
                        "Set repo labels on Feature/Epic or GITHUB_DEFAULT_REPO."
                    )
                    repo = "unknown"

                # Epic parent key logic:
                # If draft item has epic_key set, use it.
                # Else fallback to state's epic_keys.
                epic_key = item.epic_key
                if not epic_key and state.get("epic_keys"):
                    epic_key = state["epic_keys"][0]

                # Labels
                labels = [
                    ForgeLabel.FORGE_MANAGED.value,
                    f"forge:parent:{ticket_key}",
                ]
                if repo and repo != "unknown":
                    labels.append(f"repo:{repo}")

                task_key = await jira.create_task(
                    project_key=project_key,
                    summary=item.summary,
                    description=item.description,
                    parent_key=epic_key,
                    labels=labels,
                )
                task_keys.append(task_key)

                if repo and repo != "unknown":
                    if repo not in tasks_by_repo:
                        tasks_by_repo[repo] = []
                    tasks_by_repo[repo].append(task_key)

            # Store the newly created keys
            state["task_keys"] = task_keys
            state["tasks_by_repo"] = tasks_by_repo

            # Delete the draft only after 100% successful ticket creation
            await DraftManager.delete_draft_attachment(jira, ticket_key, FORGE_TASKS_DRAFT_FILENAME)
            logger.info(
                f"Successfully provisioned {len(task_keys)} Tasks across {len(tasks_by_repo)} repos and deleted draft"
            )
        except Exception as e:
            logger.error(
                f"Failed ticket provisioning during task approval for {ticket_key}: {e}",
                exc_info=True,
            )
            raise
        finally:
            await jira.close()

    # Tasks approved, proceed to implementation
    logger.info(f"Tasks approved for {ticket_key}, proceeding to implementation")
    record_approval("task")
    return "task_router"
