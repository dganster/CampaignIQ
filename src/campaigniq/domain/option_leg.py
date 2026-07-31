"""A single option leg."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.side import Side


@dataclass(frozen=True, slots=True)
class OptionLeg:
    """One executed option leg."""

    contract: OptionContract
    side: Side
    quantity: Decimal
    execution_price: Decimal
    executed_at: datetime
    broker_strategy: str
