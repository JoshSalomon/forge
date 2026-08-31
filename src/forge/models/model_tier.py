"""Model tier value types, label helpers, and marker parsers.

Pure value-type module with no I/O.  It defines the :class:`ModelTier`
enumeration together with small, focused helpers that translate a tier to and
from its plain Jira-label form and its body-text marker form.

Design notes
------------
* ``ModelTier`` is a :class:`~enum.StrEnum` so members compare and serialise as
  plain lowercase strings (matching the convention in
  :mod:`forge.models.workflow`).
* Labels use the *bare* tier value (e.g. ``"light"``).  Keeping the label a
  plain token preserves JQL discoverability (NFR-007).
* Markers are emitted as the exact single line ``forge.model-tier: {tier}``.
  Parsing is deliberately strict/conservative: only exact-case values in the
  fixed tier set are accepted, everything else is rejected (Section 9.2).

This module must remain free of Jira I/O and must not import
``forge.models.model_policy`` (behavioural isolation, NFR-001/BR-007).
"""

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "TIER_MARKER_PREFIX",
    "ModelTier",
    "TierEstimate",
    "format_marker",
    "parse_marker_line",
    "parse_tier_label",
    "tier_label",
]

# Prefix used for the body-text marker line, e.g. ``forge.model-tier: heavy``.
TIER_MARKER_PREFIX = "forge.model-tier:"


class ModelTier(StrEnum):
    """Coarse compute/cost tier assigned to a unit of work.

    Members are ordered from least to most demanding.  Values are lowercase
    strings so the enum round-trips cleanly through labels and markers.
    """

    LIGHT = "light"
    STANDARD = "standard"
    HEAVY = "heavy"


def tier_label(tier: ModelTier) -> str:
    """Return the bare label string for ``tier`` (e.g. ``"light"``)."""
    return tier.value


def parse_tier_label(label: str) -> ModelTier:
    """Parse a bare tier label back into a :class:`ModelTier`.

    The inverse of :func:`tier_label`.  Only exact, in-set lowercase values are
    accepted; empty, whitespace, wrong-case, out-of-set, or marker-prefixed
    values raise :class:`ValueError` (TS-002, TS-003).
    """
    return ModelTier(label)


def format_marker(tier: ModelTier) -> str:
    """Emit the exact marker line ``forge.model-tier: {tier}`` for ``tier``."""
    return f"{TIER_MARKER_PREFIX} {tier.value}"


def parse_marker_line(line: str) -> ModelTier:
    """Parse a single marker line into a :class:`ModelTier`.

    Accepts only a line of the exact form ``forge.model-tier: {tier}`` where
    ``{tier}`` is an in-set, exact-case value.  Missing prefixes, wrong case,
    out-of-set values, and otherwise unparseable lines raise
    :class:`ValueError` (TS-003).  Round-trips with :func:`format_marker`.
    """
    prefix = f"{TIER_MARKER_PREFIX} "
    if not line.startswith(prefix):
        raise ValueError(f"not a model-tier marker line: {line!r}")
    value = line[len(prefix) :]
    return ModelTier(value)


@dataclass(frozen=True)
class TierEstimate:
    """Result of a tier estimation: a chosen tier plus supporting reasons.

    The non-empty-``reasons`` invariant is enforced by the estimator, not by
    this value type, which stays a plain immutable carrier.
    """

    tier: ModelTier
    reasons: list[str] = field(default_factory=list)
