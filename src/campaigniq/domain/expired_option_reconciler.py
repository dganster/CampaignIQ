"""Identify expired option positions supported by broker disposition evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord


@dataclass(frozen=True, slots=True)
class ExpiredOptionCandidate:
    contract: OptionContract
    residual_quantity: Decimal
    reported_disposition_quantity: Decimal


def identify_expired_option_candidates(
    *,
    ending_lot_book: LotBook,
    realized_gain_loss: tuple[RealizedGainLossRecord, ...],
    period_end: date,
) -> tuple[ExpiredOptionCandidate, ...]:
    """Identify residual expired options with matching realized-report evidence.

    This is diagnostic only. It does not infer an expiration transaction,
    alter the lot book, or determine whether reported dispositions have
    already been accounted for by trade executions.
    """
    reported: dict[OptionContract, Decimal] = {}

    for record in realized_gain_loss:
        if not isinstance(record.instrument, OptionContract):
            continue

        contract = record.instrument
        if contract.expiration > period_end:
            continue

        # Schwab reports SPX weekly options as SPXW, while
        # Thinkorswim identifies the same contracts as SPX.
        # Normalize only for reconciliation; source records
        # retain their original broker symbols.
        if contract.underlying == "SPXW":
            contract = OptionContract(
                underlying="SPX",
                expiration=contract.expiration,
                strike=contract.strike,
                option_type=contract.option_type,
            )

        reported[contract] = (
            reported.get(contract, Decimal("0")) + record.quantity
        )

    candidates = []

    for instrument in ending_lot_book.instruments():
        if not isinstance(instrument, OptionContract):
            continue
        if instrument.expiration > period_end:
            continue

        residual = sum(
            (lot.quantity for lot in ending_lot_book.lots(instrument)),
            Decimal("0"),
        )

        if residual == 0 or instrument not in reported:
            continue

        candidates.append(
            ExpiredOptionCandidate(
                contract=instrument,
                residual_quantity=residual,
                reported_disposition_quantity=reported[instrument],
            )
        )

    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.contract.expiration,
                str(item.contract),
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class DispositionCoverage:
    contract: OptionContract
    reported_quantity: Decimal
    executed_closing_quantity: Decimal
    unexplained_quantity: Decimal


def calculate_disposition_coverage(
    *,
    reported_quantity: Decimal,
    executed_closing_quantity: Decimal,
    contract: OptionContract,
) -> DispositionCoverage:
    """Compare broker-reported dispositions with executed closing quantity.

    This is diagnostic only. It does not determine the type or direction
    of an unexplained disposition or authorize a position adjustment.
    """
    if reported_quantity < 0 or executed_closing_quantity < 0:
        raise ValueError("Disposition quantities cannot be negative.")

    if executed_closing_quantity > reported_quantity:
        raise ValueError(
            "Executed closing quantity exceeds reported dispositions."
        )

    return DispositionCoverage(
        contract=contract,
        reported_quantity=reported_quantity,
        executed_closing_quantity=executed_closing_quantity,
        unexplained_quantity=reported_quantity - executed_closing_quantity,
    )


def executed_option_closing_quantities(
    trades: tuple["Trade", ...],
) -> dict[OptionContract, Decimal]:
    """Count actual closing executions by option contract.

    Opening legs are excluded. Partial fills are counted individually.
    This function is read-only and makes no position adjustments.
    """
    from campaigniq.domain.trade import Trade
    from campaigniq.domain.position_effect import PositionEffect

    quantities: dict[OptionContract, Decimal] = {}

    for trade in trades:
        for leg in trade.legs:
            if leg.position_effect != PositionEffect.CLOSE:
                continue

            if not isinstance(leg.instrument, OptionContract):
                continue

            quantity = sum(
                (abs(execution.quantity) for execution in leg.executions),
                Decimal("0"),
            )

            if quantity < 0:
                raise ValueError("Closing execution quantity cannot be negative.")

            contract = leg.instrument
            quantities[contract] = (
                quantities.get(contract, Decimal("0")) + quantity
            )

    return quantities


def reconcile_expired_option_disposition_coverage(
    *,
    result: "PeriodImportResult",
    period_end: date,
) -> tuple[tuple[ExpiredOptionCandidate, DispositionCoverage], ...]:
    """Compare residual expired options against recorded closing executions.

    Diagnostic only: does not modify positions or create transactions.
    """
    from campaigniq.import_pipeline import PeriodImportResult

    candidates = identify_expired_option_candidates(
        ending_lot_book=result.ending_lot_book,
        realized_gain_loss=result.realized_gain_loss,
        period_end=period_end,
    )

    coverage = []

    for candidate in candidates:
        contract = candidate.contract

        # A report can contain dispositions beyond the review period.
        # Do not infer an adjustment from quantity arithmetic alone.
        matching_closes = sum(
            (
                abs(execution.quantity)
                for trade in result.trades
                for leg in trade.legs
                if leg.instrument == contract
                and leg.position_effect == PositionEffect.CLOSE
                for execution in leg.executions
                if execution.executed_at.date() <= period_end
            ),
            Decimal("0"),
        )

        comparison = calculate_disposition_coverage(
            contract=contract,
            reported_quantity=candidate.reported_disposition_quantity,
            executed_closing_quantity=matching_closes,
        )

        coverage.append((candidate, comparison))

    return tuple(coverage)
