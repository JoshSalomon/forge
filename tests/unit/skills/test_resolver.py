from pathlib import Path

import pytest

from forge.skills.resolver import resolve_skill_paths


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """Create a skills directory with a default subdirectory."""
    (tmp_path / "default").mkdir()
    return tmp_path


def test_no_override_returns_default_only(skills_dir: Path) -> None:
    result = resolve_skill_paths("PROJ-123", skills_dir)
    assert result == [str(skills_dir / "default") + "/"]


def test_with_override_returns_default_then_project(skills_dir: Path) -> None:
    (skills_dir / "proj").mkdir()
    result = resolve_skill_paths("PROJ-123", skills_dir)
    assert result == [
        str(skills_dir / "default") + "/",
        str(skills_dir / "proj") + "/",
    ]


def test_project_key_lowercased(skills_dir: Path) -> None:
    (skills_dir / "aisos").mkdir()
    result = resolve_skill_paths("AISOS-456", skills_dir)
    assert result == [
        str(skills_dir / "default") + "/",
        str(skills_dir / "aisos") + "/",
    ]


def test_ticket_key_without_dash_returns_default(skills_dir: Path) -> None:
    result = resolve_skill_paths("NOHYPHEN", skills_dir)
    assert result == [str(skills_dir / "default") + "/"]


def test_nonexistent_project_dir_returns_default(skills_dir: Path) -> None:
    result = resolve_skill_paths("MISSING-1", skills_dir)
    assert result == [str(skills_dir / "default") + "/"]


def test_override_path_is_file_returns_default(skills_dir: Path) -> None:
    (skills_dir / "proj").touch()  # file, not a directory
    result = resolve_skill_paths("PROJ-123", skills_dir)
    assert result == [str(skills_dir / "default") + "/"]
