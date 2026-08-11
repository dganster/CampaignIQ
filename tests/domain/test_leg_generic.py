from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType


def test_leg_contains_instrument_and_executions() -> None:
    instrument = Instrument("IBM")

    execution = Execution(
        quantity=Decimal("100"),
        execution_price=Decimal("250.15"),
        executed_at=datetime(2026, 7, 31, 10, 30),
    )

    leg = Leg(
        instrument=instrument,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        executions=(execution,),
    )

    assert leg.instrument == instrument
    assert leg.side == Side.BUY
    assert leg.position_effect == PositionEffect.OPEN
    assert leg.executions == (execution,)


def test_leg_can_contain_multiple_executions() -> None:
    instrument = Instrument("IBM")

    execution1 = Execution(
        quantity=Decimal("40"),
        execution_price=Decimal("250.10"),
        executed_at=datetime(2026, 7, 31, 10, 30),
    )

    execution2 = Execution(
        quantity=Decimal("60"),
        execution_price=Decimal("250.20"),
        executed_at=datetime(2026, 7, 31, 10, 31),
    )

    leg = Leg(
        instrument=instrument,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        executions=(execution1, execution2),
    )

    assert leg.executions == (execution1, execution2)
    assert sum(
        execution.quantity for execution in leg.executions
    ) == Decimal("100")

def test_leg_can_contain_option_contract() -> None:
    instrument = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    execution = Execution(
        quantity=Decimal("1"),
        execution_price=Decimal("3.25"),
        executed_at=datetime(2026, 7, 29, 10, 30),
    )

    leg = Leg(
        instrument=instrument,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        executions=(execution,),
    )

    assert leg.instrument == instrument

def test_generic_leg_does_not_define_option_directional_bias() -> None:
    instrument = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    execution = Execution(
        quantity=Decimal("1"),
        execution_price=Decimal("3.25"),
        executed_at=datetime(2026, 7, 29, 10, 30),
    )

    leg = Leg(
        instrument=instrument,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        executions=(execution,),
    )

    assert not hasattr(leg, "directional_bias")
    
