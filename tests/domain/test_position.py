import pytest

from campaigniq.domain.account import Account
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.position import Position
from campaigniq.domain.trade import Trade


def test_position_requires_at_least_one_trade():
    with pytest.raises(ValueError):
        Position(
            account=Account("Dennis Brokerage"),
            underlying=Instrument("IBM"),
            trades=(),
        )


from datetime import date, datetime
from decimal import Decimal
from campaigniq.domain.account import Account
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.position import Position
from campaigniq.domain.value_objects.instrument import Instrument


def test_position_accepts_trade() -> None:
    contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    leg = OptionLeg(
        contract=contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("3.25"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="SINGLE",
    )

    trade = Trade(legs=(leg,))

    position = Position(
        account=Account("Dennis Brokerage"),
        underlying=Instrument("IBM"),
        trades=(trade,),
    )

def test_position_trade_count() -> None:
    contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    leg = OptionLeg(
        contract=contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("3.25"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="SINGLE",
    )

    trade = Trade(legs=(leg,))

    position = Position(
        account=Account("Dennis Brokerage"),
        underlying=Instrument("IBM"),
        trades=(trade,),
    )

    assert position.trade_count() == 1

    assert position.trades == (trade,)
