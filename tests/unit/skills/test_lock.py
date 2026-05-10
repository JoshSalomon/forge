"""Unit tests for forge.skills.lock – read_lock_file and update_lock_file."""

import logging
import os
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from forge.skills.lock import read_lock_file, update_lock_file
from forge.skills.models import LockEntry, LockFile

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    source: str = "https://github.com/example/skills",
    ref: str = "main",
    resolved_commit: str = "abc1234" * 5 + "abcd",  # 40-char SHA
    mode: str = "path",
    path: str | None = "skills/",
    skill_mapping: dict[str, str] | None = None,
    target: str = "my-project",
    skills: list[str] | None = None,
    fetched_at: datetime | None = None,
) -> LockEntry:
    return LockEntry(
        source=source,
        ref=ref,
        resolved_commit=resolved_commit,
        mode=mode,  # type: ignore[arg-type]
        path=path,
        skill_mapping=skill_mapping,
        target=target,
        skills=skills or ["skill-a", "skill-b"],
        fetched_at=fetched_at or datetime(2024, 1, 15, 12, 0, 0),
    )


def _write_yaml(lock_path: Path, data: dict) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")


# ===========================================================================
# read_lock_file
# ===========================================================================


class TestReadLockFile:
    def test_returns_empty_lock_file_when_missing(self, tmp_path: Path) -> None:
        """Missing file → empty LockFile with no packages."""
        lock_path = tmp_path / "skills.lock"

        result = read_lock_file(lock_path)

        assert isinstance(result, LockFile)
        assert result.packages == []

    def test_returns_empty_lock_file_and_logs_error_for_corrupt_yaml(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Invalid YAML → empty LockFile + error log."""
        lock_path = tmp_path / "skills.lock"
        lock_path.write_text("packages:\n  - source: [\ninvalid yaml }{", encoding="utf-8")

        with caplog.at_level(logging.ERROR, logger="forge.skills.lock"):
            result = read_lock_file(lock_path)

        assert isinstance(result, LockFile)
        assert result.packages == []
        assert any("Failed to parse YAML" in r.message for r in caplog.records)

    def test_returns_empty_lock_file_and_logs_error_for_schema_mismatch(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Valid YAML but wrong schema → empty LockFile + error log."""
        lock_path = tmp_path / "skills.lock"
        # 'packages' must be a list of dicts; give it a list of ints
        _write_yaml(lock_path, {"packages": [1, 2, 3]})

        with caplog.at_level(logging.ERROR, logger="forge.skills.lock"):
            result = read_lock_file(lock_path)

        assert isinstance(result, LockFile)
        assert result.packages == []
        assert len(caplog.records) > 0

    def test_parses_valid_lock_file(self, tmp_path: Path) -> None:
        """Valid YAML with one entry is parsed into a LockFile model."""
        lock_path = tmp_path / "skills.lock"
        entry = _make_entry()
        lock = LockFile(packages=[entry])
        _write_yaml(lock_path, lock.model_dump(mode="json"))

        result = read_lock_file(lock_path)

        assert isinstance(result, LockFile)
        assert len(result.packages) == 1
        assert result.packages[0].source == entry.source
        assert result.packages[0].ref == entry.ref
        assert result.packages[0].resolved_commit == entry.resolved_commit

    def test_parses_multiple_entries(self, tmp_path: Path) -> None:
        """Multiple packages are all parsed correctly."""
        lock_path = tmp_path / "skills.lock"
        entry_a = _make_entry(source="https://github.com/example/a", path="a/")
        entry_b = _make_entry(source="https://github.com/example/b", path="b/")
        lock = LockFile(packages=[entry_a, entry_b])
        _write_yaml(lock_path, lock.model_dump(mode="json"))

        result = read_lock_file(lock_path)

        assert len(result.packages) == 2
        sources = {e.source for e in result.packages}
        assert "https://github.com/example/a" in sources
        assert "https://github.com/example/b" in sources

    def test_returns_empty_lock_file_for_empty_yaml_file(self, tmp_path: Path) -> None:
        """An empty file (produces None from safe_load) → empty LockFile."""
        lock_path = tmp_path / "skills.lock"
        lock_path.write_text("", encoding="utf-8")

        result = read_lock_file(lock_path)

        assert isinstance(result, LockFile)
        assert result.packages == []

    def test_preserves_skill_mapping_mode(self, tmp_path: Path) -> None:
        """Entries with skill_mapping mode are round-tripped correctly."""
        lock_path = tmp_path / "skills.lock"
        entry = _make_entry(
            mode="skill_mapping",
            path=None,
            skill_mapping={"my-skill": "src/skills/my-skill"},
        )
        lock = LockFile(packages=[entry])
        _write_yaml(lock_path, lock.model_dump(mode="json"))

        result = read_lock_file(lock_path)

        assert result.packages[0].mode == "skill_mapping"
        assert result.packages[0].skill_mapping == {"my-skill": "src/skills/my-skill"}


# ===========================================================================
# update_lock_file
# ===========================================================================


class TestUpdateLockFile:
    def test_creates_new_lock_file_when_none_exists(self, tmp_path: Path) -> None:
        """update_lock_file creates the file when it does not exist."""
        lock_path = tmp_path / "skills.lock"
        entry = _make_entry()

        update_lock_file(lock_path, entry)

        assert lock_path.exists()
        result = read_lock_file(lock_path)
        assert len(result.packages) == 1
        assert result.packages[0].source == entry.source

    def test_appends_entry_when_no_matching_source(self, tmp_path: Path) -> None:
        """A new entry (different source) is appended to existing packages."""
        lock_path = tmp_path / "skills.lock"
        entry_a = _make_entry(source="https://github.com/example/a", path="a/")
        update_lock_file(lock_path, entry_a)

        entry_b = _make_entry(source="https://github.com/example/b", path="b/")
        update_lock_file(lock_path, entry_b)

        result = read_lock_file(lock_path)
        assert len(result.packages) == 2
        sources = {e.source for e in result.packages}
        assert sources == {"https://github.com/example/a", "https://github.com/example/b"}

    def test_replaces_existing_entry_with_matching_source(self, tmp_path: Path) -> None:
        """Updating with the same source replaces the entry, not duplicates it."""
        lock_path = tmp_path / "skills.lock"
        source = "https://github.com/example/skills"

        original = _make_entry(source=source, resolved_commit="a" * 40)
        update_lock_file(lock_path, original)

        updated = _make_entry(
            source=source,
            resolved_commit="b" * 40,
            skills=["skill-c"],
        )
        update_lock_file(lock_path, updated)

        result = read_lock_file(lock_path)
        assert len(result.packages) == 1
        assert result.packages[0].resolved_commit == "b" * 40
        assert result.packages[0].skills == ["skill-c"]

    def test_does_not_duplicate_entry(self, tmp_path: Path) -> None:
        """Calling update twice with the same source still yields one package."""
        lock_path = tmp_path / "skills.lock"
        entry = _make_entry()

        update_lock_file(lock_path, entry)
        update_lock_file(lock_path, entry)

        result = read_lock_file(lock_path)
        assert len(result.packages) == 1

    def test_atomic_write_uses_temp_file_then_rename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the atomic write pattern: os.replace is called."""
        lock_path = tmp_path / "skills.lock"
        entry = _make_entry()

        replaced: list[tuple[str, str]] = []
        original_replace = os.replace

        def spy_replace(src: str, dst: str) -> None:
            replaced.append((src, dst))
            original_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy_replace)

        update_lock_file(lock_path, entry)

        assert len(replaced) == 1
        src, dst = replaced[0]
        # The destination must be the lock file
        assert Path(dst) == lock_path
        # The source must be a temp file in the same directory
        assert Path(src).parent == lock_path.parent
        assert src != dst

    def test_yaml_output_is_human_readable(self, tmp_path: Path) -> None:
        """The written YAML uses block style (not flow style)."""
        lock_path = tmp_path / "skills.lock"
        entry = _make_entry()

        update_lock_file(lock_path, entry)

        raw = lock_path.read_text(encoding="utf-8")
        # Block-style YAML uses newlines; flow-style would be {source: ...}
        assert "\n" in raw
        # Keys should appear on their own lines
        assert "source:" in raw
        assert "resolved_commit:" in raw

    def test_creates_parent_directories_if_missing(self, tmp_path: Path) -> None:
        """Parent directories are created if they do not exist."""
        lock_path = tmp_path / "nested" / "dir" / "skills.lock"
        entry = _make_entry()

        update_lock_file(lock_path, entry)

        assert lock_path.exists()

    def test_round_trip_preserves_all_fields(self, tmp_path: Path) -> None:
        """All LockEntry fields survive a write → read round-trip."""
        lock_path = tmp_path / "skills.lock"
        entry = _make_entry(
            source="https://github.com/example/skills",
            ref="v1.2.3",
            resolved_commit="c" * 40,
            mode="path",
            path="skills/",
            target="my-workspace",
            skills=["analyze-bug", "fix-ci"],
            fetched_at=datetime(2024, 6, 1, 9, 30, 0),
        )

        update_lock_file(lock_path, entry)
        result = read_lock_file(lock_path)

        loaded = result.packages[0]
        assert loaded.source == entry.source
        assert loaded.ref == entry.ref
        assert loaded.resolved_commit == entry.resolved_commit
        assert loaded.mode == entry.mode
        assert loaded.path == entry.path
        assert loaded.target == entry.target
        assert loaded.skills == entry.skills
        assert loaded.fetched_at == entry.fetched_at

    def test_replace_preserves_other_entries_order(self, tmp_path: Path) -> None:
        """When replacing an entry in the middle, other entries keep their order."""
        lock_path = tmp_path / "skills.lock"
        source_a = "https://github.com/example/a"
        source_b = "https://github.com/example/b"
        source_c = "https://github.com/example/c"

        update_lock_file(lock_path, _make_entry(source=source_a, path="a/"))
        update_lock_file(lock_path, _make_entry(source=source_b, path="b/"))
        update_lock_file(lock_path, _make_entry(source=source_c, path="c/"))

        # Replace the middle entry
        updated_b = _make_entry(source=source_b, path="b/", resolved_commit="d" * 40)
        update_lock_file(lock_path, updated_b)

        result = read_lock_file(lock_path)
        assert len(result.packages) == 3
        assert result.packages[0].source == source_a
        assert result.packages[1].source == source_b
        assert result.packages[1].resolved_commit == "d" * 40
        assert result.packages[2].source == source_c

    def test_logs_debug_after_update(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A debug message is logged after a successful write."""
        lock_path = tmp_path / "skills.lock"
        entry = _make_entry()

        with caplog.at_level(logging.DEBUG, logger="forge.skills.lock"):
            update_lock_file(lock_path, entry)

        assert any("Lock file updated" in r.message for r in caplog.records)
