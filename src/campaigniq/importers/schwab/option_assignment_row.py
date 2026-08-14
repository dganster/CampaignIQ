"""Representation of a Schwab option assignment row."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SchwabOptionAssignmentRow:
    """One Option Assignment row from a Schwab statement."""

    transaction_date: date
    symbol: str
    expiration: date
    strike: Decimal
    option_type: str
    quantity: Decimal
