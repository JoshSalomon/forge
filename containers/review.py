"""Review loop data structures for the container review engine.

This module provides core data structures used by the review loop engine:

- ReviewConfig: Configuration parsed from review.md frontmatter
- ReviewCycleData: Data captured for each review cycle iteration
- Verdict: Enum for review outcomes (APPROVED/REJECTED)
- parse_review_config: Parser for review.md YAML frontmatter
- detect_review_md: Locates review.md with project override precedence
- parse_verdict: Extracts verdict and feedback from review output text
- write_cycle_file: Writes review cycle data to JSON file
"""

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Default max retries for review loops
DEFAULT_MAX_RETRIES = 3

# Environment variable for max retries override
ENV_MAX_RETRIES = "AUTO_REVIEW_MAX_RETRIES"


class Verdict(StrEnum):
    """Review verdict indicating approval or rejection."""

    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class ReviewConfig:
    """Configuration for review loops parsed from review.md frontmatter.

    Attributes:
        max_retries: Maximum number of retry attempts (default: 3).
        instructions: Review instructions from the markdown body.
    """

    max_retries: int = DEFAULT_MAX_RETRIES
    instructions: str = ""


@dataclass
class ReviewCycleData:
    """Data captured for a single review cycle iteration.

    Attributes:
        cycle: Current cycle number (1-indexed).
        max_cycles: Maximum cycles allowed.
        verdict: Review outcome ("approved" or "rejected").
        feedback: Reviewer feedback text.
        skill: Name of the skill that performed the review.
        elapsed_seconds: Time taken for this review cycle.
        timestamp: ISO 8601 UTC timestamp of cycle completion.
    """

    cycle: int
    max_cycles: int
    verdict: str
    feedback: str
    skill: str
    elapsed_seconds: float
    timestamp: str


# Regex pattern for YAML frontmatter: --- at start, optional content, --- delimiter
# The frontmatter content between delimiters may be empty (just newlines)
_FRONTMATTER_PATTERN = re.compile(
    r"\A---\s*\n(.*?)---\s*\n?(.*)",
    re.DOTALL,
)


def parse_review_config(review_md_path: Path) -> ReviewConfig:
    """Parse ReviewConfig from a review.md file with YAML frontmatter.

    The file format is:
    ```
    ---
    max_retries: 5
    ---
    Review instructions here...
    ```

    Priority for max_retries:
    1. YAML frontmatter value
    2. AUTO_REVIEW_MAX_RETRIES environment variable
    3. Default value (3)

    If the file does not exist or YAML parsing fails, returns defaults
    with a warning log (per BR-006).

    Args:
        review_md_path: Path to the review.md file.

    Returns:
        ReviewConfig with parsed values or defaults.
    """
    # Get env var fallback for max_retries
    env_max_retries: int | None = None
    env_value = os.environ.get(ENV_MAX_RETRIES)
    if env_value is not None:
        try:
            env_max_retries = int(env_value)
        except ValueError:
            logger.warning(
                "Invalid %s value %r, ignoring",
                ENV_MAX_RETRIES,
                env_value,
            )

    # Default config to return on errors
    default_max_retries = env_max_retries if env_max_retries is not None else DEFAULT_MAX_RETRIES

    if not review_md_path.exists():
        logger.warning("Review config not found at %s, using defaults", review_md_path)
        return ReviewConfig(max_retries=default_max_retries, instructions="")

    try:
        content = review_md_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to read %s: %s, using defaults", review_md_path, e)
        return ReviewConfig(max_retries=default_max_retries, instructions="")

    # Try to parse frontmatter
    match = _FRONTMATTER_PATTERN.match(content)
    if not match:
        # No frontmatter found - treat entire content as instructions
        return ReviewConfig(max_retries=default_max_retries, instructions=content.strip())

    frontmatter_raw = match.group(1)
    instructions = match.group(2).strip()

    # Parse YAML frontmatter
    try:
        frontmatter = yaml.safe_load(frontmatter_raw)
    except yaml.YAMLError as e:
        logger.warning(
            "Malformed YAML frontmatter in %s: %s, using defaults",
            review_md_path,
            e,
        )
        return ReviewConfig(max_retries=default_max_retries, instructions=instructions)

    # Handle empty frontmatter
    if frontmatter is None:
        frontmatter = {}

    # Extract max_retries with fallback chain
    max_retries = default_max_retries
    if isinstance(frontmatter, dict) and "max_retries" in frontmatter:
        fm_value = frontmatter["max_retries"]
        if isinstance(fm_value, int):
            max_retries = fm_value
        else:
            logger.warning(
                "Invalid max_retries value %r in %s, using default",
                fm_value,
                review_md_path,
            )

    return ReviewConfig(max_retries=max_retries, instructions=instructions)


def find_skill_file(
    skill_name: str,
    filename: str,
    skill_paths: list[str] | None = None,
) -> Path | None:
    """Find a file within a skill directory across all mounted skill paths.

    Searches each skill path for {skill_name}/{filename}. Later paths in the
    list take precedence (matching the convention where project-specific skill
    directories are mounted after defaults).

    The skill paths are read from the AGENT_SKILL_PATHS environment variable
    if not provided explicitly.

    Args:
        skill_name: Name of the skill (e.g., "implement-task").
        filename: File to find (e.g., "SKILL.md", "review.md").
        skill_paths: List of skill directory paths. If None, reads from
            AGENT_SKILL_PATHS env var.

    Returns:
        Path to the file if found, None otherwise.
    """
    if skill_paths is None:
        env_val = os.environ.get("AGENT_SKILL_PATHS", "")
        skill_paths = [p.strip() for p in env_val.split(",") if p.strip()]

    found: Path | None = None
    for base in skill_paths:
        candidate = Path(base) / skill_name / filename
        if candidate.is_file():
            logger.debug("Found %s at %s", filename, candidate)
            found = candidate

    if found is None:
        logger.debug(
            "No %s found for skill %r in paths: %s",
            filename,
            skill_name,
            skill_paths,
        )

    return found


def detect_review_md(
    skill_name: str,
    skill_paths: list[str] | None = None,
) -> Path | None:
    """Detect the review.md file for a skill.

    Delegates to find_skill_file, searching all mounted skill paths.

    Args:
        skill_name: Name of the skill (e.g., "implement-task").
        skill_paths: Explicit skill paths to search.

    Returns:
        Path to review.md if found, None otherwise.
    """
    return find_skill_file(skill_name, "review.md", skill_paths)


def parse_verdict(output_text: str) -> tuple[Verdict, str]:
    """Extract verdict and feedback from review output text.

    Performs case-insensitive search for "APPROVED" and "REJECTED" markers.
    When both markers are present, the first occurrence wins (checks APPROVED first).

    Args:
        output_text: The raw output text from the review process.

    Returns:
        Tuple of (Verdict, feedback_string):
        - (Verdict.APPROVED, "") when APPROVED marker is found first
        - (Verdict.REJECTED, feedback) when REJECTED marker is found first,
          where feedback is the text following the marker
        - (Verdict.REJECTED, "Verdict could not be parsed") when neither marker found
    """
    # Normalize for case-insensitive matching
    text_upper = output_text.upper()

    # Find positions of both markers (case-insensitive)
    approved_pos = text_upper.find("APPROVED")
    rejected_pos = text_upper.find("REJECTED")

    # Neither marker found
    if approved_pos == -1 and rejected_pos == -1:
        return (Verdict.REJECTED, "Verdict could not be parsed")

    # Determine which marker comes first
    # If APPROVED found and (REJECTED not found OR APPROVED comes first)
    if approved_pos != -1 and (rejected_pos == -1 or approved_pos < rejected_pos):
        return (Verdict.APPROVED, "")

    # REJECTED marker found first (or only REJECTED found)
    # Extract feedback from text following the REJECTED marker
    # Skip past "REJECTED" keyword (8 characters)
    feedback_start = rejected_pos + len("REJECTED")
    feedback = output_text[feedback_start:].strip()

    return (Verdict.REJECTED, feedback)


def review_cycle_dir_name(task_key: str, skill_name: str) -> str:
    """Build the directory name for review cycle files.

    Format: {task_key}__{skill_name} (double underscore separator).

    Args:
        task_key: Jira task key (e.g., "AISOS-2126").
        skill_name: Skill name (e.g., "implement-task").

    Returns:
        Directory name string.
    """
    return f"{task_key}__{skill_name}"


def write_cycle_file(
    workspace: Path, task_key: str, skill_name: str, cycle_data: ReviewCycleData
) -> None:
    """Write review cycle data to a JSON file.

    Creates .forge/reviews/{task_key}__{skill_name}/ and writes
    review_cycle_N.json where N is the cycle number.

    Args:
        workspace: Path to the workspace root directory.
        task_key: Jira task key (e.g., "AISOS-2126").
        skill_name: Skill name (e.g., "implement-task").
        cycle_data: ReviewCycleData instance to serialize.
    """
    dir_name = review_cycle_dir_name(task_key, skill_name)
    output_dir = workspace / ".forge" / "reviews" / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / f"review_cycle_{cycle_data.cycle}.json"

    # Convert dataclass to dict
    data = asdict(cycle_data)

    # Ensure verdict is lowercase string (handle Verdict enum or string)
    verdict_value = data["verdict"]
    if hasattr(verdict_value, "value"):
        # It's a Verdict enum
        data["verdict"] = verdict_value.value
    else:
        # It's already a string, ensure lowercase
        data["verdict"] = str(verdict_value).lower()

    # Write JSON with proper formatting (2-space indent for readability)
    output_file.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    logger.debug("Wrote review cycle file: %s", output_file)
