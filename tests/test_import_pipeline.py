from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from campaigniq.import_pipeline import PeriodImportPipeline


DATA = Path("tests/data")
JANUARY = DATA / "thinkorswim/Account Trading History 2026.csv"
DECEMBER = DATA / "thinkorswim/Account Trade History December 2025.csv"
NOVEMBER = DATA / "thinkorswim/Account Trade History November 2025.csv"
DECEMBER_POSITIONS = DATA / "schwab/december_positions.txt"
JANUARY_REALIZED = DATA / "schwab/january_realized_gain_loss.txt"
JANUARY_ASSIGNMENTS = DATA / "schwab/january_assignments.txt"


def test_period_pipeline_imports_january_domain_data() -> None:
    result = PeriodImportPipeline().run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        thinkorswim_trade_history=JANUARY,
        opening_snapshot=DECEMBER_POSITIONS,
        opening_snapshot_at=datetime(2025, 12, 31),
        historical_trade_histories=(DECEMBER, NOVEMBER),
        historical_period_start=date(2025, 11, 1),
        realized_gain_loss_report=JANUARY_REALIZED,
    )

    assert result.trades
    assert result.campaigns
    assert result.position_events == ()
    assert len(result.realized_gain_loss) == 28
    assert sum((record.gain_loss for record in result.realized_gain_loss), Decimal("0")) == Decimal("126642.32")
    assert len(result.position_history.items()) == len(result.trades)
    assert result.boundary_reconstruction.unresolved_positions == ()
    assert result.boundary_reconstruction.unresolved_campaigns == ()
    assert result.boundary_reconstruction.historical_requirements == ()


def test_period_pipeline_excludes_forex_and_respects_period() -> None:
    result = PeriodImportPipeline().run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        thinkorswim_trade_history=JANUARY,
        opening_snapshot=DECEMBER_POSITIONS,
        opening_snapshot_at=datetime(2025, 12, 31),
    )

    assert len(result.trades) == 45
    assert all(
        min(
            execution.executed_at
            for leg in trade.legs
            for execution in leg.executions
        ).date().month
        == 1
        for trade in result.trades
    )


def test_period_pipeline_resolves_unambiguous_snapshot_provenance_for_in_period_rolls() -> None:
    result = PeriodImportPipeline().run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        thinkorswim_trade_history=JANUARY,
        opening_snapshot=DECEMBER_POSITIONS,
        opening_snapshot_at=datetime(2025, 12, 31),
        historical_trade_histories=(DECEMBER, NOVEMBER),
        historical_period_start=date(2025, 11, 1),
    )

    campaign_ids = {
        lot.campaign_id
        for lots in result.opening_lot_book._lots.values()
        for lot in lots
        if lot.campaign_id is not None
    }

    assert {"CAMP-000001", "CAMP-000002", "CAMP-000003"}.issubset(campaign_ids)


def test_period_pipeline_supports_realized_attribution_end_to_end() -> None:
    from campaigniq.domain.campaign_realized_pnl import aggregate_campaign_realized_pnl
    from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor

    result = PeriodImportPipeline().run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        thinkorswim_trade_history=JANUARY,
        opening_snapshot=DECEMBER_POSITIONS,
        opening_snapshot_at=datetime(2025, 12, 31),
        assignment_lines=JANUARY_ASSIGNMENTS.read_text().splitlines(),
        realized_gain_loss_report=JANUARY_REALIZED,
        historical_trade_histories=(DECEMBER, NOVEMBER),
        historical_period_start=date(2025, 11, 1),
    )

    attributions = RealizedLotAttributor(result.opening_lot_book).attribute_campaigns(
        list(result.campaigns),
        list(result.realized_gain_loss),
        list(result.position_events),
    )
    campaign_results = aggregate_campaign_realized_pnl(list(attributions))

    assert len(attributions) == 28
    assert sum((item.gain_loss for item in campaign_results), Decimal("0")) == Decimal("126642.32")
    assert sum((item.record.gain_loss for item in attributions if item.has_unassigned_campaign_allocation), Decimal("0")) == Decimal("0")
