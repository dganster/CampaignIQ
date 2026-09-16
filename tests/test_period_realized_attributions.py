from datetime import date
from decimal import Decimal

from campaigniq.analytics.period_realized_attributions import (
    attribute_period_realized_pnl,
)
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument


def test_period_attribution_helper_is_importable() -> None:
    assert callable(attribute_period_realized_pnl)


def test_realized_attribution_remains_the_analytics_boundary_type() -> None:
    record = RealizedGainLossRecord(
        closed_date=date(2026, 1, 15),
        instrument=Instrument("IBM"),
        quantity=Decimal("100"),
        closing_price=Decimal("200"),
        proceeds=Decimal("20000"),
        cost_basis=Decimal("19900"),
        gain_loss=Decimal("100"),
        basis_method="FIFO",
        term="ST",
    )

    attribution = RealizedAttribution(
        record=record,
        allocations=(
            LotAllocation(
                lot_id="LOT-TEST",
                quantity=Decimal("100"),
                broker_basis=Decimal("19900"),
                basis_source="SCHWAB_REALIZED_GAIN_LOSS",
                campaign_id="CAMP-TEST",
            ),
        ),
    )

    assert attribution.record.gain_loss == Decimal("100")
    assert attribution.campaign_ids == ("CAMP-TEST",)


from datetime import datetime

from campaigniq.analytics.monthly_analytics_summary import (
    summarize_monthly_analytics,
)
from campaigniq.import_pipeline import PeriodImportPipeline


def test_attribute_period_realized_pnl_matches_real_august_reconciliation() -> None:
    data = "tests/data"

    july = PeriodImportPipeline().run(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        thinkorswim_trade_history=(
            f"{data}/thinkorswim/Account Trade History July 2026.csv"
        ),
        opening_snapshot=f"{data}/schwab/june_positions.txt",
        opening_snapshot_at=datetime(2026, 6, 30),
        assignment_lines=(
            tuple(
                open(
                    f"{data}/schwab/july_assignments.txt",
                    encoding="utf-8",
                )
                .read()
                .splitlines()
            ),
        ),
        historical_trade_histories=(
            f"{data}/thinkorswim/Account Trade History June 2026.csv",
        ),
        historical_period_start=date(2026, 6, 1),
    )

    august = PeriodImportPipeline().run(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        carried_opening_lot_book=july.ending_lot_book,
        thinkorswim_trade_history=(
            f"{data}/thinkorswim/Account Trade History August 2026.csv"
        ),
        assignment_lines=(
            tuple(
                open(
                    f"{data}/schwab/august_assignments.txt",
                    encoding="utf-8",
                )
                .read()
                .splitlines()
            ),
        ),
        realized_gain_loss_report=(
            f"{data}/schwab/august_realized_gain_loss.txt"
        ),
        historical_trade_histories=(
            f"{data}/thinkorswim/Account Trade History July 2026.csv",
        ),
        historical_period_start=date(2026, 7, 1),
    )

    opening_lots_before = {
        instrument: tuple(lots)
        for instrument, lots in august.opening_lot_book._lots.items()
    }
    opening_next_id_before = august.opening_lot_book._next_id

    attributions = attribute_period_realized_pnl(august)

    summary = summarize_monthly_analytics(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=attributions,
    )

    assert summary.realized_pnl.broker_realized_pnl == Decimal("-22644.32")
    assert summary.realized_pnl.attributed_realized_pnl == Decimal("-22644.32")
    assert summary.realized_pnl.unattributed_realized_pnl == Decimal("0")
    assert summary.realized_pnl.attribution_complete is True

    assert summary.campaign_performance.campaign_count == 25
    assert summary.campaign_performance.winning_campaign_count == 15
    assert summary.campaign_performance.losing_campaign_count == 10
    assert summary.campaign_performance.win_rate == Decimal("0.6")

    opening_lots_after = {
        instrument: tuple(lots)
        for instrument, lots in august.opening_lot_book._lots.items()
    }

    assert opening_lots_after == opening_lots_before
    assert august.opening_lot_book._next_id == opening_next_id_before
