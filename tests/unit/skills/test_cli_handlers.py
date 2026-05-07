"""Tests for forge.skills.cli_handlers – cmd_skills_install implementation."""

import argparse
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from forge.skills.cli_handlers import (
    _install_local_path,
    _is_git_url,
    cmd_skills_install,
    cmd_skills_list,
    cmd_skills_update,
)

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


# ---------------------------------------------------------------------------
# _is_git_url
# ---------------------------------------------------------------------------


class TestIsGitUrl:
    def test_https_url(self):
        assert _is_git_url("https://github.com/org/repo.git") is True

    def test_ssh_url(self):
        assert _is_git_url("ssh://git@github.com/org/repo.git") is True

    def test_git_protocol_url(self):
        assert _is_git_url("git://github.com/org/repo.git") is True

    def test_scp_style_url(self):
        assert _is_git_url("git@github.com:org/repo.git") is True

    def test_local_path_is_not_git_url(self):
        assert _is_git_url("/some/local/path") is False

    def test_relative_path_is_not_git_url(self):
        assert _is_git_url("./relative/path") is False

    def test_bare_name_is_not_git_url(self):
        assert _is_git_url("myskills") is False


# ---------------------------------------------------------------------------
# Argument validation (no git network calls)
# ---------------------------------------------------------------------------


class TestCmdSkillsInstallValidation:
    @pytest.mark.asyncio
    async def test_missing_project_and_default_returns_2(self, capsys):
        args = _install_args()  # neither --project nor --default
        result = await cmd_skills_install(args)
        assert result == 2
        err = capsys.readouterr().err
        assert "exactly one of --project or --default" in err

    @pytest.mark.asyncio
    async def test_both_project_and_default_returns_2(self, capsys):
        args = _install_args(project="MYPROJ", default=True)
        result = await cmd_skills_install(args)
        assert result == 2
        err = capsys.readouterr().err
        assert "mutually exclusive" in err

    @pytest.mark.asyncio
    async def test_non_existent_local_path_with_project_returns_1(self, capsys):
        args = _install_args(source="/nonexistent/local/path", project="MYPROJ")
        result = await cmd_skills_install(args)
        assert result == 1
        err = capsys.readouterr().err
        assert "does not exist" in err

    @pytest.mark.asyncio
    async def test_non_existent_local_path_with_default_returns_1(self, capsys):
        args = _install_args(source="./nonexistent-relative", default=True)
        result = await cmd_skills_install(args)
        assert result == 1
        err = capsys.readouterr().err
        assert "does not exist" in err


# ---------------------------------------------------------------------------
# Successful installation – project target
# ---------------------------------------------------------------------------


class TestCmdSkillsInstallGitUrl:
    """Tests for successful Git URL installation using mocked cloning."""

    def _make_fake_clone_dir(self, tmp_path: Path) -> Path:
        """Create a fake cloned repo with a skills/ subdirectory."""
        clone_dir = tmp_path / "clone"
        skills_dir = clone_dir / "skills"
        skill_a = skills_dir / "skill-a"
        skill_a.mkdir(parents=True)
        (skill_a / "SKILL.md").write_text("# Skill A")
        skill_b = skills_dir / "skill-b"
        skill_b.mkdir(parents=True)
        (skill_b / "SKILL.md").write_text("# Skill B")
        return clone_dir

    @pytest.mark.asyncio
    async def test_installs_to_project_dir(self, tmp_path: Path, capsys):
        clone_dir = self._make_fake_clone_dir(tmp_path)

        with (
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                new=AsyncMock(return_value=clone_dir),
            ),
            patch(
                "forge.skills.cli_handlers._resolve_head_sha",
                new=AsyncMock(return_value="abc1234"),
            ),
            patch("forge.skills.cli_handlers.update_lock_file") as mock_lock,
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            args = _install_args(project="MYPROJ")
            result = await cmd_skills_install(args)

        assert result == 0

        # Skills should be installed under tmp_path/skills/MYPROJ/
        target = tmp_path / "skills" / "MYPROJ"
        assert (target / "skill-a").is_dir()
        assert (target / "skill-b").is_dir()

        # Lock file should be updated
        mock_lock.assert_called_once()
        lock_path_arg, lock_entry_arg = mock_lock.call_args.args
        assert lock_path_arg == tmp_path / "skills" / "skills.lock"
        assert lock_entry_arg.source == "https://github.com/example/skills.git"
        assert lock_entry_arg.ref == ""
        assert lock_entry_arg.resolved_commit == "abc1234"
        assert lock_entry_arg.target == "MYPROJ"
        assert "skill-a" in lock_entry_arg.skills
        assert "skill-b" in lock_entry_arg.skills

        # Temp clone dir should be cleaned up
        assert not clone_dir.exists()

        # Success message should mention skill count
        out = capsys.readouterr().out
        assert "2 skills" in out
        assert "skills/MYPROJ/" in out

    @pytest.mark.asyncio
    async def test_installs_to_default_dir(self, tmp_path: Path, capsys):
        clone_dir = self._make_fake_clone_dir(tmp_path)

        with (
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                new=AsyncMock(return_value=clone_dir),
            ),
            patch(
                "forge.skills.cli_handlers._resolve_head_sha",
                new=AsyncMock(return_value="deadbeef"),
            ),
            patch("forge.skills.cli_handlers.update_lock_file"),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            args = _install_args(default=True)
            result = await cmd_skills_install(args)

        assert result == 0
        target = tmp_path / "skills" / "default"
        assert (target / "skill-a").is_dir()
        assert (target / "skill-b").is_dir()

        out = capsys.readouterr().out
        assert "skills/default/" in out

    @pytest.mark.asyncio
    async def test_installs_with_explicit_ref(self, tmp_path: Path):
        clone_dir = self._make_fake_clone_dir(tmp_path)

        with (
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                new=AsyncMock(return_value=clone_dir),
            ) as mock_clone,
            patch(
                "forge.skills.cli_handlers._resolve_head_sha",
                new=AsyncMock(return_value="v100sha"),
            ),
            patch("forge.skills.cli_handlers.update_lock_file") as mock_lock,
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            args = _install_args(project="MYPROJ", ref="v1.0.0")
            result = await cmd_skills_install(args)

        assert result == 0
        # clone_skill_package should be called with source and ref
        mock_clone.assert_awaited_once_with("https://github.com/example/skills.git", "v1.0.0")
        # Lock entry should record the ref
        _lock_path, lock_entry = mock_lock.call_args.args
        assert lock_entry.ref == "v1.0.0"

    @pytest.mark.asyncio
    async def test_uses_repo_root_when_no_skills_subdir(self, tmp_path: Path):
        """When the clone has no skills/ subdir, root skills are installed directly."""
        clone_dir = tmp_path / "clone"
        skill_x = clone_dir / "skill-x"
        skill_x.mkdir(parents=True)
        (skill_x / "SKILL.md").write_text("# X")

        with (
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                new=AsyncMock(return_value=clone_dir),
            ),
            patch(
                "forge.skills.cli_handlers._resolve_head_sha",
                new=AsyncMock(return_value="sha123"),
            ),
            patch("forge.skills.cli_handlers.update_lock_file"),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            args = _install_args(project="PROJ")
            result = await cmd_skills_install(args)

        assert result == 0
        assert (tmp_path / "skills" / "PROJ" / "skill-x").is_dir()

    @pytest.mark.asyncio
    async def test_single_skill_uses_singular_word(self, tmp_path: Path, capsys):
        clone_dir = tmp_path / "clone"
        skills_dir = clone_dir / "skills"
        only_skill = skills_dir / "solo"
        only_skill.mkdir(parents=True)
        (only_skill / "SKILL.md").write_text("# Solo")

        with (
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                new=AsyncMock(return_value=clone_dir),
            ),
            patch(
                "forge.skills.cli_handlers._resolve_head_sha",
                new=AsyncMock(return_value="sha"),
            ),
            patch("forge.skills.cli_handlers.update_lock_file"),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            args = _install_args(project="PROJ")
            result = await cmd_skills_install(args)

        assert result == 0
        out = capsys.readouterr().out
        assert "1 skill " in out  # singular, not "skills"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestCmdSkillsInstallErrors:
    @pytest.mark.asyncio
    async def test_clone_failure_returns_1(self, capsys):
        from forge.skills.fetcher import CloneError

        with patch(
            "forge.skills.cli_handlers.clone_skill_package",
            new=AsyncMock(side_effect=CloneError("network error")),
        ):
            args = _install_args(project="MYPROJ")
            result = await cmd_skills_install(args)

        assert result == 1
        err = capsys.readouterr().err
        assert "clone failed" in err
        assert "network error" in err

    @pytest.mark.asyncio
    async def test_install_error_returns_1_and_cleans_up(self, tmp_path: Path, capsys):
        clone_dir = tmp_path / "clone"
        # skills/ dir exists but is empty – install_path_mode returns [] not error,
        # so we simulate a FileNotFoundError instead.
        clone_dir.mkdir(parents=True)

        with (
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                new=AsyncMock(return_value=clone_dir),
            ),
            patch(
                "forge.skills.cli_handlers._resolve_head_sha",
                new=AsyncMock(return_value="sha"),
            ),
            patch(
                "forge.skills.cli_handlers.install_path_mode",
                side_effect=FileNotFoundError("missing source"),
            ),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            args = _install_args(project="MYPROJ")
            result = await cmd_skills_install(args)

        assert result == 1
        err = capsys.readouterr().err
        assert "could not install skills" in err
        # clone dir must be cleaned up even on error
        assert not clone_dir.exists()


# ---------------------------------------------------------------------------
# Lock file content
# ---------------------------------------------------------------------------


class TestCmdSkillsInstallLockFile:
    @pytest.mark.asyncio
    async def test_lock_entry_has_correct_fields(self, tmp_path: Path):
        clone_dir = tmp_path / "clone"
        skills_sub = clone_dir / "skills"
        (skills_sub / "tool").mkdir(parents=True)
        (skills_sub / "tool" / "SKILL.md").write_text("# Tool")

        with (
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                new=AsyncMock(return_value=clone_dir),
            ),
            patch(
                "forge.skills.cli_handlers._resolve_head_sha",
                new=AsyncMock(return_value="cafebabe"),
            ),
            patch("forge.skills.cli_handlers.update_lock_file") as mock_lock,
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            args = _install_args(
                source="https://github.com/org/repo.git",
                project="PROJ",
                ref="main",
            )
            result = await cmd_skills_install(args)

        assert result == 0
        mock_lock.assert_called_once()
        _lp, entry = mock_lock.call_args.args
        assert entry.source == "https://github.com/org/repo.git"
        assert entry.ref == "main"
        assert entry.resolved_commit == "cafebabe"
        assert entry.mode == "path"
        assert entry.target == "PROJ"
        assert entry.skills == ["tool"]
        assert entry.fetched_at is not None


# ---------------------------------------------------------------------------
# Local path installation
# ---------------------------------------------------------------------------


class TestInstallLocalPath:
    """Tests for _install_local_path and its integration via cmd_skills_install."""

    def _make_local_skills_dir(self, tmp_path: Path) -> Path:
        """Create a local skills directory with two skill subdirectories."""
        local_dir = tmp_path / "local-skills"
        (local_dir / "skill-alpha").mkdir(parents=True)
        (local_dir / "skill-alpha" / "SKILL.md").write_text("# Alpha")
        (local_dir / "skill-beta").mkdir(parents=True)
        (local_dir / "skill-beta" / "SKILL.md").write_text("# Beta")
        return local_dir

    def test_nonexistent_path_returns_1(self, capsys):
        result = _install_local_path("/nonexistent/path", "default")
        assert result == 1
        err = capsys.readouterr().err
        assert "does not exist" in err

    def test_file_path_returns_1(self, tmp_path: Path, capsys):
        a_file = tmp_path / "somefile.txt"
        a_file.write_text("hello")
        result = _install_local_path(str(a_file), "default")
        assert result == 1
        err = capsys.readouterr().err
        assert "not a directory" in err

    def test_copies_to_project_dir(self, tmp_path: Path, capsys):
        local_dir = self._make_local_skills_dir(tmp_path)

        with (
            patch("forge.skills.cli_handlers.update_lock_file") as mock_lock,
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = _install_local_path(str(local_dir), "myproj")

        assert result == 0
        target = tmp_path / "skills" / "myproj"
        assert (target / "skill-alpha").is_dir()
        assert (target / "skill-beta").is_dir()

        mock_lock.assert_called_once()
        _lp, entry = mock_lock.call_args.args
        assert entry.target == "myproj"
        assert "skill-alpha" in entry.skills
        assert "skill-beta" in entry.skills

        out = capsys.readouterr().out
        assert "2 skills" in out
        assert "skills/myproj/" in out

    def test_copies_to_default_dir(self, tmp_path: Path, capsys):
        local_dir = self._make_local_skills_dir(tmp_path)

        with (
            patch("forge.skills.cli_handlers.update_lock_file"),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = _install_local_path(str(local_dir), "default")

        assert result == 0
        target = tmp_path / "skills" / "default"
        assert (target / "skill-alpha").is_dir()
        assert (target / "skill-beta").is_dir()

        out = capsys.readouterr().out
        assert "skills/default/" in out

    def test_overwrites_existing_target(self, tmp_path: Path):
        local_dir = self._make_local_skills_dir(tmp_path)

        # Pre-populate the target with a stale skill.
        target = tmp_path / "skills" / "myproj"
        stale = target / "stale-skill"
        stale.mkdir(parents=True)
        (stale / "SKILL.md").write_text("# Stale")

        with (
            patch("forge.skills.cli_handlers.update_lock_file"),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = _install_local_path(str(local_dir), "myproj")

        assert result == 0
        # Stale skill must no longer exist.
        assert not (target / "stale-skill").exists()
        # New skills must be present.
        assert (target / "skill-alpha").is_dir()

    def test_lock_entry_source_is_resolved_path(self, tmp_path: Path):
        local_dir = self._make_local_skills_dir(tmp_path)

        with (
            patch("forge.skills.cli_handlers.update_lock_file") as mock_lock,
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = _install_local_path(str(local_dir), "myproj")

        assert result == 0
        _lp, entry = mock_lock.call_args.args
        # Source should be stored as the resolved (absolute) path string.
        assert entry.source == str(local_dir.resolve())
        # No commit SHA for local paths.
        assert entry.resolved_commit == ""
        assert entry.ref == ""
        assert entry.mode == "path"
        assert entry.fetched_at is not None

    def test_single_skill_uses_singular_word(self, tmp_path: Path, capsys):
        local_dir = tmp_path / "local-skills"
        (local_dir / "only-skill").mkdir(parents=True)
        (local_dir / "only-skill" / "SKILL.md").write_text("# Only")

        with (
            patch("forge.skills.cli_handlers.update_lock_file"),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = _install_local_path(str(local_dir), "myproj")

        assert result == 0
        out = capsys.readouterr().out
        assert "1 skill " in out  # singular


class TestCmdSkillsInstallLocalPath:
    """Integration tests for cmd_skills_install routing local paths."""

    @pytest.mark.asyncio
    async def test_local_absolute_path_with_project(self, tmp_path: Path, capsys):
        local_dir = tmp_path / "my-skills"
        (local_dir / "skill-x").mkdir(parents=True)
        (local_dir / "skill-x" / "SKILL.md").write_text("# X")

        with (
            patch("forge.skills.cli_handlers.update_lock_file"),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            args = argparse.Namespace(
                source=str(local_dir), project="MYPROJ", default=False, ref=None
            )
            result = await cmd_skills_install(args)

        assert result == 0
        assert (tmp_path / "skills" / "MYPROJ" / "skill-x").is_dir()
        out = capsys.readouterr().out
        assert "skills/MYPROJ/" in out

    @pytest.mark.asyncio
    async def test_local_path_with_default_flag(self, tmp_path: Path):
        local_dir = tmp_path / "my-skills"
        (local_dir / "skill-y").mkdir(parents=True)
        (local_dir / "skill-y" / "SKILL.md").write_text("# Y")

        with (
            patch("forge.skills.cli_handlers.update_lock_file"),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            args = argparse.Namespace(source=str(local_dir), project=None, default=True, ref=None)
            result = await cmd_skills_install(args)

        assert result == 0
        assert (tmp_path / "skills" / "default" / "skill-y").is_dir()

    @pytest.mark.asyncio
    async def test_nonexistent_local_path_returns_1(self, capsys):
        args = argparse.Namespace(
            source="/definitely/does/not/exist", project="PROJ", default=False, ref=None
        )
        result = await cmd_skills_install(args)
        assert result == 1
        err = capsys.readouterr().err
        assert "does not exist" in err

    @pytest.mark.asyncio
    async def test_local_path_is_file_returns_1(self, tmp_path: Path, capsys):
        a_file = tmp_path / "skills.zip"
        a_file.write_text("fake zip")
        args = argparse.Namespace(source=str(a_file), project="PROJ", default=False, ref=None)
        result = await cmd_skills_install(args)
        assert result == 1
        err = capsys.readouterr().err
        assert "not a directory" in err


# ---------------------------------------------------------------------------
# Stub handlers (cmd_skills_update only – cmd_skills_list is fully tested below)
# ---------------------------------------------------------------------------


class TestStubHandlers:
    @pytest.mark.asyncio
    async def test_cmd_skills_update_returns_0(self):
        args = argparse.Namespace()
        assert await cmd_skills_update(args) == 0


# ---------------------------------------------------------------------------
# cmd_skills_list
# ---------------------------------------------------------------------------


def _make_skill_dir(parent: Path, name: str) -> Path:
    """Create a skill directory with a SKILL.md file inside *parent*."""
    skill = parent / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(f"# {name}")
    return skill


def _list_args() -> argparse.Namespace:
    return argparse.Namespace()


class TestCmdSkillsList:
    """Tests for cmd_skills_list."""

    # ------------------------------------------------------------------
    # No skills directory / empty state
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_no_skills_dir_prints_message(self, tmp_path: Path, capsys):
        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "No skills directory" in out

    @pytest.mark.asyncio
    async def test_empty_skills_dir_prints_message(self, tmp_path: Path, capsys):
        (tmp_path / "skills").mkdir()

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "No skills installed" in out

    # ------------------------------------------------------------------
    # Basic listing
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_lists_skills_in_default_dir(self, tmp_path: Path, capsys):
        default_dir = tmp_path / "skills" / "default"
        _make_skill_dir(default_dir, "skill-a")
        _make_skill_dir(default_dir, "skill-b")

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "skills/default/" in out
        assert "skill-a" in out
        assert "skill-b" in out

    @pytest.mark.asyncio
    async def test_lists_skills_in_project_dir(self, tmp_path: Path, capsys):
        proj_dir = tmp_path / "skills" / "MYPROJ"
        _make_skill_dir(proj_dir, "my-skill")

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "skills/MYPROJ/" in out
        assert "my-skill" in out

    @pytest.mark.asyncio
    async def test_multiple_project_dirs_all_shown(self, tmp_path: Path, capsys):
        _make_skill_dir(tmp_path / "skills" / "default", "common")
        _make_skill_dir(tmp_path / "skills" / "proj-a", "alpha")
        _make_skill_dir(tmp_path / "skills" / "proj-b", "beta")

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "skills/default/" in out
        assert "skills/proj-a/" in out
        assert "skills/proj-b/" in out

    # ------------------------------------------------------------------
    # Skill counts in header
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_singular_skill_count(self, tmp_path: Path, capsys):
        proj_dir = tmp_path / "skills" / "default"
        _make_skill_dir(proj_dir, "solo")

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            await cmd_skills_list(_list_args())

        out = capsys.readouterr().out
        assert "1 skill)" in out  # singular (not "skills")

    @pytest.mark.asyncio
    async def test_plural_skill_count(self, tmp_path: Path, capsys):
        proj_dir = tmp_path / "skills" / "default"
        _make_skill_dir(proj_dir, "skill-one")
        _make_skill_dir(proj_dir, "skill-two")

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            await cmd_skills_list(_list_args())

        out = capsys.readouterr().out
        assert "2 skills)" in out

    # ------------------------------------------------------------------
    # Lock file – source attribution
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_skill_with_lock_entry_shows_source(self, tmp_path: Path, capsys):
        """Skills that appear in the lock file display their source URL."""
        from datetime import UTC, datetime

        import yaml

        from forge.skills.models import LockEntry, LockFile

        proj_dir = tmp_path / "skills" / "default"
        _make_skill_dir(proj_dir, "toolbox")

        # Write a real lock file.
        entry = LockEntry(
            source="https://github.com/org/skills.git",
            ref="main",
            resolved_commit="abc123",
            mode="path",
            path=None,
            skill_mapping=None,
            target="default",
            skills=["toolbox"],
            fetched_at=datetime.now(tz=UTC),
        )
        lock = LockFile(packages=[entry])
        lock_path = tmp_path / "skills" / "skills.lock"
        lock_path.write_text(yaml.dump(lock.model_dump(mode="json")))

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "https://github.com/org/skills.git" in out

    @pytest.mark.asyncio
    async def test_skill_without_lock_entry_shows_builtin(self, tmp_path: Path, capsys):
        """Skills absent from the lock file are marked as 'builtin'."""
        proj_dir = tmp_path / "skills" / "default"
        _make_skill_dir(proj_dir, "builtin-skill")
        # No lock file written → all skills are "builtin".

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "[builtin]" in out

    @pytest.mark.asyncio
    async def test_mixed_skills_show_correct_sources(self, tmp_path: Path, capsys):
        """Some skills locked, some builtin – both labelled correctly."""
        from datetime import UTC, datetime

        import yaml

        from forge.skills.models import LockEntry, LockFile

        proj_dir = tmp_path / "skills" / "default"
        _make_skill_dir(proj_dir, "from-git")
        _make_skill_dir(proj_dir, "local-builtin")

        entry = LockEntry(
            source="https://github.com/org/repo.git",
            ref="",
            resolved_commit="deadbeef",
            mode="path",
            path=None,
            skill_mapping=None,
            target="default",
            skills=["from-git"],
            fetched_at=datetime.now(tz=UTC),
        )
        lock = LockFile(packages=[entry])
        lock_path = tmp_path / "skills" / "skills.lock"
        lock_path.write_text(yaml.dump(lock.model_dump(mode="json")))

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "https://github.com/org/repo.git" in out
        assert "[builtin]" in out

    # ------------------------------------------------------------------
    # Non-skill subdirs (no SKILL.md) are excluded
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_dirs_without_skill_md_are_excluded(self, tmp_path: Path, capsys):
        """Subdirectories that do not contain SKILL.md are not listed as skills."""
        proj_dir = tmp_path / "skills" / "default"
        _make_skill_dir(proj_dir, "real-skill")
        # Create a dir without SKILL.md – should be ignored.
        (proj_dir / "not-a-skill").mkdir(parents=True)

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            await cmd_skills_list(_list_args())

        out = capsys.readouterr().out
        assert "not-a-skill" not in out
        assert "real-skill" in out

    # ------------------------------------------------------------------
    # Always returns exit code 0
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_always_returns_0_with_skills(self, tmp_path: Path):
        _make_skill_dir(tmp_path / "skills" / "default", "s")

        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0

    @pytest.mark.asyncio
    async def test_always_returns_0_without_skills_dir(self, tmp_path: Path):
        with patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path):
            result = await cmd_skills_list(_list_args())

        assert result == 0
