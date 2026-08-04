from campaigniq.importers.thinkorswim.trade_history_reader import (
    ThinkorswimTradeHistoryReader,
)
from campaigniq.sources.thinkorswim.source_reader import (
    ThinkorswimSourceReader,
)


def test_reads_trade_history() -> None:
    source_reader = ThinkorswimSourceReader()

    statement = source_reader.read(
        "tests/data/thinkorswim/Account Trading History.csv"
    )

    section = statement.section("Account Trade History")

    reader = ThinkorswimTradeHistoryReader()

    orders = reader.read(section)

    assert len(orders) > 0
