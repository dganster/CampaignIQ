"""Read Thinkorswim executions into CampaignIQ domain trades."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from campaigniq.domain.trade import Trade
from campaigniq.importers.thinkorswim.forex_position_effect_resolver import (
    ForexPositionEffectResolver,
)
from campaigniq.importers.thinkorswim.forex_trade_reader import (
    read_forex_trades,
)
from campaigniq.importers.thinkorswim.forex_translator import (
    to_forex_trade,
)
from campaigniq.importers.thinkorswim.trade_history_reader import (
    ThinkorswimTradeHistoryReader,
)
from campaigniq.importers.thinkorswim.translator import to_trade
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


class ThinkorswimTradeReader:
    """Read Thinkorswim source records into domain trades."""

    def __init__(
        self,
        source_reader: ThinkorswimSourceReader,
        trade_history_reader: ThinkorswimTradeHistoryReader,
    ) -> None:
        self._source_reader = source_reader
        self._trade_history_reader = trade_history_reader

    def read(
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

        rows = read_forex_trades(
            statement.section("Forex Statements")
        )

        resolver = ForexPositionEffectResolver(
            initial_positions=forex_initial_positions,
        )

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

        return sorted(
            trades,
            key=lambda trade: min(
                execution.executed_at
                for leg in trade.legs
                for execution in leg.executions
            ),
        )
