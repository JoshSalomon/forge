"""Tests for the skills CLI subcommands."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from forge.cli import (
    cmd_skills_install,
    cmd_skills_list,
    cmd_skills_update,
    main,
)

# ---------------------------------------------------------------------------
# Helper: build a minimal argparse.Namespace for install
# ---------------------------------------------------------------------------


def _install_args(
    source="https://github.com/example/skill.git", project=None, default=False, ref=None
):
    import argparse

    return argparse.Namespace(
        source=source,
        project=project,
        default=default,
        ref=ref,
    )


def _update_args(project=None):
    import argparse

    return argparse.Namespace(project=project)


# ---------------------------------------------------------------------------
# Shared mock helpers for Git clone operations
# ---------------------------------------------------------------------------


def _make_clone_mock(tmp_path: Path, skill_names: list[str] | None = None):
    """Return a mock clone_skill_package that creates a fake repo in *tmp_path*.

    The mock creates a ``skills/`` subdirectory with the named skill
    subdirectories so ``install_path_mode`` finds them.
    """
    names = skill_names or ["my-skill"]
    skills_subdir = tmp_path / "skills"
    skills_subdir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (skills_subdir / name).mkdir()

    async def _fake_clone(_url, _ref, **_kwargs):
        return tmp_path

    return _fake_clone


# ---------------------------------------------------------------------------
# cmd_skills_install
# ---------------------------------------------------------------------------


class TestCmdSkillsInstall:
    """Unit tests for cmd_skills_install handler."""

    @pytest.mark.asyncio
    async def test_install_with_project_returns_0(self, tmp_path):
        args = _install_args(project="MYPROJ")
        with (
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                side_effect=_make_clone_mock(tmp_path),
            ),
            patch(
                "forge.skills.cli_handlers._resolve_head_sha", new=AsyncMock(return_value="abc123")
            ),
            patch("forge.skills.cli_handlers.update_lock_file"),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = await cmd_skills_install(args)
        assert result == 0

    @pytest.mark.asyncio
    async def test_install_with_default_flag_returns_0(self, tmp_path):
        args = _install_args(default=True)
        with (
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                side_effect=_make_clone_mock(tmp_path),
            ),
            patch(
                "forge.skills.cli_handlers._resolve_head_sha", new=AsyncMock(return_value="abc123")
            ),
            patch("forge.skills.cli_handlers.update_lock_file"),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = await cmd_skills_install(args)
        assert result == 0

    @pytest.mark.asyncio
    async def test_install_missing_both_returns_2(self, capsys):
        args = _install_args()  # neither project nor default
        result = await cmd_skills_install(args)
        assert result == 2
        captured = capsys.readouterr()
        assert "exactly one of --project or --default" in captured.err

    @pytest.mark.asyncio
    async def test_install_both_project_and_default_returns_2(self, capsys):
        args = _install_args(project="MYPROJ", default=True)
        result = await cmd_skills_install(args)
        assert result == 2
        captured = capsys.readouterr()
        assert "mutually exclusive" in captured.err

    @pytest.mark.asyncio
    async def test_install_with_ref_and_project_returns_0(self, tmp_path):
        args = _install_args(project="MYPROJ", ref="v1.2.3")
        with (
            patch(
                "forge.skills.cli_handlers.clone_skill_package",
                side_effect=_make_clone_mock(tmp_path),
            ),
            patch(
                "forge.skills.cli_handlers._resolve_head_sha", new=AsyncMock(return_value="abc123")
            ),
            patch("forge.skills.cli_handlers.update_lock_file"),
            patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
        ):
            result = await cmd_skills_install(args)
        assert result == 0


# ---------------------------------------------------------------------------
# cmd_skills_list
# ---------------------------------------------------------------------------


class TestCmdSkillsList:
    """Unit tests for cmd_skills_list handler."""

    @pytest.mark.asyncio
    async def test_list_returns_0(self):
        import argparse

        args = argparse.Namespace()
        result = await cmd_skills_list(args)
        assert result == 0


# ---------------------------------------------------------------------------
# cmd_skills_update
# ---------------------------------------------------------------------------


class TestCmdSkillsUpdate:
    """Unit tests for cmd_skills_update handler."""

    @pytest.mark.asyncio
    async def test_update_no_project_returns_0(self):
        args = _update_args()
        result = await cmd_skills_update(args)
        assert result == 0

    @pytest.mark.asyncio
    async def test_update_with_project_returns_0(self):
        args = _update_args(project="MYPROJ")
        result = await cmd_skills_update(args)
        assert result == 0


# ---------------------------------------------------------------------------
# Integration: main() dispatch
# ---------------------------------------------------------------------------


class TestMainSkillsDispatch:
    """Integration tests exercising main() argument parsing for skills."""

    def _run_main(self, argv, tmp_path=None):
        extra_patches: list = []
        if tmp_path is not None:
            # Build a fake repo directory structure
            skills_subdir = tmp_path / "skills"
            skills_subdir.mkdir(parents=True, exist_ok=True)
            (skills_subdir / "my-skill").mkdir()

            async def _fake_clone(_url, _ref, **_kwargs):
                return tmp_path

            extra_patches = [
                patch("forge.skills.cli_handlers.clone_skill_package", side_effect=_fake_clone),
                patch(
                    "forge.skills.cli_handlers._resolve_head_sha",
                    new=AsyncMock(return_value="abc123"),
                ),
                patch("forge.skills.cli_handlers.update_lock_file"),
                patch("forge.skills.cli_handlers.Path.cwd", return_value=tmp_path),
            ]

        with patch.object(sys, "argv", ["forge"] + argv):
            if extra_patches:
                with extra_patches[0], extra_patches[1], extra_patches[2], extra_patches[3]:
                    return main()
            return main()

    def test_skills_install_with_project(self, tmp_path):
        result = self._run_main(
            ["skills", "install", "https://github.com/example/skill.git", "--project", "MYPROJ"],
            tmp_path=tmp_path,
        )
        assert result == 0

    def test_skills_install_with_default(self, tmp_path):
        result = self._run_main(
            ["skills", "install", "https://github.com/example/skill.git", "--default"],
            tmp_path=tmp_path,
        )
        assert result == 0

    def test_skills_install_missing_target_returns_2(self):
        result = self._run_main(["skills", "install", "https://github.com/example/skill.git"])
        assert result == 2

    def test_skills_install_both_project_and_default_returns_2(self):
        result = self._run_main(
            [
                "skills",
                "install",
                "https://github.com/example/skill.git",
                "--project",
                "MYPROJ",
                "--default",
            ]
        )
        assert result == 2

    def test_skills_install_with_ref(self, tmp_path):
        result = self._run_main(
            [
                "skills",
                "install",
                "https://github.com/example/skill.git",
                "--project",
                "MYPROJ",
                "--ref",
                "v1.0.0",
            ],
            tmp_path=tmp_path,
        )
        assert result == 0

    def test_skills_list(self):
        result = self._run_main(["skills", "list"])
        assert result == 0

    def test_skills_update_no_project(self):
        result = self._run_main(["skills", "update"])
        assert result == 0

    def test_skills_update_with_project(self):
        result = self._run_main(["skills", "update", "--project", "MYPROJ"])
        assert result == 0
