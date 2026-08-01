"""A single stock leg."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from campaigniq.domain.side import Side
from campaigniq.domain.value_objects.instrument import Instrument


@dataclass(frozen=True, slots=True)
class InstrumentLeg:
    """One executed stock leg."""

    equity: Equity
    side: Side
    quantity: Decimal
    execution_price: Decimal
    executed_at: datetime
