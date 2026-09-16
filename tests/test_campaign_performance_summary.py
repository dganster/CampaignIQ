from decimal import Decimal

from campaigniq.analytics.campaign_performance_summary import (
    summarize_campaign_performance,
    summarize_campaign_performance_for_period,
)
from campaigniq.domain.campaign_realized_pnl import CampaignRealizedPnl


def campaign_result(
    campaign_id: str,
    gain_loss: str,
    *,
    fully_reconciled: bool = True,
) -> CampaignRealizedPnl:
    gain_loss_decimal = Decimal(gain_loss)

    return CampaignRealizedPnl(
        campaign_id=campaign_id,
        proceeds=Decimal("10000") + gain_loss_decimal,
        cost_basis=Decimal("10000"),
        gain_loss=gain_loss_decimal,
        allocation_count=1,
        record_count=1,
        fully_reconciled=fully_reconciled,
    )


def test_summarizes_fully_reconciled_campaign_performance() -> None:
    summary = summarize_campaign_performance(
        (
            campaign_result("CAMP-WIN-1", "100.00"),
            campaign_result("CAMP-WIN-2", "300.00"),
            campaign_result("CAMP-LOSS", "-200.00"),
            campaign_result("CAMP-FLAT", "0.00"),
            campaign_result(
                "CAMP-UNRECONCILED",
                "1000.00",
                fully_reconciled=False,
            ),
        )
    )

    assert summary.campaign_count == 4
    assert summary.excluded_campaign_count == 1

    assert summary.winning_campaign_count == 2
    assert summary.losing_campaign_count == 1
    assert summary.breakeven_campaign_count == 1

    assert summary.win_rate == Decimal("0.5")

    assert summary.average_win == Decimal("200.00")
    assert summary.median_win == Decimal("200.00")

    assert summary.average_loss == Decimal("-200.00")
    assert summary.median_loss == Decimal("-200.00")

    assert summary.best_campaign_id == "CAMP-WIN-2"
    assert summary.best_campaign_pnl == Decimal("300.00")

    assert summary.worst_campaign_id == "CAMP-LOSS"
    assert summary.worst_campaign_pnl == Decimal("-200.00")


def test_handles_empty_win_and_loss_categories() -> None:
    summary = summarize_campaign_performance(
        (
            campaign_result("CAMP-FLAT-1", "0.00"),
            campaign_result("CAMP-FLAT-2", "0.00"),
        )
    )

    assert summary.campaign_count == 2
    assert summary.winning_campaign_count == 0
    assert summary.losing_campaign_count == 0
    assert summary.breakeven_campaign_count == 2

    assert summary.win_rate == Decimal("0")

    assert summary.average_win is None
    assert summary.median_win is None
    assert summary.average_loss is None
    assert summary.median_loss is None

    assert summary.best_campaign_id == "CAMP-FLAT-1"
    assert summary.best_campaign_pnl == Decimal("0.00")
    assert summary.worst_campaign_id == "CAMP-FLAT-1"
    assert summary.worst_campaign_pnl == Decimal("0.00")


def test_handles_no_included_campaigns() -> None:
    summary = summarize_campaign_performance(
        (
            campaign_result(
                "CAMP-UNRECONCILED",
                "500.00",
                fully_reconciled=False,
            ),
        )
    )

    assert summary.campaign_count == 0
    assert summary.excluded_campaign_count == 1

    assert summary.winning_campaign_count == 0
    assert summary.losing_campaign_count == 0
    assert summary.breakeven_campaign_count == 0

    assert summary.win_rate is None

    assert summary.average_win is None
    assert summary.median_win is None
    assert summary.average_loss is None
    assert summary.median_loss is None

    assert summary.best_campaign_id is None
    assert summary.best_campaign_pnl is None
    assert summary.worst_campaign_id is None
    assert summary.worst_campaign_pnl is None


def test_summarizes_real_august_campaign_performance() -> None:
    from datetime import date, datetime

    from campaigniq.domain.campaign_realized_pnl import (
        aggregate_campaign_realized_pnl,
    )
    from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
    from campaigniq.import_pipeline import PeriodImportPipeline

    data = "tests/data"

    assignment_lines = tuple(
        open(f"{data}/schwab/august_assignments.txt")
        .read()
        .splitlines()
    )

    july = PeriodImportPipeline().run(
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

    result = PeriodImportPipeline().run(
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
        assignment_lines=(list(assignment_lines),),
        realized_gain_loss_report=(
            f"{data}/schwab/august_realized_gain_loss.txt"
        ),
    )

    attributions = RealizedLotAttributor(
        result.opening_lot_book
    ).attribute_campaigns(
        list(result.campaigns),
        list(result.realized_gain_loss),
        list(result.attribution_events),
    )

    summary = summarize_campaign_performance_for_period(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=attributions,
    )    

    assert summary.campaign_count == 25
    assert summary.excluded_campaign_count == 0
    assert summary.winning_campaign_count == 15
    assert summary.losing_campaign_count == 10
    assert summary.breakeven_campaign_count == 0
    assert summary.win_rate == Decimal("0.6")

    assert summary.best_campaign_id == "CAMP-000008"
    assert summary.best_campaign_pnl == Decimal("6126.05")

    assert summary.worst_campaign_id == "CAMP-000019"
    assert summary.worst_campaign_pnl == Decimal("-8168.29")


def test_summarizes_campaign_performance_for_requested_period() -> None:
    from datetime import date

    from campaigniq.analytics.campaign_performance_summary import (
        summarize_campaign_performance_for_period,
    )
    from campaigniq.domain.lot_allocation import LotAllocation
    from campaigniq.domain.lot_attribution import RealizedAttribution
    from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
    from campaigniq.domain.value_objects.instrument import Instrument

    def realized_attribution(
        *,
        closed_date: date,
        gain_loss: str,
        campaign_id: str,
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
                    lot_id=f"LOT-{closed_date.isoformat()}",
                    quantity=record.quantity,
                    broker_basis=record.cost_basis,
                    basis_source="SCHWAB_REALIZED_GAIN_LOSS",
                    campaign_id=campaign_id,
                ),
            ),
        )

    summary = summarize_campaign_performance_for_period(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=(
            realized_attribution(
                closed_date=date(2026, 8, 15),
                gain_loss="100.00",
                campaign_id="CAMP-1",
            ),
            realized_attribution(
                closed_date=date(2026, 9, 1),
                gain_loss="-500.00",
                campaign_id="CAMP-1",
            ),
        ),
    )

    assert summary.campaign_count == 1
    assert summary.winning_campaign_count == 1
    assert summary.losing_campaign_count == 0

    assert summary.best_campaign_id == "CAMP-1"
    assert summary.best_campaign_pnl == Decimal("100.00")
    assert summary.worst_campaign_id == "CAMP-1"
    assert summary.worst_campaign_pnl == Decimal("100.00")
