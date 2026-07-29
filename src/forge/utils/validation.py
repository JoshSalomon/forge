"""Input validation utilities."""

import re

_TICKET_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]+-\d+$", re.IGNORECASE)


def validate_ticket_key(ticket_key: str) -> str:
    """Validate a Jira ticket key and return it uppercased.

    Raises ValueError for keys that don't match PROJECT-NUMBER format.
    """
    if not _TICKET_KEY_PATTERN.match(ticket_key):
        raise ValueError(f"Invalid ticket key format: {ticket_key!r}")
    return ticket_key.upper()
