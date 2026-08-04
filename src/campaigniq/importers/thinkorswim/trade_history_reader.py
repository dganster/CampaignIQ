"""Read Thinkorswim trade history exports."""

from __future__ import annotations
from .broker_order import ThinkorswimBrokerOrder
from .order_builder import ThinkorswimOrderBuilder
from .trade_row_reader import ThinkorswimTradeRowReader
from campaigniq.sources.thinkorswim.section import Section

class ThinkorswimTradeHistoryReader:
    """Reads a Thinkorswim trade history CSV into broker orders."""

    def __init__(self) -> None:
        self._order_builder = ThinkorswimOrderBuilder()
        self._trade_row_reader = ThinkorswimTradeRowReader()

    def read(self, section: Section) -> list[ThinkorswimBrokerOrder]:
        """Read an Account Trade History section."""

        trades = self._trade_row_reader.read(section)

        return self._order_builder.build(trades)
