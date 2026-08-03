"""Tests for ThinkorswimTradeRow."""

from campaigniq.importers.thinkorswim.csv_columns import CsvColumns
from campaigniq.importers.thinkorswim.csv_reader import CsvReader
from campaigniq.importers.thinkorswim.trade_row import ThinkorswimTradeRow


def test_parse_first_trade_row() -> None:
    reader = CsvReader()

    rows = reader.read("tests/data/thinkorswim/Account Trading History.csv")
    trade_rows = reader.trade_history_rows(rows)

    columns = CsvColumns(trade_rows[0])

    row = trade_rows[1]

    trade = ThinkorswimTradeRow.from_csv(columns, row)

    assert trade.exec_time
    assert trade.side
    assert trade.symbol
