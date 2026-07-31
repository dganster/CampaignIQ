"""Actions that can be taken on an option contract."""

from enum import Enum


class TradeAction(Enum):
    """The action taken on an option contract."""

    BUY = "BUY"
    SELL = "SELL"
