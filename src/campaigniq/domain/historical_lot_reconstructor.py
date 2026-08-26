"""Reconstruct missing opening lots from historical evidence."""

from __future__ import annotations

from decimal import Decimal

from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade


class HistoricalLotReconstructor:
    """Reconstruct missing opening lots from historical trades."""

    @staticmethod
    def seed_missing_option_lots(
        opening_lot_book: LotBook,
        historical_trades: tuple[Trade, ...],
    ) -> None:
        """Seed option lots missing from the opening snapshot when history is sufficient."""
        pending: dict[object, list[list[object]]] = {}
        invalid: set[object] = set()

        for trade in historical_trades:
            for leg in trade.legs:
                if not isinstance(leg.instrument, OptionContract):
                    continue
                instrument = leg.instrument
                if instrument in invalid:
                    continue
                quantity = sum(
                    (abs(execution.quantity) for execution in leg.executions),
                    Decimal("0"),
                )
                if quantity == 0:
                    continue
                lots = pending.setdefault(instrument, [])
                if leg.position_effect == PositionEffect.OPEN:
                    signed = quantity if leg.side == Side.BUY else -quantity
                    opened_at = min(
                        execution.executed_at for execution in leg.executions
                    )
                    lots.append([signed, opened_at])
                    continue
                target_sign = 1 if leg.side == Side.SELL else -1
                remaining = quantity
                for lot in lots:
                    if remaining <= 0:
                        break
                    lot_quantity = lot[0]
                    if (lot_quantity > 0) != (target_sign > 0):
                        continue
                    consumed = min(abs(lot_quantity), remaining)
                    lot[0] = (
                        lot_quantity - consumed
                        if lot_quantity > 0
                        else lot_quantity + consumed
                    )
                    remaining -= consumed
                if remaining:
                    invalid.add(instrument)

        for instrument, lots in pending.items():
            if instrument in invalid:
                continue

            remaining_lots = [
                (quantity, opened_at)
                for quantity, opened_at in lots
                if quantity != 0
            ]

            historical_quantity = sum(
                (quantity for quantity, _ in remaining_lots),
                Decimal("0"),
            )

            existing_quantity = sum(
                (lot.quantity for lot in opening_lot_book.lots(instrument)),
                Decimal("0"),
            )

            missing_quantity = historical_quantity - existing_quantity

            if missing_quantity == 0:
                continue

            # Seed only the portion that is absent from the opening
            # snapshot.  This preserves the snapshot lot representing
            # the surviving position while reconstructing contracts
            # that were closed during the selected period.
            if abs(missing_quantity) < abs(historical_quantity):
                opened_at = min(
                    opened_at
                    for quantity, opened_at in remaining_lots
                    if quantity != 0
                )
                opening_lot_book.seed(
                    Lot(
                        lot_id=(
                            f"HISTORICAL-TRADE:{opened_at.isoformat()}"
                            f":{instrument}:MISSING"
                        ),
                        instrument=instrument,
                        quantity=missing_quantity,
                        opened_at=opened_at,
                        basis_total=None,
                        basis_source="HISTORICAL_TRADE_RECONSTRUCTION",
                    )
                )
                continue

            for index, (quantity, opened_at) in enumerate(
                remaining_lots, 1
            ):
                opening_lot_book.seed(
                    Lot(
                        lot_id=(
                            f"HISTORICAL-TRADE:{opened_at.isoformat()}"
                            f":{instrument}:{index}"
                        ),
                        instrument=instrument,
                        quantity=quantity,
                        opened_at=opened_at,
                        basis_total=None,
                        basis_source="HISTORICAL_TRADE_RECONSTRUCTION",
                    )
                )
