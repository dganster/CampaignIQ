from campaigniq.importers.thinkorswim.csv_columns import CsvColumns
from campaigniq.importers.thinkorswim.csv_reader import CsvReader
from campaigniq.importers.thinkorswim.order_builder import (
    ThinkorswimOrderBuilder,
)
from campaigniq.importers.thinkorswim.trade_row import (
    ThinkorswimTradeRow,
)


def test_build_orders() -> None:
    reader = CsvReader()

    rows = reader.read("tests/data/thinkorswim/Account Trading History.csv")
    trade_rows = reader.trade_history_rows(rows)

    columns = CsvColumns(trade_rows[0])

    parsed_rows = [
        ThinkorswimTradeRow.from_csv(columns, row)
        for row in trade_rows[1:]
    ]

    builder = ThinkorswimOrderBuilder()

    orders = builder.build(parsed_rows)

    first = orders[0]

    assert first.spread == "CALENDAR"
    assert len(first.legs) == 2

    assert first.legs[0].side == "SELL"
    assert first.legs[1].side == "BUY"
