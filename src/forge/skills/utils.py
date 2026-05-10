"""Utility functions for skills."""


def extract_project_key(ticket_key: str) -> str:
    """Extract the project key from a ticket key.

    Args:
        ticket_key: A ticket key in the format "PROJ-123".

    Returns:
        The uppercase project key (e.g., "PROJ").

    Raises:
        ValueError: If ticket_key is empty or contains no hyphen.
    """
    if not ticket_key:
        raise ValueError("ticket_key must not be empty")
    if "-" not in ticket_key:
        raise ValueError(f"ticket_key must contain a hyphen: {ticket_key!r}")
    return ticket_key.split("-")[0].upper()
