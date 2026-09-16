from datetime import date
from decimal import Decimal

from campaigniq.analytics.campaign_drilldown_summary import (
    CampaignDrilldownSummary,
    summarize_campaign_drilldowns,
)
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument


def attribution(
    *,
    campaign_id: str | None,
    symbol: str,
    closed_date: date,
    gain_loss: str,
    quantity: str = "1",
    broker_basis: str = "100",
) -> RealizedAttribution:
    gain = Decimal(gain_loss)
    basis = Decimal(broker_basis)
    proceeds = basis + gain

    return RealizedAttribution(
        record=RealizedGainLossRecord(
            closed_date=closed_date,
            instrument=Instrument(symbol),
            quantity=Decimal(quantity),
            closing_price=Decimal("1"),
            proceeds=proceeds,
            cost_basis=basis,
            gain_loss=gain,
            basis_method="FIFO",
            term="SHORT",
        ),
        allocations=(
            LotAllocation(
                lot_id=f"{symbol}-{closed_date.isoformat()}",
                quantity=Decimal(quantity),
                broker_basis=basis,
                basis_source="TEST",
                campaign_id=campaign_id,
            ),
        ),
    )


def test_summarizes_campaign_drilldowns() -> None:
    attributions = [
        attribution(
            campaign_id="CAMP-1",
            symbol="AAPL",
            closed_date=date(2026, 1, 5),
            gain_loss="100",
        ),
        attribution(
            campaign_id="CAMP-1",
            symbol="AAPL",
            closed_date=date(2026, 1, 20),
            gain_loss="-25",
        ),
        attribution(
            campaign_id="CAMP-2",
            symbol="MSFT",
            closed_date=date(2026, 2, 10),
            gain_loss="40",
        ),
    ]

    summaries = summarize_campaign_drilldowns(attributions)

    assert summaries == (
        CampaignDrilldownSummary(
            campaign_id="CAMP-1",
            symbols=("AAPL",),
            realized_pnl=Decimal("75"),
            record_count=2,
            allocation_count=2,
            first_closed_date=date(2026, 1, 5),
            last_closed_date=date(2026, 1, 20),
            fully_reconciled=True,
        ),
        CampaignDrilldownSummary(
            campaign_id="CAMP-2",
            symbols=("MSFT",),
            realized_pnl=Decimal("40"),
            record_count=1,
            allocation_count=1,
            first_closed_date=date(2026, 2, 10),
            last_closed_date=date(2026, 2, 10),
            fully_reconciled=True,
        ),
    )


def test_combines_multiple_symbols_for_one_campaign() -> None:
    summaries = summarize_campaign_drilldowns(
        [
            attribution(
                campaign_id="CAMP-1",
                symbol="AAPL",
                closed_date=date(2026, 1, 5),
                gain_loss="10",
            ),
            attribution(
                campaign_id="CAMP-1",
                symbol="MSFT",
                closed_date=date(2026, 1, 6),
                gain_loss="20",
            ),
        ]
    )

    assert summaries[0].symbols == ("AAPL", "MSFT")


def test_excludes_unassigned_attributions() -> None:
    summaries = summarize_campaign_drilldowns(
        [
            attribution(
                campaign_id=None,
                symbol="AAPL",
                closed_date=date(2026, 1, 5),
                gain_loss="10",
            )
        ]
    )

    assert summaries == ()


def test_accepts_generator_input() -> None:
    items = (
        item
        for item in [
            attribution(
                campaign_id="CAMP-2",
                symbol="MSFT",
                closed_date=date(2026, 2, 1),
                gain_loss="5",
            ),
            attribution(
                campaign_id="CAMP-1",
                symbol="AAPL",
                closed_date=date(2026, 1, 1),
                gain_loss="10",
            ),
        ]
    )

    summaries = summarize_campaign_drilldowns(items)

    assert [summary.campaign_id for summary in summaries] == [
        "CAMP-1",
        "CAMP-2",
    ]
