from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_activity import PositionActivity
from campaigniq.domain.position_activity_kind import PositionActivityKind
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


def stock_trade(
    *,
    side: Side,
    position_effect: PositionEffect,
    quantity: str,
    executed_at: datetime,
) -> Trade:
    execution = Execution(
        quantity=Decimal(quantity),
        execution_price=Decimal("200"),
        executed_at=executed_at,
    )

    leg = InstrumentLeg(
        instrument=Instrument("SPX"),
        side=side,
        position_effect=position_effect,
        executions=(execution,),
    )

    return Trade(legs=(leg,))


def test_multiple_trades_can_form_one_open_activity() -> None:
    trade1 = stock_trade(
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="3",
        executed_at=datetime(2026, 3, 11, 7, 34, 47),
    )

    trade2 = stock_trade(
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="2",
        executed_at=datetime(2026, 3, 11, 7, 34, 47),
    )

    trades = (trade1, trade2)

    assert len(trades) == 2
    assert sum(
        execution.quantity
        for trade in trades
        for leg in trade.legs
        for execution in leg.executions
    ) == Decimal("5")


def test_multiple_trades_can_form_one_close_activity() -> None:
    trade1 = stock_trade(
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        quantity="-2",
        executed_at=datetime(2026, 3, 24, 9, 30, 0),
    )

    trade2 = stock_trade(
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        quantity="-8",
        executed_at=datetime(2026, 3, 24, 9, 30, 1),
    )

    trades = (trade1, trade2)

    assert len(trades) == 2
    assert sum(
        execution.quantity
        for trade in trades
        for leg in trade.legs
        for execution in leg.executions
    ) == Decimal("-10")


def test_adjustment_can_contain_closing_and_opening_trades() -> None:
    close_trade = stock_trade(
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        quantity="-7",
        executed_at=datetime(2026, 3, 20, 8, 29, 5),
    )

    open_trade = stock_trade(
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="7",
        executed_at=datetime(2026, 3, 20, 8, 29, 5),
    )

    trades = (close_trade, open_trade)

    assert any(
        leg.position_effect == PositionEffect.CLOSE
        for trade in trades
        for leg in trade.legs
    )

    assert any(
        leg.position_effect == PositionEffect.OPEN
        for trade in trades
        for leg in trade.legs
    )


def test_position_activity_preserves_the_underlying_trades() -> None:
    trade1 = stock_trade(
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="3",
        executed_at=datetime(2026, 3, 11, 7, 34, 47),
    )

    trade2 = stock_trade(
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="2",
        executed_at=datetime(2026, 3, 11, 7, 34, 47),
    )

    trades = (trade1, trade2)

    assert trades[0] is trade1
    assert trades[1] is trade2


def test_position_activity_preserves_trades() -> None:
    trade1 = stock_trade(
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="3",
        executed_at=datetime(2026, 3, 11, 7, 34, 47),
    )

    trade2 = stock_trade(
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="2",
        executed_at=datetime(2026, 3, 11, 7, 34, 47),
    )

    activity = PositionActivity(
        trades=(trade1, trade2),
        kind=PositionActivityKind.OPEN,
    )

    assert activity.trades == (trade1, trade2)


def test_position_activity_records_kind() -> None:
    trade = stock_trade(
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        quantity="-5",
        executed_at=datetime(2026, 3, 24, 9, 30, 0),
    )

    activity = PositionActivity(
        trades=(trade,),
        kind=PositionActivityKind.CLOSE,
    )

    assert activity.kind == PositionActivityKind.CLOSE


def test_position_activity_allows_different_option_contracts_same_underlying() -> None:
    call_5650 = OptionContract(
        underlying="SPX",
        expiration=date(2026, 3, 20),
        strike=Decimal("5650"),
        option_type=OptionType.CALL,
    )

    call_5670 = OptionContract(
        underlying="SPX",
        expiration=date(2026, 3, 20),
        strike=Decimal("5670"),
        option_type=OptionType.CALL,
    )

    execution_5650 = Execution(
        quantity=Decimal("-5"),
        execution_price=Decimal("13.48"),
        executed_at=datetime(2026, 3, 11, 7, 34, 47),
    )

    execution_5670 = Execution(
        quantity=Decimal("5"),
        execution_price=Decimal("8.75"),
        executed_at=datetime(2026, 3, 11, 7, 34, 47),
    )

    leg_5650 = OptionLeg(
        contract=call_5650,
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        executions=(execution_5650,),
        broker_strategy="IRON CONDOR",
    )

    leg_5670 = OptionLeg(
        contract=call_5670,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        executions=(execution_5670,),
        broker_strategy="IRON CONDOR",
    )

    trade_5650 = Trade(legs=(leg_5650,))
    trade_5670 = Trade(legs=(leg_5670,))

    activity = PositionActivity(
        trades=(trade_5650, trade_5670),
        kind=PositionActivityKind.OPEN,
    )

    assert activity.trades == (trade_5650, trade_5670)


def test_position_activity_rejects_different_underlyings() -> None:
    spx_trade = stock_trade(
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="3",
        executed_at=datetime(2026, 3, 11, 7, 34, 47),
    )

    spy_execution = Execution(
        quantity=Decimal("2"),
        execution_price=Decimal("700"),
        executed_at=datetime(2026, 3, 11, 7, 34, 47),
    )

    spy_leg = InstrumentLeg(
        instrument=Instrument("SPY"),
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        executions=(spy_execution,),
    )

    spy_trade = Trade(legs=(spy_leg,))

    with pytest.raises(ValueError, match="same underlying"):
        PositionActivity(
            trades=(spx_trade, spy_trade),
            kind=PositionActivityKind.OPEN,
        )
