from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side


def test_leg_fields() -> None:
    contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 2, 20),
        strike=Decimal("220"),
        option_type=OptionType.CALL,
    )

    executed_at = datetime(2026, 1, 5, 10, 15, 30)

    execution = Execution(
        quantity=Decimal("5"),
        execution_price=Decimal("6.35"),
        executed_at=executed_at,
    )

    leg = OptionLeg(
        contract=contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        executions=(execution,),
        broker_strategy="SINGLE",
    )

    assert leg.contract is contract
    assert leg.side is Side.BUY
    assert leg.position_effect is PositionEffect.OPEN
    assert leg.executions == (execution,)
    assert leg.quantity == Decimal("5")
    assert leg.execution_price == Decimal("6.35")
    assert leg.executed_at == executed_at


def test_leg_aggregates_multiple_executions() -> None:
    execution1 = Execution(
        quantity=Decimal("1"),
        execution_price=Decimal("3.25"),
        executed_at=datetime(2026, 7, 29, 10, 31),
    )

    execution2 = Execution(
        quantity=Decimal("2"),
        execution_price=Decimal("3.30"),
        executed_at=datetime(2026, 7, 29, 10, 30),
    )

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
        executions=(execution1, execution2),
        broker_strategy="SINGLE",
    )

    assert leg.quantity == Decimal("3")
    assert leg.execution_price == Decimal("9.85") / Decimal("3")
    assert leg.executed_at == datetime(2026, 7, 29, 10, 30)
    