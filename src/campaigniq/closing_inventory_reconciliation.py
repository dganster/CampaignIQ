"""Reconcile CampaignIQ ending lots to an authoritative Schwab month-end snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from campaigniq.domain.lot import InstrumentLike
from campaigniq.domain.lot_book import LotBook
from campaigniq.importers.schwab.pending_activity_reader import (
    SchwabPendingOptionActivity,
)
from campaigniq.importers.schwab.position_snapshot import SchwabPositionSnapshotRow


@dataclass(frozen=True, slots=True)
class ClosingInventoryMismatch:
    instrument: InstrumentLike
    computed_quantity: Decimal
    snapshot_quantity: Decimal

    @property
    def difference(self) -> Decimal:
        return self.computed_quantity - self.snapshot_quantity


@dataclass(frozen=True, slots=True)
class ClosingInventoryReconciliation:
    mismatches: tuple[ClosingInventoryMismatch, ...]

    @property
    def reconciled(self) -> bool:
        return not self.mismatches


def _lot_book_quantities(lot_book: LotBook) -> dict[InstrumentLike, Decimal]:
    quantities: dict[InstrumentLike, Decimal] = {}
    for instrument, lots in lot_book._lots.items():
        quantity = sum((lot.quantity for lot in lots), Decimal("0"))
        if quantity:
            quantities[instrument] = quantity
    return quantities


def _snapshot_quantities(
    rows: list[SchwabPositionSnapshotRow] | tuple[SchwabPositionSnapshotRow, ...],
) -> dict[InstrumentLike, Decimal]:
    quantities: dict[InstrumentLike, Decimal] = {}
    for row in rows:
        instrument = row.instrument()
        quantities[instrument] = quantities.get(instrument, Decimal("0")) + row.quantity
    return {
        instrument: quantity
        for instrument, quantity in quantities.items()
        if quantity
    }


def _instrument_sort_key(instrument: InstrumentLike) -> tuple[str, ...]:
    # repr() is deterministic for these frozen value objects and keeps this
    # reconciler independent of presentation-specific formatting.
    return (type(instrument).__name__, repr(instrument))


def reconcile_closing_inventory(
    *,
    ending_lot_book: LotBook,
    snapshot_rows: list[SchwabPositionSnapshotRow]
    | tuple[SchwabPositionSnapshotRow, ...],
    pending_activity: tuple[SchwabPendingOptionActivity, ...] = (),
    period_end: date | None = None,
) -> ClosingInventoryReconciliation:
    """Compare economic ending quantities, including exact period-end pending changes."""
    computed = _lot_book_quantities(ending_lot_book)
    snapshot = _snapshot_quantities(snapshot_rows)

    for activity in pending_activity:
        if period_end is not None and activity.activity_date != period_end:
            continue
        snapshot[activity.instrument] = (
            snapshot.get(activity.instrument, Decimal("0"))
            + activity.quantity_change
        )
        if snapshot[activity.instrument] == 0:
            del snapshot[activity.instrument]

    mismatches = tuple(
        ClosingInventoryMismatch(
            instrument=instrument,
            computed_quantity=computed.get(instrument, Decimal("0")),
            snapshot_quantity=snapshot.get(instrument, Decimal("0")),
        )
        for instrument in sorted(
            set(computed) | set(snapshot),
            key=_instrument_sort_key,
        )
        if computed.get(instrument, Decimal("0"))
        != snapshot.get(instrument, Decimal("0"))
    )

    return ClosingInventoryReconciliation(mismatches=mismatches)
