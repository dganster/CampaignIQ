from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.leg import Leg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.trade_builder import TradeBuilder


def test_single_leg_creates_single_trade() -> None:
    contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    leg = Leg(
        contract=contract,
        side=Side.BUY,
        quantity=Decimal("1"),
        execution_price=Decimal("3.25"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_spread="SINGLE",
    )

    builder = TradeBuilder()

    trades = builder.build([leg])

    assert trades == [
        Trade(legs=(leg,))
    ]
