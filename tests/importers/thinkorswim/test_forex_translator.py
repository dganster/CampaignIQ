from datetime import datetime
from decimal import Decimal

from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.importers.thinkorswim.forex_trade_reader import (
    read_forex_trades,
)
from campaigniq.importers.thinkorswim.forex_translator import (
    to_forex_trade,
)
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


DATA_FILE = "tests/data/thinkorswim/Account Trading History 2026.csv"


def test_translates_forex_trade() -> None:
    statement = ThinkorswimSourceReader().read(DATA_FILE)
    section = statement.section("Forex Statements")
    row = read_forex_trades(section)[0]

    trade = to_forex_trade(row, PositionEffect.OPEN)

    leg = trade.legs[0]

    assert leg.instrument == ForexPair("EUR", "USD")
    assert leg.side is Side.BUY
    assert leg.position_effect is PositionEffect.OPEN
    assert leg.quantity == Decimal("50000")
    assert leg.execution_price == Decimal("1.17232")
    assert leg.executed_at == datetime(2026, 1, 5, 11, 50, 41)

def test_translates_forex_trade_with_resolved_quantity() -> None:
    statement = ThinkorswimSourceReader().read(DATA_FILE)
    section = statement.section("Forex Statements")
    row = read_forex_trades(section)[0]

    trade = to_forex_trade(
        row,
        PositionEffect.CLOSE,
        quantity=Decimal("25000"),
    )

    leg = trade.legs[0]

    assert leg.quantity == Decimal("25000")
    assert leg.position_effect is PositionEffect.CLOSE
    assert leg.instrument == ForexPair("EUR", "USD")