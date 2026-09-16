from datetime import date
from decimal import Decimal

from campaigniq.analytics.multi_month_analytics_summary import (
    summarize_multi_month_analytics,
)
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument


def attribution(
    *,
    closed_date: date,
    gain_loss: str,
    campaign_id: str | None,
) -> RealizedAttribution:
    gain_loss_decimal = Decimal(gain_loss)

    record = RealizedGainLossRecord(
        closed_date=closed_date,
        instrument=Instrument("IBM"),
        quantity=Decimal("100"),
        closing_price=Decimal("200"),
        proceeds=Decimal("20000"),
        cost_basis=Decimal("20000") - gain_loss_decimal,
        gain_loss=gain_loss_decimal,
        basis_method="FIFO",
        term="ST",
    )

    return RealizedAttribution(
        record=record,
        allocations=(
            LotAllocation(
                lot_id=f"LOT-{closed_date.isoformat()}-{campaign_id}",
                quantity=record.quantity,
                broker_basis=record.cost_basis,
                basis_source="SCHWAB_REALIZED_GAIN_LOSS",
                campaign_id=campaign_id,
            ),
        ),
    )


def test_summarizes_multiple_months_in_chronological_order() -> None:
    summaries = summarize_multi_month_analytics(
        monthly_attributions={
            (date(2026, 2, 1), date(2026, 2, 28)): (
                attribution(
                    closed_date=date(2026, 2, 10),
                    gain_loss="-50.00",
                    campaign_id="FEB-LOSS",
                ),
            ),
            (date(2026, 1, 1), date(2026, 1, 31)): (
                attribution(
                    closed_date=date(2026, 1, 10),
                    gain_loss="100.00",
                    campaign_id="JAN-WIN",
                ),
                attribution(
                    closed_date=date(2026, 1, 20),
                    gain_loss="25.00",
                    campaign_id="JAN-WIN-2",
                ),
            ),
        }
    )

    assert len(summaries) == 2

    january, february = summaries

    assert january.period_start == date(2026, 1, 1)
    assert january.period_end == date(2026, 1, 31)
    assert january.realized_pnl.broker_realized_pnl == Decimal("125.00")
    assert january.realized_pnl.attributed_realized_pnl == Decimal("125.00")
    assert january.realized_pnl.attribution_complete is True
    assert january.campaign_performance.campaign_count == 2
    assert january.campaign_performance.winning_campaign_count == 2
    assert january.campaign_performance.losing_campaign_count == 0

    assert february.period_start == date(2026, 2, 1)
    assert february.period_end == date(2026, 2, 28)
    assert february.realized_pnl.broker_realized_pnl == Decimal("-50.00")
    assert february.realized_pnl.attributed_realized_pnl == Decimal("-50.00")
    assert february.realized_pnl.attribution_complete is True
    assert february.campaign_performance.campaign_count == 1
    assert february.campaign_performance.winning_campaign_count == 0
    assert february.campaign_performance.losing_campaign_count == 1


def test_empty_multi_month_input_returns_empty_tuple() -> None:
    summaries = summarize_multi_month_analytics(
        monthly_attributions={}
    )

    assert summaries == ()


def test_accepts_generator_attribution_inputs() -> None:
    january_attributions = (
        item
        for item in (
            attribution(
                closed_date=date(2026, 1, 10),
                gain_loss="100.00",
                campaign_id="JAN-WIN",
            ),
            attribution(
                closed_date=date(2026, 1, 20),
                gain_loss="-25.00",
                campaign_id="JAN-LOSS",
            ),
        )
    )

    summaries = summarize_multi_month_analytics(
        monthly_attributions={
            (date(2026, 1, 1), date(2026, 1, 31)): january_attributions,
        }
    )

    assert len(summaries) == 1

    january = summaries[0]

    assert january.realized_pnl.broker_realized_pnl == Decimal("75.00")
    assert january.realized_pnl.attributed_realized_pnl == Decimal("75.00")
    assert january.realized_pnl.attribution_complete is True

    assert january.campaign_performance.campaign_count == 2
    assert january.campaign_performance.winning_campaign_count == 1
    assert january.campaign_performance.losing_campaign_count == 1
    assert january.campaign_performance.win_rate == Decimal("0.5")
