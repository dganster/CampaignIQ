"""Execution facts for a trade leg."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Execution:
    """A single execution of a trade leg."""

    quantity: Decimal
    execution_price: Decimal
    executed_at: datetime
    