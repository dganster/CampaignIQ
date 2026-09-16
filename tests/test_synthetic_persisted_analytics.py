from datetime import date
from decimal import Decimal

from campaigniq.analytics.multi_month_analytics_summary import summarize_multi_month_analytics
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.lot_attribution import LotAllocation, RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.persistence.persisted_multi_month_analytics import load_persisted_monthly_attributions
from campaigniq.persistence.realized_attribution_store import save_realized_attributions


def _attribution(*, symbol: str, closed_date: date, gain_loss: str, campaign_id: str) -> RealizedAttribution:
    pnl = Decimal(gain_loss)
    cost_basis = Decimal("100")
    record = RealizedGainLossRecord(
        instrument=Instrument(symbol),
        quantity=Decimal("1"),
        proceeds=cost_basis + pnl,
        cost_basis=cost_basis,
        gain_loss=pnl,
        term="SHORT",
        closed_date=closed_date,
        closing_price=cost_basis + pnl,
        basis_method="SYNTHETIC_TEST",
    )
    return RealizedAttribution(
        record=record,
        allocations=(
            LotAllocation(
                lot_id=f"LOT-{campaign_id}",
                quantity=Decimal("1"),
                broker_basis=cost_basis,
                basis_source="SYNTHETIC_TEST",
                campaign_id=campaign_id,
            ),
        ),
    )


def test_synthetic_persisted_periods_feed_multi_month_analytics(tmp_path) -> None:
    january_path = tmp_path / "2026-01-realized-attributions.json"
    february_path = tmp_path / "2026-02-realized-attributions.json"

    save_realized_attributions(
        january_path,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        attributions=(
            _attribution(symbol="SYNTHA", closed_date=date(2026, 1, 15), gain_loss="25.00", campaign_id="SYNTH-CAMP-001"),
            _attribution(symbol="SYNTHB", closed_date=date(2026, 1, 20), gain_loss="-5.00", campaign_id="SYNTH-CAMP-002"),
        ),
    )
    save_realized_attributions(
        february_path,
        period_start=date(2026, 2, 1),
        period_end=date(2026, 2, 28),
        attributions=(
            _attribution(symbol="SYNTHC", closed_date=date(2026, 2, 10), gain_loss="-10.00", campaign_id="SYNTH-CAMP-003"),
        ),
    )

    summaries = summarize_multi_month_analytics(
        monthly_attributions=load_persisted_monthly_attributions(
            [january_path, february_path]
        )
    )

    assert [s.period_start for s in summaries] == [date(2026, 1, 1), date(2026, 2, 1)]
    assert [s.realized_pnl.broker_record_count for s in summaries] == [2, 1]
    assert [s.realized_pnl.broker_realized_pnl for s in summaries] == [
        Decimal("20.00"), Decimal("-10.00")
    ]
    assert all(s.realized_pnl.attribution_complete for s in summaries)
    assert summaries[0].campaign_performance.campaign_count == 2
    assert summaries[0].campaign_performance.winning_campaign_count == 1
    assert summaries[0].campaign_performance.losing_campaign_count == 1
    assert summaries[1].campaign_performance.campaign_count == 1
