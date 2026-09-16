from datetime import date
from decimal import Decimal

from campaigniq.analytics.monthly_analytics_summary import (
    summarize_monthly_analytics,
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


def test_summarizes_monthly_realized_pnl_and_campaign_performance() -> None:
    summary = summarize_monthly_analytics(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=(
            attribution(
                closed_date=date(2026, 8, 5),
                gain_loss="300.00",
                campaign_id="CAMP-WIN",
            ),
            attribution(
                closed_date=date(2026, 8, 10),
                gain_loss="-100.00",
                campaign_id="CAMP-LOSS",
            ),
            attribution(
                closed_date=date(2026, 8, 15),
                gain_loss="25.00",
                campaign_id=None,
            ),
            attribution(
                closed_date=date(2026, 9, 1),
                gain_loss="999.00",
                campaign_id="CAMP-OUTSIDE",
            ),
        ),
    )

    assert summary.period_start == date(2026, 8, 1)
    assert summary.period_end == date(2026, 8, 31)

    realized = summary.realized_pnl

    assert realized.broker_realized_pnl == Decimal("225.00")
    assert realized.attributed_realized_pnl == Decimal("200.00")
    assert realized.unattributed_realized_pnl == Decimal("25.00")
    assert realized.broker_record_count == 3
    assert realized.attributed_record_count == 2
    assert realized.unattributed_record_count == 1
    assert realized.attribution_complete is False

    performance = summary.campaign_performance

    assert performance.campaign_count == 2
    assert performance.excluded_campaign_count == 0
    assert performance.winning_campaign_count == 1
    assert performance.losing_campaign_count == 1
    assert performance.breakeven_campaign_count == 0
    assert performance.win_rate == Decimal("0.5")
    assert performance.average_win == Decimal("300.00")
    assert performance.average_loss == Decimal("-100.00")
    assert performance.best_campaign_id == "CAMP-WIN"
    assert performance.best_campaign_pnl == Decimal("300.00")
    assert performance.worst_campaign_id == "CAMP-LOSS"
    assert performance.worst_campaign_pnl == Decimal("-100.00")


def test_rejects_invalid_monthly_period() -> None:
    try:
        summarize_monthly_analytics(
            period_start=date(2026, 8, 31),
            period_end=date(2026, 8, 1),
            attributions=(),
        )
    except ValueError as error:
        assert str(error) == "period_end must be on or after period_start"
    else:
        raise AssertionError("Expected ValueError")


def test_summarizes_real_august_monthly_analytics() -> None:
    from datetime import datetime

    from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
    from campaigniq.import_pipeline import PeriodImportPipeline

    data = "tests/data"

    pipeline = PeriodImportPipeline()

    july = pipeline.run(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        thinkorswim_trade_history=(
            f"{data}/thinkorswim/Account Trade History July 2026.csv"
        ),
        opening_snapshot=f"{data}/schwab/june_positions.txt",
        opening_snapshot_at=datetime(2026, 6, 30),
        historical_trade_histories=(
            f"{data}/thinkorswim/Account Trade History June 2026.csv",
        ),
        historical_period_start=date(2026, 6, 1),
        assignment_lines=(
            open(f"{data}/schwab/july_assignments.txt")
            .read()
            .splitlines(),
        ),
    )

    august = pipeline.run(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        thinkorswim_trade_history=(
            f"{data}/thinkorswim/Account Trade History August 2026.csv"
        ),
        carried_opening_lot_book=july.ending_lot_book,
        historical_trade_histories=(
            f"{data}/thinkorswim/Account Trade History July 2026.csv",
        ),
        historical_period_start=date(2026, 7, 1),
        assignment_lines=(
            open(f"{data}/schwab/august_assignments.txt")
            .read()
            .splitlines(),
        ),
        realized_gain_loss_report=(
            f"{data}/schwab/august_realized_gain_loss.txt"
        ),
    )

    attributions = RealizedLotAttributor(
        august.opening_lot_book
    ).attribute_campaigns(
        list(august.campaigns),
        list(august.realized_gain_loss),
        list(august.attribution_events),
    )

    summary = summarize_monthly_analytics(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=attributions,
    )

    assert summary.realized_pnl.broker_realized_pnl == Decimal("-22644.32")
    assert summary.realized_pnl.attributed_realized_pnl == Decimal("-22644.32")
    assert summary.realized_pnl.unattributed_realized_pnl == Decimal("0")
    assert summary.realized_pnl.attribution_complete is True

    performance = summary.campaign_performance

    assert performance.campaign_count == 25
    assert performance.excluded_campaign_count == 0
    assert performance.winning_campaign_count == 15
    assert performance.losing_campaign_count == 10
    assert performance.breakeven_campaign_count == 0
    assert performance.win_rate == Decimal("0.6")
    assert performance.best_campaign_id == "CAMP-000008"
    assert performance.best_campaign_pnl == Decimal("6126.05")
    assert performance.worst_campaign_id == "CAMP-000019"
    assert performance.worst_campaign_pnl == Decimal("-8168.29")
