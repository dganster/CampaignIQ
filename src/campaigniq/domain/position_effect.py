"""The effect of a trade on a position."""

from enum import Enum


class PositionEffect(Enum):
    """Whether a trade opens or closes a position."""

    OPEN = "OPEN"
    CLOSE = "CLOSE"
