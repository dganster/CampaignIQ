"""Cost-basis lots used to explain position closures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from campaigniq.domain.value_objects.instrument import Instrument


@dataclass(frozen=True, slots=True)
class Lot:
    """One signed position lot and its known broker/source basis."""

    lot_id: str
    instrument: Instrument
    quantity: Decimal
    opened_at: datetime
    basis_total: Decimal | None
    basis_source: str | None = None
    campaign_id: str | None = None

    def __post_init__(self) -> None:
        if self.quantity == 0:
            raise ValueError("Lot quantity cannot be zero.")

    @property
    def side(self) -> str:
        return "LONG" if self.quantity > 0 else "SHORT"

    @property
    def absolute_quantity(self) -> Decimal:
        return abs(self.quantity)
