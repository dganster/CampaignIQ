"""Translate Thinkorswim Forex rows into CampaignIQ trades."""

from decimal import Decimal
from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.forex_pair import ForexPair

from .forex_trade_row import ThinkorswimForexTradeRow

def to_forex_trade(
    row: ThinkorswimForexTradeRow,
    position_effect: PositionEffect,
    quantity: Decimal | None = None,
) -> Trade:
    """Translate one Forex statement row into a CampaignIQ trade."""

    base, quote = row.pair.split("/")

    side = (
        Side.BUY
        if row.action == "BOT"
        else Side.SELL
    )

    execution_quantity = (
        row.quantity
        if quantity is None
        else quantity
    )

    leg = InstrumentLeg(
        instrument=ForexPair(base, quote),
        side=side,
        position_effect=position_effect,
        executions=(
            Execution(
                quantity=execution_quantity,
                execution_price=row.price,
                executed_at=row.executed_at,
            ),
        ),
    )

    return Trade(legs=(leg,))