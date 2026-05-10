"""End-to-end tests for the ``forge skills`` CLI commands.

Exercises the full command stack – argument parsing → handler → filesystem –
using real temporary directories and a local bare Git repository as a stand-in
for remote Git sources.

Test scenarios covered:

- TS-017: Install from Git URL with ``--project`` flag
- TS-018: Install from local path
- TS-019: List installed skills with sources
- TS-020: Update command refreshes from lock file

Each test uses pytest's ``tmp_path`` fixture for fully isolated filesystem
state and never touches the real working directory.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from forge.skills.cli_handlers import (
    cmd_skills_install,
    cmd_skills_list,
    cmd_skills_update,
)
from forge.skills.lock import read_lock_file, update_lock_file
from forge.skills.models import LockEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _install_args(
    source: str = "https://github.com/example/skills.git",
    project: str | None = None,
    default: bool = False,
    ref: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(source=source, project=project, default=default, ref=ref)


def _list_args() -> argparse.Namespace:
    return argparse.Namespace()


def _update_args(project: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(project=project)


def _make_skill_dir(parent: Path, name: str, skill_md_content: str = "# Skill") -> Path:
    """Create a single skill directory with a SKILL.md marker file."""
    skill_dir = parent / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_md_content, encoding="utf-8")
    return skill_dir


def _make_skill_package(root: Path, skill_names: list[str]) -> Path:
    """Create a local skill package directory with multiple skills.

    Produces the layout::

        root/
            skills/
                <skill_name>/
                    SKILL.md

    Returns *root* for convenience.
    """
    skills_container = root / "skills"
    for name in skill_names:
        _make_skill_dir(skills_container, name)
    return root


def _make_local_skill_source(root: Path, skill_names: list[str]) -> Path:
    """Create a flat local skill source directory.

    Produces the layout::

        root/
            <skill_name>/
                SKILL.md

    The source path itself (``root``) is what callers pass to
    ``forge skills install <source>``.
    """
    for name in skill_names:
        _make_skill_dir(root, name)
    return root


def _read_lock_yaml(skills_root: Path) -> dict:
    """Read and parse skills.lock as a plain dict."""
    lock_path = skills_root / "skills.lock"
    assert lock_path.exists(), f"Lock file not found at {lock_path}"
    return yaml.safe_load(lock_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def local_git_repo(tmp_path: Path) -> Path:
    """Create a local bare Git repository containing a skills package.

    The repository has:
    - A ``skills/`` subdirectory with two skills (``skill-alpha`` and
      ``skill-beta``), each containing a ``SKILL.md`` file.
    - A single commit on the default branch (``main``).

    Returns the path to the bare clone of the repository (suitable for use
    as a ``file://…`` remote URL in ``git clone`` calls).
    """
    # Build the source repo.
    repo_dir = tmp_path / "source_repo"
    repo_dir.mkdir()

    _make_skill_package(repo_dir, ["skill-alpha", "skill-beta"])

    subprocess.run(["git", "init", "-b", "main", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial skills commit"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )

    # Create a bare clone so tests can use it as a proper remote.
    bare_dir = tmp_path / "bare_repo.git"
    subprocess.run(
        ["git", "clone", "--bare", str(repo_dir), str(bare_dir)],
        check=True,
        capture_output=True,
    )

    return bare_dir


# ---------------------------------------------------------------------------
# TS-017: Install from Git URL with --project flag
# ---------------------------------------------------------------------------


class TestTS017InstallFromGitUrl:
    """TS-017: Install from Git URL with --project flag.

    Uses a real local Git repository via ``file://`` URL so that the full
    clone path is exercised.  No network access is needed.
    """

    @pytest.mark.asyncio
    async def test_creates_correct_directory_structure(
        self,
        tmp_path: Path,
        local_git_repo: Path,
    ):
        """Installing from a Git URL creates skills/<project>/<skill-name>/ dirs."""
        git_url = local_git_repo.as_uri()
        project_key = "MYPROJ"

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_install(_install_args(source=git_url, project=project_key))

        assert result == 0, f"Expected exit code 0, got {result}"

        # Verify directory layout.
        target_dir = tmp_path / "skills" / project_key
        assert target_dir.is_dir(), f"Target dir {target_dir} does not exist"
        assert (target_dir / "skill-alpha").is_dir()
        assert (target_dir / "skill-beta").is_dir()

    @pytest.mark.asyncio
    async def test_lock_file_is_created(
        self,
        tmp_path: Path,
        local_git_repo: Path,
    ):
        """A skills.lock file must be written after a successful Git install."""
        git_url = local_git_repo.as_uri()
        project_key = "MYPROJ"

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_install(_install_args(source=git_url, project=project_key))

        assert result == 0

        lock_path = tmp_path / "skills" / "skills.lock"
        assert lock_path.exists(), "skills.lock was not created"

        lock_data = _read_lock_yaml(tmp_path / "skills")
        packages = lock_data.get("packages", [])
        assert len(packages) == 1

        pkg = packages[0]
        assert pkg["source"] == git_url
        assert pkg["target"] == project_key
        assert "skill-alpha" in pkg["skills"]
        assert "skill-beta" in pkg["skills"]

    @pytest.mark.asyncio
    async def test_resolved_commit_is_recorded(
        self,
        tmp_path: Path,
        local_git_repo: Path,
    ):
        """The lock file entry must contain a non-empty resolved_commit SHA."""
        git_url = local_git_repo.as_uri()

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_install(_install_args(source=git_url, project="PROJ"))

        assert result == 0

        lock_data = _read_lock_yaml(tmp_path / "skills")
        pkg = lock_data["packages"][0]
        assert pkg["resolved_commit"], "resolved_commit should be a non-empty SHA"
        # A commit SHA is typically 40 hex chars; allow short SHAs too.
        assert len(pkg["resolved_commit"]) >= 7

    @pytest.mark.asyncio
    async def test_stdout_reports_installed_skills(
        self,
        tmp_path: Path,
        local_git_repo: Path,
        capsys,
    ):
        """Stdout should report the number of skills and the target directory."""
        git_url = local_git_repo.as_uri()

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_install(_install_args(source=git_url, project="PROJ"))

        assert result == 0
        out = capsys.readouterr().out
        assert "2 skills" in out
        assert "skills/PROJ/" in out

    @pytest.mark.asyncio
    async def test_exit_code_on_invalid_git_url(self, tmp_path: Path, capsys):
        """An unreachable Git URL must return exit code 1."""
        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_install(
                _install_args(
                    source="https://invalid.example.invalid/no-such-repo.git",
                    project="PROJ",
                )
            )

        assert result == 1
        err = capsys.readouterr().err
        assert "clone failed" in err or "Error" in err

    @pytest.mark.asyncio
    async def test_install_with_ref_records_ref_in_lock(
        self,
        tmp_path: Path,
        local_git_repo: Path,
    ):
        """When a ref is given, the lock entry must record it."""
        git_url = local_git_repo.as_uri()

        # Use 'main' as an explicit ref (the branch created by the fixture).
        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_install(
                _install_args(source=git_url, project="PROJ", ref="main")
            )

        assert result == 0

        lock_data = _read_lock_yaml(tmp_path / "skills")
        pkg = lock_data["packages"][0]
        assert pkg["ref"] == "main"

    @pytest.mark.asyncio
    async def test_missing_project_and_default_returns_exit_2(self, tmp_path: Path, capsys):
        """Missing both --project and --default must return exit code 2."""
        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_install(
                _install_args(source="https://github.com/example/repo.git")
            )

        assert result == 2
        err = capsys.readouterr().err
        assert "exactly one of --project or --default" in err


# ---------------------------------------------------------------------------
# TS-018: Install from local path
# ---------------------------------------------------------------------------


class TestTS018InstallFromLocalPath:
    """TS-018: Install from local path.

    Tests that ``forge skills install <local-path>`` copies the skill
    directories to ``skills/<project>/`` and creates a lock file entry.
    """

    @pytest.mark.asyncio
    async def test_copies_skills_to_target_dir(self, tmp_path: Path):
        """Skills from a local directory are copied to skills/<project>/."""
        source_dir = tmp_path / "local_source"
        _make_local_skill_source(source_dir, ["skill-one", "skill-two"])

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=project_dir):
            result = await cmd_skills_install(
                _install_args(source=str(source_dir), project="LOCAL")
            )

        assert result == 0

        target = project_dir / "skills" / "LOCAL"
        assert target.is_dir()
        assert (target / "skill-one").is_dir()
        assert (target / "skill-two").is_dir()

    @pytest.mark.asyncio
    async def test_lock_file_is_created_for_local_install(self, tmp_path: Path):
        """Installing from a local path must create a skills.lock entry."""
        source_dir = tmp_path / "local_source"
        _make_local_skill_source(source_dir, ["skill-one", "skill-two"])

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=project_dir):
            result = await cmd_skills_install(
                _install_args(source=str(source_dir), project="LOCAL")
            )

        assert result == 0

        lock_path = project_dir / "skills" / "skills.lock"
        assert lock_path.exists(), "skills.lock was not created"

        lock_data = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
        packages = lock_data.get("packages", [])
        assert len(packages) == 1
        pkg = packages[0]
        assert pkg["target"] == "LOCAL"
        assert "skill-one" in pkg["skills"]
        assert "skill-two" in pkg["skills"]

    @pytest.mark.asyncio
    async def test_local_path_install_to_default(self, tmp_path: Path, capsys):
        """Installing a local path with --default places skills in skills/default/."""
        source_dir = tmp_path / "local_source"
        _make_local_skill_source(source_dir, ["my-skill"])

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=project_dir):
            result = await cmd_skills_install(_install_args(source=str(source_dir), default=True))

        assert result == 0
        assert (project_dir / "skills" / "default" / "my-skill").is_dir()
        out = capsys.readouterr().out
        assert "skills/default/" in out

    @pytest.mark.asyncio
    async def test_nonexistent_local_path_returns_exit_1(self, tmp_path: Path, capsys):
        """A non-existent local path must return exit code 1."""
        missing = tmp_path / "does_not_exist"

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=project_dir):
            result = await cmd_skills_install(_install_args(source=str(missing), project="LOCAL"))

        assert result == 1
        err = capsys.readouterr().err
        assert "does not exist" in err

    @pytest.mark.asyncio
    async def test_reinstall_overwrites_existing_skills(self, tmp_path: Path):
        """Re-installing from a local path replaces existing skill directories."""
        source_dir = tmp_path / "local_source"
        _make_local_skill_source(source_dir, ["skill-one"])

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # First install.
        with patch("forge.skills.cli_handlers.Path.cwd", return_value=project_dir):
            await cmd_skills_install(_install_args(source=str(source_dir), project="LOCAL"))

        # Add a stale file to the installed skill to confirm overwrite.
        stale_file = project_dir / "skills" / "LOCAL" / "skill-one" / "stale.txt"
        stale_file.write_text("should be gone after reinstall")

        # Second install.
        with patch("forge.skills.cli_handlers.Path.cwd", return_value=project_dir):
            result = await cmd_skills_install(
                _install_args(source=str(source_dir), project="LOCAL")
            )

        assert result == 0
        assert not stale_file.exists(), "Stale file should have been removed on reinstall"

    @pytest.mark.asyncio
    async def test_stdout_reports_skill_names(self, tmp_path: Path, capsys):
        """Stdout lists each installed skill name."""
        source_dir = tmp_path / "local_source"
        _make_local_skill_source(source_dir, ["skill-one", "skill-two"])

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=project_dir):
            result = await cmd_skills_install(
                _install_args(source=str(source_dir), project="LOCAL")
            )

        assert result == 0
        out = capsys.readouterr().out
        assert "skill-one" in out
        assert "skill-two" in out


# ---------------------------------------------------------------------------
# TS-019: List installed skills with sources
# ---------------------------------------------------------------------------


class TestTS019ListInstalledSkills:
    """TS-019: List installed skills with sources.

    Verifies the hierarchical output format and that source URLs are correctly
    pulled from the lock file.
    """

    def _setup_installed_skills(
        self,
        tmp_path: Path,
        project: str,
        skill_names: list[str],
        source: str = "https://github.com/example/repo.git",
    ) -> Path:
        """Create installed skill directories and a matching lock file entry."""
        skills_root = tmp_path / "skills"
        project_dir = skills_root / project

        for name in skill_names:
            _make_skill_dir(project_dir, name)

        # Write a lock file entry.
        from datetime import UTC, datetime

        lock_path = skills_root / "skills.lock"
        entry = LockEntry(
            source=source,
            ref="main",
            resolved_commit="abc1234567890",
            mode="path",
            path=None,
            skill_mapping=None,
            target=project,
            skills=skill_names,
            fetched_at=datetime.now(tz=UTC),
        )
        update_lock_file(lock_path, entry)

        return tmp_path

    @pytest.mark.asyncio
    async def test_lists_project_and_skills(self, tmp_path: Path, capsys):
        """Output contains the project directory header and skill names."""
        self._setup_installed_skills(tmp_path, "MYPROJ", ["skill-a", "skill-b"])

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "skills/MYPROJ/" in out
        assert "skill-a" in out
        assert "skill-b" in out

    @pytest.mark.asyncio
    async def test_output_format_includes_source_url(self, tmp_path: Path, capsys):
        """Each skill line shows its source URL in brackets."""
        source_url = "https://github.com/example/repo.git"
        self._setup_installed_skills(tmp_path, "MYPROJ", ["skill-a"], source=source_url)

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert f"[{source_url}]" in out

    @pytest.mark.asyncio
    async def test_skill_count_in_header(self, tmp_path: Path, capsys):
        """The project header line includes the skill count."""
        self._setup_installed_skills(tmp_path, "MYPROJ", ["skill-a", "skill-b"])

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "2 skills" in out

    @pytest.mark.asyncio
    async def test_singular_skill_count(self, tmp_path: Path, capsys):
        """Header uses '1 skill' (singular) when exactly one skill is installed."""
        self._setup_installed_skills(tmp_path, "MYPROJ", ["only-skill"])

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "1 skill" in out
        assert "1 skills" not in out

    @pytest.mark.asyncio
    async def test_builtin_source_for_skills_not_in_lock(self, tmp_path: Path, capsys):
        """Skills absent from the lock file are displayed with [builtin] source."""
        # Create a skill directory but no lock file.
        skills_root = tmp_path / "skills"
        _make_skill_dir(skills_root / "default", "mystery-skill")

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "mystery-skill" in out
        assert "[builtin]" in out

    @pytest.mark.asyncio
    async def test_multiple_project_dirs_all_shown(self, tmp_path: Path, capsys):
        """All project directories are listed when multiple exist."""
        self._setup_installed_skills(tmp_path, "PROJ-A", ["skill-x"])
        self._setup_installed_skills(
            tmp_path,
            "PROJ-B",
            ["skill-y"],
            source="https://github.com/other/repo.git",
        )

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "skills/PROJ-A/" in out
        assert "skills/PROJ-B/" in out

    @pytest.mark.asyncio
    async def test_no_skills_dir_returns_0_with_message(self, tmp_path: Path, capsys):
        """When the skills/ directory does not exist, exit 0 with informative message."""
        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "No skills directory found" in out or "No skills" in out

    @pytest.mark.asyncio
    async def test_empty_skills_dir_returns_0_with_message(self, tmp_path: Path, capsys):
        """When skills/ exists but is empty, exit 0 with informative message."""
        (tmp_path / "skills").mkdir()

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "No skills installed" in out

    @pytest.mark.asyncio
    async def test_exit_code_is_always_0(self, tmp_path: Path):
        """cmd_skills_list always returns exit code 0."""
        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0


# ---------------------------------------------------------------------------
# TS-020: Update command refreshes from lock file
# ---------------------------------------------------------------------------


class TestTS020UpdateRefreshesFromLockFile:
    """TS-020: Update command refreshes packages from the lock file.

    Uses the mocked clone/resolve infrastructure so tests remain fast and
    deterministic.  The key invariant is that the lock file is updated with a
    new ``resolved_commit`` when a remote SHA differs from the stored value.
    """

    _OLD_SHA = "aaa1111111111111111111111111111111111111"
    _NEW_SHA = "bbb2222222222222222222222222222222222222"

    def _write_git_lock_entry(
        self,
        tmp_path: Path,
        source: str = "https://github.com/example/skills.git",
        target: str = "MYPROJ",
        resolved_commit: str = _OLD_SHA,
        skill_names: list[str] | None = None,
    ) -> Path:
        """Write a lock file entry and install matching skill directories."""
        from datetime import UTC, datetime

        if skill_names is None:
            skill_names = ["skill-a"]

        skills_root = tmp_path / "skills"

        # Create the installed skill directories (so list/update can find them).
        for name in skill_names:
            _make_skill_dir(skills_root / target, name)

        lock_path = skills_root / "skills.lock"
        entry = LockEntry(
            source=source,
            ref="main",
            resolved_commit=resolved_commit,
            mode="path",
            path=None,
            skill_mapping=None,
            target=target,
            skills=skill_names,
            fetched_at=datetime.now(tz=UTC),
        )
        update_lock_file(lock_path, entry)
        return tmp_path

    def _make_fake_clone(self, tmp_path: Path, skill_names: list[str] | None = None) -> Path:
        """Create a fake clone directory for the update command to use."""
        if skill_names is None:
            skill_names = ["skill-a"]
        clone_dir = tmp_path / "_fake_clone"
        skills_dir = clone_dir / "skills"
        for name in skill_names:
            _make_skill_dir(skills_dir, name)
        return clone_dir

    @pytest.mark.asyncio
    async def test_up_to_date_package_is_skipped(self, tmp_path: Path, capsys):
        """When the remote SHA matches the lock, no re-clone is performed."""
        source = "https://github.com/example/skills.git"
        self._write_git_lock_entry(tmp_path, source=source, resolved_commit=self._OLD_SHA)

        with (
            patch(
                "forge.skills.cli_handlers.resolve_ref_sha",
                new=AsyncMock(return_value=self._OLD_SHA),
            ),
            patch("forge.skills.cli_handlers.clone_skill_package") as mock_clone,
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = await cmd_skills_update(_update_args())

        assert result == 0
        mock_clone.assert_not_called()
        out = capsys.readouterr().out
        assert "Up to date" in out

    @pytest.mark.asyncio
    async def test_changed_sha_triggers_reclone(self, tmp_path: Path, capsys):
        """When the remote SHA differs from the lock, a re-clone is performed."""
        source = "https://github.com/example/skills.git"
        self._write_git_lock_entry(tmp_path, source=source, resolved_commit=self._OLD_SHA)
        clone_dir = self._make_fake_clone(tmp_path)

        with (
            patch(
                "forge.skills.cli_handlers.resolve_ref_sha",
                new=AsyncMock(return_value=self._NEW_SHA),
            ),
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                new=AsyncMock(return_value=clone_dir),
            ),
            patch(
                "forge.skills.cli_handlers._resolve_head_sha",
                new=AsyncMock(return_value=self._NEW_SHA),
            ),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = await cmd_skills_update(_update_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "Updating" in out

    @pytest.mark.asyncio
    async def test_lock_file_updated_after_reclone(self, tmp_path: Path):
        """The lock file must contain the new SHA after a successful update."""
        source = "https://github.com/example/skills.git"
        self._write_git_lock_entry(tmp_path, source=source, resolved_commit=self._OLD_SHA)
        clone_dir = self._make_fake_clone(tmp_path)

        with (
            patch(
                "forge.skills.cli_handlers.resolve_ref_sha",
                new=AsyncMock(return_value=self._NEW_SHA),
            ),
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                new=AsyncMock(return_value=clone_dir),
            ),
            patch(
                "forge.skills.cli_handlers._resolve_head_sha",
                new=AsyncMock(return_value=self._NEW_SHA),
            ),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = await cmd_skills_update(_update_args())

        assert result == 0

        # Verify the lock file was updated.
        lock_file = read_lock_file(tmp_path / "skills" / "skills.lock")
        assert len(lock_file.packages) == 1
        assert lock_file.packages[0].resolved_commit == self._NEW_SHA

    @pytest.mark.asyncio
    async def test_project_filter_limits_updates(self, tmp_path: Path, capsys):
        """``--project`` flag causes only matching entries to be processed."""
        source_a = "https://github.com/example/skills-a.git"
        source_b = "https://github.com/example/skills-b.git"
        self._write_git_lock_entry(
            tmp_path, source=source_a, target="PROJ-A", resolved_commit=self._OLD_SHA
        )
        self._write_git_lock_entry(
            tmp_path, source=source_b, target="PROJ-B", resolved_commit=self._OLD_SHA
        )
        clone_dir = self._make_fake_clone(tmp_path)

        with (
            patch(
                "forge.skills.cli_handlers.resolve_ref_sha",
                new=AsyncMock(return_value=self._NEW_SHA),
            ),
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                new=AsyncMock(return_value=clone_dir),
            ),
            patch(
                "forge.skills.cli_handlers._resolve_head_sha",
                new=AsyncMock(return_value=self._NEW_SHA),
            ),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = await cmd_skills_update(_update_args(project="PROJ-A"))

        assert result == 0
        out = capsys.readouterr().out
        # Only PROJ-A should appear in the update output.
        assert source_a in out or "PROJ-A" in out

        # PROJ-B lock entry must be untouched (still has the old SHA).
        lock_file = read_lock_file(tmp_path / "skills" / "skills.lock")
        proj_b = next(e for e in lock_file.packages if e.target == "PROJ-B")
        assert proj_b.resolved_commit == self._OLD_SHA

    @pytest.mark.asyncio
    async def test_no_lock_file_returns_0_with_message(self, tmp_path: Path, capsys):
        """When there is no lock file, update exits 0 with an informative message."""
        (tmp_path / "skills").mkdir()

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_update(_update_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "No packages in lock file" in out or "No packages" in out

    @pytest.mark.asyncio
    async def test_local_path_entries_skipped(self, tmp_path: Path, capsys):
        """Local-path lock entries are skipped during update with an informative message."""
        from datetime import UTC, datetime

        skills_root = tmp_path / "skills"
        _make_skill_dir(skills_root / "LOCAL", "skill-a")

        lock_path = skills_root / "skills.lock"
        entry = LockEntry(
            source="/some/local/path/to/skills",
            ref="",
            resolved_commit="",
            mode="path",
            path=None,
            skill_mapping=None,
            target="LOCAL",
            skills=["skill-a"],
            fetched_at=datetime.now(tz=UTC),
        )
        update_lock_file(lock_path, entry)

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_update(_update_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "Skipping" in out or "local path" in out

    @pytest.mark.asyncio
    async def test_resolve_error_returns_exit_1(self, tmp_path: Path, capsys):
        """A RefResolutionError during update must set exit code to 1."""
        from forge.skills.fetcher import RefResolutionError

        source = "https://github.com/example/skills.git"
        self._write_git_lock_entry(tmp_path, source=source, resolved_commit=self._OLD_SHA)

        with (
            patch(
                "forge.skills.cli_handlers.resolve_ref_sha",
                new=AsyncMock(side_effect=RefResolutionError("network failure")),
            ),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = await cmd_skills_update(_update_args())

        assert result == 1
        err = capsys.readouterr().err
        assert "Error" in err or "could not resolve" in err

    @pytest.mark.asyncio
    async def test_clone_error_returns_exit_1(self, tmp_path: Path, capsys):
        """A CloneError during update must set exit code to 1."""
        from forge.skills.fetcher import CloneError

        source = "https://github.com/example/skills.git"
        self._write_git_lock_entry(tmp_path, source=source, resolved_commit=self._OLD_SHA)

        with (
            patch(
                "forge.skills.cli_handlers.resolve_ref_sha",
                new=AsyncMock(return_value=self._NEW_SHA),
            ),
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                new=AsyncMock(side_effect=CloneError("disk full")),
            ),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = await cmd_skills_update(_update_args())

        assert result == 1
        err = capsys.readouterr().err
        assert "Error" in err or "clone failed" in err

    @pytest.mark.asyncio
    async def test_project_filter_no_match_returns_exit_0(self, tmp_path: Path, capsys):
        """``--project`` filter with no matching entries exits 0 with a message."""
        source = "https://github.com/example/skills.git"
        self._write_git_lock_entry(tmp_path, source=source, target="EXISTING")

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_update(_update_args(project="NONEXISTENT"))

        assert result == 0
        out = capsys.readouterr().out
        assert "Nothing to update" in out or "No lock file entries" in out

    @pytest.mark.asyncio
    async def test_update_installs_new_skill_files(self, tmp_path: Path):
        """After update, newly-added skill directories appear on the filesystem."""
        source = "https://github.com/example/skills.git"
        self._write_git_lock_entry(
            tmp_path, source=source, resolved_commit=self._OLD_SHA, skill_names=["skill-a"]
        )

        # The updated clone now has skill-a plus a new skill-b.
        clone_dir = self._make_fake_clone(tmp_path, skill_names=["skill-a", "skill-b"])

        with (
            patch(
                "forge.skills.cli_handlers.resolve_ref_sha",
                new=AsyncMock(return_value=self._NEW_SHA),
            ),
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                new=AsyncMock(return_value=clone_dir),
            ),
            patch(
                "forge.skills.cli_handlers._resolve_head_sha",
                new=AsyncMock(return_value=self._NEW_SHA),
            ),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = await cmd_skills_update(_update_args())

        assert result == 0

        # Both skills should now be present.
        target_dir = tmp_path / "skills" / "MYPROJ"
        assert (target_dir / "skill-a").is_dir()
        assert (target_dir / "skill-b").is_dir()
