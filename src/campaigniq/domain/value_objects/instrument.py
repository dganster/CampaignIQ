"""
Represents an equity security.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Instrument:
    """
    A publicly traded equity security.

    Examples:
        IBM
        AAPL
        SPY
    """

    symbol: str


