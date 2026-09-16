from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.import_pipeline import PeriodImportPipeline


DATA = "tests/data"


def test_period_pipeline_reconstructs_may_covered_call_equity() -> None:
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
        historical_trade_histories=(
            f"{DATA}/thinkorswim/Account Trade History March 2026.csv",
            f"{DATA}/thinkorswim/Account Trade History April 2026.csv",
        ),
        historical_period_start=date(2026, 3, 1),
    )

    assert result.boundary_reconstruction.unresolved_positions == ()
    assert result.boundary_reconstruction.unresolved_campaigns == ()
    assert result.boundary_reconstruction.historical_requirements == ()

    msft_lots = result.opening_lot_book.lots(Instrument("MSFT"))

    assert sum(
        (lot.quantity for lot in msft_lots),
        Decimal("0"),
    ) == Decimal("500")

    assert sum(
        (
            lot.quantity
            for lot in msft_lots
            if lot.campaign_id == "CAMP-000012"
        ),
        Decimal("0"),
    ) == Decimal("400")

    assert sum(
        (
            lot.quantity
            for lot in msft_lots
            if lot.campaign_id is None
        ),
        Decimal("0"),
    ) == Decimal("100")

    orcl_lots = result.opening_lot_book.lots(Instrument("ORCL"))

    assert sum(
        (lot.quantity for lot in orcl_lots),
        Decimal("0"),
    ) == Decimal("500")

    assert sum(
        (
            lot.quantity
            for lot in orcl_lots
            if lot.campaign_id == "CAMP-000013"
        ),
        Decimal("0"),
    ) == Decimal("400")

    assert sum(
        (
            lot.quantity
            for lot in orcl_lots
            if lot.campaign_id is None
        ),
        Decimal("0"),
    ) == Decimal("100")

    msft_call = OptionContract(
        underlying="MSFT",
        expiration=date(2026, 6, 18),
        strike=Decimal("340"),
        option_type=OptionType.CALL,
    )
    msft_call_lots = result.opening_lot_book.lots(msft_call)

    assert any(
        lot.quantity == Decimal("-4")
        and lot.campaign_id == "CAMP-000012"
        for lot in msft_call_lots
    )

    orcl_call = OptionContract(
        underlying="ORCL",
        expiration=date(2026, 6, 18),
        strike=Decimal("125"),
        option_type=OptionType.CALL,
    )
    orcl_call_lots = result.opening_lot_book.lots(orcl_call)

    assert any(
        lot.quantity == Decimal("-4")
        and lot.campaign_id == "CAMP-000013"
        for lot in orcl_call_lots
    )
