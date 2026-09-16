from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.import_pipeline import PeriodImportPipeline


DATA = "tests/data"


def test_may_can_use_june_assignment_as_boundary_evidence_without_importing_june_event() -> None:
    result = PeriodImportPipeline().run(
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
        thinkorswim_trade_history=(
            f"{DATA}/thinkorswim/Account Trade History May 2026.csv"
        ),
        opening_snapshot=f"{DATA}/schwab/april_positions.txt",
        opening_snapshot_at=datetime(2026, 4, 30),
        assignment_lines=(
            Path(f"{DATA}/schwab/may_assignments.txt").read_text().splitlines(),
        ),
        boundary_assignment_lines=(
            Path(f"{DATA}/schwab/june_assignments.txt").read_text().splitlines(),
        ),
        historical_trade_histories=(
            f"{DATA}/thinkorswim/Account Trade History March 2026.csv",
            f"{DATA}/thinkorswim/Account Trade History April 2026.csv",
        ),
        historical_period_start=date(2026, 3, 1),
    )

    assert all(
        event.occurred_at.date() <= date(2026, 5, 31)
        for event in result.position_events
    )

    gs_call = OptionContract(
        underlying="GS",
        expiration=date(2026, 6, 18),
        strike=Decimal("720"),
        option_type=OptionType.CALL,
    )

    option_lots = result.opening_lot_book.lots(gs_call)
    equity_lots = result.opening_lot_book.lots(Instrument("GS"))

    assert len(option_lots) == 1
    assert len(equity_lots) == 1

    assert option_lots[0].campaign_id is not None
    assert option_lots[0].campaign_id.startswith("HIST-CAMP-")
    assert equity_lots[0].campaign_id == option_lots[0].campaign_id


def test_may_gs_realized_attribution_uses_boundary_assignment_evidence() -> None:
    result = PeriodImportPipeline().run(
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
        thinkorswim_trade_history=(
            f"{DATA}/thinkorswim/Account Trade History May 2026.csv"
        ),
        opening_snapshot=f"{DATA}/schwab/april_positions.txt",
        opening_snapshot_at=datetime(2026, 4, 30),
        assignment_lines=(
            Path(f"{DATA}/schwab/may_assignments.txt").read_text().splitlines(),
        ),
        boundary_assignment_lines=(
            Path(f"{DATA}/schwab/june_assignments.txt").read_text().splitlines(),
        ),
        realized_gain_loss_report=(
            f"{DATA}/schwab/may_realized_gain_loss.txt"
        ),
        historical_trade_histories=(
            f"{DATA}/thinkorswim/Account Trade History March 2026.csv",
            f"{DATA}/thinkorswim/Account Trade History April 2026.csv",
        ),
        historical_period_start=date(2026, 3, 1),
    )

    attributions = RealizedLotAttributor(
        result.opening_lot_book
    ).attribute_campaigns(
        list(result.campaigns),
        list(result.realized_gain_loss),
        list(result.attribution_events),
    )

    gs_attributions = [
        item
        for item in attributions
        if item.record.instrument == Instrument("GS")
        and item.record.closed_date == date(2026, 5, 29)
    ]

    assert len(gs_attributions) == 1

    gs = gs_attributions[0]

    assert gs.record.quantity == Decimal("100")
    assert gs.record.cost_basis == Decimal("92053.00")
    assert gs.record.gain_loss == Decimal("1566.39")

    assert len(gs.allocations) == 1
    assert gs.allocations[0].quantity == Decimal("100")
    assert gs.allocations[0].campaign_id is not None
    assert gs.allocations[0].campaign_id.startswith("HIST-CAMP-")

