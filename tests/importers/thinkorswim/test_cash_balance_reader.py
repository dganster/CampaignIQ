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

    rows = reader.read(section)

    assert rows[0].transaction_type == "EXP"
    assert rows[2].transaction_type == "DOI"
    assert rows[0].transaction_date.year == 2026
    assert rows[0].transaction_time.hour == 1
    assert rows[0].transaction_time.minute == 15
    assert rows[0].transaction_time.second == 58

    assert len(rows) == 4
