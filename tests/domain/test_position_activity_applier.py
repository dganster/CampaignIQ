from datetime import datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.position_activity import PositionActivity
from campaigniq.domain.position_activity_applier import PositionActivityApplier
from campaigniq.domain.position_activity_kind import PositionActivityKind
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


def stock_trade(
    *,
    quantity: str,
    side: Side,
    position_effect: PositionEffect,
) -> Trade:
    return Trade(
        legs=(
            InstrumentLeg(
                instrument=Instrument("SPY"),
                side=side,
                position_effect=position_effect,
                executions=(
                    Execution(
                        quantity=Decimal(quantity),
                        execution_price=Decimal("700"),
                        executed_at=datetime(2026, 3, 11, 9, 30),
                    ),
                ),
            ),
        ),
    )


def test_open_then_partial_close_leaves_remaining_position() -> None:
    opening_trade = stock_trade(
        quantity="10",
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
    )

    closing_trade = stock_trade(
        quantity="-4",
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
    )

    opening_activity = PositionActivity(
        trades=(opening_trade,),
        kind=PositionActivityKind.OPEN,
    )

    closing_activity = PositionActivity(
        trades=(closing_trade,),
        kind=PositionActivityKind.CLOSE,
    )

    applier = PositionActivityApplier()

    applier.apply(opening_activity)
    applier.apply(closing_activity)

    state = applier.state(Instrument("SPY"))

    assert state.quantity == Decimal("6")
    assert state.quantity_known is True
    assert state.started_before_data is False


def test_open_then_full_close_returns_to_zero() -> None:
    opening_trade = stock_trade(
        quantity="5",
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
    )

    closing_trade = stock_trade(
        quantity="-5",
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
    )

    opening_activity = PositionActivity(
        trades=(opening_trade,),
        kind=PositionActivityKind.OPEN,
    )

    closing_activity = PositionActivity(
        trades=(closing_trade,),
        kind=PositionActivityKind.CLOSE,
    )

    applier = PositionActivityApplier()

    applier.apply(opening_activity)
    applier.apply(closing_activity)

    state = applier.state(Instrument("SPY"))

    assert state.quantity == Decimal("0")
    assert state.quantity_known is True
    assert state.started_before_data is False
