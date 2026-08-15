"""Match broker realized records to CampaignIQ closing activity."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord


@dataclass(frozen=True, slots=True)
class RealizedAttribution:
    """Broker realized record reconciled to CampaignIQ lot allocations."""

    record: RealizedGainLossRecord
    allocations: tuple[LotAllocation, ...]

    @property
    def allocated_quantity(self) -> Decimal:
        return sum((a.quantity for a in self.allocations), Decimal("0"))

    @property
    def basis_reconciled(self) -> bool:
        if any(a.broker_basis is None for a in self.allocations):
            return False
        total = sum(
            (a.broker_basis for a in self.allocations if a.broker_basis is not None),
            Decimal("0"),
        )
        return total == self.record.cost_basis

    @property
    def gain_loss_reconciled(self) -> bool:
        return self.record.expected_gain_loss == self.record.gain_loss
