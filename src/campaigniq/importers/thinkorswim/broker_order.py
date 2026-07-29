"""Representation of a Thinkorswim brokerage order."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from campaigniq.importers.thinkorswim.trade_row import ThinkorswimTradeRow


@dataclass(slots=True)
class ThinkorswimBrokerOrder:
    """One brokerage order consisting of one or more trade rows."""

    exec_time: datetime
    spread: str
    legs: list[ThinkorswimTradeRow]

    @property
    def quantity(self) -> int:
        """Number of option legs in the order."""
        return len(self.legs)
