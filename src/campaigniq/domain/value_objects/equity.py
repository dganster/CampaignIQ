"""
Represents an equity security.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Equity:
    """
    A publicly traded equity security.

    Examples:
        IBM
        AAPL
        SPY
    """

    symbol: str

def test_different_symbols_are_not_equal():
    assert Equity("IBM") != Equity("AAPL")

