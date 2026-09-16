from datetime import date, datetime
from pathlib import Path

from campaigniq.domain.option_contract import OptionContract
from campaigniq.import_pipeline import PeriodImportPipeline


DATA = Path("tests/data")


def test_july_apd_assignment_is_applied_after_june_ending_snapshot() -> None:
    result = PeriodImportPipeline().run(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        thinkorswim_trade_history=(
            DATA / "thinkorswim/Account Trade History July 2026.csv"
        ),
        opening_snapshot=DATA / "schwab/june_positions.txt",
        opening_snapshot_at=datetime(2026, 6, 30),
        assignment_lines=(
            (DATA / "schwab/july_assignments.txt").read_text().splitlines(),
        ),
        realized_gain_loss_report=(
            DATA / "schwab/july_realized_gain_loss.txt"
        ),
        historical_trade_histories=(
            DATA / "thinkorswim/Account Trade History June 2026.csv",
        ),
        historical_period_start=date(2026, 6, 1),
    )

    apd_events = [
        event
        for event in result.position_events
        if any(
            getattr(change.instrument, "symbol", None) == "APD"
            or getattr(change.instrument, "underlying", None) == "APD"
            for change in event.changes
        )
    ]

    assert len(apd_events) == 1
    assert apd_events[0].occurred_at == datetime(2026, 7, 1)

    apd_ending_lots = [
        lot
        for instrument, lots in result.ending_lot_book._lots.items()
        if (
            instrument.underlying
            if isinstance(instrument, OptionContract)
            else instrument.symbol
        )
        == "APD"
        for lot in lots
    ]

    assert apd_ending_lots == []