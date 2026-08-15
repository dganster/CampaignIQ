"""Representation of a Thinkorswim Forex statement trade."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ThinkorswimForexTradeRow:
    """One TRD row from the Thinkorswim Forex Statements section."""

    executed_at: datetime
    reference: str
    action: str
    quantity: Decimal
    pair: str
    price: Decimal
    broker_pnl_usd: Decimal | None
