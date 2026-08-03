"""Plan approval gate for human-in-the-loop review of Epic decomposition.

The plan approval workflow uses labels:
- forge:plan-pending  - Plan awaiting approval
- forge:plan-approved - Plan approved (triggers task generation)

To approve: Change label from forge:plan-pending to forge:plan-approved
To request revision: Add a comment starting with ! (keep forge:plan-pending)
"""

import logging
from typing import TYPE_CHECKING, Any, cast

from langgraph.graph import END

from forge.api.routes.metrics import record_approval, record_revision_requested
from forge.workflow.feature.state import FeatureState as WorkflowState
from forge.workflow.utils import check_yolo_mode, set_paused

if TYPE_CHECKING:
    from forge.integrations.jira.client import JiraClient

logger = logging.getLogger(__name__)


def plan_approval_gate(state: WorkflowState) -> WorkflowState:
    """Pause workflow for Tech Lead to review Epic decomposition and plans.

    This gate pauses the workflow until a human approves or rejects
    the generated Epics and their implementation plans. The workflow resumes when:
    - Label changes to forge:plan-approved -> proceed to task generation
    - Comment starting with ! -> regenerate Epics with feedback

    Args:
        state: Current workflow state.

    Returns:
        State with is_paused=True, or error state if no epics.
    """
    ticket_key = state["ticket_key"]
    epic_keys = state.get("epic_keys", [])
    epic_count = len(epic_keys)

    # Validate that we actually have epics to approve
    if epic_count == 0 and check_yolo_mode(state):
        logger.error(
            f"Plan approval gate reached with 0 Epics for {ticket_key}. "
            "This indicates epic decomposition failed. Routing back to retry."
        )
        return {
            **state,
            "last_error": "No Epics generated - decomposition may have failed",
            "current_node": "decompose_epics",
            "retry_count": state.get("retry_count", 0) + 1,
        }

    logger.info(f"Plan approval gate: pausing workflow for {ticket_key} ({epic_count} Epics)")

    return cast(WorkflowState, set_paused(cast(dict[str, Any], state), "plan_approval_gate"))


async def route_plan_approval(state: WorkflowState) -> str:
    """Route based on plan approval status.

    Args:
        state: Current workflow state.

    Returns:
        Next node name or END.
    """
    if state.get("current_node") == "decompose_epics":
        return "decompose_epics"

    # Check if this is a question (Q&A mode) - check FIRST
    if state.get("is_question") and state.get("feedback_comment"):
        logger.info(f"Q&A mode: routing to answer_question for {state['ticket_key']}")
        return "answer_question"

    # YOLO mode: auto-approve without human input
    if check_yolo_mode(state):
        logger.info(f"YOLO mode: auto-approving plan for {state['ticket_key']}")
        record_approval("plan")
        return "provision_epics"

    # Check if revision requested
    if state.get("revision_requested"):
        feedback = state.get("feedback_comment", "")
        current_epic = state.get("current_epic_key")

        if current_epic:
            # Single Epic update
            logger.info(f"Single Epic revision requested for {current_epic}")
            record_revision_requested("plan")
            return "update_single_epic"
        elif feedback:
            # Feature-level regeneration
            logger.info(f"Full Epic regeneration requested for {state['ticket_key']}")
            record_revision_requested("plan")
            return "regenerate_all_epics"

    # Check if still paused - END and wait for approval webhook
    if state.get("is_paused"):
        logger.info(
            f"Plan approval gate: workflow paused for {state['ticket_key']}, "
            "waiting for approval webhook"
        )
        return END

    # All Epics approved, proceed to standard epic provisioning node
    logger.info(f"Epics approved for {state['ticket_key']}, proceeding to epic provisioning node")
    record_approval("plan")
    return "provision_epics"


async def provision_epics(state: WorkflowState) -> WorkflowState:
    """Standard LangGraph node to provision Epics from draft.

    Args:
        state: Current workflow state.

    Returns:
        Updated workflow state with epic_keys.
    """
    ticket_key = state["ticket_key"]
    if not state.get("epic_keys"):
        from forge.integrations.jira.client import JiraClient

        jira = JiraClient()
        try:
            epic_keys = await provision_epics_from_draft(state, jira)
            state = {**state, "epic_keys": epic_keys}
        except Exception as e:
            logger.error(
                f"Failed ticket provisioning during plan approval for {ticket_key}: {e}",
                exc_info=True,
            )
            raise
        finally:
            await jira.close()

    return state


async def provision_epics_from_draft(state: WorkflowState, jira: "JiraClient") -> list[str]:
    """Provision Epics from the plan draft attachment on Jira.

    Args:
        state: The workflow state dictionary.
        jira: An active JiraClient instance.

    Returns:
        List of created Epic ticket keys.
    """
    ticket_key = state["ticket_key"]
    from forge.models.workflow import ForgeLabel
    from forge.workflow.utils.draft_manager import FORGE_STORIES_DRAFT_FILENAME, DraftManager

    # Idempotency guard: check if Epics already exist on Jira with this parent label
    jql = f'labels = "forge:parent:{ticket_key}" AND issuetype = Epic'
    existing_issues = await jira.search_issues(jql)
    if isinstance(existing_issues, list) and existing_issues:
        existing_keys = [issue.key for issue in existing_issues]
        logger.info(
            f"Idempotency Guard: Found {len(existing_keys)} existing Epics for parent {ticket_key}: {existing_keys}. "
            f"Skipping duplicate ticket creation, deleting draft and returning existing keys."
        )
        try:
            await DraftManager.delete_draft_attachment(
                jira, ticket_key, FORGE_STORIES_DRAFT_FILENAME
            )
        except Exception as e:
            logger.warning(f"Draft deletion skipped or failed during idempotency recovery: {e}")
        return existing_keys

    logger.info(f"Downloading plan draft for {ticket_key}")
    draft = await DraftManager.get_draft_attachment(jira, ticket_key, FORGE_STORIES_DRAFT_FILENAME)
    if not draft:
        raise ValueError(f"Approved draft {FORGE_STORIES_DRAFT_FILENAME} not found on {ticket_key}")

    parent_issue = await jira.get_issue(ticket_key)
    project_key = parent_issue.project_key

    epic_keys = []
    for item in draft.items:
        if item.excluded:
            logger.info(f"Skipping excluded plan item {item.id}: {item.summary}")
            continue

        labels = [
            ForgeLabel.FORGE_MANAGED.value,
            f"forge:parent:{ticket_key}",
        ]
        if item.repo and "/" in item.repo:
            labels.append(f"repo:{item.repo}")

        epic_key = await jira.create_epic(
            project_key=project_key,
            summary=item.summary,
            description=item.description,
            parent_key=ticket_key,
            labels=labels,
        )
        epic_keys.append(epic_key)

    # Delete the draft only after 100% successful ticket creation
    await DraftManager.delete_draft_attachment(jira, ticket_key, FORGE_STORIES_DRAFT_FILENAME)
    logger.info(
        f"Successfully provisioned {len(epic_keys)} Epics for {ticket_key} and deleted draft"
    )
    return epic_keys
