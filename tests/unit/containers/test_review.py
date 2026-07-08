"""Unit tests for containers.review module."""

import json
from pathlib import Path

import pytest
from review import (
    DEFAULT_MAX_RETRIES,
    ENV_MAX_RETRIES,
    ReviewConfig,
    ReviewCycleData,
    Verdict,
    detect_review_md,
    find_skill_file,
    parse_review_config,
    parse_verdict,
    write_cycle_file,
)

# ---------------------------------------------------------------------------
# Verdict enum tests
# ---------------------------------------------------------------------------


class TestVerdict:
    def test_approved_value(self):
        assert Verdict.APPROVED == "approved"
        assert Verdict.APPROVED.value == "approved"

    def test_rejected_value(self):
        assert Verdict.REJECTED == "rejected"
        assert Verdict.REJECTED.value == "rejected"

    def test_is_str_enum(self):
        # StrEnum values can be used directly as strings
        assert f"Verdict: {Verdict.APPROVED}" == "Verdict: approved"


# ---------------------------------------------------------------------------
# ReviewConfig dataclass tests
# ---------------------------------------------------------------------------


class TestReviewConfig:
    def test_default_values(self):
        config = ReviewConfig()
        assert config.max_retries == DEFAULT_MAX_RETRIES
        assert config.instructions == ""

    def test_custom_values(self):
        config = ReviewConfig(max_retries=5, instructions="Check for bugs")
        assert config.max_retries == 5
        assert config.instructions == "Check for bugs"

    def test_default_max_retries_is_3(self):
        assert DEFAULT_MAX_RETRIES == 3


# ---------------------------------------------------------------------------
# ReviewCycleData dataclass tests
# ---------------------------------------------------------------------------


class TestReviewCycleData:
    def test_all_fields_required(self):
        data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="approved",
            feedback="Looks good!",
            skill="code-review",
            elapsed_seconds=12.5,
            timestamp="2024-01-15T10:30:00Z",
        )
        assert data.cycle == 1
        assert data.max_cycles == 3
        assert data.verdict == "approved"
        assert data.feedback == "Looks good!"
        assert data.skill == "code-review"
        assert data.elapsed_seconds == 12.5
        assert data.timestamp == "2024-01-15T10:30:00Z"

    def test_verdict_can_be_approved_or_rejected(self):
        approved = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict=Verdict.APPROVED,
            feedback="",
            skill="test",
            elapsed_seconds=1.0,
            timestamp="2024-01-01T00:00:00Z",
        )
        assert approved.verdict == "approved"

        rejected = ReviewCycleData(
            cycle=2,
            max_cycles=3,
            verdict=Verdict.REJECTED,
            feedback="Needs work",
            skill="test",
            elapsed_seconds=2.0,
            timestamp="2024-01-01T00:00:00Z",
        )
        assert rejected.verdict == "rejected"


# ---------------------------------------------------------------------------
# parse_review_config tests
# ---------------------------------------------------------------------------


class TestParseReviewConfig:
    """Tests for parse_review_config function."""

    def test_parse_valid_frontmatter(self, tmp_path: Path):
        review_md = tmp_path / "review.md"
        review_md.write_text(
            """\
---
max_retries: 5
---
Review the code for security issues.
"""
        )
        config = parse_review_config(review_md)
        assert config.max_retries == 5
        assert config.instructions == "Review the code for security issues."

    def test_parse_only_instructions_no_frontmatter(self, tmp_path: Path):
        review_md = tmp_path / "review.md"
        review_md.write_text("Just some instructions, no frontmatter.")

        config = parse_review_config(review_md)
        assert config.max_retries == DEFAULT_MAX_RETRIES
        assert config.instructions == "Just some instructions, no frontmatter."

    def test_parse_empty_frontmatter(self, tmp_path: Path):
        review_md = tmp_path / "review.md"
        review_md.write_text(
            """\
---
---
Instructions after empty frontmatter.
"""
        )
        config = parse_review_config(review_md)
        assert config.max_retries == DEFAULT_MAX_RETRIES
        assert config.instructions == "Instructions after empty frontmatter."

    def test_file_not_found_returns_defaults(self, tmp_path: Path):
        review_md = tmp_path / "nonexistent.md"
        config = parse_review_config(review_md)
        assert config.max_retries == DEFAULT_MAX_RETRIES
        assert config.instructions == ""

    def test_malformed_yaml_logs_warning_and_returns_defaults(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        review_md = tmp_path / "review.md"
        review_md.write_text(
            """\
---
max_retries: [invalid yaml
---
Some instructions.
"""
        )
        config = parse_review_config(review_md)
        assert config.max_retries == DEFAULT_MAX_RETRIES
        assert config.instructions == "Some instructions."
        assert "Malformed YAML" in caplog.text

    def test_env_var_fallback_when_no_frontmatter_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(ENV_MAX_RETRIES, "7")

        review_md = tmp_path / "review.md"
        review_md.write_text("No frontmatter, just instructions.")

        config = parse_review_config(review_md)
        assert config.max_retries == 7
        assert config.instructions == "No frontmatter, just instructions."

    def test_frontmatter_overrides_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "7")

        review_md = tmp_path / "review.md"
        review_md.write_text(
            """\
---
max_retries: 2
---
Instructions here.
"""
        )
        config = parse_review_config(review_md)
        assert config.max_retries == 2

    def test_invalid_env_var_is_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv(ENV_MAX_RETRIES, "not-a-number")

        review_md = tmp_path / "review.md"
        review_md.write_text("Instructions only.")

        config = parse_review_config(review_md)
        assert config.max_retries == DEFAULT_MAX_RETRIES
        assert "Invalid AUTO_REVIEW_MAX_RETRIES" in caplog.text

    def test_non_integer_max_retries_in_frontmatter_uses_default(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        review_md = tmp_path / "review.md"
        review_md.write_text(
            """\
---
max_retries: "five"
---
Instructions.
"""
        )
        config = parse_review_config(review_md)
        assert config.max_retries == DEFAULT_MAX_RETRIES
        assert "Invalid max_retries" in caplog.text

    def test_multiline_instructions(self, tmp_path: Path):
        review_md = tmp_path / "review.md"
        review_md.write_text(
            """\
---
max_retries: 4
---
Line one.
Line two.
Line three.
"""
        )
        config = parse_review_config(review_md)
        assert config.max_retries == 4
        assert "Line one." in config.instructions
        assert "Line two." in config.instructions
        assert "Line three." in config.instructions

    def test_frontmatter_with_extra_fields_ignored(self, tmp_path: Path):
        review_md = tmp_path / "review.md"
        review_md.write_text(
            """\
---
max_retries: 6
author: test
some_other_field: value
---
Instructions.
"""
        )
        config = parse_review_config(review_md)
        assert config.max_retries == 6
        assert config.instructions == "Instructions."

    def test_env_var_fallback_for_file_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(ENV_MAX_RETRIES, "10")

        review_md = tmp_path / "nonexistent.md"
        config = parse_review_config(review_md)
        assert config.max_retries == 10
        assert config.instructions == ""

    def test_env_var_fallback_for_malformed_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(ENV_MAX_RETRIES, "8")

        review_md = tmp_path / "review.md"
        review_md.write_text(
            """\
---
: invalid: yaml:
---
Some instructions.
"""
        )
        config = parse_review_config(review_md)
        assert config.max_retries == 8
        assert config.instructions == "Some instructions."

    def test_no_trailing_newline_after_frontmatter(self, tmp_path: Path):
        review_md = tmp_path / "review.md"
        review_md.write_text("---\nmax_retries: 3\n---\nInstructions.")
        config = parse_review_config(review_md)
        assert config.max_retries == 3
        assert config.instructions == "Instructions."

    def test_whitespace_handling_in_instructions(self, tmp_path: Path):
        review_md = tmp_path / "review.md"
        review_md.write_text(
            """\
---
max_retries: 1
---

   Indented instructions with leading/trailing whitespace.

"""
        )
        config = parse_review_config(review_md)
        # Instructions should be stripped
        assert config.instructions == "Indented instructions with leading/trailing whitespace."


# ---------------------------------------------------------------------------
# detect_review_md tests
# ---------------------------------------------------------------------------


class TestFindSkillFile:
    """Tests for find_skill_file — generic skill file search across mounted paths."""

    def test_finds_file_in_single_path(self, tmp_path: Path):
        skill_dir = tmp_path / "skill_0" / "implement-task"
        skill_dir.mkdir(parents=True)
        review_file = skill_dir / "review.md"
        review_file.write_text("Review instructions")

        result = find_skill_file("implement-task", "review.md", [str(tmp_path / "skill_0")])

        assert result == review_file

    def test_later_path_overrides_earlier(self, tmp_path: Path):
        default_dir = tmp_path / "skill_0" / "implement-task"
        default_dir.mkdir(parents=True)
        (default_dir / "review.md").write_text("Default review")

        project_dir = tmp_path / "skill_1" / "implement-task"
        project_dir.mkdir(parents=True)
        project_file = project_dir / "review.md"
        project_file.write_text("Project override")

        result = find_skill_file(
            "implement-task", "review.md",
            [str(tmp_path / "skill_0"), str(tmp_path / "skill_1")],
        )

        assert result == project_file
        assert result.read_text() == "Project override"

    def test_falls_back_to_earlier_path(self, tmp_path: Path):
        default_dir = tmp_path / "skill_0" / "implement-task"
        default_dir.mkdir(parents=True)
        default_file = default_dir / "review.md"
        default_file.write_text("Default review")

        # skill_1 exists but has no review.md for this skill
        (tmp_path / "skill_1").mkdir(parents=True)

        result = find_skill_file(
            "implement-task", "review.md",
            [str(tmp_path / "skill_0"), str(tmp_path / "skill_1")],
        )

        assert result == default_file

    def test_returns_none_when_not_found(self, tmp_path: Path):
        (tmp_path / "skill_0").mkdir(parents=True)

        result = find_skill_file(
            "implement-task", "review.md",
            [str(tmp_path / "skill_0")],
        )

        assert result is None

    def test_returns_none_for_empty_paths(self):
        result = find_skill_file("implement-task", "review.md", [])
        assert result is None

    def test_reads_from_env_var(self, tmp_path: Path, monkeypatch):
        skill_dir = tmp_path / "mounted" / "implement-task"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("Skill content")

        monkeypatch.setenv("AGENT_SKILL_PATHS", str(tmp_path / "mounted"))

        result = find_skill_file("implement-task", "SKILL.md")

        assert result == skill_file

    def test_finds_skill_md(self, tmp_path: Path):
        skill_dir = tmp_path / "skills" / "implement-task"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("Skill instructions")

        result = find_skill_file("implement-task", "SKILL.md", [str(tmp_path / "skills")])

        assert result == skill_file

    def test_ignores_directory_with_same_name(self, tmp_path: Path):
        skill_dir = tmp_path / "skills" / "edge-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "review.md").mkdir()  # directory, not file

        result = find_skill_file("edge-skill", "review.md", [str(tmp_path / "skills")])

        assert result is None

    def test_handles_trailing_slash_in_paths(self, tmp_path: Path):
        skill_dir = tmp_path / "skill_0" / "my-skill"
        skill_dir.mkdir(parents=True)
        review_file = skill_dir / "review.md"
        review_file.write_text("Review")

        result = find_skill_file("my-skill", "review.md", [str(tmp_path / "skill_0") + "/"])

        assert result == review_file


class TestDetectReviewMd:
    """Tests for detect_review_md — delegates to find_skill_file."""

    def test_finds_review_md_via_skill_paths(self, tmp_path: Path):
        skill_dir = tmp_path / "skill_0" / "implement-task"
        skill_dir.mkdir(parents=True)
        review_file = skill_dir / "review.md"
        review_file.write_text("Review instructions")

        result = detect_review_md(
            "implement-task",
            skill_paths=[str(tmp_path / "skill_0")],
        )

        assert result == review_file

    def test_returns_none_when_not_found(self, tmp_path: Path):
        (tmp_path / "skill_0").mkdir(parents=True)

        result = detect_review_md(
            "implement-task",
            skill_paths=[str(tmp_path / "skill_0")],
        )

        assert result is None

    def test_reads_env_var_when_no_skill_paths(self, tmp_path: Path, monkeypatch):
        skill_dir = tmp_path / "mounted" / "implement-task"
        skill_dir.mkdir(parents=True)
        review_file = skill_dir / "review.md"
        review_file.write_text("Review")

        monkeypatch.setenv("AGENT_SKILL_PATHS", str(tmp_path / "mounted"))

        result = detect_review_md("implement-task")

        assert result == review_file


# ---------------------------------------------------------------------------
# parse_verdict tests
# ---------------------------------------------------------------------------


class TestParseVerdict:
    """Tests for parse_verdict function (SC-002)."""

    # ----- APPROVED marker tests -----

    def test_approved_uppercase(self):
        """APPROVED marker in uppercase returns (APPROVED, '')."""
        result = parse_verdict("The code looks good. APPROVED")
        assert result == (Verdict.APPROVED, "")

    def test_approved_lowercase(self):
        """Case-insensitive: 'approved' returns (APPROVED, '')."""
        result = parse_verdict("Code review complete. approved")
        assert result == (Verdict.APPROVED, "")

    def test_approved_mixed_case(self):
        """Case-insensitive: 'Approved' returns (APPROVED, '')."""
        result = parse_verdict("All tests pass. Approved")
        assert result == (Verdict.APPROVED, "")

    def test_approved_with_text_before_and_after(self):
        """APPROVED marker in middle of text still returns (APPROVED, '')."""
        result = parse_verdict("Summary: Code is clean. APPROVED. No further changes needed.")
        assert result == (Verdict.APPROVED, "")

    def test_approved_at_start(self):
        """APPROVED at start of text."""
        result = parse_verdict("APPROVED - code meets all requirements")
        assert result == (Verdict.APPROVED, "")

    # ----- REJECTED marker tests -----

    def test_rejected_uppercase(self):
        """REJECTED marker returns (REJECTED, feedback)."""
        result = parse_verdict("REJECTED: Code has security issues.")
        assert result[0] == Verdict.REJECTED
        assert result[1] == ": Code has security issues."

    def test_rejected_lowercase(self):
        """Case-insensitive: 'rejected' returns (REJECTED, feedback)."""
        result = parse_verdict("Code review result: rejected due to missing tests.")
        assert result[0] == Verdict.REJECTED
        assert result[1] == "due to missing tests."

    def test_rejected_mixed_case(self):
        """Case-insensitive: 'Rejected' returns (REJECTED, feedback)."""
        result = parse_verdict("Rejected - needs refactoring.")
        assert result[0] == Verdict.REJECTED
        assert result[1] == "- needs refactoring."

    def test_rejected_extracts_feedback_after_marker(self):
        """Feedback is all text after REJECTED marker."""
        result = parse_verdict(
            "Review: REJECTED\n\nPlease fix the following:\n1. Bug in line 42\n2. Missing docstring"
        )
        assert result[0] == Verdict.REJECTED
        assert "Please fix the following:" in result[1]
        assert "Bug in line 42" in result[1]
        assert "Missing docstring" in result[1]

    def test_rejected_feedback_is_stripped(self):
        """Feedback text is stripped of leading/trailing whitespace."""
        result = parse_verdict("REJECTED   \n\n  Needs work.  \n\n")
        assert result[0] == Verdict.REJECTED
        assert result[1] == "Needs work."

    def test_rejected_with_empty_feedback(self):
        """SC-003: Empty feedback after REJECTED marker is handled correctly."""
        result = parse_verdict("REJECTED")
        assert result[0] == Verdict.REJECTED
        assert result[1] == ""

    def test_rejected_with_only_whitespace_feedback(self):
        """Whitespace-only feedback after REJECTED is stripped to empty string."""
        result = parse_verdict("REJECTED   \n\n  \t  \n")
        assert result[0] == Verdict.REJECTED
        assert result[1] == ""

    # ----- Neither marker present -----

    def test_neither_marker_returns_rejected_with_error_message(self):
        """Neither marker present returns (REJECTED, 'Verdict could not be parsed')."""
        result = parse_verdict("This review output has no clear verdict.")
        assert result == (Verdict.REJECTED, "Verdict could not be parsed")

    def test_empty_string_returns_rejected_with_error_message(self):
        """Empty string returns (REJECTED, 'Verdict could not be parsed')."""
        result = parse_verdict("")
        assert result == (Verdict.REJECTED, "Verdict could not be parsed")

    def test_whitespace_only_returns_rejected_with_error_message(self):
        """Whitespace-only string returns (REJECTED, 'Verdict could not be parsed')."""
        result = parse_verdict("   \n\t\n   ")
        assert result == (Verdict.REJECTED, "Verdict could not be parsed")

    # ----- Both markers present -----

    def test_both_markers_approved_first_wins(self):
        """When both markers present, APPROVED first wins."""
        result = parse_verdict("Code is APPROVED, not REJECTED because tests pass.")
        assert result == (Verdict.APPROVED, "")

    def test_both_markers_rejected_first_wins(self):
        """When both markers present, REJECTED first wins if it comes first."""
        result = parse_verdict(
            "This code is REJECTED. It would have been APPROVED if tests passed."
        )
        assert result[0] == Verdict.REJECTED
        assert "It would have been APPROVED if tests passed." in result[1]

    def test_both_markers_same_position_edge_case(self):
        """Edge case: if somehow both start at same position, check behavior.

        In practice this can't happen since they're different strings,
        but we verify APPROVED is checked first per spec.
        """
        # This tests the logic: APPROVED found, REJECTED found, but APPROVED position is smaller
        text = "APPROVED followed by REJECTED"
        result = parse_verdict(text)
        assert result == (Verdict.APPROVED, "")

    # ----- Partial matches should not be detected -----

    def test_approved_as_word_in_middle(self):
        """APPROVED as substring in longer word is still detected (per current impl)."""
        # NOTE: The spec doesn't mention word boundaries, so "APPROVED" in "UNAPPROVED"
        # would still match. This documents current behavior.
        result = parse_verdict("This is PREAPPROVED for the next phase")
        assert result == (Verdict.APPROVED, "")

    def test_rejected_as_word_in_middle(self):
        """REJECTED as substring is still detected (per current impl)."""
        result = parse_verdict("Previously rejected items were fixed")
        # "rejected" is found in the middle
        assert result[0] == Verdict.REJECTED

    # ----- Complex real-world scenarios -----

    def test_real_world_approved_review(self):
        """Real-world style APPROVED review."""
        review = """
## Code Review Summary

The implementation looks good and follows the coding standards.
All tests pass and documentation is adequate.

**Verdict: APPROVED**

No further changes required.
"""
        result = parse_verdict(review)
        assert result == (Verdict.APPROVED, "")

    def test_real_world_rejected_review(self):
        """Real-world style REJECTED review with detailed feedback."""
        review = """
## Code Review Summary

The implementation has several issues that need to be addressed.

**Verdict: REJECTED**

### Issues Found:
1. Missing error handling in `parse_data()` function
2. No unit tests for edge cases
3. Documentation needs to be updated

### Recommendations:
- Add try/except blocks for file operations
- Add tests for empty input and malformed data
"""
        result = parse_verdict(review)
        assert result[0] == Verdict.REJECTED
        assert "Missing error handling" in result[1]
        assert "No unit tests for edge cases" in result[1]
        assert "Recommendations:" in result[1]

    def test_multiline_approved(self):
        """APPROVED marker on its own line."""
        review = """
Review complete.

APPROVED

Ship it!
"""
        result = parse_verdict(review)
        assert result == (Verdict.APPROVED, "")


# ---------------------------------------------------------------------------
# write_cycle_file tests
# ---------------------------------------------------------------------------


class TestWriteCycleFile:
    """Tests for write_cycle_file function (SC-007)."""

    def test_creates_step_directory(self, tmp_path: Path):
        """Creates .forge/{step_name}/ directory if it doesn't exist."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="approved",
            feedback="",
            skill="local-code-review",
            elapsed_seconds=15.5,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "implement", cycle_data)

        step_dir = workspace / ".forge" / "implement"
        assert step_dir.exists()
        assert step_dir.is_dir()

    def test_creates_nested_forge_and_step_directories(self, tmp_path: Path):
        """Creates both .forge/ and step subdirectory when neither exists."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="approved",
            feedback="",
            skill="code-review",
            elapsed_seconds=10.0,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "my-step", cycle_data)

        assert (workspace / ".forge").exists()
        assert (workspace / ".forge" / "my-step").exists()

    def test_writes_review_cycle_n_json(self, tmp_path: Path):
        """File written to .forge/{step_name}/review_cycle_N.json."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle_data = ReviewCycleData(
            cycle=2,
            max_cycles=5,
            verdict="rejected",
            feedback="Needs work",
            skill="test-skill",
            elapsed_seconds=20.0,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "step-name", cycle_data)

        output_file = workspace / ".forge" / "step-name" / "review_cycle_2.json"
        assert output_file.exists()
        assert output_file.is_file()

    def test_json_contains_all_required_fields(self, tmp_path: Path):
        """SC-007: JSON contains all ReviewCycleData fields."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="approved",
            feedback="Looks good!",
            skill="local-code-review",
            elapsed_seconds=12.5,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "implement", cycle_data)

        output_file = workspace / ".forge" / "implement" / "review_cycle_1.json"
        data = json.loads(output_file.read_text(encoding="utf-8"))

        assert data["cycle"] == 1
        assert data["max_cycles"] == 3
        assert data["verdict"] == "approved"
        assert data["feedback"] == "Looks good!"
        assert data["skill"] == "local-code-review"
        assert data["elapsed_seconds"] == 12.5
        assert data["timestamp"] == "2024-01-15T10:30:00Z"

    def test_timestamp_iso_8601_utc_format(self, tmp_path: Path):
        """Timestamp is ISO 8601 UTC format."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # ISO 8601 UTC timestamp
        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="approved",
            feedback="",
            skill="test",
            elapsed_seconds=5.0,
            timestamp="2024-06-20T14:30:45Z",
        )

        write_cycle_file(workspace, "step", cycle_data)

        output_file = workspace / ".forge" / "step" / "review_cycle_1.json"
        data = json.loads(output_file.read_text(encoding="utf-8"))

        # Verify timestamp format preserved
        assert data["timestamp"] == "2024-06-20T14:30:45Z"

    def test_verdict_lowercase_approved(self, tmp_path: Path):
        """Verdict is lowercase string 'approved'."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="approved",
            feedback="",
            skill="test",
            elapsed_seconds=5.0,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "step", cycle_data)

        output_file = workspace / ".forge" / "step" / "review_cycle_1.json"
        data = json.loads(output_file.read_text(encoding="utf-8"))

        assert data["verdict"] == "approved"
        assert data["verdict"].islower()

    def test_verdict_lowercase_rejected(self, tmp_path: Path):
        """Verdict is lowercase string 'rejected'."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="rejected",
            feedback="Fix the bugs",
            skill="test",
            elapsed_seconds=8.0,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "step", cycle_data)

        output_file = workspace / ".forge" / "step" / "review_cycle_1.json"
        data = json.loads(output_file.read_text(encoding="utf-8"))

        assert data["verdict"] == "rejected"
        assert data["verdict"].islower()

    def test_verdict_enum_converted_to_lowercase(self, tmp_path: Path):
        """Verdict.APPROVED enum is converted to lowercase string."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict=Verdict.APPROVED,
            feedback="",
            skill="test",
            elapsed_seconds=5.0,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "step", cycle_data)

        output_file = workspace / ".forge" / "step" / "review_cycle_1.json"
        data = json.loads(output_file.read_text(encoding="utf-8"))

        assert data["verdict"] == "approved"

    def test_verdict_enum_rejected_converted_to_lowercase(self, tmp_path: Path):
        """Verdict.REJECTED enum is converted to lowercase string."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict=Verdict.REJECTED,
            feedback="Needs work",
            skill="test",
            elapsed_seconds=5.0,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "step", cycle_data)

        output_file = workspace / ".forge" / "step" / "review_cycle_1.json"
        data = json.loads(output_file.read_text(encoding="utf-8"))

        assert data["verdict"] == "rejected"

    def test_multiple_cycles_written_separately(self, tmp_path: Path):
        """Multiple cycles are written to separate files."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle1 = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="rejected",
            feedback="First attempt needs work",
            skill="code-review",
            elapsed_seconds=10.0,
            timestamp="2024-01-15T10:30:00Z",
        )
        cycle2 = ReviewCycleData(
            cycle=2,
            max_cycles=3,
            verdict="rejected",
            feedback="Still has issues",
            skill="code-review",
            elapsed_seconds=12.0,
            timestamp="2024-01-15T10:35:00Z",
        )
        cycle3 = ReviewCycleData(
            cycle=3,
            max_cycles=3,
            verdict="approved",
            feedback="",
            skill="code-review",
            elapsed_seconds=8.0,
            timestamp="2024-01-15T10:40:00Z",
        )

        write_cycle_file(workspace, "impl", cycle1)
        write_cycle_file(workspace, "impl", cycle2)
        write_cycle_file(workspace, "impl", cycle3)

        # All three files should exist
        assert (workspace / ".forge" / "impl" / "review_cycle_1.json").exists()
        assert (workspace / ".forge" / "impl" / "review_cycle_2.json").exists()
        assert (workspace / ".forge" / "impl" / "review_cycle_3.json").exists()

        # Verify content of each
        data1 = json.loads((workspace / ".forge" / "impl" / "review_cycle_1.json").read_text())
        data2 = json.loads((workspace / ".forge" / "impl" / "review_cycle_2.json").read_text())
        data3 = json.loads((workspace / ".forge" / "impl" / "review_cycle_3.json").read_text())

        assert data1["verdict"] == "rejected"
        assert data2["verdict"] == "rejected"
        assert data3["verdict"] == "approved"

    def test_json_format_is_pretty_printed(self, tmp_path: Path):
        """JSON is formatted with indentation for readability."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="approved",
            feedback="",
            skill="test",
            elapsed_seconds=5.0,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "step", cycle_data)

        output_file = workspace / ".forge" / "step" / "review_cycle_1.json"
        content = output_file.read_text(encoding="utf-8")

        # Should have newlines (pretty printed)
        assert "\n" in content
        # Should have indentation
        assert "  " in content

    def test_overwrites_existing_file(self, tmp_path: Path):
        """Overwrites existing cycle file if run again with same cycle number."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Write first version
        cycle_data1 = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="rejected",
            feedback="First feedback",
            skill="test",
            elapsed_seconds=5.0,
            timestamp="2024-01-15T10:30:00Z",
        )
        write_cycle_file(workspace, "step", cycle_data1)

        # Overwrite with second version
        cycle_data2 = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="approved",
            feedback="Updated feedback",
            skill="test",
            elapsed_seconds=8.0,
            timestamp="2024-01-15T10:35:00Z",
        )
        write_cycle_file(workspace, "step", cycle_data2)

        output_file = workspace / ".forge" / "step" / "review_cycle_1.json"
        data = json.loads(output_file.read_text(encoding="utf-8"))

        # Should have the updated values
        assert data["verdict"] == "approved"
        assert data["feedback"] == "Updated feedback"
        assert data["elapsed_seconds"] == 8.0

    def test_empty_feedback(self, tmp_path: Path):
        """Empty feedback string is preserved."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="approved",
            feedback="",
            skill="test",
            elapsed_seconds=5.0,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "step", cycle_data)

        output_file = workspace / ".forge" / "step" / "review_cycle_1.json"
        data = json.loads(output_file.read_text(encoding="utf-8"))

        assert data["feedback"] == ""

    def test_multiline_feedback(self, tmp_path: Path):
        """Multiline feedback is preserved correctly."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        multiline_feedback = """Fix the following issues:
1. Missing error handling
2. No unit tests
3. Documentation incomplete"""

        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="rejected",
            feedback=multiline_feedback,
            skill="code-review",
            elapsed_seconds=15.0,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "step", cycle_data)

        output_file = workspace / ".forge" / "step" / "review_cycle_1.json"
        data = json.loads(output_file.read_text(encoding="utf-8"))

        assert data["feedback"] == multiline_feedback
        assert "1. Missing error handling" in data["feedback"]
        assert "2. No unit tests" in data["feedback"]

    def test_special_characters_in_feedback(self, tmp_path: Path):
        """Special characters in feedback are handled correctly."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="rejected",
            feedback='Fix "quotes", <tags>, and unicode: émoji 🎉',
            skill="test",
            elapsed_seconds=5.0,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "step", cycle_data)

        output_file = workspace / ".forge" / "step" / "review_cycle_1.json"
        data = json.loads(output_file.read_text(encoding="utf-8"))

        assert data["feedback"] == 'Fix "quotes", <tags>, and unicode: émoji 🎉'

    def test_elapsed_seconds_float_precision(self, tmp_path: Path):
        """Float precision for elapsed_seconds is preserved."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="approved",
            feedback="",
            skill="test",
            elapsed_seconds=123.456789,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "step", cycle_data)

        output_file = workspace / ".forge" / "step" / "review_cycle_1.json"
        data = json.loads(output_file.read_text(encoding="utf-8"))

        assert data["elapsed_seconds"] == 123.456789

    def test_step_name_with_hyphens(self, tmp_path: Path):
        """Step name with hyphens is handled correctly."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="approved",
            feedback="",
            skill="test",
            elapsed_seconds=5.0,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "local-code-review", cycle_data)

        assert (workspace / ".forge" / "local-code-review" / "review_cycle_1.json").exists()

    def test_step_name_with_underscores(self, tmp_path: Path):
        """Step name with underscores is handled correctly."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        cycle_data = ReviewCycleData(
            cycle=1,
            max_cycles=3,
            verdict="approved",
            feedback="",
            skill="test",
            elapsed_seconds=5.0,
            timestamp="2024-01-15T10:30:00Z",
        )

        write_cycle_file(workspace, "my_step_name", cycle_data)

        assert (workspace / ".forge" / "my_step_name" / "review_cycle_1.json").exists()
