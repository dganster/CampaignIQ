"""Command-line interface for CampaignIQ."""

from __future__ import annotations

import argparse
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.campaign_realized_pnl import aggregate_campaign_realized_pnl
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
from campaigniq.import_pipeline import PeriodImportPipeline
from campaigniq.importers.thinkorswim.trade_history_reader import (
    ThinkorswimTradeHistoryReader,
)
from campaigniq.importers.thinkorswim.translator import to_trade
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


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

    print(f"{executed_at:%Y-%m-%d %H:%M}  {strategy or 'SINGLE'}")

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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="campaigniq")
    subparsers = parser.add_subparsers(dest="command", required=True)

    campaigns = subparsers.add_parser(
        "campaigns",
        help="reconstruct option campaigns from a Thinkorswim statement",
    )
    campaigns.add_argument("statement", type=Path)

    period = subparsers.add_parser(
        "import-period",
        help="run one period through the CampaignIQ economic pipeline",
    )
    period.add_argument("--start", required=True, type=date.fromisoformat)
    period.add_argument("--end", required=True, type=date.fromisoformat)
    period.add_argument("--trades", required=True, type=Path)
    period.add_argument("--opening-snapshot", required=True, type=Path)
    period.add_argument("--snapshot-at", required=True, type=datetime.fromisoformat)
    period.add_argument("--realized", type=Path)
    period.add_argument("--assignments", type=Path)
    period.add_argument(
        "--history",
        action="append",
        default=[],
        type=Path,
        help="historical Thinkorswim trade-history file; may be repeated",
    )
    period.add_argument("--history-start", type=date.fromisoformat)

    return parser


def _run_campaigns(statement_file: Path) -> None:
    statement = ThinkorswimSourceReader().read(str(statement_file))
    section = statement.section("Account Trade History")
    orders = ThinkorswimTradeHistoryReader().read(section)
    option_orders = [order for order in orders if _is_option_order(order)]
    trades = [to_trade(order) for order in option_orders]
    campaigns = CampaignReconstructor().reconstruct(trades)

    print("\n# CampaignIQ\n")
    print(f"Source: {statement_file}\n")
    print("## Import\n")
    print(f"Brokerage orders:        {len(orders)}")
    print(f"Option orders:           {len(option_orders)}")
    print(f"CampaignIQ trades:       {len(trades)}")
    print(f"Campaigns reconstructed: {len(campaigns)}\n")

    print("## Campaigns\n")
    for number, campaign in enumerate(campaigns, start=1):
        underlying = campaign.trades[0].legs[0].contract.underlying
        bias = campaign.trades[-1].directional_bias().value
        status = "CONTINUED FROM PRIOR PERIOD" if campaign.started_before_data else "NEW"
        print(f"### Campaign {number} — {underlying}\n")
        print(f"Status: {status}")
        print(f"Bias:   {bias}")
        print(f"Trades: {len(campaign.trades)}\n")
        for trade in campaign.trades:
            _print_trade(trade)
            print()


def _run_period(args: argparse.Namespace) -> None:
    assignment_lines = (
        args.assignments.read_text().splitlines()
        if args.assignments is not None
        else None
    )

    result = PeriodImportPipeline().run(
        period_start=args.start,
        period_end=args.end,
        thinkorswim_trade_history=args.trades,
        opening_snapshot=args.opening_snapshot,
        opening_snapshot_at=args.snapshot_at,
        assignment_lines=assignment_lines,
        realized_gain_loss_report=args.realized,
        historical_trade_histories=tuple(args.history),
        historical_period_start=args.history_start,
    )

    attributions = ()
    campaign_results = ()
    if result.realized_gain_loss:
        attributions = RealizedLotAttributor(result.opening_lot_book).attribute_campaigns(
            list(result.campaigns),
            list(result.realized_gain_loss),
            list(result.position_events),
        )
        campaign_results = aggregate_campaign_realized_pnl(list(attributions))

    schwab_total = sum(
        (record.gain_loss for record in result.realized_gain_loss),
        Decimal("0"),
    )
    attributed_total = sum(
        (item.gain_loss for item in campaign_results),
        Decimal("0"),
    )
    unassigned_total = sum(
        (
            item.record.gain_loss
            for item in attributions
            if item.has_unassigned_campaign_allocation
        ),
        Decimal("0"),
    )

    print("# CampaignIQ Period Import")
    print()
    print(f"Period:                  {args.start} to {args.end}")
    print(f"Trades imported:         {len(result.trades)}")
    print(f"Campaigns reconstructed: {len(result.campaigns)}")
    print(f"Position events:         {len(result.position_events)}")
    print(f"Realized records:        {len(result.realized_gain_loss)}")
    print()
    print("## Historical Boundary")
    print()
    print(f"Unresolved positions:    {len(result.boundary_reconstruction.unresolved_positions)}")
    print(f"Unresolved campaigns:    {len(result.boundary_reconstruction.unresolved_campaigns)}")
    print(f"Historical requirements: {len(result.boundary_reconstruction.historical_requirements)}")
    print()

    if result.realized_gain_loss:
        print("## Realized P&L")
        print()
        print(f"Schwab realized P&L:     ${schwab_total:,.2f}")
        print(f"Campaign-attributed P&L: ${attributed_total:,.2f}")
        print(f"Unassigned P&L:          ${unassigned_total:,.2f}")
        print(f"Reconciliation:          ${attributed_total + unassigned_total:,.2f}")
        print()
        print("## Campaign Results")
        print()
        for item in sorted(campaign_results, key=lambda value: value.campaign_id):
            status = "RECONCILED" if item.fully_reconciled else "CHECK"
            print(f"{item.campaign_id:12} ${item.gain_loss:>12,.2f}  {status}")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "campaigns":
        _run_campaigns(args.statement)
    elif args.command == "import-period":
        _run_period(args)
