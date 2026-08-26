"""Build historical Forex opening positions from Thinkorswim executions."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .forex_trade_row import ThinkorswimForexTradeRow


def build_forex_positions(
    rows: list[ThinkorswimForexTradeRow],
) -> dict[str, Decimal]:
    """Return the net Forex position for each pair."""

    positions: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    for row in sorted(rows, key=lambda item: item.executed_at):
        positions[row.pair] += row.quantity

    return {
        pair: quantity
        for pair, quantity in positions.items()
        if quantity != 0
    }