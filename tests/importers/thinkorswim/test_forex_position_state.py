from decimal import Decimal

from campaigniq.importers.thinkorswim.forex_trade_reader import (
    read_forex_trades,
)
from campaigniq.importers.thinkorswim.forex_position_state import (
    build_forex_positions,
)
from campaigniq.sources.thinkorswim.source_reader import (
    ThinkorswimSourceReader,
)


DATA_FILE = (
    "tests/data/thinkorswim/"
    "Account Trade History December 2025.csv"
)

def test_builds_december_forex_opening_positions() -> None:
    statement = ThinkorswimSourceReader().read(DATA_FILE)

    rows = read_forex_trades(
        statement.section("Forex Statements")
    )

    positions = build_forex_positions(rows)

    assert positions["USD/MXN"] == Decimal("-50000")

def test_excludes_flat_forex_pairs() -> None:
    statement = ThinkorswimSourceReader().read(DATA_FILE)

    rows = read_forex_trades(
        statement.section("Forex Statements")
    )

    positions = build_forex_positions(rows)

    assert "EUR/USD" not in positions
    assert "AUD/USD" not in positions
    assert "USD/CAD" not in positions
    assert "USD/JPY" not in positions