"""Comment classification for Forge Q&A mode."""

import re
from enum import StrEnum
from typing import Any


class CommentType(StrEnum):
    """Type of comment detected in Jira comments."""

    QUESTION = "question"
    FEEDBACK = "feedback"
    INFORMATIONAL = "informational"
    COMMAND = "command"


# Legacy @forge ask pattern (case insensitive).
_FORGE_ASK_PATTERN = re.compile(r"^\s*@forge\s+ask", re.IGNORECASE)

# Pattern for question mark at start (allowing leading whitespace)
_QUESTION_MARK_PATTERN = re.compile(r"^\s*\?")

# Pattern for revision prefix (allowing leading whitespace)
_REVISION_PATTERN = re.compile(r"^\s*!")

# Regex to match case-insensitive /forge command prefix followed by command name
_FORGE_COMMAND_PATTERN = re.compile(r"^\s*/forge\s+([a-zA-Z0-9_-]+)", re.IGNORECASE)

# Regex to match key-value pairs supporting single/double quoted string values or unquoted values
_SINGLE_PAIR_PATTERN = re.compile(
    r'\s*([a-zA-Z_][a-zA-Z0-9_-]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s\'"]+))'
)


def parse_comment_command(comment_text: str) -> dict[str, Any] | None:
    """Parse a /forge comment command and extract its parameters.

    Supported commands:
    - remove: /forge remove <int_id>
    - add: /forge add key=val key2="val with spaces"
    - update: /forge update <int_id> key=val key2="val with spaces"
    - exclude: /forge exclude <int_id>
    - approve: /forge approve

    Args:
        comment_text: The comment text to parse.

    Returns:
        A dictionary containing the parsed 'command' and arguments,
        or an 'error' description if parameters are malformed,
        or None if not a recognized /forge command.
    """
    if not comment_text or not comment_text.strip():
        return None

    match = _FORGE_COMMAND_PATTERN.match(comment_text)
    if not match:
        return None

    cmd_name = match.group(1).lower()
    valid_commands = {"remove", "add", "update", "exclude", "approve"}
    if cmd_name not in valid_commands:
        return None

    args_text = comment_text[match.end() :].strip()

    if cmd_name == "approve":
        if args_text:
            return {
                "command": "approve",
                "error": "approve command does not accept parameters",
            }
        return {"command": "approve"}

    if cmd_name in ("remove", "exclude"):
        if not args_text:
            return {
                "command": cmd_name,
                "error": f"Missing integer ID for {cmd_name} command",
            }
        if re.match(r"^\d+$", args_text):
            return {"command": cmd_name, "id": int(args_text)}
        return {
            "command": cmd_name,
            "error": f"Invalid integer ID for {cmd_name} command: '{args_text}'",
        }

    if cmd_name == "add":
        if not args_text:
            return {
                "command": "add",
                "error": "Missing key-value parameters for add command",
            }
        pos = 0
        params = {}
        while pos < len(args_text):
            m = _SINGLE_PAIR_PATTERN.match(args_text, pos)
            if not m:
                return {
                    "command": "add",
                    "error": f"Malformed parameters or trailing junk near: '{args_text[pos:]}'",
                }
            key = m.group(1)
            val = (
                m.group(2)
                if m.group(2) is not None
                else (m.group(3) if m.group(3) is not None else m.group(4))
            )
            params[key] = val
            pos = m.end()
        return {"command": "add", "params": params}

    if cmd_name == "update":
        if not args_text:
            return {
                "command": "update",
                "error": "Missing integer ID and parameters for update command",
            }
        id_match = re.match(r"^(\d+)(?:\s+(.*))?$", args_text)
        if not id_match:
            first_word = args_text.split(None, 1)[0]
            if not re.match(r"^\d+$", first_word):
                return {
                    "command": "update",
                    "error": f"Invalid integer ID for update command: '{first_word}'",
                }
            return {
                "command": "update",
                "error": "Missing integer ID for update command",
            }
        id_val = int(id_match.group(1))
        params_text = (id_match.group(2) or "").strip()
        params = {}
        if params_text:
            pos = 0
            while pos < len(params_text):
                m = _SINGLE_PAIR_PATTERN.match(params_text, pos)
                if not m:
                    return {
                        "command": "update",
                        "error": f"Malformed parameters or trailing junk near: '{params_text[pos:]}'",
                    }
                key = m.group(1)
                val = (
                    m.group(2)
                    if m.group(2) is not None
                    else (m.group(3) if m.group(3) is not None else m.group(4))
                )
                params[key] = val
                pos = m.end()
        return {"command": "update", "id": id_val, "params": params}

    return None


def classify_comment(comment_text: str) -> CommentType:
    """Classify a comment into question, feedback, command, or informational.

    Classification rules:
    - Commands: Comments starting with /forge (except skip-gate/unskip-gate)
    - Questions: Comments starting with '?' or '@forge ask'
    - Feedback (revision request): Comments starting with '!'
    - Informational: Everything else — ignored by the workflow

    Approvals are handled exclusively via label changes (forge:*-approved),
    not via comment text.

    Args:
        comment_text: The text of the comment to classify.

    Returns:
        The classified comment type.
    """
    if not comment_text or not comment_text.strip():
        return CommentType.INFORMATIONAL

    # Check for commands first, since they are specific prefix patterns.
    # Note: skip-gate/unskip-gate are excluded and should not return CommentType.COMMAND.
    if parse_comment_command(comment_text) is not None:
        return CommentType.COMMAND

    if _QUESTION_MARK_PATTERN.match(comment_text):
        return CommentType.QUESTION

    if _FORGE_ASK_PATTERN.match(comment_text):
        return CommentType.QUESTION

    if _REVISION_PATTERN.match(comment_text):
        return CommentType.FEEDBACK

    return CommentType.INFORMATIONAL
