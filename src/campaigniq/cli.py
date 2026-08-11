"""Command-line interface for CampaignIQ."""

from __future__ import annotations

import sys

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.importers.thinkorswim.trade_history_reader import (
    ThinkorswimTradeHistoryReader,
)
from campaigniq.importers.thinkorswim.translator import to_trade
from campaigniq.sources.thinkorswim.source_reader import (
    ThinkorswimSourceReader,
)


def _is_option_order(order) -> bool:
    """Return True when every leg in an order is an option."""
    return all(
        row.option_type.upper() in {"CALL", "PUT"}
        for row in order.legs
    )


def _print_trade(trade) -> None:
    """Print the details of one trade."""
    first_leg = trade.legs[0]
    strategy = getattr(first_leg, "broker_strategy", "")

    executed_at = min(
        execution.executed_at
        for leg in trade.legs
        for execution in leg.executions
    )

    print(
        f"{executed_at:%Y-%m-%d %H:%M}  "
        f"{strategy or 'SINGLE'}"
    )

    for leg in trade.legs:
        contract = leg.contract

        for execution in leg.executions:
            print(
                f"{leg.side.value:4} "
                f"{abs(execution.quantity):>5} "
                f"{contract.expiration:%b %d} "
                f"${contract.strike} "
                f"{contract.option_type.value} "
                f"{leg.position_effect.value}"
            )


def main() -> None:
    """Run CampaignIQ against a Thinkorswim statement."""
    if len(sys.argv) != 2:
        print("Usage: campaigniq <thinkorswim-statement.csv>")
        raise SystemExit(1)

    filename = sys.argv[1]

    statement = ThinkorswimSourceReader().read(filename)
    section = statement.section("Account Trade History")

    orders = ThinkorswimTradeHistoryReader().read(section)

    option_orders = [
        order
        for order in orders
        if _is_option_order(order)
    ]

    trades = [to_trade(order) for order in option_orders]
    campaigns = CampaignReconstructor().reconstruct(trades)

    print()
    print("# CampaignIQ")
    print()
    print(f"Source: {filename}")
    print()
    print("## Import")
    print()
    print(f"Brokerage orders:        {len(orders)}")
    print(f"Option orders:           {len(option_orders)}")
    print(f"CampaignIQ trades:       {len(trades)}")
    print(f"Campaigns reconstructed: {len(campaigns)}")
    print()

    print("## Campaigns")
    print()

    for number, campaign in enumerate(campaigns, start=1):
        underlying = campaign.trades[0].legs[0].contract.underlying
        bias = campaign.trades[-1].directional_bias().value

        status = (
            "CONTINUED FROM PRIOR PERIOD"
            if campaign.started_before_data
            else "NEW"
        )

        print(f"### Campaign {number} — {underlying}")
        print()
        print(f"Status: {status}")
        print(f"Bias:   {bias}")
        print(f"Trades: {len(campaign.trades)}")
        print()

        for trade in campaign.trades:
            _print_trade(trade)
            print()
            