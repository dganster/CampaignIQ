"""Orchestrate broker imports into the CampaignIQ economic pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.campaign import Campaign
from campaigniq.domain.boundary_reconstruction import (
    BoundaryReconstruction,
    BoundaryReconstructionAnalyzer,
)
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
from campaigniq.domain.historical_lot_reconstructor import (
    HistoricalLotReconstructor,
)
from campaigniq.domain.position_event import PositionEvent
from campaigniq.domain.position_event_reconciler import (
    PositionEventReconciler,
)
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.trade import Trade
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.importers.schwab.option_assignment_flow import (
    read_option_assignment_events,
)
from campaigniq.importers.schwab.position_snapshot import to_lots
from campaigniq.domain.forex_settlement_attribution import (
    ForexSettlementAttribution,
    attribute_forex_settlements,
)
from campaigniq.importers.schwab.forex_transaction_reader import (
    SchwabForexTransactionReport,
    read_forex_transaction_report,
)
from campaigniq.importers.schwab.realized_gain_loss_reader import (
    read_realized_gain_loss_section,
)
from campaigniq.importers.schwab.position_snapshot_reader import (
    read_position_snapshot_section,
)
from campaigniq.importers.thinkorswim.trade_history_reader import (
    ThinkorswimTradeHistoryReader,
)
from campaigniq.importers.thinkorswim.trade_reader import (
    ThinkorswimTradeReader,
)
from campaigniq.importers.thinkorswim.cash_balance_reader import (
    ThinkorswimCashBalanceReader,
)
from campaigniq.importers.thinkorswim.expiration_event_reader import (
    ThinkorswimExpirationEventReader,
)
from campaigniq.importers.thinkorswim.translator import to_trade
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


from campaigniq.domain.value_objects.forex_pair import ForexPair

from campaigniq.importers.thinkorswim.historical_forex_position_reader import (
    ThinkorswimHistoricalForexPositionReader,
)

@dataclass(frozen=True, slots=True)
class PeriodImportResult:
    """Results produced by one period import."""

    trades: tuple[Trade, ...]
    campaigns: tuple
    position_events: tuple[PositionEvent, ...]
    attribution_events: tuple[PositionEvent, ...]
    position_history: PositionHistory
    opening_lot_book: LotBook
    ending_lot_book: LotBook
    boundary_reconstruction: BoundaryReconstruction
    realized_gain_loss: tuple[RealizedGainLossRecord, ...]
    forex_transaction_report: SchwabForexTransactionReport | None = None
    forex_settlement_attributions: tuple[ForexSettlementAttribution, ...] = ()


class PeriodImportPipeline:
    """Translate supported broker files into the CampaignIQ domain pipeline."""

    def __init__(self) -> None:
        self._source_reader = ThinkorswimSourceReader()
        self._historical_forex_position_reader = (
            ThinkorswimHistoricalForexPositionReader(
                self._source_reader,
            )
        )
        self._trade_history_reader = ThinkorswimTradeHistoryReader()
        self._trade_reader = ThinkorswimTradeReader(
            self._source_reader,
            self._trade_history_reader,
        )
        self._cash_balance_reader = ThinkorswimCashBalanceReader()
        self._expiration_event_reader = ThinkorswimExpirationEventReader(
            self._cash_balance_reader,
        )
        self._position_event_reconciler = PositionEventReconciler()
        self._campaign_reconstructor = CampaignReconstructor()
        self._historical_lot_reconstructor = HistoricalLotReconstructor(
            campaign_reconstructor=self._campaign_reconstructor,
        )
        self._boundary_analyzer = BoundaryReconstructionAnalyzer()
        self._lot_book_period_applier = LotBookPeriodApplier()

    def run(
        self,
        *,
        period_start: date,
        period_end: date,
        thinkorswim_trade_history: str | Path,
        opening_snapshot: str | Path | None = None,
        opening_snapshot_at: datetime | None = None,
        carried_opening_lot_book: LotBook | None = None,
        assignment_lines: tuple[list[str], ...] = (),
        boundary_assignment_lines: tuple[list[str], ...] = (),
        realized_gain_loss_report: str | Path | None = None,
        forex_transaction_report: str | Path | None = None,
        historical_trade_histories: tuple[str | Path, ...] = (),
        historical_period_start: date | None = None,
        historical_source_root: str | Path | None = None,
    ) -> PeriodImportResult:
        """Run one monthly import from source records through boundary analysis."""

        # Preserve the existing manual-history behavior exactly.
        if historical_source_root is None:
            return self._run_once(
                period_start=period_start,
                period_end=period_end,
                thinkorswim_trade_history=thinkorswim_trade_history,
                opening_snapshot=opening_snapshot,
                opening_snapshot_at=opening_snapshot_at,
                carried_opening_lot_book=carried_opening_lot_book,
                assignment_lines=assignment_lines,
                boundary_assignment_lines=boundary_assignment_lines,
                realized_gain_loss_report=realized_gain_loss_report,
                forex_transaction_report=forex_transaction_report,
                historical_trade_histories=historical_trade_histories,
                historical_period_start=historical_period_start,
            )

        # First pass: deliberately do not seed historical evidence.
        first_pass = self._run_once(
            period_start=period_start,
            period_end=period_end,
            thinkorswim_trade_history=thinkorswim_trade_history,
            opening_snapshot=opening_snapshot,
            opening_snapshot_at=opening_snapshot_at,
            carried_opening_lot_book=carried_opening_lot_book,
            assignment_lines=assignment_lines,
            boundary_assignment_lines=boundary_assignment_lines,
            realized_gain_loss_report=realized_gain_loss_report,
            forex_transaction_report=forex_transaction_report,
            historical_trade_histories=historical_trade_histories,
            historical_period_start=historical_period_start,
            seed_historical=False,
        )

        requirements = first_pass.boundary_reconstruction.historical_requirements

        if not requirements:
            return first_pass

        from campaigniq.domain.historical_evidence import (
            ThinkorswimHistoricalEvidenceRepository,
        )
        from campaigniq.domain.historical_evidence_resolver import (
            HistoricalEvidenceResolver,
        )

        resolver = HistoricalEvidenceResolver(
            ThinkorswimHistoricalEvidenceRepository(
                historical_source_root
            )
        )

        discovered_paths: dict[Path, object] = {}

        for requirement in requirements:
            resolution = resolver.resolve(requirement)

            for evidence in resolution.trade_history:
                discovered_paths[evidence.path] = evidence

        if not discovered_paths:
            return first_pass

        all_history = tuple(
            dict.fromkeys(
                (
                    *historical_trade_histories,
                    *discovered_paths.keys(),
                )
            )
        )

        historical_starts = [
            evidence.coverage_start
            for evidence in discovered_paths.values()
        ]

        effective_historical_period_start = historical_period_start

        if historical_starts:
            discovered_start = min(historical_starts)

            if (
                effective_historical_period_start is None
                or discovered_start < effective_historical_period_start
            ):
                effective_historical_period_start = discovered_start

        return self._run_once(
            period_start=period_start,
            period_end=period_end,
            thinkorswim_trade_history=thinkorswim_trade_history,
            opening_snapshot=opening_snapshot,
            opening_snapshot_at=opening_snapshot_at,
            carried_opening_lot_book=carried_opening_lot_book,
            assignment_lines=assignment_lines,
            boundary_assignment_lines=boundary_assignment_lines,
            realized_gain_loss_report=realized_gain_loss_report,
            forex_transaction_report=forex_transaction_report,
            historical_trade_histories=all_history,
            historical_period_start=effective_historical_period_start,
        )

    def _read_historical_forex_positions(
        self,
        filenames: tuple[str | Path, ...],
        *,
        start: date | None,
        end: date,
    ) -> dict[str, Decimal]:
        """Build Forex positions from historical executions before the period."""

        return self._historical_forex_position_reader.read(
            filenames,
            start=start,
            end=end,
        )

    def _build_opening_lot_book(
        self,
        *,
        opening_snapshot: str | Path | None,
        opening_snapshot_at: datetime | None,
        carried_opening_lot_book: LotBook | None,
    ) -> LotBook:
        """Build independent opening state from exactly one source."""

        snapshot_supplied = opening_snapshot is not None
        snapshot_at_supplied = opening_snapshot_at is not None
        carried_supplied = carried_opening_lot_book is not None

        valid_snapshot_source = (
            snapshot_supplied
            and snapshot_at_supplied
            and not carried_supplied
        )
        valid_carried_source = (
            carried_supplied
            and not snapshot_supplied
            and not snapshot_at_supplied
        )

        if not (valid_snapshot_source or valid_carried_source):
            raise ValueError(
                "Provide exactly one opening state source: "
                "opening_snapshot with opening_snapshot_at, "
                "or carried_opening_lot_book."
            )

        if carried_opening_lot_book is not None:
            return carried_opening_lot_book.clone()

        assert opening_snapshot is not None
        assert opening_snapshot_at is not None

        opening_lot_book = LotBook()

        snapshot_lines = Path(opening_snapshot).read_text().splitlines()
        snapshot_rows = read_position_snapshot_section(
            snapshot_lines,
            snapshot_at=opening_snapshot_at,
        )

        for lot in to_lots(list(snapshot_rows)):
            opening_lot_book.seed(lot)

        return opening_lot_book

    def _run_once(
        self,
        *,
        period_start: date,
        period_end: date,
        thinkorswim_trade_history: str | Path,
        opening_snapshot: str | Path | None,
        opening_snapshot_at: datetime | None,
        carried_opening_lot_book: LotBook | None,
        assignment_lines: tuple[list[str], ...],
        boundary_assignment_lines: tuple[list[str], ...],
        realized_gain_loss_report: str | Path | None,
        forex_transaction_report: str | Path | None,
        historical_trade_histories: tuple[str | Path, ...],
        historical_period_start: date | None,
        seed_historical: bool = True,
    ) -> PeriodImportResult:
        """Run one complete pipeline pass."""

        forex_initial_positions = self._read_historical_forex_positions(
            historical_trade_histories,
            start=historical_period_start,
            end=period_start - date.resolution,
        )

        trades = tuple(
            self._read_trades(
                thinkorswim_trade_history,
                start=period_start,
                end=period_end,
                forex_initial_positions=forex_initial_positions,
            )
        )

        non_forex_trades = [
            trade
            for trade in trades
            if not isinstance(trade.legs[0].instrument, ForexPair)
        ]
        forex_trades = [
            trade
            for trade in trades
            if isinstance(trade.legs[0].instrument, ForexPair)
        ]

        non_forex_campaigns = tuple(
            self._campaign_reconstructor.reconstruct(non_forex_trades)
        )
        raw_forex_campaigns = tuple(
            self._campaign_reconstructor.reconstruct(forex_trades)
        )
        forex_campaigns = tuple(
            Campaign(
                campaign_id=f"CAMP-{index:06d}",
                trades=campaign.trades,
                started_before_data=campaign.started_before_data,
            )
            for index, campaign in enumerate(
                raw_forex_campaigns,
                start=len(non_forex_campaigns) + 1,
            )
        )
        campaigns = (*non_forex_campaigns, *forex_campaigns)

        assignment_events = tuple(
            event
            for lines in assignment_lines
            for event in read_option_assignment_events(lines)
            if period_start <= event.occurred_at.date() <= period_end
        )

        boundary_assignment_date = period_end + date.resolution
        while boundary_assignment_date.weekday() >= 5:
            boundary_assignment_date += date.resolution

        boundary_assignment_events = tuple(
            event
            for lines in boundary_assignment_lines
            for event in read_option_assignment_events(lines)
            if event.occurred_at.date() == boundary_assignment_date
        )

        assignment_evidence = (
            *assignment_events,
            *boundary_assignment_events,
        )

        expiration_events = self._read_expiration_events(
            thinkorswim_trade_history,
            start=period_start,
            end=period_end,
        )

        position_events = self._position_event_reconciler.reconcile(
            (*assignment_events, *expiration_events),
            corroborating_assignments=boundary_assignment_events,
        )

        attribution_events = (
            *position_events,
            *boundary_assignment_events,
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

        opening_lot_book = self._build_opening_lot_book(
            opening_snapshot=opening_snapshot,
            opening_snapshot_at=opening_snapshot_at,
            carried_opening_lot_book=carried_opening_lot_book,
        )

        if seed_historical:
            historical_trades = tuple(
                trade
                for filename in historical_trade_histories
                for trade in self._read_trades(
                    filename,
                    start=historical_period_start,
                    end=period_start - date.resolution,
                )
            )

            historical_expiration_events = (
                [
                    event
                    for filename in historical_trade_histories
                    for event in self._read_expiration_events(
                        filename,
                        start=historical_period_start,
                        end=period_start - date.resolution,
                    )
                ]
                if historical_period_start is not None
                else []
            )

            self._historical_lot_reconstructor.seed_missing_option_lots(
                opening_lot_book,
                historical_trades,
                historical_expiration_events=historical_expiration_events,
            )

            self._historical_lot_reconstructor.assign_assignment_option_provenance(
                opening_lot_book,
                assignment_evidence,
                historical_trades,
)

            self._historical_lot_reconstructor.seed_missing_expiration_lots(
                opening_lot_book,
                historical_expiration_events,
                campaigns,
            )

            self._historical_lot_reconstructor.seed_missing_covered_equity_lots(
                opening_lot_book,
                campaigns,
                historical_trades,
            )

            self._historical_lot_reconstructor.assign_rolled_covered_equity_provenance(
                opening_lot_book,
                campaigns,
                historical_trades,
            )

            self._historical_lot_reconstructor.assign_boundary_assignment_covered_equity_provenance(
                opening_lot_book,
                boundary_assignment_events,
                historical_trades,
            )

            self._historical_lot_reconstructor.seed_missing_assignment_covered_equity_lots(
                opening_lot_book,
                assignment_events,
                historical_trades,
            )
        else:
            historical_trades = ()

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

        ending_lot_book = self._lot_book_period_applier.apply(
            opening_lot_book=opening_lot_book,
            position_history=position_history,
            campaigns=campaigns,
        )

        parsed_forex_report = (
            read_forex_transaction_report(forex_transaction_report)
            if forex_transaction_report is not None
            else None
        )
        forex_settlement_attributions = (
            attribute_forex_settlements(
                campaigns,
                parsed_forex_report.settlements,
            )
            if parsed_forex_report is not None
            else ()
        )

        return PeriodImportResult(
            trades=trades,
            campaigns=campaigns,
            position_events=position_events,
            attribution_events=attribution_events,
            position_history=position_history,
            opening_lot_book=opening_lot_book,
            ending_lot_book=ending_lot_book,
            boundary_reconstruction=boundary,
            realized_gain_loss=realized_gain_loss,
            forex_transaction_report=parsed_forex_report,
            forex_settlement_attributions=forex_settlement_attributions,
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

        return self._expiration_event_reader.read(
            statement.section("Cash Balance"),
            start=start,
            end=end,
        )

    def _read_trades(
        self,
        filename: str | Path,
        *,
        start: date | None,
        end: date | None,
        forex_initial_positions: dict[str, Decimal] | None = None,
    ) -> list[Trade]:
        """Read domain trades within an optional date range."""

        return self._trade_reader.read(
            filename,
            start=start,
            end=end,
            forex_initial_positions=forex_initial_positions,
        )
