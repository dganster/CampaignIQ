"""Directional character of an investment."""

from enum import Enum


class DirectionalBias(Enum):
    """The directional character of an investment."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    