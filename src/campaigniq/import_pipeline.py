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
from campaigniq.domain.historical_lot_reconstructor import (
    HistoricalLotReconstructor,
)
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

from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.importers.thinkorswim.forex_position_effect_resolver import (
    ForexPositionEffectResolver,
)
from campaigniq.importers.thinkorswim.forex_position_state import (
    build_forex_positions,
)
from campaigniq.importers.thinkorswim.forex_trade_reader import (
    read_forex_trades,
)
from campaigniq.importers.thinkorswim.forex_translator import (
    to_forex_trade,
)

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
                assignment_lines=assignment_lines,
                realized_gain_loss_report=realized_gain_loss_report,
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
            assignment_lines=assignment_lines,
            realized_gain_loss_report=realized_gain_loss_report,
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
            assignment_lines=assignment_lines,
            realized_gain_loss_report=realized_gain_loss_report,
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

        rows = []

        for filename in filenames:
            statement = self._source_reader.read(str(filename))
            rows.extend(
                row
                for row in read_forex_trades(
                    statement.section("Forex Statements")
                )
                if (start is None or row.executed_at.date() >= start)
                and row.executed_at.date() <= end
            )

        return build_forex_positions(rows)

    def _run_once(
        self,
        *,
        period_start: date,
        period_end: date,
        thinkorswim_trade_history: str | Path,
        opening_snapshot: str | Path,
        opening_snapshot_at: datetime,
        assignment_lines: tuple[list[str], ...],
        realized_gain_loss_report: str | Path | None,
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

        campaign_trades = tuple(
            trade
            for trade in trades
            if not isinstance(
                trade.legs[0].instrument,
                ForexPair,
            )
        )

        campaigns = tuple(
            self._campaign_reconstructor.reconstruct(
                list(campaign_trades)
            )
        )

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

            HistoricalLotReconstructor.seed_missing_option_lots(
                opening_lot_book,
                historical_trades,
            )

            self._seed_missing_historical_expiration_lots(
                opening_lot_book,
                historical_expiration_events,
                campaigns,
            )

            self._seed_missing_historical_covered_equity_lots(
                opening_lot_book,
                campaigns,
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

    @staticmethod
    def _seed_missing_historical_covered_equity_lots(
        opening_lot_book: LotBook,
        campaigns: tuple,
        historical_trades: tuple[Trade, ...],
    ) -> None:
        """Reconstruct equity consumed by a continuing covered-call campaign.

        A boundary campaign can continue a covered-call position that was
        established before the selected period.  In that case the opening
        snapshot may contain only the surviving portion of the underlying
        shares while the historical option position explains the larger
        covered position.

        Example:
            historical option position: 5 short calls
            opening snapshot:            1 short call + 100 shares
            current-period close:        4 calls + 400 shares

        The missing 400-share lot is reconstructed so the current-period
        campaign can resolve its equity close without claiming the surviving
        100-share snapshot lot.
        """
        if not historical_trades:
            return

        historical_campaigns = CampaignReconstructor().reconstruct(
            list(historical_trades)
        )

        historical_survivors: dict[OptionContract, Decimal] = defaultdict(
            Decimal
        )

        for historical_campaign in historical_campaigns:
            for trade in historical_campaign.trades:
                for leg in trade.legs:
                    if not isinstance(leg.instrument, OptionContract):
                        continue

                    for execution in leg.executions:
                        historical_survivors[leg.instrument] += (
                            execution.quantity
                        )

        for campaign in campaigns:
            closing_options: dict[OptionContract, Decimal] = defaultdict(
                Decimal
            )
            closing_equity: dict[Instrument, Decimal] = defaultdict(
                Decimal
            )

            for trade in campaign.trades:
                for leg in trade.legs:
                    if leg.position_effect != PositionEffect.CLOSE:
                        continue

                    quantity = sum(
                        (
                            abs(execution.quantity)
                            for execution in leg.executions
                        ),
                        Decimal("0"),
                    )

                    if quantity == 0:
                        continue

                    if isinstance(leg.instrument, OptionContract):
                        closing_options[leg.instrument] += quantity
                    elif isinstance(leg.instrument, Instrument):
                        closing_equity[leg.instrument] += quantity

            if not closing_options or not closing_equity:
                continue

            for option, closing_option_quantity in closing_options.items():
                historical_quantity = historical_survivors.get(
                    option,
                    Decimal("0"),
                )

                if historical_quantity >= 0:
                    continue

                # This is specifically the continuing-boundary pattern:
                # the historical position contains more short contracts than
                # the current campaign closes, leaving a smaller short option
                # position in the opening snapshot.
                historical_contracts = abs(historical_quantity)
                if historical_contracts <= closing_option_quantity:
                    continue

                opening_option_quantity = sum(
                    (
                        lot.quantity
                        for lot in opening_lot_book.lots(option)
                    ),
                    Decimal("0"),
                )

                if opening_option_quantity != historical_quantity:
                    continue

                underlying = Instrument(option.underlying)
                equity_close_quantity = closing_equity.get(
                    underlying,
                    Decimal("0"),
                )

                if equity_close_quantity <= 0:
                    continue

                opening_equity_lots = [
                    lot
                    for lot in opening_lot_book.lots(underlying)
                    if lot.quantity > 0 and lot.campaign_id is None
                ]

                if len(opening_equity_lots) != 1:
                    continue

                surviving_equity_lot = opening_equity_lots[0]

                if surviving_equity_lot.quantity >= equity_close_quantity:
                    continue

                missing_equity_quantity = equity_close_quantity

                historical_campaign_id = None
                for historical_campaign in historical_campaigns:
                    historical_quantity_for_option = sum(
                        (
                            execution.quantity
                            for trade in historical_campaign.trades
                            for leg in trade.legs
                            if (
                                leg.instrument == option
                                and leg.position_effect
                                == PositionEffect.OPEN
                            )
                            for execution in leg.executions
                        ),
                        Decimal("0"),
                    )

                    if historical_quantity_for_option == historical_quantity:
                        if historical_campaign_id is not None:
                            historical_campaign_id = None
                            break
                        historical_campaign_id = (
                            f"HIST-{historical_campaign.campaign_id}"
                        )

                if historical_campaign_id is None:
                    continue

                # The surviving snapshot shares belong to the historical
                # covered-call campaign.  Remove them from consideration by
                # the current boundary resolver, leaving only the missing
                # shares that were consumed by this period's close.
                opening_lot_book.assign_campaign(
                    surviving_equity_lot.lot_id,
                    historical_campaign_id,
                )

                opened_at = surviving_equity_lot.opened_at
                opening_lot_book.seed(
                    Lot(
                        lot_id=(
                            f"HISTORICAL-COVERED-EQUITY:"
                            f"{opened_at.isoformat()}:"
                            f"{underlying}:"
                            f"{historical_campaign_id}"
                        ),
                        instrument=underlying,
                        quantity=missing_equity_quantity,
                        opened_at=opened_at,
                        basis_total=None,
                        basis_source="HISTORICAL_TRADE_RECONSTRUCTION",
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

    def _read_forex_trades(
        self,
        filename: str | Path,
        *,
        start: date | None,
        end: date | None,
        initial_positions: dict[str, Decimal] | None = None,
    ) -> list[Trade]:
        """Read Forex executions with economic position effects."""

        statement = self._source_reader.read(str(filename))

        rows = read_forex_trades(
            statement.section("Forex Statements")
        )

        resolver = ForexPositionEffectResolver(
            initial_positions=initial_positions,
        )

        trades: list[Trade] = []

        for row in sorted(rows, key=lambda item: item.executed_at):
            effects = resolver.resolve(
                pair=row.pair,
                quantity=row.quantity,
            )

            occurred_date = row.executed_at.date()

            if start is not None and occurred_date < start:
                continue

            if end is not None and occurred_date > end:
                continue

            for effect in effects:
                trades.append(
                    to_forex_trade(
                        row,
                        effect.position_effect,
                        quantity=effect.quantity,
                    )
                )

        return trades

    def _read_trades(
        self,
        filename: str | Path,
        *,
        start: date | None,
        end: date | None,
        forex_initial_positions: dict[str, Decimal] | None = None,
    ) -> list[Trade]:
        """Read domain trades within an optional date range."""

        statement = self._source_reader.read(str(filename))

        trades: list[Trade] = []

        # Account Trade History supplies the normal stock, ETF, and
        # option trades. Forex records are handled separately because
        # Thinkorswim's Forex Statements section contains the actual
        # executed Forex trades and prices.

        orders = self._trade_history_reader.read(
            statement.section("Account Trade History")
        )

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

        trades.extend(
            self._read_forex_trades(
                filename,
                start=start,
                end=end,
                initial_positions=forex_initial_positions,
            )
        )

        return sorted(
            trades,
            key=lambda trade: min(
                execution.executed_at
                for leg in trade.legs
                for execution in leg.executions
            ),
        )
