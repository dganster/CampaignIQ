from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_exit import PositionExit
from campaigniq.domain.position_lifecycle_transition import (
    PositionLifecycleTransition,
    PositionLifecycleTransitionKind,
)
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.artifact_storage import (
    LocalFilesystemArtifactStorage,
)
from campaigniq.persistence.lifecycle_history import (
    load_lifecycle_history_from_storage,
)
from campaigniq.persistence.lifecycle_transition_store import (
    save_lifecycle_transitions_to_storage,
)
from campaigniq.persistence.monthly_publication import (
    ensure_publication_protocol_in_storage,
    publish_finalized_month_marker_to_storage,
)


def _exit(
    symbol: str,
    occurred_at: datetime,
) -> PositionLifecycleTransition:
    instrument = Instrument(symbol)
    leg = InstrumentLeg(
        instrument=instrument,
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        executions=(
            Execution(
                quantity=Decimal("-100"),
                execution_price=Decimal("10"),
                executed_at=occurred_at,
            ),
        ),
    )
    return PositionLifecycleTransition.from_position_exit(
        PositionExit(
            underlying=symbol,
            trade=Trade(legs=(leg,)),
            before_positions=((instrument, Decimal("100")),),
            after_positions=(),
        )
    )


def _save_month(
    storage,
    *,
    period_start: date,
    period_end: date,
    transitions,
) -> None:
    save_lifecycle_transitions_to_storage(
        storage,
        f"{period_end:%Y-%m}-lifecycle-transitions.json",
        period_start=period_start,
        period_end=period_end,
        transitions=transitions,
    )


def test_history_combines_periods_in_chronological_order(tmp_path):
    storage = LocalFilesystemArtifactStorage(tmp_path)

    march = _exit("MSFT", datetime(2026, 3, 20, 12))
    april = _exit("AAPL", datetime(2026, 4, 10, 12))

    # Persist deliberately out of chronological order.
    _save_month(
        storage,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        transitions=(april,),
    )
    _save_month(
        storage,
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        transitions=(march,),
    )

    history = load_lifecycle_history_from_storage(storage)

    assert tuple(
        period.period_end for period in history.periods
    ) == (
        date(2026, 3, 31),
        date(2026, 4, 30),
    )
    assert history.transitions == (march, april)


def test_history_ignores_unpublished_protected_month(tmp_path):
    storage = LocalFilesystemArtifactStorage(tmp_path)

    ensure_publication_protocol_in_storage(
        storage,
        first_period_end=date(2026, 4, 30),
    )

    march = _exit("MSFT", datetime(2026, 3, 20, 12))
    april = _exit("AAPL", datetime(2026, 4, 10, 12))

    _save_month(
        storage,
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        transitions=(march,),
    )
    _save_month(
        storage,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        transitions=(april,),
    )

    history = load_lifecycle_history_from_storage(storage)

    # March predates the cutover and retains legacy publication semantics.
    assert history.transitions == (march,)

    publish_finalized_month_marker_to_storage(
        storage,
        period_end=date(2026, 4, 30),
    )

    history = load_lifecycle_history_from_storage(storage)
    assert history.transitions == (march, april)


def test_history_filters_by_symbol_and_kind(tmp_path):
    storage = LocalFilesystemArtifactStorage(tmp_path)

    aapl = _exit("AAPL", datetime(2026, 4, 10, 12))
    msft = _exit("MSFT", datetime(2026, 4, 11, 12))

    _save_month(
        storage,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        transitions=(msft, aapl),
    )

    history = load_lifecycle_history_from_storage(storage)

    assert history.for_symbol("aapl") == (aapl,)
    assert history.of_kind(
        PositionLifecycleTransitionKind.EXIT
    ) == (aapl, msft)


def test_history_rejects_period_end_mismatch(tmp_path):
    storage = LocalFilesystemArtifactStorage(tmp_path)

    transition = _exit(
        "AAPL",
        datetime(2026, 4, 10, 12),
    )

    save_lifecycle_transitions_to_storage(
        storage,
        "2026-05-lifecycle-transitions.json",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        transitions=(transition,),
    )

    with pytest.raises(
        ValueError,
        match="period end does not match",
    ):
        load_lifecycle_history_from_storage(storage)


def test_empty_storage_returns_empty_history(tmp_path):
    storage = LocalFilesystemArtifactStorage(tmp_path)

    history = load_lifecycle_history_from_storage(storage)

    assert history.periods == ()
    assert history.transitions == ()

def test_history_rejects_period_start_mismatch(tmp_path):
    storage = LocalFilesystemArtifactStorage(tmp_path)

    transition = _exit(
        "AAPL",
        datetime(2026, 4, 10, 12),
    )

    save_lifecycle_transitions_to_storage(
        storage,
        "2026-04-lifecycle-transitions.json",
        period_start=date(2026, 3, 15),
        period_end=date(2026, 4, 30),
        transitions=(transition,),
    )

    with pytest.raises(
        ValueError,
        match="period start does not match",
    ):
        load_lifecycle_history_from_storage(storage)


def test_history_rejects_nested_lifecycle_artifact_key(tmp_path):
    storage = LocalFilesystemArtifactStorage(tmp_path)

    transition = _exit(
        "AAPL",
        datetime(2026, 4, 10, 12),
    )

    save_lifecycle_transitions_to_storage(
        storage,
        "archive/2026-04-lifecycle-transitions.json",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        transitions=(transition,),
    )

    with pytest.raises(
        ValueError,
        match="Invalid lifecycle artifact key",
    ):
        load_lifecycle_history_from_storage(storage)
