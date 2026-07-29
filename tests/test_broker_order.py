from campaigniq.importers.thinkorswim.broker_order import (
    ThinkorswimBrokerOrder,
)
from campaigniq.importers.thinkorswim.csv_columns import CsvColumns
from campaigniq.importers.thinkorswim.csv_reader import CsvReader
from campaigniq.importers.thinkorswim.trade_row import ThinkorswimTradeRow


def test_create_broker_order() -> None:
    reader = CsvReader()

    rows = reader.read("tests/data/Account Trading History.csv")
    trade_rows = reader.trade_history_rows(rows)

    columns = CsvColumns(trade_rows[0])

    first_leg = ThinkorswimTradeRow.from_csv(columns, trade_rows[1])

    order = ThinkorswimBrokerOrder(
        exec_time=first_leg.exec_time,
        spread=first_leg.spread,
        legs=[first_leg],
    )

    assert order.exec_time == first_leg.exec_time
    assert order.spread == "CALENDAR"
    assert order.quantity == 1
