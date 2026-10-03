"""Reconcile CampaignIQ ending lots to an authoritative Schwab month-end snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from campaigniq.domain.lot import InstrumentLike
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.importers.schwab.forex_transaction_reader import SchwabForexTransactionReport
from campaigniq.importers.schwab.pending_activity_reader import (
    SchwabPendingOptionActivity,
    SchwabPendingPositionActivity,
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


def _forex_closing_quantities(
    report: SchwabForexTransactionReport,
    *,
    period_end: date,
    opening_lot_book: LotBook | None,
) -> dict[ForexPair, Decimal]:
    """Use broker Total Position, never the computed ending lots, as the control.

    A complete monthly report has no position changes beyond its transaction
    rows. Pairs without activity retain previously authoritative opening units.
    Financing records do not change units. Crypto is a separate account.
    """
    period_start = period_end.replace(day=1)
    if report.period_end != period_end or report.period_start not in {
        period_start, period_start - date.resolution,
    }:
        raise ValueError("FOREX closing control requires the complete selected-month report.")
    positions = {
        instrument: quantity
        for instrument, quantity in _lot_book_quantities(opening_lot_book).items()
        if isinstance(instrument, ForexPair)
    } if opening_lot_book is not None else {}
    events = sorted(
        (*report.new_transactions, *report.settlements),
        key=lambda row: (row.trade_at, row.settlement_at),
    )
    for row in events:
        # The report's declared interval defines the closing control. Avoid
        # silently accepting future events or entries outside that interval.
        if not report.period_start <= row.trade_at.date() <= period_end:
            raise ValueError("FOREX position-control transaction is outside the report period.")
        if not row.total_position.is_finite():
            raise ValueError("Non-finite FOREX Total Position.")
        positions[ForexPair.from_symbol(row.instrument)] = row.total_position
    return positions


def reconcile_closing_inventory(
    *,
    ending_lot_book: LotBook,
    snapshot_rows: list[SchwabPositionSnapshotRow]
    | tuple[SchwabPositionSnapshotRow, ...],
    pending_activity: tuple[SchwabPendingOptionActivity | SchwabPendingPositionActivity, ...] = (),
    period_end: date | None = None,
    forex_report: SchwabForexTransactionReport | None = None,
    opening_lot_book: LotBook | None = None,
) -> ClosingInventoryReconciliation:
    """Compare economic ending quantities, including exact period-end pending changes."""
    computed = _lot_book_quantities(ending_lot_book)
    snapshot = _snapshot_quantities(snapshot_rows)
    if forex_report is not None:
        if period_end is None:
            raise ValueError("FOREX closing control requires a selected period end.")
        snapshot.update(_forex_closing_quantities(
            forex_report, period_end=period_end, opening_lot_book=opening_lot_book,
        ))

    for activity in pending_activity:
        if period_end is not None:
            activity_day = activity.activity_date
            settlement_day = activity.settlement_date
            if activity_day is None or not (
                period_end.replace(day=1) <= activity_day <= period_end
            ):
                continue
            if settlement_day is not None:
                if settlement_day <= period_end:
                    continue
            elif activity_day != period_end:
                # Preserve legacy period-end evidence with no settlement date;
                # earlier activity needs an explicit post-period settlement.
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
