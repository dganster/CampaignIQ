"""Orchestrate broker imports into the CampaignIQ economic pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from campaigniq.domain.historical_campaign_provenance import (
    HistoricalCampaignProvenanceResolver,
)
from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.boundary_reconstruction import (
    BoundaryReconstruction,
    BoundaryReconstructionAnalyzer,
)
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.position_event import PositionEvent
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.trade import Trade
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.importers.schwab.option_assignment_flow import (
    read_option_assignment_events,
)
from campaigniq.importers.schwab.position_snapshot import to_lots
from campaigniq.importers.schwab.realized_gain_loss_reader import (
    read_realized_gain_loss_section,
)
from campaigniq.importers.schwab.position_snapshot_reader import (
    read_position_snapshot_section,
)
from campaigniq.importers.thinkorswim.trade_history_reader import (
    ThinkorswimTradeHistoryReader,
)
from campaigniq.importers.thinkorswim.cash_balance_reader import (
    ThinkorswimCashBalanceReader,
)
from campaigniq.importers.thinkorswim.cash_balance_event import (
    to_expiration_event,
)
from campaigniq.importers.thinkorswim.translator import to_trade
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader

from collections import defaultdict


@dataclass(frozen=True, slots=True)
class PeriodImportResult:
    """Results produced by one period import."""

    trades: tuple[Trade, ...]
    campaigns: tuple
    position_events: tuple[PositionEvent, ...]
    position_history: PositionHistory
    opening_lot_book: LotBook
    boundary_reconstruction: BoundaryReconstruction
    realized_gain_loss: tuple[RealizedGainLossRecord, ...]


class PeriodImportPipeline:
    """Translate supported broker files into the CampaignIQ domain pipeline."""

    def __init__(self) -> None:
        self._source_reader = ThinkorswimSourceReader()
        self._trade_history_reader = ThinkorswimTradeHistoryReader()
        self._cash_balance_reader = ThinkorswimCashBalanceReader()
        self._campaign_reconstructor = CampaignReconstructor()
        self._boundary_analyzer = BoundaryReconstructionAnalyzer()

    def run(
        self,
        *,
        period_start: date,
        period_end: date,
        thinkorswim_trade_history: str | Path,
        opening_snapshot: str | Path,
        opening_snapshot_at: datetime,
        assignment_lines: tuple[list[str], ...] = (),
        realized_gain_loss_report: str | Path | None = None,
        historical_trade_histories: tuple[str | Path, ...] = (),
        historical_period_start: date | None = None,
    ) -> PeriodImportResult:
        """Run one monthly import from source records through boundary analysis."""
        trades = tuple(
            self._read_trades(
                thinkorswim_trade_history,
                start=period_start,
                end=period_end,
            )
        )

        campaigns = tuple(self._campaign_reconstructor.reconstruct(list(trades)))

        assignment_events = tuple(
            event
            for lines in assignment_lines
            for event in read_option_assignment_events(lines)
            if period_start <= event.occurred_at.date() <= period_end
        )

        expiration_events = self._read_expiration_events(
            thinkorswim_trade_history,
            start=period_start,
            end=period_end,
        )

        position_events = tuple(
            sorted(
                (*assignment_events, *expiration_events),
                key=lambda event: event.occurred_at,
            )
        )

        position_history = PositionHistory()
        for trade in trades:
            position_history.add_trade(trade)
        for event in position_events:
            position_history.add_event(event)

        realized_gain_loss = (
            read_realized_gain_loss_section(
                Path(realized_gain_loss_report).read_text().splitlines()
            )
            if realized_gain_loss_report is not None
            else ()
        )

        opening_lot_book = LotBook()
        snapshot_lines = Path(opening_snapshot).read_text().splitlines()
        snapshot_rows = read_position_snapshot_section(
            snapshot_lines,
            snapshot_at=opening_snapshot_at,
        )
        for lot in to_lots(list(snapshot_rows)):
            opening_lot_book.seed(lot)

        historical_trades = tuple(
            trade
            for filename in historical_trade_histories
            for trade in self._read_trades(
                filename,
                start=historical_period_start,
                end=period_start - date.resolution,
            )
        )
        
        historical_expiration_events = [
            event
            for filename in historical_trade_histories
            for event in self._read_expiration_events(
                filename,
                start=historical_period_start,
                end=period_start - date.resolution,
            )
        ] if historical_period_start is not None else []

        self._seed_missing_historical_option_lots(
            opening_lot_book,
            historical_trades,
        )

        self._seed_missing_historical_expiration_lots(
            opening_lot_book,
            historical_expiration_events,
            campaigns,
        )

        boundary = self._boundary_analyzer.analyze(
            period_start=period_start,
            campaigns=campaigns,
            opening_lot_book=opening_lot_book,
            historical_trades=historical_trades,
            historical_period_start=historical_period_start,
        )

        for campaign in campaigns:
            if not campaign.started_before_data:
                opening_lot_book.resolve_boundary_campaign(campaign)
        return PeriodImportResult(
            trades=trades,
            campaigns=campaigns,
            position_events=position_events,
            position_history=position_history,
            opening_lot_book=opening_lot_book,
            boundary_reconstruction=boundary,
            realized_gain_loss=realized_gain_loss,
        )
    
    @staticmethod
    def _seed_missing_historical_option_lots(
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
            if instrument in invalid or opening_lot_book.lots(instrument):
                continue
            remaining_lots = [
                (quantity, opened_at)
                for quantity, opened_at in lots
                if quantity != 0
            ]
            for index, (quantity, opened_at) in enumerate(remaining_lots, 1):
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

    @staticmethod
    def _seed_missing_historical_expiration_lots(
        opening_lot_book: LotBook,
        historical_expiration_events: list[PositionEvent],
        campaigns: tuple,
    ) -> None:
        """Seed missing equity lots created by historical expiration events."""
        required: dict[object, Decimal] = {}

        for campaign in campaigns:
            for trade in campaign.trades:
                for leg in trade.legs:
                    if leg.position_effect != PositionEffect.CLOSE:
                        continue

                    if not isinstance(leg.instrument, Instrument):
                        continue

                    quantity = sum(
                        (
                            abs(execution.quantity)
                            for execution in leg.executions
                        ),
                        Decimal("0"),
                    )

                    if quantity:
                        required[leg.instrument] = (
                            required.get(leg.instrument, Decimal("0"))
                            + quantity
                        )

        if not required:
            return

        for event in historical_expiration_events:
            for change in event.changes:
                if change.quantity <= 0:
                    continue

                instrument = change.instrument
                if not isinstance(instrument, Instrument):
                    continue

                if instrument not in required:
                    continue

                existing_quantity = sum(
                    (
                        lot.quantity
                        for lot in opening_lot_book.lots(instrument)
                        if lot.quantity > 0
                    ),
                    Decimal("0"),
                )

                missing_quantity = required[instrument] - existing_quantity
                if missing_quantity <= 0:
                    continue

                quantity = min(change.quantity, missing_quantity)

                opening_lot_book.seed(
                    Lot(
                        lot_id=(
                            f"HISTORICAL-EXPIRATION:"
                            f"{event.occurred_at.isoformat()}:"
                            f"{instrument}"
                        ),
                        instrument=instrument,
                        quantity=quantity,
                        opened_at=event.occurred_at,
                        basis_total=None,
                        basis_source="HISTORICAL_EXPIRATION_RECONSTRUCTION",
                    )
                )

    def _read_expiration_events(
        self,
        filename: str | Path,
        *,
        start: date,
        end: date,
    ) -> list[PositionEvent]:
        """Read TOS EXP Cash Balance records within a date range."""

        statement = self._source_reader.read(str(filename))
        rows = self._cash_balance_reader.read(
            statement.section("Cash Balance")
        )

        events: list[PositionEvent] = []

        for row in rows:
            if row.transaction_type != "EXP":
                continue

            if row.transaction_date < start:
                continue

            if row.transaction_date > end:
                continue

            events.append(to_expiration_event(row))

        return sorted(
            events,
            key=lambda event: event.occurred_at,
        )

    def _read_trades(
        self,
        filename: str | Path,
        *,
        start: date | None,
        end: date | None,
    ) -> list[Trade]:
        """Read non-Forex domain trades within an optional date range."""
        statement = self._source_reader.read(str(filename))
        orders = self._trade_history_reader.read(
            statement.section("Account Trade History")
        )

        trades: list[Trade] = []
        for order in orders:
            if any(
                row.option_type.upper() == "FOREX"
                for row in order.legs
            ):
                continue

            trade = to_trade(order)
            occurred_at = min(
                execution.executed_at
                for leg in trade.legs
                for execution in leg.executions
            )
            occurred_date = occurred_at.date()

            if start is not None and occurred_date < start:
                continue
            if end is not None and occurred_date > end:
                continue

            trades.append(trade)

        return sorted(
            trades,
            key=lambda trade: min(
                execution.executed_at
                for leg in trade.legs
                for execution in leg.executions
            ),
        )
