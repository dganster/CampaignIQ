from decimal import Decimal

from campaigniq.importers.thinkorswim.csv_reader import CsvReader
from campaigniq.importers.thinkorswim.forex_trade_reader import (
    read_forex_trades,
)
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


DATA_FILE = "tests/data/thinkorswim/Account Trading History 2026.csv"


def test_reads_january_forex_trades() -> None:
    statement = ThinkorswimSourceReader().read(DATA_FILE)
    section = statement.section("Forex Statements")

    rows = read_forex_trades(section)

    first = rows[0]

    assert first.pair == "EUR/USD"
    assert first.action == "BOT"
    assert first.quantity == Decimal("50000")
    assert first.price == Decimal("1.17232")
    assert first.broker_pnl_usd is None

    second = rows[1]

    assert second.pair == "EUR/USD"
    assert second.action == "SOLD"
    assert second.quantity == Decimal("-50000")
    assert second.price == Decimal("1.1724")
    assert second.broker_pnl_usd == Decimal("4.00")

def test_reads_january_forex_broker_pnl() -> None:
    statement = ThinkorswimSourceReader().read(DATA_FILE)
    section = statement.section("Forex Statements")

    rows = read_forex_trades(section)

    pnl = sum(
        (
            row.broker_pnl_usd
            for row in rows
            if row.broker_pnl_usd is not None
            and row.executed_at.date().month == 1
        ),
        Decimal("0"),
    )

    assert pnl == Decimal("-18.48")

def test_ignores_non_trade_forex_statement_rows() -> None:
    statement = ThinkorswimSourceReader().read(DATA_FILE)
    section = statement.section("Forex Statements")

    rows = read_forex_trades(section)

    assert all(row.action in {"BOT", "SOLD"} for row in rows)
