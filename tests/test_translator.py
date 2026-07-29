from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.option_type import OptionType
from campaigniq.importers.thinkorswim.trade_row import ThinkorswimTradeRow
from campaigniq.importers.thinkorswim.translator import to_option_contract


def test_to_option_contract() -> None:
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

    contract = to_option_contract(row)

    assert contract.underlying == "IBM"
    assert contract.expiration == date(2026, 8, 21)
    assert contract.strike == Decimal("250")
    assert contract.option_type is OptionType.CALL

def test_to_option_contract_rejects_stock_trade() -> None:
    row = ThinkorswimTradeRow(
        exec_time=datetime(2026, 7, 29, 10, 30),
        spread="",
        side="BUY",
        qty=Decimal("100"),
        pos_effect="TO OPEN",
        symbol="IBM",
        exp=None,
        strike=None,
        option_type="STOCK",
        price=Decimal("250.00"),
        net_price="250.00",
        order_type="LIMIT",
    )

    with pytest.raises(ValueError, match="not an option trade"):
        to_option_contract(row)
