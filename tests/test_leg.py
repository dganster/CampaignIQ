from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.leg import Leg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.side import Side


def test_leg_fields() -> None:
    contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 2, 20),
        strike=Decimal("220"),
        option_type=OptionType.CALL,
    )

    executed_at = datetime(2026, 1, 5, 10, 15, 30)

    leg = Leg(
        contract=contract,
        side=Side.BUY,
        quantity=Decimal("5"),
        execution_price=Decimal("6.35"),
        executed_at=executed_at,
        broker_strategy="SINGLE",
    )

    assert leg.contract is contract
    assert leg.side is Side.BUY
    assert leg.quantity == Decimal("5")
    assert leg.execution_price == Decimal("6.35")
    assert leg.executed_at == executed_at
