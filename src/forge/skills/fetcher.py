"""Git ref SHA resolution and repository cloning for skill fetching.

Uses ``git ls-remote`` to resolve branch/tag refs to their exact commit SHAs.
When a ref resolves to nothing (empty output), the ref is assumed to already
be a commit SHA and ``None`` is returned.

Provides :func:`clone_skill_package` to clone a repository into a temporary
directory, using a shallow clone for speed and falling back to a full clone
when the ref is a commit SHA or when the shallow clone fails.  The
:func:`clone_context` context manager wraps the clone and guarantees cleanup.

Provides :func:`should_fetch_entry` to compare a resolved SHA against the
current lock file and determine whether a re-fetch is needed.
"""

import asyncio
import logging
import shutil
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from forge.skills.models import LockFile, SkillEntry

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30


class RefResolutionError(Exception):
    """Raised when git ls-remote fails due to a network or subprocess error."""


async def resolve_ref_sha(
    source_url: str,
    ref: str,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str | None:
    """Resolve a git ref to its commit SHA via ``git ls-remote``.

    Runs ``git ls-remote <source_url> <ref>`` asynchronously and parses the
    output to extract the SHA.  If the output is empty the ref is assumed to
    be a direct commit SHA (not a branch or tag name) and ``None`` is returned
    so the caller can use the ref as-is.

    Args:
        source_url: Git repository URL to query.
        ref: Branch name, tag name, or commit SHA to resolve.
        timeout: Maximum seconds to wait for the subprocess (default 30 s).

    Returns:
        The full 40-character commit SHA when the ref is a known branch/tag,
        or ``None`` when ``git ls-remote`` returns empty output (indicating the
        ref is likely already a commit SHA).

    Raises:
        RefResolutionError: When the subprocess cannot be started, times out,
            or exits with a non-zero return code.
    """
    cmd = ("git", "ls-remote", source_url, ref)
    logger.debug("Running: %s", " ".join(cmd))

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise RefResolutionError(
            f"Failed to start git ls-remote for {source_url!r}: {exc}"
        ) from exc

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
    except TimeoutError as exc:
        process.kill()
        raise RefResolutionError(
            f"git ls-remote timed out after {timeout}s for {source_url!r}"
        ) from exc

    if process.returncode != 0:
        stderr = stderr_bytes.decode(errors="replace").strip()
        raise RefResolutionError(
            f"git ls-remote exited with code {process.returncode} for {source_url!r}: {stderr}"
        )

    stdout = stdout_bytes.decode(errors="replace").strip()

    if not stdout:
        logger.debug("git ls-remote returned empty output; ref %r is likely a commit SHA", ref)
        return None

    # Output format: "<SHA>\t<refname>\n..."
    # Return the SHA from the first matching line.
    first_line = stdout.splitlines()[0]
    sha = first_line.split("\t", 1)[0].strip()
    logger.debug("Resolved ref %r -> %s for %s", ref, sha, source_url)
    return sha


# ---------------------------------------------------------------------------
# Repository cloning
# ---------------------------------------------------------------------------

_GIT_CLONE_TIMEOUT = 300  # seconds – generous limit for large repositories


class CloneError(Exception):
    """Raised when a repository cannot be cloned or checked out."""


async def _run_git(*args: str, timeout: float = _GIT_CLONE_TIMEOUT) -> tuple[int, str]:
    """Run a git sub-command and return *(returncode, stderr)*.

    Args:
        *args: Arguments passed directly to ``git`` (e.g. ``"clone"``, ``"--depth"``, …).
        timeout: Maximum seconds to wait for the command.

    Returns:
        A ``(returncode, stderr)`` tuple.

    Raises:
        CloneError: If the subprocess cannot be started or times out.
    """
    cmd = ("git", *args)
    logger.debug("Running: %s", " ".join(cmd))

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise CloneError(f"Failed to start git {args[0]!r}: {exc}") from exc

    try:
        _stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
    except TimeoutError as exc:
        process.kill()
        raise CloneError(f"git {args[0]!r} timed out after {timeout}s") from exc

    stderr = stderr_bytes.decode(errors="replace").strip()
    return process.returncode, stderr


def _looks_like_commit_sha(ref: str) -> bool:
    """Return True when *ref* looks like a hex commit SHA (7–40 hex chars)."""
    return len(ref) >= 7 and all(c in "0123456789abcdefABCDEF" for c in ref)


async def clone_skill_package(
    source_url: str,
    ref: str | None,
    timeout: float = _GIT_CLONE_TIMEOUT,
) -> Path:
    """Clone a skill repository into a temporary directory.

    Strategy:

    1. **Shallow clone** – attempted first when *ref* is given and does *not*
       look like a bare commit SHA.  Runs::

           git clone --depth 1 --branch <ref> <url> <temp_dir>

    2. **Full clone + checkout** – used when:

       * *ref* is ``None`` (clone default branch, no checkout needed), or
       * *ref* looks like a commit SHA (shallow clones can't target SHAs), or
       * the shallow clone exits with a non-zero return code.

       Runs::

           git clone <url> <temp_dir>
           git checkout <ref>   # only when ref is not None

    Args:
        source_url: Git repository URL to clone.
        ref: Branch name, tag name, commit SHA, or ``None`` for the default branch.
        timeout: Maximum seconds to allow for each individual git command.

    Returns:
        :class:`~pathlib.Path` pointing to the root of the cloned repository.

    Raises:
        CloneError: When cloning or checkout fails (invalid ref, network error, …).
    """
    temp_dir = tempfile.mkdtemp()
    logger.debug("Created temp directory %s for cloning %s", temp_dir, source_url)

    try:
        cloned = await _clone_into(source_url, ref, temp_dir, timeout)
    except Exception:
        # Clean up on error so the caller isn't left with a dangling directory.
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    if not cloned:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise CloneError(f"Failed to clone {source_url!r} at ref {ref!r}")

    return Path(temp_dir)


async def _clone_into(
    source_url: str,
    ref: str | None,
    temp_dir: str,
    timeout: float,
) -> bool:
    """Perform the actual clone, returning *True* on success.

    Internal helper split out to keep :func:`clone_skill_package` readable.
    """
    use_shallow = ref is not None and not _looks_like_commit_sha(ref)

    if use_shallow:
        rc, stderr = await _run_git(
            "clone",
            "--depth",
            "1",
            "--branch",
            ref,
            source_url,
            temp_dir,
            timeout=timeout,
        )
        if rc == 0:
            logger.info("Shallow clone succeeded for %s at ref %r", source_url, ref)
            return True

        logger.warning(
            "Shallow clone failed (rc=%d) for %s at ref %r – falling back to full clone: %s",
            rc,
            source_url,
            ref,
            stderr,
        )
        # Clear the (possibly partial) temp_dir before the full clone.
        shutil.rmtree(temp_dir, ignore_errors=True)

    # Full clone (into the same temp_dir path).
    rc, stderr = await _run_git("clone", source_url, temp_dir, timeout=timeout)
    if rc != 0:
        raise CloneError(f"git clone failed (rc={rc}) for {source_url!r}: {stderr}")

    logger.info("Full clone succeeded for %s", source_url)

    if ref is not None:
        rc, stderr = await _run_git("-C", temp_dir, "checkout", ref, timeout=timeout)
        if rc != 0:
            raise CloneError(f"git checkout {ref!r} failed (rc={rc}) in {temp_dir!r}: {stderr}")
        logger.info("Checked out ref %r in %s", ref, temp_dir)

    return True


@asynccontextmanager
async def clone_context(
    source_url: str,
    ref: str | None,
    timeout: float = _GIT_CLONE_TIMEOUT,
) -> AsyncGenerator[Path, None]:
    """Async context manager that clones a repository and cleans up on exit.

    Yields the :class:`~pathlib.Path` to the cloned directory.  The temporary
    directory is removed when the ``async with`` block exits, regardless of
    whether an exception was raised.

    Example::

        async with clone_context("https://github.com/org/skills.git", "v1.2.0") as repo:
            skills_dir = repo / "skills"
            ...
        # temp directory is gone here

    Args:
        source_url: Git repository URL to clone.
        ref: Branch name, tag name, commit SHA, or ``None`` for the default branch.
        timeout: Maximum seconds to allow for each git command.

    Yields:
        :class:`~pathlib.Path` to the root of the cloned repository.

    Raises:
        CloneError: Propagated from :func:`clone_skill_package` when cloning fails.
    """
    cloned_path = await clone_skill_package(source_url, ref, timeout=timeout)
    try:
        yield cloned_path
    finally:
        shutil.rmtree(cloned_path, ignore_errors=True)
        logger.debug("Removed temp directory %s", cloned_path)


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------


def should_fetch_entry(
    entry: SkillEntry,
    resolved_sha: str | None,
    lock: LockFile,
) -> bool:
    """Determine whether a skill entry needs to be fetched (or re-fetched).

    Compares the resolved SHA for *entry* against the ``resolved_commit`` stored
    in *lock*.  The comparison strategy depends on whether *resolved_sha* is
    available:

    * **resolved_sha is not None** – the ref was resolved via ``git ls-remote``
      (branch or tag).  Compare *resolved_sha* against the lock entry's
      ``resolved_commit``.

    * **resolved_sha is None** – the ref is assumed to already be a commit SHA
      (``git ls-remote`` returned empty output).  Compare ``entry.ref`` directly
      against the lock entry's ``resolved_commit``.

    Args:
        entry: The skill configuration entry whose source URL is used for the
            lock file lookup.
        resolved_sha: The SHA returned by :func:`resolve_ref_sha`, or ``None``
            when the entry's ref is itself a commit SHA.
        lock: The current lock file to search for an existing entry.

    Returns:
        ``True`` when the entry should be fetched (no lock entry exists or the
        SHA has changed), ``False`` when the lock entry is already up-to-date.
    """
    lock_entry = lock.find_by_source(entry.source)

    if lock_entry is None:
        logger.debug("No lock entry found for %s – fetch required", entry.source)
        return True

    # Determine which SHA to compare against the lock.
    current_sha = resolved_sha if resolved_sha is not None else entry.ref

    if current_sha != lock_entry.resolved_commit:
        logger.debug(
            "Skill %s is stale: lock has %s, current is %s – fetch required",
            entry.source,
            lock_entry.resolved_commit,
            current_sha,
        )
        return True

    logger.debug(
        "Skill %s is current at %s – no fetch needed",
        entry.source,
        lock_entry.resolved_commit,
    )
    return False
