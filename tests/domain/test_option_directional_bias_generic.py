from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.directional_bias import DirectionalBias
from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_directional_bias import (
    option_leg_directional_bias,
)
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side


@pytest.mark.parametrize(
    ("side", "option_type", "expected"),
    [
        (Side.BUY, OptionType.CALL, DirectionalBias.BULLISH),
        (Side.SELL, OptionType.PUT, DirectionalBias.BULLISH),
        (Side.BUY, OptionType.PUT, DirectionalBias.BEARISH),
        (Side.SELL, OptionType.CALL, DirectionalBias.BEARISH),
    ],
)
def test_generic_leg_option_directional_bias(
    side: Side,
    option_type: OptionType,
    expected: DirectionalBias,
) -> None:
    leg = Leg(
        instrument=OptionContract(
            underlying="IBM",
            expiration=date(2026, 8, 21),
            strike=Decimal("250"),
            option_type=option_type,
        ),
        side=side,
        position_effect=PositionEffect.OPEN,
        executions=(
            Execution(
                quantity=Decimal("1"),
                execution_price=Decimal("3.25"),
                executed_at=datetime(2026, 7, 29, 10, 30),
            ),
        ),
    )

    assert option_leg_directional_bias(leg) == expected


def test_closing_option_leg_has_no_independent_directional_bias() -> None:
    leg = Leg(
        instrument=OptionContract(
            underlying="IBM",
            expiration=date(2026, 8, 21),
            strike=Decimal("250"),
            option_type=OptionType.CALL,
        ),
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        executions=(
            Execution(
                quantity=Decimal("1"),
                execution_price=Decimal("4.50"),
                executed_at=datetime(2026, 8, 3, 11, 15),
            ),
        ),
    )

    with pytest.raises(ValueError):
        option_leg_directional_bias(leg)
        