"""Translate Thinkorswim objects into CampaignIQ domain objects."""

from datetime import datetime

from campaigniq.domain.execution import Execution
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.trade import Trade
from campaigniq.importers.thinkorswim.broker_order import (
    ThinkorswimBrokerOrder,
)
from campaigniq.importers.thinkorswim.parsers import (
    parse_option_type,
    parse_side,
)
from campaigniq.importers.thinkorswim.trade_row import ThinkorswimTradeRow


def parse_position_effect(value: str) -> PositionEffect:
    """Translate a Thinkorswim position effect into the domain enum."""

    normalized = value.strip().upper()

    if normalized == "TO OPEN":
        return PositionEffect.OPEN

    if normalized == "TO CLOSE":
        return PositionEffect.CLOSE

    raise ValueError(
        f"Unsupported position effect: {value!r}"
    )


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
    """Translate a Thinkorswim trade row into a domain OptionLeg."""

    executed_at = row.exec_time or execution_time

    if executed_at is None:
        raise ValueError("Trade row is missing an execution time.")

    execution = Execution(
        quantity=row.qty,
        execution_price=row.price,
        executed_at=executed_at,
    )

    return OptionLeg(
        contract=to_option_contract(row),
        side=parse_side(row.side),
        position_effect=parse_position_effect(row.pos_effect),
        executions=(execution,),
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
