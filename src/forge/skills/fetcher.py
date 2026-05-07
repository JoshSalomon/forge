"""Git ref SHA resolution for skill fetching.

Uses ``git ls-remote`` to resolve branch/tag refs to their exact commit SHAs.
When a ref resolves to nothing (empty output), the ref is assumed to already
be a commit SHA and ``None`` is returned.
"""

import asyncio
import logging

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
