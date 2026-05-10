"""Smoke test confirming resolver routes to real skills/ directory."""

from pathlib import Path

from forge.skills.resolver import resolve_skill_paths

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def test_default_skills_dir_exists() -> None:
    skills_dir = PROJECT_ROOT / "skills"
    assert (skills_dir / "default").is_dir(), "skills/default/ must exist"
    assert any((skills_dir / "default").iterdir()), "skills/default/ must not be empty"


def test_no_override_points_to_default() -> None:
    skills_dir = PROJECT_ROOT / "skills"
    result = resolve_skill_paths("NOPROJ-1", skills_dir)
    assert len(result) == 1
    assert result[0].endswith("skills/default/")


def test_project_override_resolves() -> None:
    skills_dir = PROJECT_ROOT / "skills"
    result = resolve_skill_paths("AISOS-123", skills_dir)
    assert len(result) == 2
    assert result[0].endswith("skills/default/")
    assert result[1].endswith("skills/aisos/")
