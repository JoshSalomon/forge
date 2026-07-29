"""Tests for input validation utilities."""

import pytest

from forge.utils.validation import validate_ticket_key


class TestValidateTicketKey:
    def test_valid_key(self):
        assert validate_ticket_key("FORGE-123") == "FORGE-123"

    def test_valid_lowercase_returns_uppercased(self):
        assert validate_ticket_key("forge-123") == "FORGE-123"

    def test_valid_multichar_project(self):
        assert validate_ticket_key("MYPROJ-42") == "MYPROJ-42"

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="Invalid ticket key"):
            validate_ticket_key("../../etc-1")

    def test_rejects_slashes(self):
        with pytest.raises(ValueError, match="Invalid ticket key"):
            validate_ticket_key("FORGE/123")

    def test_rejects_spaces(self):
        with pytest.raises(ValueError, match="Invalid ticket key"):
            validate_ticket_key("FORGE 123")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Invalid ticket key"):
            validate_ticket_key("")

    def test_rejects_semicolons(self):
        with pytest.raises(ValueError, match="Invalid ticket key"):
            validate_ticket_key("FORGE;rm-1")

    def test_rejects_no_digits(self):
        with pytest.raises(ValueError, match="Invalid ticket key"):
            validate_ticket_key("FORGE-")

    def test_rejects_no_project(self):
        with pytest.raises(ValueError, match="Invalid ticket key"):
            validate_ticket_key("-123")
