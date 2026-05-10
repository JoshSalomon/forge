"""Unit tests for forge.skills.models – SkillEntry, LockEntry, LockFile."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from forge.skills.models import LockEntry, LockFile, SkillEntry

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

REPO_URL = "https://github.com/example/skills.git"
COMMIT_SHA = "abc123def456abc123def456abc123def456abc1"


def _lock_entry(**overrides) -> LockEntry:
    """Return a valid LockEntry with sensible defaults."""
    defaults: dict = {
        "source": REPO_URL,
        "ref": "main",
        "resolved_commit": COMMIT_SHA,
        "mode": "path",
        "path": "skills/",
        "skill_mapping": None,
        "target": "my-project",
        "skills": ["analyze-bug", "generate-prd"],
        "fetched_at": datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return LockEntry(**defaults)


# ---------------------------------------------------------------------------
# SkillEntry – source validation
# ---------------------------------------------------------------------------


class TestSkillEntrySource:
    def test_source_required(self):
        with pytest.raises(ValidationError) as exc_info:
            SkillEntry(path="skills/")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("source",) for e in errors)

    def test_source_accepts_any_string(self):
        entry = SkillEntry(source=REPO_URL, path="skills/")
        assert entry.source == REPO_URL

    def test_source_accepts_ssh_url(self):
        entry = SkillEntry(source="git@github.com:example/skills.git", path="skills/")
        assert entry.source == "git@github.com:example/skills.git"


# ---------------------------------------------------------------------------
# SkillEntry – ref field
# ---------------------------------------------------------------------------


class TestSkillEntryRef:
    def test_ref_defaults_to_none(self):
        entry = SkillEntry(source=REPO_URL, path="skills/")
        assert entry.ref is None

    def test_ref_accepts_tag(self):
        entry = SkillEntry(source=REPO_URL, ref="v1.2.3", path="skills/")
        assert entry.ref == "v1.2.3"

    def test_ref_accepts_branch(self):
        entry = SkillEntry(source=REPO_URL, ref="feature/branch", path="skills/")
        assert entry.ref == "feature/branch"

    def test_ref_accepts_sha(self):
        entry = SkillEntry(source=REPO_URL, ref=COMMIT_SHA, path="skills/")
        assert entry.ref == COMMIT_SHA


# ---------------------------------------------------------------------------
# SkillEntry – mutual exclusivity of path / skill_mapping
# ---------------------------------------------------------------------------


class TestSkillEntryMutualExclusivity:
    def test_path_only_is_valid(self):
        entry = SkillEntry(source=REPO_URL, path="skills/")
        assert entry.path == "skills/"
        assert entry.skill_mapping is None

    def test_skill_mapping_only_is_valid(self):
        mapping = {"analyze-bug": "skills/analyze-bug"}
        entry = SkillEntry(source=REPO_URL, skill_mapping=mapping)
        assert entry.skill_mapping == mapping
        assert entry.path is None

    def test_both_path_and_skill_mapping_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            SkillEntry(
                source=REPO_URL,
                path="skills/",
                skill_mapping={"analyze-bug": "skills/analyze-bug"},
            )
        error_messages = str(exc_info.value)
        assert "exactly one" in error_messages.lower() or "not both" in error_messages.lower()

    def test_neither_path_nor_skill_mapping_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            SkillEntry(source=REPO_URL)
        error_messages = str(exc_info.value)
        assert "exactly one" in error_messages.lower()

    def test_path_none_and_skill_mapping_none_raises(self):
        with pytest.raises(ValidationError):
            SkillEntry(source=REPO_URL, path=None, skill_mapping=None)

    def test_skill_mapping_empty_dict_is_valid(self):
        """An empty mapping is allowed – callers decide if it is meaningful."""
        entry = SkillEntry(source=REPO_URL, skill_mapping={})
        assert entry.skill_mapping == {}

    def test_skill_mapping_multiple_entries(self):
        mapping = {
            "analyze-bug": "skills/analyze-bug",
            "generate-prd": "skills/generate-prd",
            "generate-spec": "skills/generate-spec",
        }
        entry = SkillEntry(source=REPO_URL, skill_mapping=mapping)
        assert len(entry.skill_mapping) == 3


# ---------------------------------------------------------------------------
# SkillEntry – serialization
# ---------------------------------------------------------------------------


class TestSkillEntrySerialization:
    def test_model_dump_path_mode(self):
        entry = SkillEntry(source=REPO_URL, ref="v1.0", path="skills/")
        data = entry.model_dump()
        assert data == {
            "source": REPO_URL,
            "ref": "v1.0",
            "path": "skills/",
            "skill_mapping": None,
        }

    def test_model_dump_skill_mapping_mode(self):
        mapping = {"analyze-bug": "skills/analyze-bug"}
        entry = SkillEntry(source=REPO_URL, skill_mapping=mapping)
        data = entry.model_dump()
        assert data == {
            "source": REPO_URL,
            "ref": None,
            "path": None,
            "skill_mapping": mapping,
        }

    def test_model_validate_roundtrip(self):
        original = SkillEntry(source=REPO_URL, ref="main", path="skills/")
        data = original.model_dump()
        restored = SkillEntry.model_validate(data)
        assert restored == original

    def test_model_validate_from_raw_dict(self):
        raw = {"source": REPO_URL, "path": "skills/forge/"}
        entry = SkillEntry.model_validate(raw)
        assert entry.source == REPO_URL
        assert entry.path == "skills/forge/"
        assert entry.ref is None


# ---------------------------------------------------------------------------
# LockEntry – field validation
# ---------------------------------------------------------------------------


class TestLockEntryFields:
    def test_all_required_fields_present(self):
        entry = _lock_entry()
        assert entry.source == REPO_URL
        assert entry.ref == "main"
        assert entry.resolved_commit == COMMIT_SHA
        assert entry.mode == "path"
        assert entry.path == "skills/"
        assert entry.target == "my-project"
        assert entry.skills == ["analyze-bug", "generate-prd"]
        assert isinstance(entry.fetched_at, datetime)

    def test_missing_source_raises(self):
        with pytest.raises(ValidationError):
            LockEntry(
                ref="main",
                resolved_commit=COMMIT_SHA,
                mode="path",
                path="skills/",
                target="my-project",
                skills=[],
                fetched_at=datetime.now(tz=UTC),
            )

    def test_missing_resolved_commit_raises(self):
        with pytest.raises(ValidationError):
            LockEntry(
                source=REPO_URL,
                ref="main",
                mode="path",
                path="skills/",
                target="my-project",
                skills=[],
                fetched_at=datetime.now(tz=UTC),
            )

    def test_mode_path_literal(self):
        entry = _lock_entry(mode="path")
        assert entry.mode == "path"

    def test_mode_skill_mapping_literal(self):
        entry = _lock_entry(
            mode="skill_mapping",
            path=None,
            skill_mapping={"analyze-bug": "skills/analyze-bug"},
        )
        assert entry.mode == "skill_mapping"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValidationError):
            _lock_entry(mode="invalid")  # type: ignore[arg-type]

    def test_skills_can_be_empty_list(self):
        entry = _lock_entry(skills=[])
        assert entry.skills == []

    def test_skills_list_preserved(self):
        skills = ["a", "b", "c"]
        entry = _lock_entry(skills=skills)
        assert entry.skills == skills

    def test_fetched_at_is_datetime(self):
        now = datetime.now(tz=UTC)
        entry = _lock_entry(fetched_at=now)
        assert entry.fetched_at == now

    def test_path_optional_defaults_none(self):
        entry = _lock_entry(mode="skill_mapping", path=None, skill_mapping={"k": "v"})
        assert entry.path is None

    def test_skill_mapping_optional_defaults_none(self):
        entry = _lock_entry(mode="path", skill_mapping=None)
        assert entry.skill_mapping is None


# ---------------------------------------------------------------------------
# LockEntry – serialization
# ---------------------------------------------------------------------------


class TestLockEntrySerialization:
    def test_model_dump_returns_dict(self):
        entry = _lock_entry()
        data = entry.model_dump()
        assert isinstance(data, dict)
        assert data["source"] == REPO_URL
        assert data["resolved_commit"] == COMMIT_SHA
        assert data["mode"] == "path"

    def test_model_validate_roundtrip(self):
        original = _lock_entry()
        data = original.model_dump()
        restored = LockEntry.model_validate(data)
        assert restored == original

    def test_serialization_datetime_preserved(self):
        ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        entry = _lock_entry(fetched_at=ts)
        data = entry.model_dump()
        restored = LockEntry.model_validate(data)
        assert restored.fetched_at == ts


# ---------------------------------------------------------------------------
# LockFile – container and lookup
# ---------------------------------------------------------------------------


class TestLockFile:
    def test_empty_lockfile(self):
        lf = LockFile()
        assert lf.packages == []

    def test_lockfile_with_packages(self):
        entry = _lock_entry()
        lf = LockFile(packages=[entry])
        assert len(lf.packages) == 1

    def test_find_by_source_returns_matching_entry(self):
        entry = _lock_entry(source=REPO_URL)
        lf = LockFile(packages=[entry])
        result = lf.find_by_source(REPO_URL)
        assert result is entry

    def test_find_by_source_returns_none_when_not_found(self):
        entry = _lock_entry(source=REPO_URL)
        lf = LockFile(packages=[entry])
        result = lf.find_by_source("https://github.com/other/repo.git")
        assert result is None

    def test_find_by_source_empty_lockfile_returns_none(self):
        lf = LockFile()
        result = lf.find_by_source(REPO_URL)
        assert result is None

    def test_find_by_source_returns_first_match(self):
        entry1 = _lock_entry(source=REPO_URL, target="project-a")
        entry2 = _lock_entry(source=REPO_URL, target="project-b")
        lf = LockFile(packages=[entry1, entry2])
        result = lf.find_by_source(REPO_URL)
        assert result is entry1

    def test_find_by_source_multiple_distinct_entries(self):
        url_a = "https://github.com/example/skills-a.git"
        url_b = "https://github.com/example/skills-b.git"
        entry_a = _lock_entry(source=url_a)
        entry_b = _lock_entry(source=url_b)
        lf = LockFile(packages=[entry_a, entry_b])

        assert lf.find_by_source(url_a) is entry_a
        assert lf.find_by_source(url_b) is entry_b
        assert lf.find_by_source("https://other.git") is None

    def test_lockfile_serialization_roundtrip(self):
        entries = [
            _lock_entry(source="https://github.com/example/a.git"),
            _lock_entry(
                source="https://github.com/example/b.git",
                mode="skill_mapping",
                path=None,
                skill_mapping={"skill-x": "path/to/x"},
            ),
        ]
        lf = LockFile(packages=entries)
        data = lf.model_dump()
        restored = LockFile.model_validate(data)
        assert len(restored.packages) == 2
        assert restored.packages[0].source == "https://github.com/example/a.git"
        assert restored.packages[1].mode == "skill_mapping"

    def test_lockfile_model_dump_excludes_none_optionally(self):
        """model_dump(exclude_none=True) should work for YAML-friendly output."""
        entry = _lock_entry()
        lf = LockFile(packages=[entry])
        data = lf.model_dump(exclude_none=True)
        # skill_mapping is None in path mode – should be excluded
        pkg = data["packages"][0]
        assert "skill_mapping" not in pkg
