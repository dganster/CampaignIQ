from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_applier import PositionEventApplier
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


def short_put_trade(quantity: str) -> Trade:
    contract = OptionContract(
        underlying="ADP",
        expiration=date(2026, 7, 17),
        strike=Decimal("200"),
        option_type=OptionType.PUT,
    )

    return Trade(
        legs=(
            Leg(
                instrument=contract,
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    Execution(
                        quantity=Decimal(quantity),
                        execution_price=Decimal("5"),
                        executed_at=datetime(2026, 6, 20, 10, 30),
                    ),
                ),
            ),
        ),
    )


def test_partial_assignment_reduces_short_put_position() -> None:
    trade = short_put_trade("-10")
    option = trade.legs[0].instrument

    assignment = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=option,
                quantity=Decimal("4"),
            ),
        ),
        occurred_at=datetime(2026, 7, 1, 0, 0),
    )

    applier = PositionEventApplier()

    applier.apply_trade(trade)
    applier.apply_event(assignment)

    state = applier.state(option)

    assert state.quantity == Decimal("-6")
    assert state.quantity_known is True
    assert state.started_before_data is False


def test_partial_put_assignment_creates_stock_position() -> None:
    trade = short_put_trade("-5")
    option = trade.legs[0].instrument
    stock = Instrument("ADP")

    assignment = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=option,
                quantity=Decimal("2"),
            ),
            PositionChange(
                instrument=stock,
                quantity=Decimal("200"),
            ),
        ),
        occurred_at=datetime(2026, 7, 1, 0, 0),
    )

    applier = PositionEventApplier()

    applier.apply_trade(trade)
    applier.apply_event(assignment)

    option_state = applier.state(option)
    stock_state = applier.state(stock)

    assert option_state.quantity == Decimal("-3")
    assert option_state.quantity_known is True
    assert option_state.started_before_data is False

    assert stock_state.quantity == Decimal("200")
    assert stock_state.quantity_known is True
    assert stock_state.started_before_data is False
