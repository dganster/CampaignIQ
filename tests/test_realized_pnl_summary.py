from datetime import date
from decimal import Decimal

from campaigniq.analytics.realized_pnl_summary import summarize_realized_pnl
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument


def realized_record(
    *,
    closed_date: date,
    gain_loss: str,
) -> RealizedGainLossRecord:
    gain_loss_decimal = Decimal(gain_loss)

    return RealizedGainLossRecord(
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


def attribution(
    record: RealizedGainLossRecord,
    *,
    campaign_id: str | None,
) -> RealizedAttribution:
    return RealizedAttribution(
        record=record,
        allocations=(
            LotAllocation(
                lot_id="LOT-1",
                quantity=record.quantity,
                broker_basis=record.cost_basis,
                basis_source="SCHWAB_REALIZED_GAIN_LOSS",
                campaign_id=campaign_id,
            ),
        ),
    )


def test_summarizes_realized_pnl_for_requested_period() -> None:
    attributed = realized_record(
        closed_date=date(2026, 8, 15),
        gain_loss="-100.00",
    )
    unattributed = realized_record(
        closed_date=date(2026, 8, 20),
        gain_loss="25.00",
    )
    outside_period = realized_record(
        closed_date=date(2026, 9, 1),
        gain_loss="50.00",
    )

    summary = summarize_realized_pnl(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=(
            attribution(attributed, campaign_id="CAMP-1"),
            attribution(unattributed, campaign_id=None),
            attribution(outside_period, campaign_id="CAMP-2"),
        ),
    )

    assert summary.period_start == date(2026, 8, 1)
    assert summary.period_end == date(2026, 8, 31)

    assert summary.broker_realized_pnl == Decimal("-75.00")
    assert summary.attributed_realized_pnl == Decimal("-100.00")
    assert summary.unattributed_realized_pnl == Decimal("25.00")

    assert summary.broker_record_count == 2
    assert summary.attributed_record_count == 1
    assert summary.unattributed_record_count == 1

    assert summary.attribution_complete is False

    assert (
        summary.broker_realized_pnl
        == summary.attributed_realized_pnl
        + summary.unattributed_realized_pnl
    )


def test_summarizes_real_august_period_from_reconciled_data() -> None:
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

    result = pipeline.run(
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
        result.opening_lot_book
    ).attribute_campaigns(
        list(result.campaigns),
        list(result.realized_gain_loss),
        list(result.attribution_events),
    )

    summary = summarize_realized_pnl(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=attributions,
    )

    assert summary.broker_realized_pnl == Decimal("-22644.32")
    assert summary.attributed_realized_pnl == Decimal("-22644.32")
    assert summary.unattributed_realized_pnl == Decimal("0")

    assert summary.broker_record_count == 59
    assert summary.attributed_record_count == 59
    assert summary.unattributed_record_count == 0

    assert summary.attribution_complete is True

    assert (
        summary.broker_realized_pnl
        == summary.attributed_realized_pnl
        + summary.unattributed_realized_pnl
    )
