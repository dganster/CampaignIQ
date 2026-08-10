from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.directional_bias import DirectionalBias
from campaigniq.domain.execution import Execution
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade


def make_execution(
    quantity: str,
    price: str,
    executed_at: datetime,
) -> Execution:
    return Execution(
        quantity=Decimal(quantity),
        execution_price=Decimal(price),
        executed_at=executed_at,
    )


def make_leg(
    contract: OptionContract,
    side: Side,
    position_effect: PositionEffect,
    quantity: str,
    price: str,
    executed_at: datetime,
    broker_strategy: str,
) -> OptionLeg:
    return OptionLeg(
        contract=contract,
        side=side,
        position_effect=position_effect,
        executions=(
            make_execution(
                quantity=quantity,
                price=price,
                executed_at=executed_at,
            ),
        ),
        broker_strategy=broker_strategy,
    )


def test_trade_contains_legs() -> None:
    contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    leg = make_leg(
        contract=contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="3.25",
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="SINGLE",
    )

    trade = Trade(legs=(leg,))

    assert trade.legs == (leg,)


def test_trade_directional_bias_for_opening_call() -> None:
    contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    leg = make_leg(
        contract=contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="3.25",
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="SINGLE",
    )

    trade = Trade(legs=(leg,))

    assert trade.directional_bias() == DirectionalBias.BULLISH


def test_bull_call_spread_is_bullish() -> None:
    lower_strike_call = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    higher_strike_call = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("260"),
        option_type=OptionType.CALL,
    )

    long_leg = make_leg(
        contract=lower_strike_call,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="5.00",
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    short_leg = make_leg(
        contract=higher_strike_call,
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="2.00",
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    trade = Trade(legs=(long_leg, short_leg))

    assert trade.directional_bias() == DirectionalBias.BULLISH


def test_bear_call_spread_is_bearish() -> None:
    lower_strike_call = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    higher_strike_call = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("260"),
        option_type=OptionType.CALL,
    )

    short_leg = make_leg(
        contract=lower_strike_call,
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="5.00",
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    long_leg = make_leg(
        contract=higher_strike_call,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="2.00",
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    trade = Trade(legs=(short_leg, long_leg))

    assert trade.directional_bias() == DirectionalBias.BEARISH


def test_bull_put_spread_is_bullish() -> None:
    lower_strike_put = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.PUT,
    )

    higher_strike_put = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("260"),
        option_type=OptionType.PUT,
    )

    short_leg = make_leg(
        contract=lower_strike_put,
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="2.00",
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    long_leg = make_leg(
        contract=higher_strike_put,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="4.00",
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    trade = Trade(legs=(short_leg, long_leg))

    assert trade.directional_bias() == DirectionalBias.BULLISH


def test_bear_put_spread_is_bearish() -> None:
    lower_strike_put = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.PUT,
    )

    higher_strike_put = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("260"),
        option_type=OptionType.PUT,
    )

    long_leg = make_leg(
        contract=lower_strike_put,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="2.00",
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    short_leg = make_leg(
        contract=higher_strike_put,
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="4.00",
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    trade = Trade(legs=(long_leg, short_leg))

    assert trade.directional_bias() == DirectionalBias.BEARISH


def test_long_straddle_is_neutral() -> None:
    call_contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    put_contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.PUT,
    )

    call_leg = make_leg(
        contract=call_contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="5.00",
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="STRADDLE",
    )

    put_leg = make_leg(
        contract=put_contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="5.00",
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="STRADDLE",
    )

    trade = Trade(legs=(call_leg, put_leg))

    assert trade.directional_bias() == DirectionalBias.NEUTRAL
    