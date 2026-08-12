from datetime import datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.value_objects.instrument import Instrument


def test_instrument_leg_fields() -> None:
    instrument = Instrument("IBM")
    executed_at = datetime(2026, 7, 31, 10, 30)

    execution = Execution(
        quantity=Decimal("100"),
        execution_price=Decimal("250.15"),
        executed_at=executed_at,
    )

    leg = InstrumentLeg(
        instrument=instrument,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        executions=(execution,),
    )

    assert leg.instrument is instrument
    assert leg.side is Side.BUY
    assert leg.position_effect is PositionEffect.OPEN
    assert leg.executions == (execution,)
    assert leg.quantity == Decimal("100")
    assert leg.execution_price == Decimal("250.15")
    assert leg.executed_at == executed_at


def test_instrument_leg_aggregates_multiple_executions() -> None:
    execution1 = Execution(
        quantity=Decimal("40"),
        execution_price=Decimal("250.10"),
        executed_at=datetime(2026, 7, 31, 10, 31),
    )

    execution2 = Execution(
        quantity=Decimal("60"),
        execution_price=Decimal("250.20"),
        executed_at=datetime(2026, 7, 31, 10, 30),
    )

    leg = InstrumentLeg(
        instrument=Instrument("IBM"),
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        executions=(execution1, execution2),
    )

    assert leg.quantity == Decimal("100")
    assert leg.execution_price == Decimal("250.16")
    assert leg.executed_at == datetime(2026, 7, 31, 10, 30)
