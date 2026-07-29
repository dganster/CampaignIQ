"""Build brokerage orders from Thinkorswim trade rows."""

from __future__ import annotations

from campaigniq.importers.thinkorswim.broker_order import (
    ThinkorswimBrokerOrder,
)
from campaigniq.importers.thinkorswim.trade_row import (
    ThinkorswimTradeRow,
)


class ThinkorswimOrderBuilder:
    """Convert trade rows into brokerage orders."""

    def build(
        self,
        rows: list[ThinkorswimTradeRow],
    ) -> list[ThinkorswimBrokerOrder]:

        orders: list[ThinkorswimBrokerOrder] = []

        current: ThinkorswimBrokerOrder | None = None

        for row in rows:

            if row.exec_time is not None:
                current = ThinkorswimBrokerOrder(
                    exec_time=row.exec_time,
                    spread=row.spread,
                    legs=[row],
                )
                orders.append(current)

            else:
                if current is None:
                    raise ValueError(
                        "Trade row without preceding order header."
                    )

                current.legs.append(row)

        return orders
