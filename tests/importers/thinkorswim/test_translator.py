
from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.value_objects.instrument import Instrument

from campaigniq.domain.option_type import OptionType
from campaigniq.domain.side import Side

from campaigniq.importers.thinkorswim.trade_row import ThinkorswimTradeRow
from campaigniq.importers.thinkorswim.translator import (
    to_leg,
    to_option_contract,
    to_trade,
)



def make_option_row(**overrides) -> ThinkorswimTradeRow:
    """Create a Thinkorswim option trade row for tests."""

    row = ThinkorswimTradeRow(
    exec_time=datetime(2026, 7, 29, 10, 30),
    spread="",
    side="BUY",
    qty=Decimal("1"),
    pos_effect="TO OPEN",
    symbol="IBM",
    exp=date(2026, 8, 21),
    strike=Decimal("250"),
    option_type="CALL",
    price=Decimal("3.25"),
    net_price="3.25",
    order_type="LIMIT",
)

    for name, value in overrides.items():
        if not hasattr(row, name):
            raise AttributeError(f"ThinkorswimTradeRow has no field named {name!r}")
        setattr(row, name, value)

    return row

def test_to_leg() -> None:
    executed_at = datetime(2026, 7, 29, 10, 30)

    row = ThinkorswimTradeRow(
        exec_time=executed_at,
        spread="",
        side="BUY",
        qty=Decimal("1"),
        pos_effect="TO OPEN",
        symbol="IBM",
        exp=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type="CALL",
        price=Decimal("3.25"),
        net_price="3.25",
        order_type="LIMIT",
    )

    leg = to_leg(row)

    assert leg.contract.underlying == "IBM"
    assert leg.side is Side.BUY
    assert leg.quantity == Decimal("1")
    assert leg.execution_price == Decimal("3.25")
    assert leg.executed_at == executed_at
    assert leg.broker_strategy == ""

def test_to_option_contract() -> None:
    row = make_option_row()

    contract = to_option_contract(row)

    assert contract.underlying == "IBM"
    assert contract.expiration == date(2026, 8, 21)
    assert contract.strike == Decimal("250")
    assert contract.option_type is OptionType.CALL

def test_to_option_contract_rejects_stock_trade() -> None:
    row = make_option_row(
    qty=Decimal("100"),
    exp=None,
    strike=None,
    option_type="STOCK",
    price=Decimal("250.00"),
    net_price="250.00",
)

    with pytest.raises(ValueError, match="not an option trade"):
        to_option_contract(row)

def test_to_leg_requires_execution_time() -> None:
    row = make_option_row(exec_time=None)

    with pytest.raises(ValueError, match="execution time"):
        to_leg(row)

def test_to_leg_translates_stock_trade() -> None:
    executed_at = datetime(2026, 1, 26, 10, 30)

    row = ThinkorswimTradeRow(
        exec_time=executed_at,
        spread="COVERED",
        side="SELL",
        qty=Decimal("-500"),
        pos_effect="TO CLOSE",
        symbol="COIN",
        exp=None,
        strike=None,
        option_type="STOCK",
        price=Decimal("217.44"),
        net_price="217.44",
        order_type="LIMIT",
    )

    leg = to_leg(row)

    assert isinstance(leg, InstrumentLeg)
    assert leg.instrument == Instrument("COIN")
    assert leg.side is Side.SELL
    assert leg.position_effect is PositionEffect.CLOSE
    assert leg.quantity == Decimal("-500")
    assert leg.execution_price == Decimal("217.44")
    assert leg.executed_at == executed_at

def test_to_trade_preserves_coins_covered_call_closing_legs() -> None:
    filename = "tests/data/thinkorswim/Account Trading History 2026.csv"

    from campaigniq.sources.thinkorswim.source_reader import (
        ThinkorswimSourceReader,
    )
    from campaigniq.importers.thinkorswim.trade_history_reader import (
        ThinkorswimTradeHistoryReader,
    )

    statement = ThinkorswimSourceReader().read(filename)
    section = statement.section("Account Trade History")
    orders = ThinkorswimTradeHistoryReader().read(section)

    order = orders[41]
    trade = to_trade(order)

    assert len(trade.legs) == 2

    option_leg = trade.legs[0]
    stock_leg = trade.legs[1]

    assert isinstance(option_leg, OptionLeg)
    assert option_leg.contract.underlying == "COIN"
    assert option_leg.contract.strike == Decimal("200")
    assert option_leg.side is Side.BUY
    assert option_leg.position_effect is PositionEffect.CLOSE
    assert option_leg.quantity == Decimal("5")

    assert isinstance(stock_leg, InstrumentLeg)
    assert stock_leg.instrument == Instrument("COIN")
    assert stock_leg.side is Side.SELL
    assert stock_leg.position_effect is PositionEffect.CLOSE
    assert stock_leg.quantity == Decimal("-500")
