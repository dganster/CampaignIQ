from datetime import datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_applier import PositionEventApplier
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade


def option_trade(
    *,
    quantity: str,
    executed_at: datetime,
    position_effect: PositionEffect,
) -> Trade:
    contract = OptionContract(
        underlying="APD",
        expiration=datetime(2026, 7, 17).date(),
        strike=Decimal("270"),
        option_type=OptionType.CALL,
    )

    leg = OptionLeg(
        contract=contract,
        side=(
            Side.SELL
            if position_effect == PositionEffect.OPEN
            else Side.BUY
        ),
        position_effect=position_effect,
        executions=(
            Execution(
                quantity=Decimal(quantity),
                execution_price=Decimal("2.00"),
                executed_at=executed_at,
            ),
        ),
        broker_strategy="SINGLE",    
    )

    return Trade(legs=(leg,))


def test_position_history_applies_trades_and_events_chronologically() -> None:
    opening_trade = option_trade(
        quantity="-10",
        executed_at=datetime(2026, 6, 1, 10, 0),
        position_effect=PositionEffect.OPEN,
    )

    instrument = opening_trade.legs[0].instrument

    assignment = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=instrument,
                quantity=Decimal("4"),
            ),
        ),
        occurred_at=datetime(2026, 6, 15, 9, 0),
    )

    closing_trade = option_trade(
        quantity="2",
        executed_at=datetime(2026, 6, 20, 10, 0),
        position_effect=PositionEffect.CLOSE,
    )

    history = PositionHistory()

    # Deliberately add them out of chronological order.
    history.add_event(assignment)
    history.add_trade(closing_trade)
    history.add_trade(opening_trade)

    applier = PositionEventApplier()
    history.apply(applier)

    state = applier.state(instrument)

    assert state.quantity == Decimal("-4")
    assert state.quantity_known is True
    assert state.started_before_data is False
