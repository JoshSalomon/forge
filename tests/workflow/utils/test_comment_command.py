"""Tests for parse_comment_command functionality."""

from forge.workflow.utils import parse_comment_command


def test_parse_remove_command_success() -> None:
    """Test successful parsing of remove command."""
    result = parse_comment_command("/forge remove 2")
    assert result == {"command": "remove", "id": 2}

    # Case insensitivity
    result = parse_comment_command("  /FORGE Remove 42  ")
    assert result == {"command": "remove", "id": 42}


def test_parse_remove_command_failures() -> None:
    """Test parsing failures of remove command."""
    # Missing ID
    result = parse_comment_command("/forge remove")
    assert result is not None
    assert "error" in result
    assert result["command"] == "remove"

    # Invalid ID (string)
    result = parse_comment_command("/forge remove abc")
    assert result is not None
    assert "error" in result
    assert result["command"] == "remove"

    # Invalid ID (negative)
    result = parse_comment_command("/forge remove -5")
    assert result is not None
    assert "error" in result
    assert result["command"] == "remove"


def test_parse_exclude_command_success() -> None:
    """Test successful parsing of exclude command."""
    result = parse_comment_command("/forge exclude 3")
    assert result == {"command": "exclude", "id": 3}


def test_parse_exclude_command_failures() -> None:
    """Test parsing failures of exclude command."""
    result = parse_comment_command("/forge exclude")
    assert result is not None
    assert "error" in result
    assert result["command"] == "exclude"

    result = parse_comment_command("/forge exclude xyz")
    assert result is not None
    assert "error" in result
    assert result["command"] == "exclude"


def test_parse_approve_command_success() -> None:
    """Test successful parsing of approve command."""
    result = parse_comment_command("/forge approve")
    assert result == {"command": "approve"}

    result = parse_comment_command("  /FORGE approve  ")
    assert result == {"command": "approve"}


def test_parse_approve_command_failures() -> None:
    """Test parsing failures of approve command."""
    result = parse_comment_command("/forge approve 1")
    assert result is not None
    assert "error" in result
    assert result["command"] == "approve"


def test_parse_add_command_success() -> None:
    """Test successful parsing of add command."""
    result = parse_comment_command(
        '/forge add summary="Implement API" repo="core-api" description="Set up endpoints"'
    )
    assert result == {
        "command": "add",
        "params": {
            "summary": "Implement API",
            "repo": "core-api",
            "description": "Set up endpoints",
        },
    }

    # Mix of double, single and no quotes
    result = parse_comment_command("/forge add summary='test single' count=42 name=\"quoted\"")
    assert result == {
        "command": "add",
        "params": {
            "summary": "test single",
            "count": "42",
            "name": "quoted",
        },
    }


def test_parse_add_command_failures() -> None:
    """Test parsing failures of add command."""
    # Missing parameters
    result = parse_comment_command("/forge add")
    assert result is not None
    assert "error" in result
    assert result["command"] == "add"

    # Malformed parameter (no key)
    result = parse_comment_command("/forge add =value")
    assert result is not None
    assert "error" in result
    assert result["command"] == "add"

    # Malformed parameters (trailing junk)
    result = parse_comment_command('/forge add key="value" junk')
    assert result is not None
    assert "error" in result
    assert result["command"] == "add"


def test_parse_update_command_success() -> None:
    """Test successful parsing of update command."""
    result = parse_comment_command('/forge update 1 summary="New Summary"')
    assert result == {
        "command": "update",
        "id": 1,
        "params": {"summary": "New Summary"},
    }

    result = parse_comment_command("/forge update 100")
    assert result == {
        "command": "update",
        "id": 100,
        "params": {},
    }


def test_parse_update_command_failures() -> None:
    """Test parsing failures of update command."""
    # Missing everything
    result = parse_comment_command("/forge update")
    assert result is not None
    assert "error" in result
    assert result["command"] == "update"

    # Missing ID but has parameters
    result = parse_comment_command('/forge update summary="test"')
    assert result is not None
    assert "error" in result
    assert result["command"] == "update"

    # Invalid ID
    result = parse_comment_command('/forge update abc summary="test"')
    assert result is not None
    assert "error" in result
    assert result["command"] == "update"

    # Malformed parameters
    result = parse_comment_command('/forge update 1 summary="test" junk')
    assert result is not None
    assert "error" in result
    assert result["command"] == "update"


def test_parse_command_non_matching() -> None:
    """Test that unrelated texts or other /forge commands return None."""
    assert parse_comment_command("/forge skip-gate build") is None
    assert parse_comment_command("/forge unskip-gate test") is None
    assert parse_comment_command("/forge rebase") is None
    assert parse_comment_command("/forge foo") is None
    assert parse_comment_command("?what is this?") is None
    assert parse_comment_command("!please update") is None
    assert parse_comment_command("") is None
