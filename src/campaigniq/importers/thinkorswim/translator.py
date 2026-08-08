"""Translate Thinkorswim objects into CampaignIQ domain objects."""

from campaigniq.domain.option_contract import OptionContract
from campaigniq.importers.thinkorswim.parsers import parse_option_type
from campaigniq.importers.thinkorswim.trade_row import ThinkorswimTradeRow
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.importers.thinkorswim.parsers import parse_side
from campaigniq.domain.trade import Trade
from campaigniq.importers.thinkorswim.broker_order import ThinkorswimBrokerOrder
from datetime import datetime

def to_option_contract(row: ThinkorswimTradeRow) -> OptionContract:
    """Translate a Thinkorswim trade row into a domain OptionContract."""

    if row.option_type.upper() not in {"CALL", "PUT"}:
        raise ValueError(
            f"Trade row is not an option trade: {row.option_type!r}"
        )

    if row.exp is None:
        raise ValueError("Option trade is missing an expiration date.")

    if row.strike is None:
        raise ValueError("Option trade is missing a strike price.")

    return OptionContract(
        underlying=row.symbol,
        expiration=row.exp,
        strike=row.strike,
        option_type=parse_option_type(row.option_type),
    )

def to_leg(
    row: ThinkorswimTradeRow,
    execution_time: datetime | None = None,
) -> OptionLeg:
    """Translate a Thinkorswim trade row into a domain Leg."""

    executed_at = row.exec_time or execution_time

    if executed_at is None:
        raise ValueError("Trade row is missing an execution time.")

    return OptionLeg(
        contract=to_option_contract(row),
        side=parse_side(row.side),
        quantity=row.qty,
        execution_price=row.price,
        executed_at=executed_at,
        broker_strategy=row.spread,
    )

def to_trade(order: ThinkorswimBrokerOrder) -> Trade:
    """Translate a Thinkorswim brokerage order into one domain Trade."""

    return Trade(
        legs=tuple(
            to_leg(row, execution_time=order.exec_time)
            for row in order.legs
        )
    )