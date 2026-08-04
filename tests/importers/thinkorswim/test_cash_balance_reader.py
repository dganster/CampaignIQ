from campaigniq.importers.thinkorswim.cash_balance_reader import (
    ThinkorswimCashBalanceReader,
)
from campaigniq.sources.thinkorswim.source_reader import (
    ThinkorswimSourceReader,
)


def test_reads_cash_balance_section() -> None:
    source_reader = ThinkorswimSourceReader()

    statement = source_reader.read(
        "tests/data/thinkorswim/Account Trading History.csv"
    )

    section = statement.section("Cash Balance")

    reader = ThinkorswimCashBalanceReader()

    columns = reader.read(section)

    assert columns["DATE"] == 0
    assert columns["TIME"] == 1
    assert columns["BALANCE"] == 8
