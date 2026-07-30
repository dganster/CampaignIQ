
from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.option_type import OptionType
from campaigniq.domain.side import Side

from campaigniq.importers.thinkorswim.trade_row import ThinkorswimTradeRow
from campaigniq.importers.thinkorswim.translator import (
    to_leg,
    to_option_contract,
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
    assert leg.broker_spread == ""

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
