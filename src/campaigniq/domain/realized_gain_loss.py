"""Broker-reported realized gain/loss facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from campaigniq.domain.value_objects.instrument import Instrument


@dataclass(frozen=True, slots=True)
class RealizedGainLossRecord:
    """One broker-reported closed transaction."""

    closed_date: date
    instrument: Instrument
    quantity: Decimal
    closing_price: Decimal
    proceeds: Decimal
    cost_basis: Decimal
    gain_loss: Decimal
    basis_method: str
    term: str
    disallowed_loss: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("Realized quantity must be positive.")

    @property
    def expected_gain_loss(self) -> Decimal:
        return self.proceeds - self.cost_basis + self.disallowed_loss
