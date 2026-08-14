"""Representation of a Schwab option assignment."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SchwabOptionAssignment:
    """One option assignment reported by Schwab."""

    occurred_at: datetime
    symbol: str
    expiration: date
    strike: Decimal
    option_type: str
    quantity: Decimal
