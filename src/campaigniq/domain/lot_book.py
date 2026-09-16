"""FIFO position-lot tracking."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from campaigniq.domain.campaign import Campaign
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

    def clone(self) -> "LotBook":
        """Return an independent copy of the current lot state."""

        cloned = LotBook()
        cloned._lots = defaultdict(
            list,
            {
                instrument: list(lots)
                for instrument, lots in self._lots.items()
            },
        )
        cloned._next_id = self._next_id
        return cloned

    def assign_unassigned_lots_to_campaign(
        self,
        instrument: Instrument,
        *,
        quantity: Decimal,
        campaign_id: str,
    ) -> None:
        """Assign an existing unassigned positive lot to an assignment campaign.

        If no existing positive lot is present, the assignment is creating a
        new equity position; apply_signed_change() will create that lot with
        the supplied campaign_id.
        """
        remaining = quantity

        lots = self._lots.get(instrument, [])
        matching = [
            lot
            for lot in lots
            if lot.campaign_id is None and lot.quantity > 0
        ]

        if not matching:
            return

        for lot in list(matching):
            if remaining <= 0:
                break

            take = min(lot.quantity, remaining)

            if take == lot.quantity:
                self.assign_campaign(lot.lot_id, campaign_id)
            else:
                self.split_and_assign_campaign(
                    lot.lot_id,
                    quantity=take,
                    campaign_id=campaign_id,
                )

            remaining -= take

        if remaining:
            raise ValueError(
                f"Insufficient unassigned {instrument} lots to assign "
                f"{quantity}; {remaining} remains unmatched."
            )

    def assign_campaign(
        self,
        lot_id: str,
        campaign_id: str,
    ) -> None:
        """Assign campaign provenance to an existing lot."""
        for lots in self._lots.values():
            for lot in lots:
                if lot.lot_id != lot_id:
                    continue

                if lot.campaign_id is not None:
                    raise ValueError(
                        f"Lot {lot_id} is already assigned to "
                        f"campaign {lot.campaign_id}."
                    )

                replacement = Lot(
                    lot_id=lot.lot_id,
                    instrument=lot.instrument,
                    quantity=lot.quantity,
                    opened_at=lot.opened_at,
                    basis_total=lot.basis_total,
                    basis_source=lot.basis_source,
                    campaign_id=campaign_id,
                )

                position = lots.index(lot)
                lots[position] = replacement
                return

        raise ValueError(f"Lot {lot_id} not found.")

    def reassign_campaign(
        self,
        lot_id: str,
        campaign_id: str,
    ) -> None:
        """Rebind an existing lot to a new period-local campaign ID."""
        for lots in self._lots.values():
            for lot in lots:
                if lot.lot_id != lot_id:
                    continue

                replacement = Lot(
                    lot_id=lot.lot_id,
                    instrument=lot.instrument,
                    quantity=lot.quantity,
                    opened_at=lot.opened_at,
                    basis_total=lot.basis_total,
                    basis_source=lot.basis_source,
                    campaign_id=campaign_id,
                )

                position = lots.index(lot)
                lots[position] = replacement
                return

        raise ValueError(f"Lot {lot_id} not found.")

    def split_and_reassign_campaign(
        self,
        lot_id: str,
        *,
        quantity: Decimal,
        campaign_id: str,
    ) -> None:
        """Split part of an assigned lot and rebind only that part."""
        if quantity <= 0:
            raise ValueError("Split quantity must be positive.")

        for lots in self._lots.values():
            for lot in list(lots):
                if lot.lot_id != lot_id:
                    continue

                if lot.campaign_id is None:
                    raise ValueError(
                        f"Lot {lot_id} is not already assigned to a campaign."
                    )

                absolute_quantity = abs(lot.quantity)
                if quantity >= absolute_quantity:
                    raise ValueError(
                        f"Split quantity {quantity} must be less than "
                        f"lot quantity {absolute_quantity}."
                    )

                sign = Decimal("1") if lot.quantity > 0 else Decimal("-1")
                reassigned_quantity = sign * quantity
                remainder_quantity = lot.quantity - reassigned_quantity

                if lot.basis_total is None:
                    reassigned_basis = None
                    remainder_basis = None
                else:
                    reassigned_basis = (
                        lot.basis_total
                        * quantity
                        / absolute_quantity
                    )
                    remainder_basis = lot.basis_total - reassigned_basis

                reassigned = Lot(
                    lot_id=lot.lot_id,
                    instrument=lot.instrument,
                    quantity=reassigned_quantity,
                    opened_at=lot.opened_at,
                    basis_total=reassigned_basis,
                    basis_source=lot.basis_source,
                    campaign_id=campaign_id,
                )

                remainder = Lot(
                    lot_id=self._new_lot_id(),
                    instrument=lot.instrument,
                    quantity=remainder_quantity,
                    opened_at=lot.opened_at,
                    basis_total=remainder_basis,
                    basis_source=lot.basis_source,
                    campaign_id=lot.campaign_id,
                )

                position = lots.index(lot)
                lots[position : position + 1] = [reassigned, remainder]
                return

        raise ValueError(f"Lot {lot_id} not found.")

    def split_and_assign_campaign(
        self,
        lot_id: str,
        *,
        quantity: Decimal,
        campaign_id: str,
    ) -> None:
        """Split part of an unassigned lot and assign that part to a campaign."""
        if quantity <= 0:
            raise ValueError("Split quantity must be positive.")

        for lots in self._lots.values():
            for lot in list(lots):
                if lot.lot_id != lot_id:
                    continue

                if lot.campaign_id is not None:
                    raise ValueError(
                        f"Lot {lot_id} is already assigned to "
                        f"campaign {lot.campaign_id}."
                    )

                absolute_quantity = abs(lot.quantity)
                if quantity >= absolute_quantity:
                    raise ValueError(
                        f"Split quantity {quantity} must be less than "
                        f"lot quantity {absolute_quantity}."
                    )

                sign = Decimal("1") if lot.quantity > 0 else Decimal("-1")
                assigned_quantity = sign * quantity
                remainder_quantity = lot.quantity - assigned_quantity

                if lot.basis_total is None:
                    assigned_basis = None
                    remainder_basis = None
                else:
                    assigned_basis = (
                        lot.basis_total
                        * quantity
                        / absolute_quantity
                    )
                    remainder_basis = lot.basis_total - assigned_basis

                assigned = Lot(
                    lot_id=lot.lot_id,
                    instrument=lot.instrument,
                    quantity=assigned_quantity,
                    opened_at=lot.opened_at,
                    basis_total=assigned_basis,
                    basis_source=lot.basis_source,
                    campaign_id=campaign_id,
                )

                remainder = Lot(
                    lot_id=self._new_lot_id(),
                    instrument=lot.instrument,
                    quantity=remainder_quantity,
                    opened_at=lot.opened_at,
                    basis_total=remainder_basis,
                    basis_source=lot.basis_source,
                    campaign_id=None,
                )

                position = lots.index(lot)
                lots[position : position + 1] = [assigned, remainder]
                return

        raise ValueError(f"Lot {lot_id} not found.")

    def resolve_boundary_campaign(
        self,
        campaign: Campaign,
    ) -> dict[str, str]:
        """Assign exact pre-period lots to a boundary campaign.

        A campaign may claim historical lots only when it started before the
        available trade data and its observed closing activity exactly
        consumes those lots. No partial or ambiguous provenance is assigned.
        """
        if not campaign.trades:
            return {}

        if campaign.started_before_data:
            trades_to_inspect = campaign.trades
        else:
            has_open = any(
                leg.position_effect == PositionEffect.OPEN
                for trade in campaign.trades
                for leg in trade.legs
            )
            has_close = any(
                leg.position_effect == PositionEffect.CLOSE
                for trade in campaign.trades
                for leg in trade.legs
            )

            if not (has_open and has_close):
                return {}

            trades_to_inspect = campaign.trades

        required: dict[Instrument, Decimal] = defaultdict(Decimal)

        for trade in trades_to_inspect:
            for leg in trade.legs:
                if leg.position_effect != PositionEffect.CLOSE:
                    continue

                quantity = sum(
                    (abs(execution.quantity) for execution in leg.executions),
                    Decimal("0"),
                )

                if quantity:
                    required[leg.instrument] += quantity

        if not required:
            return {}

        candidates: dict[str, Lot] = {}

        for instrument, quantity in required.items():
            matching_lots = [
                lot
                for lot in self._lots.get(instrument, [])
                if lot.campaign_id is None
            ]

            # No opening lot exists for this instrument.
            # This is normal for a position opened during the campaign.
            if not matching_lots:
                continue

            # The campaign may claim multiple unassigned lots of the same
            # instrument when together they exactly match the required quantity.

            if sum(
                (abs(lot.quantity) for lot in matching_lots),
                Decimal("0"),
            ) != quantity:
                return {}

            for lot in matching_lots:
                candidates[lot.lot_id] = lot
                
        for lot in candidates.values():
            self.assign_campaign(
                lot.lot_id,
                campaign.campaign_id,
            )

        return {
            lot_id: campaign.campaign_id
            for lot_id in candidates
        }

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
                    campaign_id=campaign_id,
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

        total_quantity = sum(
            (a.quantity for a in allocations),
            Decimal("0"),
        )

        result: list[LotAllocation] = []
        remaining = record.cost_basis

        for index, allocation in enumerate(allocations):
            if index == len(allocations) - 1:
                basis = remaining
            else:
                basis = (
                    record.cost_basis
                    * allocation.quantity
                    / total_quantity
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
        campaign_id: str | None = None,
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
                    campaign_id=(
                        lot.campaign_id
                        if lot.campaign_id is not None
                        else campaign_id
                    ),
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
                if lot.basis_total is None:
                    remaining_basis = None
                else:
                    remaining_basis = (
                        lot.basis_total
                        * abs(new_quantity)
                        / abs(lot.quantity)
                    )

                replacement = Lot(
                    lot_id=lot.lot_id,
                    instrument=lot.instrument,
                    quantity=new_quantity,
                    opened_at=lot.opened_at,
                    basis_total=remaining_basis,
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
        return min(
            execution.executed_at
            for execution in leg.executions
        )
