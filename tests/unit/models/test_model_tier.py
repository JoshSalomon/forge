"""Tests for model tier value types, label parsers, and marker parsers.

RED-phase (TDD) tests authored before ``forge.models.model_tier`` exists.  They
pin the contract that the implementation must satisfy:

* :class:`ModelTier` is a string enum with a fixed, ordered set of members.
* ``tier_label`` / ``parse_tier_label`` round-trip and reject invalid values
  (TS-002, TS-003).
* ``format_marker`` emits the exact ``forge.model-tier: {tier}`` line and
  ``parse_marker_line`` accepts valid markers while rejecting missing,
  unparseable, and out-of-set values.

Until ``model_tier.py`` is implemented the import below fails, so every test in
this module errors out (the expected RED result).
"""

import pytest

from forge.models.model_tier import (
    ModelTier,
    format_marker,
    parse_marker_line,
    parse_tier_label,
    tier_label,
)

# ---------------------------------------------------------------------------
# ModelTier enum membership / values
# ---------------------------------------------------------------------------


def test_model_tier_members_and_values() -> None:
    """The enum exposes the expected members mapped to their string values."""
    assert {tier.value for tier in ModelTier} == {"light", "standard", "heavy"}
    assert ModelTier.LIGHT.value == "light"
    assert ModelTier.STANDARD.value == "standard"
    assert ModelTier.HEAVY.value == "heavy"


def test_model_tier_is_string_enum() -> None:
    """Members behave as plain strings (StrEnum)."""
    assert ModelTier.STANDARD == "standard"
    assert str(ModelTier.HEAVY) == "heavy"
    assert isinstance(ModelTier.LIGHT, str)


def test_model_tier_membership() -> None:
    """Known values construct; unknown values raise ``ValueError``."""
    assert ModelTier("light") is ModelTier.LIGHT
    assert ModelTier("heavy") is ModelTier.HEAVY
    with pytest.raises(ValueError):
        ModelTier("gigantic")


# ---------------------------------------------------------------------------
# tier_label / parse_tier_label round-trip (TS-002)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", list(ModelTier))
def test_tier_label_round_trip(tier: ModelTier) -> None:
    """``parse_tier_label(tier_label(tier))`` returns the original tier."""
    label = tier_label(tier)
    assert isinstance(label, str)
    assert parse_tier_label(label) is tier


def test_tier_label_values() -> None:
    """Labels are the bare tier value strings."""
    assert tier_label(ModelTier.LIGHT) == "light"
    assert tier_label(ModelTier.STANDARD) == "standard"
    assert tier_label(ModelTier.HEAVY) == "heavy"


def test_parse_tier_label_accepts_all_members() -> None:
    for tier in ModelTier:
        assert parse_tier_label(tier.value) is tier


# ---------------------------------------------------------------------------
# parse_tier_label rejects invalid / out-of-set values (TS-002, TS-003)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_label",
    [
        "",
        "   ",
        "medium",
        "LIGHT",
        "Standard",
        "heavyweight",
        "light ",
        "forge.model-tier: light",
    ],
)
def test_parse_tier_label_rejects_invalid_values(bad_label: str) -> None:
    with pytest.raises(ValueError):
        parse_tier_label(bad_label)


# ---------------------------------------------------------------------------
# format_marker emits the exact line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tier", "expected"),
    [
        (ModelTier.LIGHT, "forge.model-tier: light"),
        (ModelTier.STANDARD, "forge.model-tier: standard"),
        (ModelTier.HEAVY, "forge.model-tier: heavy"),
    ],
)
def test_format_marker_exact_line(tier: ModelTier, expected: str) -> None:
    assert format_marker(tier) == expected


def test_format_marker_round_trips_through_parse_marker_line() -> None:
    for tier in ModelTier:
        assert parse_marker_line(format_marker(tier)) is tier


# ---------------------------------------------------------------------------
# parse_marker_line accepts valid markers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("forge.model-tier: light", ModelTier.LIGHT),
        ("forge.model-tier: standard", ModelTier.STANDARD),
        ("forge.model-tier: heavy", ModelTier.HEAVY),
    ],
)
def test_parse_marker_line_accepts_valid_markers(line: str, expected: ModelTier) -> None:
    assert parse_marker_line(line) is expected


# ---------------------------------------------------------------------------
# parse_marker_line rejects missing / unparseable / out-of-set (TS-003)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_line",
    [
        "",
        "   ",
        "light",
        "forge.model-tier:",
        "forge.model-tier: ",
        "forge.model-tier: medium",
        "forge.model-tier: LIGHT",
        "model-tier: light",
        "forge.model_tier: light",
        "some other line",
        "forge.model-tier light",
    ],
)
def test_parse_marker_line_rejects_bad_lines(bad_line: str) -> None:
    with pytest.raises(ValueError):
        parse_marker_line(bad_line)
