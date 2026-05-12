"""Local code review node — reviews and fixes breaking issues before PR creation."""

import logging
from pathlib import Path

from forge.config import get_settings
from forge.integrations.jira import JiraClient
from forge.prompts import load_prompt
from forge.sandbox import ContainerRunner
from forge.workflow.feature.state import FeatureState as WorkflowState
from forge.workflow.utils import update_state_timestamp
from forge.workflow.utils.jira_status import post_status_comment
from forge.workspace.git_ops import GitOperations
from forge.workspace.manager import Workspace

logger = logging.getLogger(__name__)

MAX_REVIEW_ATTEMPTS = 2


def _validate_pass_number(raw_value: int | None) -> int | None:
    """Validate and sanitize pass number value.

    Args:
        raw_value: Raw pass number from state (may be None, negative, or invalid).

    Returns:
        Valid pass number (positive integer) or None if invalid.
    """
    if raw_value is None:
        return None

    # Check if it's an integer type
    if not isinstance(raw_value, int) or isinstance(raw_value, bool):
        logger.warning(
            f"Invalid pass_number type: {type(raw_value).__name__} (value: {raw_value}), "
            "expected int"
        )
        return None

    # Check if it's positive
    if raw_value < 1:
        logger.warning(
            f"Invalid pass_number value: {raw_value}, expected positive integer >= 1"
        )
        return None

    return raw_value


async def local_review_changes(state: WorkflowState) -> WorkflowState:
    """Review implemented changes locally and fix breaking issues before PR creation.

    Runs after all tasks are implemented but before the PR is created. The
    review skill runs inside the container with full workspace access and uses
    git diff to find changes vs main, fixing any breaking issues in-place.

    Args:
        state: Current workflow state.

    Returns:
        Updated state routing to create_pr.
    """
    ticket_key = state["ticket_key"]
    workspace_path = state.get("workspace_path")
    review_attempts = state.get("local_review_attempts", 0)

    if not workspace_path:
        logger.info(f"No workspace for local review on {ticket_key}, skipping")
        return update_state_timestamp({**state, "current_node": "create_pr"})

    # Safely retrieve and validate pass_number with defensive handling
    raw_pass_number = state.get("local_review_pass_number", 1)
    pass_number = _validate_pass_number(raw_pass_number)

    # Handle pass tracking failures with generic fallback
    if pass_number is None:
        logger.warning(
            f"Pass number tracking unavailable or corrupted for {ticket_key} "
            f"(raw value: {raw_pass_number!r}), using generic status comment"
        )
        settings = get_settings()
        jira = JiraClient(settings)
        try:
            await post_status_comment(
                jira,
                ticket_key,
                "🔧 Local review found issues, applying fixes.",
            )
        finally:
            await jira.close()
        # Set pass_number to 1 for state updates to prevent further issues
        pass_number = 1
    elif pass_number == 1:
        # Post initial status comment only on first pass
        logger.info(f"Starting local review pass 1 for {ticket_key}")
        settings = get_settings()
        jira = JiraClient(settings)
        try:
            await post_status_comment(
                jira,
                ticket_key,
                "🔍 Running local code review on changes before creating PR.",
            )
        finally:
            await jira.close()
    else:
        # Post fix pass status comment on subsequent review iterations
        logger.info(f"Starting local review pass {pass_number} for {ticket_key}")
        settings = get_settings()
        jira = JiraClient(settings)
        try:
            await post_status_comment(
                jira,
                ticket_key,
                f"🔧 Local review found issues, applying fixes (pass {pass_number}).",
            )
        finally:
            await jira.close()

    if review_attempts >= MAX_REVIEW_ATTEMPTS:
        logger.warning(
            f"Max local review attempts ({MAX_REVIEW_ATTEMPTS}) reached for "
            f"{ticket_key}, proceeding to PR"
        )
        return update_state_timestamp(
            {
                **state,
                "local_review_attempts": 0,
                "current_node": "create_pr",
            }
        )

    logger.info(
        f"Running local code review for {ticket_key} "
        f"(attempt {review_attempts + 1}/{MAX_REVIEW_ATTEMPTS})"
    )

    settings = get_settings()
    spec_content = state.get("spec_content", "Not available")
    guardrails = state.get("context", {}).get("guardrails", "")
    current_repo = state.get("current_repo", "")
    branch_name = state.get("context", {}).get("branch_name", "")

    task_description = load_prompt(
        "local-review",
        workspace_path=workspace_path,
        spec_content=spec_content[:3000] if spec_content else "Not available",
        guardrails=guardrails[:2000] if guardrails else "",
    )

    try:
        runner = ContainerRunner(settings)
        result = await runner.run(
            workspace_path=Path(workspace_path),
            task_summary="Local code review — fix breaking issues",
            task_description=task_description,
            ticket_key=ticket_key,
            task_key=f"{ticket_key}-review",
            repo_name=current_repo,
        )

        git = GitOperations(
            Workspace(
                path=Path(workspace_path),
                repo_name=current_repo,
                branch_name=branch_name,
                ticket_key=ticket_key,
            )
        )

        if git.has_uncommitted_changes():
            git.stage_all()
            git.commit(f"[{ticket_key}] fix: address breaking issues found in local review")
            logger.info(f"Committed local review fixes for {ticket_key}")

        # If the review found unfixed breaking issues and we have retries left, loop
        output = (result.stdout or "") + (result.stderr or "")
        has_unfixed = _has_unfixed_breaking_issues(output)

        if has_unfixed and review_attempts + 1 < MAX_REVIEW_ATTEMPTS:
            logger.warning(
                f"Breaking issues remain after review attempt {review_attempts + 1}, retrying"
            )
            return update_state_timestamp(
                {
                    **state,
                    "local_review_attempts": review_attempts + 1,
                    "local_review_pass_number": pass_number + 1,
                    "current_node": "local_review",
                }
            )

        if has_unfixed:
            logger.warning(
                f"Could not fix all breaking issues after {MAX_REVIEW_ATTEMPTS} attempts "
                f"for {ticket_key}, proceeding to PR"
            )
        else:
            logger.info(f"Local review passed for {ticket_key}")

        return update_state_timestamp(
            {
                **state,
                "local_review_attempts": 0,
                "current_node": "create_pr",
                "last_error": None,
            }
        )

    except Exception as e:
        # Non-blocking — a review failure should not stop the PR from being created
        logger.error(f"Local review failed for {ticket_key}: {e}")
        return update_state_timestamp(
            {
                **state,
                "local_review_attempts": 0,
                "current_node": "create_pr",
                "last_error": None,
            }
        )


def _has_unfixed_breaking_issues(output: str) -> bool:
    """Check if the review output indicates unfixed breaking issues remain."""
    lower = output.lower()
    return "unfixed" in lower and "breaking" in lower
