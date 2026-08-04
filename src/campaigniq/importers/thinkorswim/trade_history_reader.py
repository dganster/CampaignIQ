"""Read Thinkorswim trade history exports."""

from __future__ import annotations
from .broker_order import ThinkorswimBrokerOrder
from .csv_columns import CsvColumns
from .order_builder import ThinkorswimOrderBuilder
from .trade_row import ThinkorswimTradeRow
from campaigniq.sources.thinkorswim.section import Section

class ThinkorswimTradeHistoryReader:
    """Reads a Thinkorswim trade history CSV into broker orders."""

    def __init__(self) -> None:
        self._order_builder = ThinkorswimOrderBuilder()

    def read(self, section: Section) -> list[ThinkorswimBrokerOrder]:
        """Read an Account Trade History section."""

        header = section.lines[0].split(",")
        data_rows = [
            row.split(",")
            for row in section.lines[1:]
        ]

        columns = CsvColumns(header)

        trades = [
            ThinkorswimTradeRow.from_csv(columns, row)
            for row in data_rows
        ]

        return self._order_builder.build(trades)
