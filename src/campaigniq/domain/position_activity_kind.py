"""Kinds of activity that can occur in a position."""

from enum import Enum


class PositionActivityKind(Enum):
    """Classify how a position changes."""

    OPEN = "OPEN"
    ADD = "ADD"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"
    ADJUST = "ADJUST"
