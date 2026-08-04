from campaigniq.importers.thinkorswim.trade_row_reader import (
    ThinkorswimTradeRowReader,
)
from campaigniq.sources.thinkorswim.source_reader import (
    ThinkorswimSourceReader,
)


def test_reads_trade_rows() -> None:
    source_reader = ThinkorswimSourceReader()

    statement = source_reader.read(
        "tests/data/thinkorswim/Account Trading History.csv"
    )

    section = statement.section("Account Trade History")

    reader = ThinkorswimTradeRowReader()

    rows = reader.read(section)

    assert len(rows) > 0
