from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.directional_bias import DirectionalBias

def test_trade_contains_legs() -> None:
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
        quantity=Decimal("1"),
        execution_price=Decimal("3.25"),
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

    leg = OptionLeg(
        contract=contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("3.25"),
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

    long_leg = OptionLeg(
        contract=lower_strike_call,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("5.00"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    short_leg = OptionLeg(
        contract=higher_strike_call,
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("2.00"),
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

    short_leg = OptionLeg(
        contract=lower_strike_call,
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("5.00"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    long_leg = OptionLeg(
        contract=higher_strike_call,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("2.00"),
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

    short_leg = OptionLeg(
        contract=lower_strike_put,
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("2.00"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    long_leg = OptionLeg(
        contract=higher_strike_put,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("4.00"),
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

    long_leg = OptionLeg(
        contract=lower_strike_put,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("2.00"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    short_leg = OptionLeg(
        contract=higher_strike_put,
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("4.00"),
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

    call_leg = OptionLeg(
        contract=call_contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("5.00"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="STRADDLE",
    )

    put_leg = OptionLeg(
        contract=put_contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("5.00"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="STRADDLE",
    )

    trade = Trade(legs=(call_leg, put_leg))

    assert trade.directional_bias() == DirectionalBias.NEUTRAL