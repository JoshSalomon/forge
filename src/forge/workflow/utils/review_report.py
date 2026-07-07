"""Review exhaustion reporting utility."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from forge.sandbox.runner import ContainerResult


def collect_review_exhaustion(
    container_result: ContainerResult,
    task_key: str,
    step_name: str,
) -> dict[str, Any] | None:
    """Build exhaustion report entry if review cycles exhausted.

    Args:
        container_result: Result from container execution.
        task_key: Jira task key (e.g., "AISOS-2053").
        step_name: Workflow step name (e.g., "implement_task").

    Returns:
        Dict to append to state['review_exhaustion_report'], or None
        if review passed or no review ran.
    """
    if not container_result.review_exhausted:
        return None

    cycles = container_result.review_cycles
    last_cycle = cycles[-1]
    return {
        "task_key": task_key,
        "step_name": step_name,
        "skill": last_cycle.skill,
        "max_retries": last_cycle.max_cycles,
        "final_feedback": last_cycle.feedback,
        "cycles": [
            {"cycle": c.cycle, "verdict": c.verdict, "feedback": c.feedback}
            for c in cycles
        ],
    }
