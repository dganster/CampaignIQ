from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.directional_bias import DirectionalBias
from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade


def test_trade_can_contain_generic_option_leg() -> None:
    leg = Leg(
        instrument=OptionContract(
            underlying="IBM",
            expiration=date(2026, 8, 21),
            strike=Decimal("250"),
            option_type=OptionType.CALL,
        ),
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        executions=(
            Execution(
                quantity=Decimal("1"),
                execution_price=Decimal("3.25"),
                executed_at=datetime(2026, 7, 29, 10, 30),
            ),
        ),
    )

    trade = Trade(legs=(leg,))

    assert trade.directional_bias() == DirectionalBias.BULLISH
    