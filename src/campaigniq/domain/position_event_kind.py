"""Kinds of economic events affecting a position."""

from enum import Enum


class PositionEventKind(Enum):
    """Classify an economic event affecting a position."""

    SPLIT = "SPLIT"
    TRADE = "TRADE"
    ASSIGNMENT = "ASSIGNMENT"
    EXPIRATION = "EXPIRATION"
    EXERCISE = "EXERCISE"
