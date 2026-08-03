from pathlib import Path

from campaigniq.importers.thinkorswim.trade_history_reader import (
    ThinkorswimTradeHistoryReader,
)


def test_reads_trade_history() -> None:
    reader = ThinkorswimTradeHistoryReader()

    orders = reader.read(
        Path("tests/data/thinkorswim/Account Trading History.csv")
    )

    assert len(orders) > 0
