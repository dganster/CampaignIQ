from datetime import date, datetime
from pathlib import Path

from campaigniq.domain.position_lifecycle_transition import (
    PositionLifecycleTransitionKind,
)
from campaigniq.import_pipeline import PeriodImportPipeline


DATA = Path("tests/data")

MARCH = DATA / "thinkorswim/Account Trade History March 2026.csv"
APRIL = DATA / "thinkorswim/Account Trade History April 2026.csv"
MARCH_POSITIONS = DATA / "schwab/march_positions.txt"
APRIL_REALIZED = DATA / "schwab/april_realized_gain_loss.txt"


def _april_result():
    return PeriodImportPipeline().run(
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        thinkorswim_trade_history=APRIL,
        opening_snapshot=MARCH_POSITIONS,
        opening_snapshot_at=datetime(
            2026,
            2,
            28,
            23,
            59,
            59,
        ),
        realized_gain_loss_report=APRIL_REALIZED,
        historical_trade_histories=(MARCH,),
        historical_period_start=date(2026, 3, 1),
    )


def test_pipeline_exposes_lifecycle_transitions() -> None:
    result = _april_result()

    assert isinstance(result.lifecycle_transitions, tuple)


def test_april_pipeline_contains_real_nflx_exit() -> None:
    result = _april_result()

    nflx = tuple(
        transition
        for transition in result.lifecycle_transitions
        if transition.symbol == "NFLX"
    )

    assert len(nflx) == 1

    transition = nflx[0]

    assert transition.kind is PositionLifecycleTransitionKind.EXIT
    assert transition.occurred_at == datetime(
        2026,
        4,
        16,
        10,
        52,
        1,
    )

    assert transition.position_exit is not None
    assert transition.position_exit.after_positions == ()


def test_april_pipeline_does_not_reemit_historical_march_rolls() -> None:
    result = _april_result()

    nflx_rolls = tuple(
        transition
        for transition in result.lifecycle_transitions
        if (
            transition.symbol == "NFLX"
            and transition.kind is PositionLifecycleTransitionKind.ROLL
        )
    )

    assert nflx_rolls == ()
