"""Kinds of economic events affecting a position."""

from enum import Enum


class PositionEventKind(Enum):
    """Classify an economic event affecting a position."""

    TRADE = "TRADE"
    ASSIGNMENT = "ASSIGNMENT"
    EXPIRATION = "EXPIRATION"
    EXERCISE = "EXERCISE"
