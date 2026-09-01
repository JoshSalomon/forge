"""Task approval gate for human-in-the-loop review before implementation.

The task approval workflow uses labels:
- forge:task-pending  - Tasks awaiting approval before implementation
- forge:task-approved - Tasks approved (triggers implementation)

To approve: Change label from forge:task-pending to forge:task-approved
To request revision: Add a comment starting with ! (keeps forge:task-pending)
"""

import logging
from typing import TYPE_CHECKING, Any, cast

from langgraph.graph import END

from forge.api.routes.metrics import record_approval, record_revision_requested
from forge.integrations.jira.client import JiraClient
from forge.workflow.feature.state import FeatureState as WorkflowState
from forge.workflow.utils import check_direct_mode, check_yolo_mode, set_paused

if TYPE_CHECKING:
    from forge.integrations.jira.client import JiraClient

logger = logging.getLogger(__name__)


async def _assign_tiers_for_approved_tasks(task_keys: list[str]) -> None:
    """Reconcile the model tier for each Task presented for approval (BR-011).

    Approved-draft Tasks are Forge-managed Tasks that must carry a model-tier
    label + marker. This reconciles each pending Task's tier: it is a no-op for
    Tasks that already carry an in-sync auto-owned label and assigns a tier to
    any Task that lacks one (``allow_overwrite=False`` never clobbers a
    human-owned tier). Per BR-013 / SC-001, tier assignment failures MUST NOT
    fail or roll back the approval flow, so each call is failure-isolated.
    """
    jira = JiraClient()
    try:
        for task_key in task_keys:
            try:
                await jira.resolve_and_maybe_assign_tier(task_key, allow_overwrite=False)
            except Exception as e:
                logger.warning(f"Failed to assign model tier to Task {task_key}: {e}")
    finally:
        await jira.close()


async def task_approval_gate(state: WorkflowState) -> WorkflowState:
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

    # Validate that we actually have tasks to approve
    if task_count == 0 and (check_yolo_mode(state) or check_direct_mode(state)):
        logger.error(
            f"Task approval gate reached with 0 Tasks for {ticket_key}. "
            "This indicates task generation failed. Routing back to retry."
        )
        return cast(
            WorkflowState,
            {
                **state,
                "last_error": "No Tasks generated - task generation may have failed",
                "current_node": "generate_tasks",
                "retry_count": state.get("retry_count", 0) + 1,
            },
        )

    # Assign the model tier for the approved-draft Tasks (BR-011). This is
    # failure-isolated so a tier-assignment failure can never fail or roll back
    # the approval gate (BR-013 / SC-001).
    try:
        await _assign_tiers_for_approved_tasks(task_keys)
    except Exception as e:
        logger.warning(f"Model-tier assignment step failed for {ticket_key}: {e}")

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
    if state.get("current_node") == "generate_tasks":
        return "generate_tasks"

    ticket_key = state["ticket_key"]

    # Check if this is a question (Q&A mode) - check FIRST
    if state.get("is_question") and state.get("feedback_comment"):
        logger.info(f"Q&A mode: routing to answer_question for {ticket_key}")
        return "answer_question"

    # YOLO mode: auto-approve without human input
    if check_yolo_mode(state):
        logger.info(f"YOLO mode: auto-approving tasks for {ticket_key}")
        record_approval("task")
        return "provision_tasks"

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

    # Tasks approved, proceed to standard task provisioning node
    logger.info(f"Tasks approved for {ticket_key}, proceeding to task provisioning node")
    record_approval("task")
    return "provision_tasks"


async def provision_tasks(state: WorkflowState) -> WorkflowState:
    """Standard LangGraph node to provision Tasks from draft.

    Args:
        state: Current workflow state.

    Returns:
        Updated workflow state with task_keys and tasks_by_repo.
    """
    ticket_key = state["ticket_key"]
    if not state.get("task_keys"):
        from forge.integrations.jira.client import JiraClient

        jira = JiraClient()
        try:
            task_keys, tasks_by_repo = await provision_tasks_from_draft(state, jira)
            state = {**state, "task_keys": task_keys, "tasks_by_repo": tasks_by_repo}
        except Exception as e:
            logger.error(
                f"Failed ticket provisioning during task approval for {ticket_key}: {e}",
                exc_info=True,
            )
            raise
        finally:
            await jira.close()

    return state


async def provision_tasks_from_draft(
    state: WorkflowState, jira: "JiraClient"
) -> tuple[list[str], dict[str, list[str]]]:
    """Provision Tasks from the task draft attachment on Jira.

    Args:
        state: The workflow state dictionary.
        jira: An active JiraClient instance.

    Returns:
        Tuple of (task_keys, tasks_by_repo).
    """
    ticket_key = state["ticket_key"]
    from forge.config import get_settings
    from forge.integrations.jira.client import MissingProjectConfig
    from forge.models.workflow import ForgeLabel

    # Idempotency guard: check if Tasks already exist on Jira with this parent label
    jql = f'labels = "forge:parent:{ticket_key}" AND issuetype = Task'
    existing_issues = await jira.search_issues(jql)
    if isinstance(existing_issues, list) and existing_issues:
        existing_keys = [issue.key for issue in existing_issues]
        logger.info(
            f"Idempotency Guard: Found {len(existing_keys)} existing Tasks for parent {ticket_key}: {existing_keys}. "
            "Skipping duplicate ticket creation and returning existing keys."
        )

        # Reconstruct tasks_by_repo from existing issues
        reconstructed_tasks_by_repo: dict[str, list[str]] = {}
        for issue in existing_issues:
            repo = "unknown"
            for label in issue.labels:
                if label.startswith("repo:"):
                    repo = label[len("repo:") :]
                    break

            if repo not in reconstructed_tasks_by_repo:
                reconstructed_tasks_by_repo[repo] = []
            reconstructed_tasks_by_repo[repo].append(issue.key)

        return existing_keys, reconstructed_tasks_by_repo

    settings = get_settings()
    logger.info(f"Retrieving task draft for {ticket_key} from state")
    draft_raw = state.get("tasks_draft")
    if not draft_raw:
        raise ValueError(
            f"Approved draft 'tasks_draft' not found in workflow state for {ticket_key}"
        )
    if isinstance(draft_raw, dict):
        from forge.models.draft import ForgeDecompositionDraft

        draft = ForgeDecompositionDraft.model_validate(draft_raw)
    else:
        draft = draft_raw

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
            logger.warning(
                f"Task '{item.summary}' (ID {item.id}) is missing epic_key. Reparenting to first Epic '{epic_key}'."
            )
            try:
                await jira.add_comment(
                    ticket_key,
                    f"⚠️ **Notice:** Task '{item.summary}' (ID: {item.id}) was missing an Epic parent key. "
                    f"It has been reparented to Epic '{epic_key}'.",
                )
            except Exception as comment_err:
                logger.warning(f"Failed to post reparenting notice comment: {comment_err}")

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

    logger.info(
        f"Successfully provisioned {len(task_keys)} Tasks across {len(tasks_by_repo)} repos"
    )
    return task_keys, tasks_by_repo
