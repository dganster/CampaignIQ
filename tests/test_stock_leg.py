from datetime import datetime
from decimal import Decimal

from campaigniq.domain.side import Side
from campaigniq.domain.stock_leg import StockLeg
from campaigniq.domain.value_objects.instrument import Instrument


def test_stock_leg() -> None:
    leg = StockLeg(
        equity=Instrument("IBM"),
        side=Side.BUY,
        quantity=Decimal("100"),
        execution_price=Decimal("250.15"),
        executed_at=datetime(2026, 7, 31, 10, 30),
    )

    assert leg.equity == Instrument("IBM")
    assert leg.quantity == Decimal("100")
