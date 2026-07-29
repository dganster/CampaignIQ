"""Read Thinkorswim trade history exports."""

from __future__ import annotations

from pathlib import Path

from .broker_order import ThinkorswimBrokerOrder
from .csv_columns import CsvColumns
from .csv_reader import CsvReader
from .order_builder import ThinkorswimOrderBuilder
from .trade_row import ThinkorswimTradeRow


class ThinkorswimTradeHistoryReader:
    """Reads a Thinkorswim trade history CSV into broker orders."""

    def __init__(self) -> None:
        self._csv_reader = CsvReader()
        self._order_builder = ThinkorswimOrderBuilder()

    def read(self, filename: str | Path) -> list[ThinkorswimBrokerOrder]:
        """Read a Thinkorswim CSV file."""

        rows = self._csv_reader.read(filename)
        table = self._csv_reader.trade_history_rows(rows)

        header = table[0]
        data_rows = table[1:]

        columns = CsvColumns(header)

        trades = [
            ThinkorswimTradeRow.from_csv(columns, row)
            for row in data_rows
        ]

        return self._order_builder.build(trades)
