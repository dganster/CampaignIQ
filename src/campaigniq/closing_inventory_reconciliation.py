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
    forex_source_findings: tuple[str, ...] = ()

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
    standard_period = (
        report.period_start == period_start - date.resolution
        and report.period_end == period_end
    )
    alternative_period = (
        report.period_start == period_start
        and report.period_end == period_end + date.resolution
    )
    if not (standard_period or alternative_period):
        raise ValueError("FOREX closing control requires the complete selected-month report.")

    if alternative_period:
        transaction_dates = (
            [row.trade_at.date() for row in report.new_transactions]
            + [row.settlement_at.date() for row in report.new_transactions]
            + [row.trade_at.date() for row in report.settlements]
            + [row.settlement_at.date() for row in report.settlements]
            + [row.occurred_at.date() for row in report.financing]
        )
        if any(not period_start <= day <= period_end for day in transaction_dates):
            raise ValueError(
                "FOREX closing control contains transactions outside the selected month."
            )
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
    forex_closing_evidence=None,
) -> ClosingInventoryReconciliation:
    """Compare economic ending quantities, including exact period-end pending changes."""
    computed = _lot_book_quantities(ending_lot_book)
    snapshot = _snapshot_quantities(snapshot_rows)
    findings = []
    if forex_report is not None:
        if period_end is None:
            raise ValueError("FOREX closing control requires a selected period end.")
        csv_positions = _forex_closing_quantities(
            forex_report, period_end=period_end, opening_lot_book=opening_lot_book,
        )
        snapshot.update(csv_positions)
    if forex_closing_evidence is not None:
        from campaigniq.forex_opening_statement import ForexOpeningEvidence
        evidence = forex_closing_evidence
        if (not isinstance(evidence, ForexOpeningEvidence) or forex_report is None
                or evidence.period_end != period_end or opening_lot_book is None):
            raise ValueError("Monthly Forex closing control requires matching verified evidence and opening state.")
        actual_opening = {i.symbol: q for i, q in _lot_book_quantities(opening_lot_book).items() if isinstance(i, ForexPair)}
        if actual_opening != dict(evidence.opening):
            raise ValueError("Monthly Forex evidence differs from the actual opening inventory.")
        monthly = {}
        for symbol, quantity in evidence.closing:
            pair = ForexPair.from_symbol(symbol)
            if pair in monthly or not quantity.is_finite() or not quantity:
                raise ValueError("Invalid or duplicate monthly Forex closing quantity.")
            monthly[pair] = quantity
        events = (*forex_report.new_transactions, *forex_report.settlements)
        csv_ids = {row.order_id for row in events}
        missing = set(evidence.execution_ids) - csv_ids
        if csv_ids - set(evidence.execution_ids):
            raise ValueError("CSV Forex execution references conflict with corroborated monthly evidence.")
        csv_nonzero = {i: q for i, q in csv_positions.items() if q}
        # A pair without a CSV transaction still has an opening carry in
        # csv_positions. That inferred carry is not an explicit Total Position.
        explicit_pairs = {ForexPair.from_symbol(row.instrument) for row in events}
        if any(csv_positions.get(pair, Decimal(0)) != monthly.get(pair, Decimal(0))
               for pair in explicit_pairs):
            raise ValueError("Explicit CSV Forex Total Position conflicts with the monthly statement.")
        if missing:
            findings.append("Forex transaction CSV omits execution reference(s): " + ", ".join(sorted(missing)) +
                ". Closing inventory uses the corroborated monthly statement; settlement records and monetary controls remain unchanged. Publication is blocked pending resolution.")
        if csv_nonzero != monthly:
            if not missing:
                raise ValueError("Unexplained Forex source disagreement.")
            findings.append("CSV-derived Forex closing quantities disagree with the monthly statement; the CSV control is retained as an unresolved source finding.")
        # Replace only the Forex quantity control, using independent statement
        # positions already corroborated against history and opening quantities.
        snapshot = {i: q for i, q in snapshot.items() if not isinstance(i, ForexPair)}
        snapshot.update(monthly)

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

    return ClosingInventoryReconciliation(mismatches=mismatches, forex_source_findings=tuple(findings))
