"""Orchestrate broker imports into the CampaignIQ economic pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.boundary_reconstruction import (
    BoundaryReconstruction,
    BoundaryReconstructionAnalyzer,
)
from campaigniq.domain.lot_book import LotBook
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
from campaigniq.importers.thinkorswim.translator import to_trade
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


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
        assignment_lines: list[str] | None = None,
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

        position_events = tuple(
            read_option_assignment_events(assignment_lines)
            if assignment_lines
            else ()
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

        boundary = self._boundary_analyzer.analyze(
            period_start=period_start,
            campaigns=campaigns,
            opening_lot_book=opening_lot_book,
            historical_trades=historical_trades,
            historical_period_start=historical_period_start,
        )

        # Boundary analysis resolves campaigns whose provenance begins before
        # the selected period.  Campaigns that begin inside the period can
        # still consume a pre-period snapshot lot when their first trade is an
        # unambiguous open/close transaction (for example, a calendar roll).
        # Resolve only those in-period cases here; do not bypass the historical
        # ancestry requirement for started_before_data campaigns.

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
