from datetime import datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event_applier import PositionEventApplier
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.forex_pair import ForexPair


def forex_trade(
    *,
    quantity: str,
    executed_at: datetime,
) -> Trade:
    pair = ForexPair("EUR", "USD")

    leg = Leg(
        instrument=pair,
        side=(
            Side.BUY
            if Decimal(quantity) > 0
            else Side.SELL
        ),
        position_effect=(
            PositionEffect.OPEN
            if Decimal(quantity) > 0
            else PositionEffect.CLOSE
        ),
        executions=(
            Execution(
                quantity=Decimal(quantity),
                execution_price=Decimal("1.1700"),
                executed_at=executed_at,
            ),
        ),
    )

    return Trade(legs=(leg,))


def test_position_history_supports_forex_pair() -> None:
    opening_trade = forex_trade(
        quantity="100000",
        executed_at=datetime(2026, 8, 3, 10, 0),
    )

    partial_close = forex_trade(
        quantity="-40000",
        executed_at=datetime(2026, 8, 5, 10, 0),
    )

    final_close = forex_trade(
        quantity="-60000",
        executed_at=datetime(2026, 8, 7, 10, 0),
    )

    instrument = opening_trade.legs[0].instrument

    history = PositionHistory()
    history.add_trade(final_close)
    history.add_trade(partial_close)
    history.add_trade(opening_trade)

    applier = PositionEventApplier()
    history.apply(applier)

    state = applier.state(instrument)

    assert state.quantity == Decimal("0")
    assert state.quantity_known is True
    assert state.started_before_data is False
