from datetime import date, datetime
from decimal import Decimal

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
        opening_snapshot=f"{DATA}/schwab/may_positions.txt",
        opening_snapshot_at=datetime(2026, 4, 30),
        historical_trade_histories=(
            f"{DATA}/thinkorswim/Account Trade History April 2026.csv",
        ),
        historical_period_start=date(2026, 4, 1),
    )

    assert result.boundary_reconstruction.unresolved_positions == ()
    assert result.boundary_reconstruction.unresolved_campaigns == ()
    assert result.boundary_reconstruction.historical_requirements == ()

    msft_lots = result.opening_lot_book.lots(Instrument("MSFT"))
    msft_reconstructed = [
        lot
        for lot in msft_lots
        if lot.quantity == Decimal("400")
    ]

    assert len(msft_reconstructed) == 1
    assert msft_reconstructed[0].campaign_id == "CAMP-000012"
    assert (
        msft_reconstructed[0].basis_source
        == "HISTORICAL_TRADE_RECONSTRUCTION"
    )

    orcl_lots = result.opening_lot_book.lots(Instrument("ORCL"))
    orcl_reconstructed = [
        lot
        for lot in orcl_lots
        if lot.quantity == Decimal("400")
    ]

    assert len(orcl_reconstructed) == 1
    assert orcl_reconstructed[0].campaign_id == "CAMP-000013"
    assert (
        orcl_reconstructed[0].basis_source
        == "HISTORICAL_TRADE_RECONSTRUCTION"
    )

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
