from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.importers.thinkorswim.broker_order import (
    ThinkorswimBrokerOrder,
)
from campaigniq.importers.thinkorswim.trade_row import ThinkorswimTradeRow
from campaigniq.importers.thinkorswim.translator import to_trade


def test_to_trade_preserves_multiple_legs() -> None:
    row1 = ThinkorswimTradeRow(
        exec_time=datetime(2026, 7, 29, 10, 30),
        spread="VERTICAL",
        side="SELL",
        qty=Decimal("1"),
        pos_effect="TO OPEN",
        symbol="IBM",
        exp=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type="CALL",
        price=Decimal("5.00"),
        net_price="5.00",
        order_type="LIMIT",
    )

    row2 = ThinkorswimTradeRow(
        exec_time=None,
        spread="VERTICAL",
        side="BUY",
        qty=Decimal("1"),
        pos_effect="TO OPEN",
        symbol="IBM",
        exp=date(2026, 8, 21),
        strike=Decimal("260"),
        option_type="CALL",
        price=Decimal("2.50"),
        net_price="2.50",
        order_type="LIMIT",
    )

    order = ThinkorswimBrokerOrder(
        exec_time=datetime(2026, 7, 29, 10, 30),
        spread="VERTICAL",
        legs=[row1, row2],
    )

    trade = to_trade(order)

    assert trade == Trade(
        legs=(
            OptionLeg(
                contract=OptionContract(
                    underlying="IBM",
                    expiration=date(2026, 8, 21),
                    strike=Decimal("250"),
                    option_type=OptionType.CALL,
                ),
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    Execution(
                        quantity=Decimal("1"),
                        execution_price=Decimal("5.00"),
                        executed_at=datetime(2026, 7, 29, 10, 30),
                    ),
                ),
                broker_strategy="VERTICAL",
            ),
            OptionLeg(
                contract=OptionContract(
                    underlying="IBM",
                    expiration=date(2026, 8, 21),
                    strike=Decimal("260"),
                    option_type=OptionType.CALL,
                ),
                side=Side.BUY,
                position_effect=PositionEffect.OPEN,
                executions=(
                    Execution(
                        quantity=Decimal("1"),
                        execution_price=Decimal("2.50"),
                        executed_at=datetime(2026, 7, 29, 10, 30),
                    ),
                ),
                broker_strategy="VERTICAL",
            ),
        )
    )
    