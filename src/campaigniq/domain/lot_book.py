"""FIFO position-lot tracking."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from campaigniq.domain.leg import Leg
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event import PositionEvent
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


class LotBook:
    """Track opening lots and allocate closes against them in FIFO order.

    This class deliberately does not invent broker cost basis. Opening lots may
    have unknown basis; when a broker Realized Gain/Loss record is available,
    its reported basis is attached to the resulting allocations.
    """

    def __init__(self) -> None:
        self._lots: dict[Instrument, list[Lot]] = defaultdict(list)
        self._next_id = 1

    def seed(self, lot: Lot) -> None:
        """Seed a pre-period lot, typically from a broker position snapshot."""
        self._lots[lot.instrument].append(lot)

    def lots(self, instrument: Instrument) -> tuple[Lot, ...]:
        """Return currently open lots for an instrument."""
        return tuple(self._lots.get(instrument, ()))

    def apply_event(
        self,
        event: PositionEvent,
        *,
        campaign_id: str | None = None,
    ) -> tuple[LotAllocation, ...]:
        """Apply a signed economic position event and return closed-lot allocations."""
        allocations: list[LotAllocation] = []
        for change in event.changes:
            allocations.extend(
                self.apply_signed_change(
                    instrument=change.instrument,
                    quantity=change.quantity,
                    occurred_at=event.occurred_at,
                    campaign_id=campaign_id,
                )
            )
        return tuple(allocations)

    def apply_signed_change(
        self,
        *,
        instrument: Instrument,
        quantity: Decimal,
        occurred_at: datetime,
        campaign_id: str | None = None,
    ) -> tuple[LotAllocation, ...]:
        """Apply a signed position change, closing opposite lots first."""
        if quantity == 0:
            return ()

        allocations: list[LotAllocation] = []
        if quantity > 0:
            closing_quantity = self._available_opposite_quantity(instrument, -1)
            take = min(quantity, closing_quantity)
            if take:
                allocations.extend(
                    self._close(
                        instrument=instrument,
                        quantity=take,
                        closing_side=Side.BUY,
                    )
                )

            remaining = quantity - take
            if remaining:
                self._append_lot(
                    instrument,
                    remaining,
                    occurred_at,
                    campaign_id=campaign_id,
                )
        else:
            quantity_abs = abs(quantity)
            closing_quantity = self._available_opposite_quantity(instrument, 1)
            take = min(quantity_abs, closing_quantity)
            if take:
                allocations.extend(
                    self._close(
                        instrument=instrument,
                        quantity=take,
                        closing_side=Side.SELL,
                    )
                )

            remaining = quantity_abs - take
            if remaining:
                self._append_lot(
                    instrument,
                    -remaining,
                    occurred_at,
                    campaign_id=campaign_id,
                )
        return tuple(allocations)

    def _append_lot(
        self,
        instrument: Instrument,
        quantity: Decimal,
        opened_at: datetime,
        *,
        campaign_id: str | None = None,
    ) -> None:
        self._lots[instrument].append(
            Lot(
                lot_id=self._new_lot_id(),
                instrument=instrument,
                quantity=quantity,
                opened_at=opened_at,
                basis_total=None,
                campaign_id=campaign_id,
            )
        )

    def _available_opposite_quantity(
        self,
        instrument: Instrument,
        sign: int,
    ) -> Decimal:
        return sum(
            (
                abs(lot.quantity)
                for lot in self._lots.get(instrument, [])
                if (lot.quantity > 0) == (sign > 0)
            ),
            Decimal("0"),
        )

    def apply_trade(
        self,
        trade: Trade,
        *,
        campaign_id: str | None = None,
    ) -> tuple[LotAllocation, ...]:
        """Apply a trade and return allocations created by closing legs."""
        allocations: list[LotAllocation] = []
        for leg in trade.legs:
            quantity = sum(
                (abs(execution.quantity) for execution in leg.executions),
                Decimal("0"),
            )
            if quantity == 0:
                continue

            if leg.position_effect == PositionEffect.OPEN:
                signed = quantity if leg.side == Side.BUY else -quantity
                self._lots[leg.instrument].append(
                    Lot(
                        lot_id=self._new_lot_id(),
                        instrument=leg.instrument,
                        quantity=signed,
                        opened_at=self._leg_time(leg),
                        basis_total=None,
                        campaign_id=campaign_id,
                    )
                )
                continue

            allocations.extend(
                self._close(
                    instrument=leg.instrument,
                    quantity=quantity,
                    closing_side=leg.side,
                )
            )
        return tuple(allocations)

    def apply_realized_basis(
        self,
        allocations: tuple[LotAllocation, ...],
        record: "RealizedGainLossRecord",
    ) -> tuple[LotAllocation, ...]:
        """Attach one broker-reported total basis to an allocation set.

        When several lots are consumed, the broker total is allocated
        proportionally by quantity. This is an attribution of the broker fact,
        not a claim that CampaignIQ independently reproduced Schwab's FIFO lot
        calculation.
        """
        from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord

        if not allocations:
            raise ValueError("Cannot attach basis without allocations.")
        total_quantity = sum((a.quantity for a in allocations), Decimal("0"))
        result: list[LotAllocation] = []
        remaining = record.cost_basis
        for index, allocation in enumerate(allocations):
            if index == len(allocations) - 1:
                basis = remaining
            else:
                basis = (
                    record.cost_basis * allocation.quantity / total_quantity
                ).quantize(Decimal("0.01"))
                remaining -= basis
            result.append(
                LotAllocation(
                    lot_id=allocation.lot_id,
                    quantity=allocation.quantity,
                    broker_basis=basis,
                    basis_source="SCHWAB_REALIZED_GAIN_LOSS",
                    campaign_id=allocation.campaign_id,
                )
            )
        return tuple(result)

    def _close(
        self,
        *,
        instrument: Instrument,
        quantity: Decimal,
        closing_side: Side,
    ) -> list[LotAllocation]:
        lots = self._lots.get(instrument, [])
        target_sign = 1 if closing_side == Side.SELL else -1
        remaining = quantity
        allocations: list[LotAllocation] = []

        for lot in list(lots):
            if remaining == 0:
                break
            if (lot.quantity > 0) != (target_sign > 0):
                continue

            consumed = min(abs(lot.quantity), remaining)
            allocations.append(
                LotAllocation(
                    lot_id=lot.lot_id,
                    quantity=consumed,
                    broker_basis=None,
                    campaign_id=lot.campaign_id,
                )
            )

            new_quantity = (
                lot.quantity - consumed
                if lot.quantity > 0
                else lot.quantity + consumed
            )

            if new_quantity == 0:
                lots.remove(lot)
            else:
                replacement = Lot(
                    lot_id=lot.lot_id,
                    instrument=lot.instrument,
                    quantity=new_quantity,
                    opened_at=lot.opened_at,
                    basis_total=lot.basis_total,
                    basis_source=lot.basis_source,
                    campaign_id=lot.campaign_id,
                )
                position = lots.index(lot)
                lots[position] = replacement
            remaining -= consumed

        if remaining:
            raise ValueError(
                f"Insufficient {instrument} lots to close {quantity}; "
                f"{remaining} remains unmatched."
            )
        return allocations

    def _new_lot_id(self) -> str:
        lot_id = f"LOT-{self._next_id:06d}"
        self._next_id += 1
        return lot_id

    @staticmethod
    def _leg_time(leg: Leg) -> datetime:
        if not leg.executions:
            raise ValueError("Trade leg must contain an execution.")
        return min(execution.executed_at for execution in leg.executions)
