from datetime import datetime
from decimal import Decimal

from campaigniq.domain.side import Side
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.value_objects.instrument import Instrument


def test_instrument_leg() -> None:
    leg = InstrumentLeg(
        equity=Instrument("IBM"),
        side=Side.BUY,
        quantity=Decimal("100"),
        execution_price=Decimal("250.15"),
        executed_at=datetime(2026, 7, 31, 10, 30),
    )

    assert leg.equity == Instrument("IBM")
    assert leg.quantity == Decimal("100")

