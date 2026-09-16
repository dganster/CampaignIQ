from datetime import datetime
from decimal import Decimal

from campaigniq.domain.campaign_realized_pnl import aggregate_campaign_realized_pnl
from campaigniq.domain.forex_settlement_attribution import ForexSettlementAttribution
from campaigniq.importers.schwab.forex_transaction_reader import SchwabForexSettlement


def fx_attr(campaign_id: str, pnl: str) -> ForexSettlementAttribution:
    return ForexSettlementAttribution(
        settlement=SchwabForexSettlement(
            order_id="1",
            trade_at=datetime(2026, 8, 27, 20, 19, 57),
            settlement_at=datetime(2026, 8, 28, 17, 0),
            instrument="EUR/USD",
            side="Sell",
            rate=Decimal("1.16508"),
            amount=Decimal("-100000"),
            settlement_pl_usd=Decimal(pnl),
            total_position=Decimal("0"),
        ),
        campaign_id=campaign_id,
    )


def test_forex_settlement_pnl_enters_campaign_results():
    results = aggregate_campaign_realized_pnl([], [fx_attr("CAMP-000026", "3.00")])
    assert len(results) == 1
    result = results[0]
    assert result.campaign_id == "CAMP-000026"
    assert result.gain_loss == Decimal("3.00")
    assert result.proceeds == Decimal("0")
    assert result.cost_basis == Decimal("0")
    assert result.record_count == 1
    assert result.fully_reconciled


def test_multiple_forex_settlements_sum_by_campaign():
    results = aggregate_campaign_realized_pnl(
        [],
        [fx_attr("CAMP-000026", "3.00"), fx_attr("CAMP-000026", "-1.25")],
    )
    assert results[0].gain_loss == Decimal("1.75")
    assert results[0].record_count == 2


def test_existing_realized_attribution_api_remains_backward_compatible():
    assert aggregate_campaign_realized_pnl([]) == ()
