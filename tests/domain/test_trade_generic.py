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
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.value_objects.instrument import Instrument

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

def test_trade_ignores_opening_instrument_leg_for_directional_bias() -> None:
    stock_leg = InstrumentLeg(
        instrument=Instrument("COIN"),
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        executions=(
            Execution(
                quantity=Decimal("500"),
                execution_price=Decimal("200"),
                executed_at=datetime(2026, 1, 5, 10, 30),
            ),
        ),
    )

    option_leg = Leg(
        instrument=OptionContract(
            underlying="COIN",
            expiration=date(2026, 2, 20),
            strike=Decimal("220"),
            option_type=OptionType.CALL,
        ),
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        executions=(
            Execution(
                quantity=Decimal("5"),
                execution_price=Decimal("6.35"),
                executed_at=datetime(2026, 1, 5, 10, 30),
            ),
        ),
    )

    trade = Trade(legs=(stock_leg, option_leg))

    assert trade.directional_bias() == DirectionalBias.BEARISH