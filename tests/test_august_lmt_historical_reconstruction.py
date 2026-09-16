from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.import_pipeline import PeriodImportPipeline


DATA = "tests/data"


def _run_july():
    return PeriodImportPipeline().run(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        thinkorswim_trade_history=(
            f"{DATA}/thinkorswim/Account Trade History July 2026.csv"
        ),
        opening_snapshot=f"{DATA}/schwab/june_positions.txt",
        opening_snapshot_at=datetime(2026, 6, 30),
        historical_trade_histories=(
            f"{DATA}/thinkorswim/Account Trade History June 2026.csv",
        ),
        historical_period_start=date(2026, 6, 1),
        assignment_lines=(
            open(f"{DATA}/schwab/july_assignments.txt")
            .read()
            .splitlines(),
        ),
    )


def test_period_pipeline_reconstructs_august_lmt_covered_call() -> None:
    result = PeriodImportPipeline().run(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        thinkorswim_trade_history=(
            f"{DATA}/thinkorswim/Account Trade History August 2026.csv"
        ),
        carried_opening_lot_book=_run_july().ending_lot_book,
        historical_trade_histories=(
            f"{DATA}/thinkorswim/Account Trade History July 2026.csv",
        ),
        historical_period_start=date(2026, 7, 1),
    )

    assert result.boundary_reconstruction.unresolved_positions == ()
    assert result.boundary_reconstruction.unresolved_campaigns == ()
    assert result.boundary_reconstruction.historical_requirements == ()

    lmt_lots = result.opening_lot_book.lots(Instrument("LMT"))

    assert len(lmt_lots) == 1

    lmt_equity = lmt_lots[0]

    assert lmt_equity.lot_id == "LOT-000036"
    assert lmt_equity.quantity == Decimal("100")
    assert lmt_equity.opened_at == datetime(2026, 7, 20, 10, 26, 3)

    lmt_call = OptionContract(
        underlying="LMT",
        expiration=date(2026, 8, 21),
        strike=Decimal("480"),
        option_type=OptionType.CALL,
    )

    lmt_call_lots = result.opening_lot_book.lots(lmt_call)

    assert len(lmt_call_lots) == 1

    lmt_call_lot = lmt_call_lots[0]

    assert lmt_call_lot.lot_id == "LOT-000035"
    assert lmt_call_lot.quantity == Decimal("-1")
    assert lmt_call_lot.opened_at == datetime(2026, 7, 20, 10, 26, 3)


def test_period_pipeline_reconstructs_august_ibm_assignment_covered_call() -> None:
    assignment_lines = tuple(
        open(f"{DATA}/schwab/august_assignments.txt")
        .read()
        .splitlines()
    )

    result = PeriodImportPipeline().run(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        thinkorswim_trade_history=(
            f"{DATA}/thinkorswim/Account Trade History August 2026.csv"
        ),
        carried_opening_lot_book=_run_july().ending_lot_book,
        historical_trade_histories=(
            f"{DATA}/thinkorswim/Account Trade History July 2026.csv",
        ),
        historical_period_start=date(2026, 7, 1),
        assignment_lines=(list(assignment_lines),),
    )

    ibm_lots = result.opening_lot_book.lots(Instrument("IBM"))

    assert len(ibm_lots) == 1

    ibm_equity = ibm_lots[0]

    assert ibm_equity.quantity == Decimal("100")
    assert ibm_equity.basis_total == Decimal("26595.00")
    assert ibm_equity.basis_source == "SCHWAB_POSITION_SNAPSHOT"
    assert ibm_equity.opened_at == datetime(2026, 6, 30)

    ibm_call = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("195"),
        option_type=OptionType.CALL,
    )

    ibm_call_lots = result.opening_lot_book.lots(ibm_call)

    assert len(ibm_call_lots) == 1

    ibm_call_lot = ibm_call_lots[0]

    assert ibm_call_lot.lot_id == "LOT-000018"
    assert ibm_call_lot.quantity == Decimal("-1")
    assert ibm_call_lot.opened_at == datetime(2026, 7, 17, 12, 30, 3)

    # Schwab's assignment and Thinkorswim's next-day EXP cash record
    # describe the same IBM share delivery. The pipeline must retain
    # only the assignment economic event.
    assert len(result.position_events) == 1

    event = result.position_events[0]

    assert event.kind == PositionEventKind.ASSIGNMENT
    assert event.occurred_at == datetime(2026, 8, 7)

    assert any(
        change.instrument == Instrument("IBM")
        and change.quantity == Decimal("-100")
        for change in event.changes
    )
