"""Read historical Thinkorswim Forex positions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from campaigniq.importers.thinkorswim.forex_position_state import (
    build_forex_positions,
)
from campaigniq.importers.thinkorswim.forex_trade_reader import (
    read_forex_trades,
)
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


class ThinkorswimHistoricalForexPositionReader:
    """Build Forex positions from historical Thinkorswim statements."""

    def __init__(
        self,
        source_reader: ThinkorswimSourceReader,
    ) -> None:
        self._source_reader = source_reader

    def read(
        self,
        filenames: tuple[str | Path, ...],
        *,
        start: date | None,
        end: date,
    ) -> dict[str, Decimal]:
        """Build net Forex positions from historical executions."""

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