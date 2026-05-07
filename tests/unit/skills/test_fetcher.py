"""Unit tests for forge.skills.fetcher – resolve_ref_sha."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.skills.fetcher import RefResolutionError, resolve_ref_sha

REPO_URL = "https://github.com/example/skills.git"
BRANCH_SHA = "abc123def456abc123def456abc123def456abc1"
TAG_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_process(stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> MagicMock:
    """Return a mock asyncio subprocess with the given output."""
    process = MagicMock()
    process.returncode = returncode
    process.communicate = AsyncMock(return_value=(stdout, stderr))
    process.kill = MagicMock()
    return process


def _patch_exec(process: MagicMock):
    """Return a patch context manager that mocks create_subprocess_exec as async."""
    return patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process))


# ---------------------------------------------------------------------------
# Success cases
# ---------------------------------------------------------------------------


class TestResolveRefShaSuccess:
    @pytest.mark.asyncio
    async def test_branch_ref_returns_sha(self):
        """resolve_ref_sha returns the SHA for a valid branch ref."""
        stdout = f"{BRANCH_SHA}\trefs/heads/main\n".encode()
        process = _make_process(stdout)

        with _patch_exec(process) as mock_exec:
            result = await resolve_ref_sha(REPO_URL, "main")

        mock_exec.assert_called_once_with(
            "git",
            "ls-remote",
            REPO_URL,
            "main",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert result == BRANCH_SHA

    @pytest.mark.asyncio
    async def test_tag_ref_returns_sha(self):
        """resolve_ref_sha returns the SHA for a valid tag ref."""
        stdout = f"{TAG_SHA}\trefs/tags/v1.0.0\n".encode()
        process = _make_process(stdout)

        with _patch_exec(process):
            result = await resolve_ref_sha(REPO_URL, "v1.0.0")

        assert result == TAG_SHA

    @pytest.mark.asyncio
    async def test_multiple_lines_returns_first_sha(self):
        """When ls-remote returns multiple lines, the first SHA is used."""
        other_sha = "1111111111111111111111111111111111111111"
        stdout = (f"{BRANCH_SHA}\trefs/heads/main\n{other_sha}\trefs/heads/main-old\n").encode()
        process = _make_process(stdout)

        with _patch_exec(process):
            result = await resolve_ref_sha(REPO_URL, "main")

        assert result == BRANCH_SHA

    @pytest.mark.asyncio
    async def test_empty_output_returns_none(self):
        """resolve_ref_sha returns None when git ls-remote returns empty output.

        Empty output indicates the ref is likely a direct commit SHA.
        """
        process = _make_process(stdout=b"", stderr=b"")

        with _patch_exec(process):
            result = await resolve_ref_sha(REPO_URL, "abc123def456")

        assert result is None

    @pytest.mark.asyncio
    async def test_whitespace_only_output_returns_none(self):
        """Whitespace-only stdout is treated the same as empty output."""
        process = _make_process(stdout=b"   \n  \n", stderr=b"")

        with _patch_exec(process):
            result = await resolve_ref_sha(REPO_URL, "abc123def456")

        assert result is None

    @pytest.mark.asyncio
    async def test_custom_timeout_is_forwarded(self):
        """The caller-supplied timeout is passed to asyncio.wait_for."""
        stdout = f"{BRANCH_SHA}\trefs/heads/main\n".encode()
        process = _make_process(stdout)

        with _patch_exec(process), patch("asyncio.wait_for", wraps=asyncio.wait_for) as mock_wait:
            result = await resolve_ref_sha(REPO_URL, "main", timeout=60)

        assert result == BRANCH_SHA
        _, kwargs = mock_wait.call_args
        assert kwargs.get("timeout") == 60


# ---------------------------------------------------------------------------
# Error / failure cases
# ---------------------------------------------------------------------------


class TestResolveRefShaErrors:
    @pytest.mark.asyncio
    async def test_nonzero_exit_code_raises(self):
        """Non-zero returncode from git raises RefResolutionError."""
        process = _make_process(
            stdout=b"",
            stderr=b"fatal: repository not found",
            returncode=128,
        )

        with _patch_exec(process), pytest.raises(RefResolutionError, match="exited with code 128"):
            await resolve_ref_sha(REPO_URL, "main")

    @pytest.mark.asyncio
    async def test_os_error_on_exec_raises(self):
        """OSError when spawning the subprocess raises RefResolutionError."""
        with (
            patch("asyncio.create_subprocess_exec", side_effect=OSError("git not found")),
            pytest.raises(RefResolutionError, match="Failed to start git ls-remote"),
        ):
            await resolve_ref_sha(REPO_URL, "main")

    @pytest.mark.asyncio
    async def test_timeout_raises_and_kills_process(self):
        """asyncio.TimeoutError is converted to RefResolutionError; process is killed."""
        process = MagicMock()
        process.kill = MagicMock()
        # communicate() hangs forever
        process.communicate = AsyncMock(side_effect=asyncio.TimeoutError)

        with (
            patch("asyncio.create_subprocess_exec", return_value=process),
            patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
            pytest.raises(RefResolutionError, match="timed out"),
        ):
            await resolve_ref_sha(REPO_URL, "main", timeout=1)

        process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_message_includes_url(self):
        """RefResolutionError messages include the source URL for diagnostics."""
        process = _make_process(stdout=b"", stderr=b"connection refused", returncode=1)

        with _patch_exec(process), pytest.raises(RefResolutionError, match=REPO_URL):
            await resolve_ref_sha(REPO_URL, "main")

    @pytest.mark.asyncio
    async def test_stderr_included_in_error_message(self):
        """Stderr from git is included in the raised RefResolutionError."""
        process = _make_process(
            stdout=b"",
            stderr=b"fatal: unable to connect to github.com",
            returncode=128,
        )

        with _patch_exec(process), pytest.raises(RefResolutionError, match="unable to connect"):
            await resolve_ref_sha(REPO_URL, "main")
