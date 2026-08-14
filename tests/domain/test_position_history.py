from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_applier import PositionEventApplier
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.importers.schwab.option_assignment import (
    SchwabOptionAssignment,
)
from campaigniq.importers.schwab.translator import to_position_event


def option_trade(
    *,
    quantity: str,
    executed_at: datetime,
    position_effect: PositionEffect,
) -> Trade:
    contract = OptionContract(
        underlying="ADP",
        expiration=date(2026, 7, 17),
        strike=Decimal("200"),
        option_type=OptionType.PUT,
    )

    leg = Leg(
        instrument=contract,
        side=Side.SELL if Decimal(quantity) < 0 else Side.BUY,
        position_effect=position_effect,
        executions=(
            Execution(
                quantity=Decimal(quantity),
                execution_price=Decimal("2.00"),
                executed_at=executed_at,
            ),
        ),
    )

    return Trade(legs=(leg,))


def test_history_orders_trades_and_events_chronologically() -> None:
    trade = option_trade(
        quantity="-10",
        executed_at=datetime(2026, 6, 1, 10, 0),
        position_effect=PositionEffect.OPEN,
    )

    instrument = trade.legs[0].instrument

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

    close = option_trade(
        quantity="2",
        executed_at=datetime(2026, 6, 20, 10, 0),
        position_effect=PositionEffect.CLOSE,
    )

    history = PositionHistory()

    history.add_event(assignment)
    history.add_trade(close)
    history.add_trade(trade)

    items = history.items()

    assert len(items) == 3
    assert items[0].trade is trade
    assert items[1].event is assignment
    assert items[2].trade is close


def test_history_applies_trades_and_events_in_chronological_order() -> None:
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
    history.add_event(assignment)
    history.add_trade(closing_trade)
    history.add_trade(opening_trade)

    applier = PositionEventApplier()

    history.apply(applier)

    assert applier.quantity(instrument) == Decimal("-4")

def test_schwab_assignment_can_be_applied_through_position_history() -> None:
    opening_trade = option_trade(
        quantity="-10",
        executed_at=datetime(2026, 7, 1, 10, 0),
        position_effect=PositionEffect.OPEN,
    )

    assignment = SchwabOptionAssignment(
        occurred_at=datetime(2026, 7, 10, 0, 0),
        symbol="ADP",
        expiration=date(2026, 7, 17),
        strike=Decimal("200"),
        option_type="PUT",
        quantity=Decimal("4"),
    )

    event = to_position_event(assignment)

    instrument = opening_trade.legs[0].instrument

    assert event.changes[0].instrument == instrument

    history = PositionHistory()
    history.add_event(event)
    history.add_trade(opening_trade)

    applier = PositionEventApplier()

    history.apply(applier)

    assert applier.quantity(instrument) == Decimal("-6")

def test_multiple_schwab_assignments_accumulate() -> None:
    opening_trade = option_trade(
        quantity="-10",
        executed_at=datetime(2026, 7, 1, 10, 0),
        position_effect=PositionEffect.OPEN,
    )

    instrument = opening_trade.legs[0].instrument

    first_assignment = SchwabOptionAssignment(
        occurred_at=datetime(2026, 7, 10, 0, 0),
        symbol="ADP",
        expiration=date(2026, 7, 17),
        strike=Decimal("200"),
        option_type="PUT",
        quantity=Decimal("3"),
    )

    second_assignment = SchwabOptionAssignment(
        occurred_at=datetime(2026, 7, 11, 0, 0),
        symbol="ADP",
        expiration=date(2026, 7, 17),
        strike=Decimal("200"),
        option_type="PUT",
        quantity=Decimal("2"),
    )

    history = PositionHistory()
    history.add_event(to_position_event(second_assignment))
    history.add_trade(opening_trade)
    history.add_event(to_position_event(first_assignment))

    applier = PositionEventApplier()
    history.apply(applier)

    assert applier.quantity(instrument) == Decimal("-5")
