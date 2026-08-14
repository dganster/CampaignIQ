"""Instrument value object."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Instrument:
    """A tradeable non-option instrument."""

    symbol: str

    @property
    def underlying(self) -> str:
        """Return the underlying symbol for this instrument."""
        return self.symbol
