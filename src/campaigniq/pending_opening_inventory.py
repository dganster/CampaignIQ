"""Adjust settled Schwab statement positions to trade-date opening inventory."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.importers.schwab.pending_activity_reader import (
    read_pending_position_activity,
)
from campaigniq.importers.schwab.position_snapshot import (
    SchwabPositionSnapshotRow,
    to_lots,
)


def reconstruct_pending_opening_inventory(
    snapshot_rows: tuple[SchwabPositionSnapshotRow, ...],
    statement_lines: list[str],
    *,
    period_end: date,
) -> LotBook:
    """Reconstruct economic positions from settled positions and pending trades.

    Only activity executed on or before period-end and settling afterward
    is eligible. Missing dates and unmatched reductions fail closed.
    """
    lots = list(to_lots(list(snapshot_rows)))
    pending = read_pending_position_activity(
        statement_lines, period_end=period_end
    )

    # Net eligible pending activity by instrument before adjusting lots.
    # Same-contract opening and closing transactions can offset entirely.
    net_changes = {}
    for activity in pending:
        traded = activity.activity_date
        settled = activity.settlement_date

        if traded is None or settled is None:
            raise ValueError(
                "Pending opening-inventory activity has an unknown date."
            )
        if traded > period_end or settled <= period_end:
            continue
        if traded < period_end.replace(day=1):
            raise ValueError(
                "Pending activity predates the statement month."
            )
        if activity.quantity_change == 0:
            raise ValueError("Pending activity has zero quantity.")

        net_changes[activity.instrument] = (
            net_changes.get(activity.instrument, Decimal("0"))
            + activity.quantity_change
        )

    for index, (instrument, change) in enumerate(net_changes.items()):
        if change == 0:
            continue

        traded = period_end

        matching = [
            (i, lot)
            for i, lot in enumerate(lots)
            if lot.instrument == instrument and lot.quantity != 0
        ]
        existing = sum(
            (lot.quantity for _, lot in matching), Decimal("0")
        )

        # A pending cover must reduce an existing short position.
        # Never turn an unmatched cover into a new long position.
        if change > 0 and existing >= 0:
            raise ValueError(
                f"Pending close has no matching short inventory: {instrument}"
            )

        # Opposite-sign activity reduces existing positions first.
        if existing != 0 and existing * change < 0:
            if abs(change) > abs(existing):
                raise ValueError(
                    f"Pending close exceeds settled inventory: {instrument}"
                )

            remaining = abs(change)
            for i, lot in matching:
                if remaining == 0:
                    break
                consumed = min(abs(lot.quantity), remaining)
                new_quantity = (
                    lot.quantity
                    - consumed * (Decimal("1") if lot.quantity > 0
                                  else Decimal("-1"))
                )

                if lot.basis_total is not None:
                    new_basis = (
                        lot.basis_total * new_quantity / lot.quantity
                    )
                else:
                    new_basis = None

                lots[i] = (
                    replace(
                        lot,
                        quantity=new_quantity,
                        basis_total=new_basis,
                    )
                    if new_quantity
                    else None
                )
                remaining -= consumed

            lots = [lot for lot in lots if lot is not None]
            continue

        # Pending opening trades establish new positions. Their cost basis
        # remains unknown until supported by explicit broker evidence.
        lots.append(
            Lot(
                lot_id=(
                    f"SCHWAB-PENDING:{period_end.isoformat()}:"
                    f"{index}:{instrument!r}"
                ),
                instrument=instrument,
                quantity=change,
                opened_at=datetime.combine(
                    traded, datetime.min.time()
                ),
                basis_total=None,
                basis_source=None,
            )
        )

    book = LotBook()
    for lot in lots:
        book.seed(lot)
    return book
