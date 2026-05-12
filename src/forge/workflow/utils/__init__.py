"""Utility functions for workflow state management and comment classification."""

from datetime import datetime
from typing import Any

from forge.workflow.utils.comment_classifier import CommentType, classify_comment
from forge.workflow.utils.jira_status import (
    post_status_comment,
    set_implementing_label,
    transition_tasks_to_in_progress,
)
from forge.workflow.utils.qa_summary import post_qa_summary_if_needed


def update_state_timestamp(state: dict[str, Any]) -> dict[str, Any]:
    """Update the state timestamp.

    Args:
        state: Current workflow state.

    Returns:
        State with updated timestamp.
    """
    return {**state, "updated_at": datetime.utcnow().isoformat()}


def set_paused(state: dict[str, Any], node_name: str) -> dict[str, Any]:
    """Set the state to paused at a specific node.

    Args:
        state: Current workflow state.
        node_name: Name of the node where paused.

    Returns:
        Updated state.
    """
    return {
        **state,
        "current_node": node_name,
        "is_paused": True,
        "updated_at": datetime.utcnow().isoformat(),
    }


def resume_state(state: dict[str, Any]) -> dict[str, Any]:
    """Resume a paused state.

    Args:
        state: Current workflow state.

    Returns:
        Updated state with is_paused=False.
    """
    return {
        **state,
        "is_paused": False,
        "updated_at": datetime.utcnow().isoformat(),
    }


def set_error(state: dict[str, Any], error: str) -> dict[str, Any]:
    """Record an error in the state.

    Args:
        state: Current workflow state.
        error: Error message.

    Returns:
        Updated state with error recorded.
    """
    return {
        **state,
        "last_error": error,
        "retry_count": state.get("retry_count", 0) + 1,
        "updated_at": datetime.utcnow().isoformat(),
    }


__all__ = [
    "CommentType",
    "classify_comment",
    "post_qa_summary_if_needed",
    "post_status_comment",
    "resume_state",
    "set_error",
    "set_implementing_label",
    "set_paused",
    "transition_tasks_to_in_progress",
    "update_state_timestamp",
]
