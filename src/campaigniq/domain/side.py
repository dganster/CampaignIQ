"""The side of a trade."""

from enum import Enum


class Side(Enum):
    """Whether a leg is bought or sold."""

    BUY = "BUY"
    SELL = "SELL"
