from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.import_pipeline import PeriodImportPipeline

from campaigniq.domain.boundary_validation import HistoricalRequirement
from campaigniq.domain.historical_evidence import (
    ThinkorswimHistoricalEvidenceRepository,
)
from campaigniq.domain.historical_evidence_resolver import (
    HistoricalEvidenceResolver,
)

from campaigniq.domain.value_objects.forex_pair import ForexPair


DATA = Path("tests/data")

JANUARY = DATA / "thinkorswim/Account Trading History 2026.csv"
MARCH = DATA / "thinkorswim/Account Trade History March 2026.csv"
APRIL = DATA / "thinkorswim/Account Trade History April 2026.csv"

DECEMBER = DATA / "thinkorswim/Account Trade History December 2025.csv"
NOVEMBER = DATA / "thinkorswim/Account Trade History November 2025.csv"

DECEMBER_POSITIONS = DATA / "schwab/december_positions.txt"
MARCH_POSITIONS = DATA / "schwab/march_positions.txt"

JANUARY_REALIZED = DATA / "schwab/january_realized_gain_loss.txt"
JANUARY_ASSIGNMENTS = DATA / "schwab/january_assignments.txt"
APRIL_ASSIGNMENTS = DATA / "schwab/march_assignments.txt"
APRIL_REALIZED = DATA / "schwab/april_realized_gain_loss.txt"


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
    assert len(result.position_events) == 2
    assert {
        (
            event.changes[0].instrument,
            event.changes[0].quantity,
        )
        for event in result.position_events
    } == {
        (Instrument("DXCM"), Decimal("-500")),
        (Instrument("EL"), Decimal("-500")),
    }
    assert len(result.realized_gain_loss) == 28
    assert (
        sum(
            (record.gain_loss for record in result.realized_gain_loss),
            Decimal("0"),
        )
        == Decimal("126642.32")
    )
    assert len(result.position_history.items()) == (
        len(result.trades) + len(result.position_events)
    )
    assert result.boundary_reconstruction.unresolved_positions == ()
    assert result.boundary_reconstruction.unresolved_campaigns == ()
    assert result.boundary_reconstruction.historical_requirements == ()

    campaign_ids = {
        lot.campaign_id
        for lots in result.opening_lot_book._lots.values()
        for lot in lots
        if lot.campaign_id is not None
    }

    assert {"CAMP-000001", "CAMP-000002", "CAMP-000003"}.issubset(
        campaign_ids
    )

    assert result.boundary_reconstruction.historical_requirements == ()

def test_period_pipeline_can_load_resolved_historical_trade_history() -> None:
    repository = ThinkorswimHistoricalEvidenceRepository(
    DATA / "thinkorswim"
)

    requirement = HistoricalRequirement(
        case_id="TEST-RESOLVED",
        earliest_unresolved_date=date(2025, 12, 1),
        months=("2025-12",),
        document_types=("Account Trade History",),
        reason="Historical campaign ancestry is unresolved.",
    )

    resolution = HistoricalEvidenceResolver(repository).resolve(
        requirement
    )

    assert resolution.complete
    assert len(resolution.trade_history) == 1

    pipeline = PeriodImportPipeline()

    trades = pipeline._read_trades(
        resolution.trade_history[0].path,
        start=date(2025, 12, 1),
        end=date(2025, 12, 31),
    )

    assert trades
    assert all(
        min(
            execution.executed_at
            for leg in trade.legs
            for execution in leg.executions
        ).date().month
        == 12
        for trade in trades
    )

def test_period_pipeline_imports_forex_and_respects_period() -> None:
    result = PeriodImportPipeline().run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        thinkorswim_trade_history=JANUARY,
        opening_snapshot=DECEMBER_POSITIONS,
        opening_snapshot_at=datetime(2025, 12, 31),
    )

    assert len(result.trades) == 67

    forex_trades = [
        trade
        for trade in result.trades
        if isinstance(
            trade.legs[0].instrument,
            ForexPair,
        )
    ]

    assert len(forex_trades) == 22

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

    assert {"CAMP-000001", "CAMP-000002", "CAMP-000003"}.issubset(
        campaign_ids
    )


def test_period_pipeline_assigns_campaign_provenance_to_opening_option_lots() -> None:
    result = PeriodImportPipeline().run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        thinkorswim_trade_history=JANUARY,
        opening_snapshot=DECEMBER_POSITIONS,
        opening_snapshot_at=datetime(2025, 12, 31),
        historical_trade_histories=(DECEMBER, NOVEMBER),
        historical_period_start=date(2025, 11, 1),
    )

    option_lots = [
        lot
        for lots in result.opening_lot_book._lots.values()
        for lot in lots
        if lot.instrument.__class__.__name__ == "OptionContract"
        and lot.campaign_id is not None
    ]

    assert option_lots


def test_period_pipeline_assignment_provenance_reaches_equity_lot() -> None:
    from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor

    result = PeriodImportPipeline().run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        thinkorswim_trade_history=JANUARY,
        opening_snapshot=DECEMBER_POSITIONS,
        opening_snapshot_at=datetime(2025, 12, 31),
        assignment_lines=(
            JANUARY_ASSIGNMENTS.read_text().splitlines(),
        ),
        historical_trade_histories=(DECEMBER, NOVEMBER),
        historical_period_start=date(2025, 11, 1),
    )

    RealizedLotAttributor(result.opening_lot_book).attribute_campaigns(
        list(result.campaigns),
        list(result.realized_gain_loss),
        list(result.position_events),
    )

    assigned_equity_lots = [
        lot
        for lots in result.opening_lot_book._lots.values()
        for lot in lots
        if lot.campaign_id is not None
        and isinstance(lot.instrument, Instrument)
    ]

    assert assigned_equity_lots


def test_period_pipeline_supports_realized_attribution_end_to_end() -> None:
    from campaigniq.domain.campaign_realized_pnl import (
        aggregate_campaign_realized_pnl,
    )
    from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor

    result = PeriodImportPipeline().run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        thinkorswim_trade_history=JANUARY,
        opening_snapshot=DECEMBER_POSITIONS,
        opening_snapshot_at=datetime(2025, 12, 31),
        assignment_lines=(
            JANUARY_ASSIGNMENTS.read_text().splitlines(),
        ),
        realized_gain_loss_report=JANUARY_REALIZED,
        historical_trade_histories=(DECEMBER, NOVEMBER),
        historical_period_start=date(2025, 11, 1),
    )

    attributions = RealizedLotAttributor(
        result.opening_lot_book
    ).attribute_campaigns(
        list(result.campaigns),
        list(result.realized_gain_loss),
        list(result.position_events),
    )
    campaign_results = aggregate_campaign_realized_pnl(list(attributions))

    assert len(attributions) == 28
    assert (
        sum(
            (item.gain_loss for item in campaign_results),
            Decimal("0"),
        )
        == Decimal("126642.32")
    )
    assert (
        sum(
            (
                item.record.gain_loss
                for item in attributions
                if item.has_unassigned_campaign_allocation
            ),
            Decimal("0"),
        )
        == Decimal("0")
    )

def test_period_pipeline_reconstructs_crwd_shares_from_historical_expiration() -> None:
    result = PeriodImportPipeline().run(
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        thinkorswim_trade_history=APRIL,
        opening_snapshot=MARCH_POSITIONS,
        opening_snapshot_at=datetime(2026, 3, 31),
        realized_gain_loss_report=APRIL_REALIZED,
        historical_trade_histories=(MARCH,),
        historical_period_start=date(2026, 3, 1),
    )

    assert result.boundary_reconstruction.unresolved_positions == ()
    assert result.boundary_reconstruction.unresolved_campaigns == ()
    assert result.boundary_reconstruction.historical_requirements == ()

    crwd_lots = [
        lot
        for lot in result.opening_lot_book.lots(Instrument("CRWD"))
        if lot.quantity == Decimal("500")
    ]

    assert crwd_lots
    assert crwd_lots[0].campaign_id == "CAMP-000005"
    assert (
        crwd_lots[0].basis_source
        == "HISTORICAL_EXPIRATION_RECONSTRUCTION"
    )

    assert len(result.realized_gain_loss) == 23
    assert (
        sum(
            (record.gain_loss for record in result.realized_gain_loss),
            Decimal("0"),
        )
        == Decimal("-170808.05")
    )

def test_period_pipeline_automatically_discovers_historical_trade_history() -> None:
    result = PeriodImportPipeline().run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        thinkorswim_trade_history=JANUARY,
        opening_snapshot=DECEMBER_POSITIONS,
        opening_snapshot_at=datetime(2025, 12, 31),
        historical_source_root=DATA / "thinkorswim",
    )

    assert result.boundary_reconstruction.historical_requirements == ()
    assert result.boundary_reconstruction.unresolved_positions == ()
    assert result.boundary_reconstruction.unresolved_campaigns == ()

    campaign_ids = {
        lot.campaign_id
        for lots in result.opening_lot_book._lots.values()
        for lot in lots
        if lot.campaign_id is not None
    }

    assert {
        "CAMP-000001",
        "CAMP-000002",
        "CAMP-000003",
    }.issubset(campaign_ids)

def test_period_pipeline_imports_forex_trades() -> None:
    result = PeriodImportPipeline().run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        thinkorswim_trade_history=JANUARY,
        opening_snapshot=DECEMBER_POSITIONS,
        opening_snapshot_at=datetime(2025, 12, 31),
    )

    forex_trades = [
        trade
        for trade in result.trades
        if isinstance(
            trade.legs[0].instrument,
            ForexPair,
        )
    ]

    assert forex_trades