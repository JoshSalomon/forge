"""Unit tests for forge.skills.utils."""

import pytest

from forge.skills.utils import extract_project_key


class TestExtractProjectKey:
    def test_standard_ticket_key(self) -> None:
        assert extract_project_key("MYPROJ-123") == "MYPROJ"

    def test_short_project_key(self) -> None:
        assert extract_project_key("ABC-1") == "ABC"

    def test_lowercase_input_returns_uppercase(self) -> None:
        assert extract_project_key("test-456") == "TEST"

    def test_empty_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            extract_project_key("")

    def test_no_hyphen_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            extract_project_key("INVALID")
