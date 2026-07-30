"""Plan approval gate for human-in-the-loop review of Epic decomposition.

The plan approval workflow uses labels:
- forge:plan-pending  - Plan awaiting approval
- forge:plan-approved - Plan approved (triggers task generation)

To approve: Change label from forge:plan-pending to forge:plan-approved
To request revision: Add a comment starting with ! (keep forge:plan-pending)
"""

import logging
from typing import Any, cast

from langgraph.graph import END

from forge.api.routes.metrics import record_approval, record_revision_requested
from forge.workflow.feature.state import FeatureState as WorkflowState
from forge.workflow.utils import set_paused

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

    is_yolo = state.get("yolo_mode") or "forge:yolo" in state.get("context", {}).get("labels", [])

    # Validate that we actually have epics to approve
    if epic_count == 0 and is_yolo:
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
    # Check if this is a question (Q&A mode) - check FIRST
    if state.get("is_question") and state.get("feedback_comment"):
        logger.info(f"Q&A mode: routing to answer_question for {state['ticket_key']}")
        return "answer_question"

    # YOLO mode: auto-approve without human input
    if state.get("yolo_mode"):
        logger.info(f"YOLO mode: auto-approving plan for {state['ticket_key']}")
        record_approval("plan")
        return "generate_tasks"

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

    # Handle standard (non-YOLO) approval draft ticket provisioning
    if not state.get("epic_keys"):
        ticket_key = state["ticket_key"]
        from forge.integrations.jira.client import JiraClient
        from forge.models.workflow import ForgeLabel
        from forge.workflow.utils.draft_manager import FORGE_STORIES_DRAFT_FILENAME, DraftManager

        jira = JiraClient()
        try:
            logger.info(f"Downloading plan draft for {ticket_key}")
            draft = await DraftManager.get_draft_attachment(
                jira, ticket_key, FORGE_STORIES_DRAFT_FILENAME
            )
            if not draft:
                raise ValueError(
                    f"Approved draft {FORGE_STORIES_DRAFT_FILENAME} not found on {ticket_key}"
                )

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

            # Store the newly created keys
            state["epic_keys"] = epic_keys

            # Delete the draft only after 100% successful ticket creation
            await DraftManager.delete_draft_attachment(
                jira, ticket_key, FORGE_STORIES_DRAFT_FILENAME
            )
            logger.info(
                f"Successfully provisioned {len(epic_keys)} Epics for {ticket_key} and deleted draft"
            )
        except Exception as e:
            logger.error(
                f"Failed ticket provisioning during plan approval for {ticket_key}: {e}",
                exc_info=True,
            )
            raise
        finally:
            await jira.close()

    # All Epics approved, proceed to task generation
    logger.info(f"Epics approved for {state['ticket_key']}, proceeding to task generation")
    record_approval("plan")
    return "generate_tasks"
