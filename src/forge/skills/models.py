"""Pydantic data models for skill configuration and lock file entries.

Skills can be installed in two modes:
- path mode: a single directory path within the repository is mounted as a skill source
- skill_mapping mode: specific skills are mapped from repository paths to skill names
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator


class SkillEntry(BaseModel):
    """Configuration entry for a skill package to be installed.

    Exactly one of ``path`` or ``skill_mapping`` must be provided:

    - **path mode**: the value of ``path`` is used as the skill source directory
      relative to the cloned repository root.
    - **skill_mapping mode**: ``skill_mapping`` maps skill names (keys) to their
      paths inside the repository (values).
    """

    source: str
    """Git URL of the repository containing the skill(s)."""

    ref: str | None = None
    """Optional tag, branch, or commit SHA to check out. Defaults to the
    repository's default branch when not set."""

    path: str | None = None
    """Directory path within the repository (path mode).  Mutually exclusive
    with ``skill_mapping``."""

    skill_mapping: dict[str, str] | None = None
    """Mapping of ``skill_name -> repo_path`` (skill_mapping mode).  Mutually
    exclusive with ``path``."""

    @model_validator(mode="after")
    def _validate_mode_exclusivity(self) -> "SkillEntry":
        """Enforce that exactly one of path or skill_mapping is set."""
        has_path = self.path is not None
        has_mapping = self.skill_mapping is not None

        if has_path and has_mapping:
            raise ValueError(
                "SkillEntry must specify exactly one of 'path' or 'skill_mapping', not both."
            )
        if not has_path and not has_mapping:
            raise ValueError("SkillEntry must specify exactly one of 'path' or 'skill_mapping'.")

        return self


class LockEntry(BaseModel):
    """Lock file entry recording the resolved state of an installed skill package."""

    source: str
    """Git URL of the skill repository."""

    ref: str
    """The ref (tag/branch/SHA) that was requested."""

    resolved_commit: str
    """The exact commit SHA that was resolved and fetched."""

    mode: Literal["path", "skill_mapping"]
    """Installation mode used for this entry."""

    path: str | None = None
    """Resolved path within the repository (only set when mode is 'path')."""

    skill_mapping: dict[str, str] | None = None
    """Resolved skill mapping (only set when mode is 'skill_mapping')."""

    target: str
    """Project directory name where the skills were installed."""

    skills: list[str]
    """Names of the skills that were installed from this entry."""

    fetched_at: datetime
    """Timestamp when this entry was fetched and locked."""


class LockFile(BaseModel):
    """Container model for a skill lock file.

    Holds a list of :class:`LockEntry` records and provides convenience
    methods for looking up entries.
    """

    packages: list[LockEntry] = []
    """All locked skill packages."""

    def find_by_source(self, source: str) -> LockEntry | None:
        """Return the first :class:`LockEntry` whose source URL matches.

        Args:
            source: The Git URL to search for.

        Returns:
            The matching :class:`LockEntry`, or ``None`` if not found.
        """
        for entry in self.packages:
            if entry.source == source:
                return entry
        return None
