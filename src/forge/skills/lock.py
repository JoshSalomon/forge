"""Lock file management for the skills system.

Provides functions to read and write ``skills.lock`` files using YAML format
for human readability.  Writes use an atomic temp-file + rename pattern to
prevent partial writes from corrupting the lock file.

Lock file path convention: ``<skills_dir>/skills.lock``
"""

import logging
import os
import tempfile
from pathlib import Path

import yaml

from forge.skills.models import LockEntry, LockFile

logger = logging.getLogger(__name__)


def read_lock_file(lock_path: Path) -> LockFile:
    """Read and parse a skills lock file from *lock_path*.

    Returns an empty :class:`~forge.skills.models.LockFile` when:

    - The file does not exist (normal on first run).
    - The file contains invalid YAML or data that does not conform to the
      :class:`~forge.skills.models.LockFile` schema (error is logged).

    Args:
        lock_path: Absolute or relative path to the ``skills.lock`` file.

    Returns:
        Parsed :class:`~forge.skills.models.LockFile`, or an empty instance on
        any read/parse error.
    """
    if not lock_path.exists():
        logger.debug("Lock file not found at %s; returning empty LockFile", lock_path)
        return LockFile()

    try:
        raw = lock_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)

        # safe_load returns None for an empty file
        if data is None:
            logger.debug("Lock file %s is empty; returning empty LockFile", lock_path)
            return LockFile()

        return LockFile.model_validate(data)

    except yaml.YAMLError as exc:
        logger.error("Failed to parse YAML in lock file %s: %s", lock_path, exc)
        return LockFile()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load lock file %s: %s", lock_path, exc)
        return LockFile()


def update_lock_file(lock_path: Path, entry: LockEntry) -> None:
    """Update *lock_path* by inserting or replacing *entry*.

    If a :class:`~forge.skills.models.LockEntry` with the same ``source`` URL
    already exists it is replaced in-place; otherwise *entry* is appended.

    The write is performed atomically: the new content is first written to a
    temporary file in the same directory and then renamed over the target path
    so that readers never see a partially-written file.

    Args:
        lock_path: Absolute or relative path to the ``skills.lock`` file.
        entry: The :class:`~forge.skills.models.LockEntry` to insert or replace.
    """
    lock = read_lock_file(lock_path)

    # Replace existing entry with matching source, or append
    updated = False
    new_packages: list[LockEntry] = []
    for existing in lock.packages:
        if existing.source == entry.source:
            new_packages.append(entry)
            updated = True
        else:
            new_packages.append(existing)

    if not updated:
        new_packages.append(entry)

    lock.packages = new_packages

    # Serialise to a plain dict so yaml.dump produces human-readable output
    data = lock.model_dump(mode="json")

    yaml_text = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Atomic write: write to a temp file in the same directory, then rename
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=lock_path.parent, suffix=".lock.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(yaml_text)
        os.replace(tmp_path, lock_path)
    except Exception:
        # Clean up the temp file if the rename failed
        with __import__("contextlib").suppress(OSError):
            os.unlink(tmp_path)
        raise

    logger.debug("Lock file updated at %s (source=%s)", lock_path, entry.source)
