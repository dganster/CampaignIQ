from datetime import date, datetime
from decimal import Decimal

from campaigniq.analytics.lifecycle_analytics import (
    LifecycleAnalyticsSummary,
    SymbolLifecycleSummary,
    summarize_lifecycle_history,
)
from campaigniq.domain.covered_position import CoveredCallPosition
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
from campaigniq.persistence.lifecycle_history import LifecycleHistory
from campaigniq.persistence.lifecycle_transition_store import (
    PersistedLifecycleTransitions,
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


def _covered(
    symbol: str,
    occurred_at: datetime,
) -> PositionLifecycleTransition:
    return PositionLifecycleTransition.from_covered_position(
        evidence=CoveredCallPosition(
            underlying=symbol,
            share_quantity=Decimal("100"),
            short_call_quantity=Decimal("1"),
            required_share_quantity=Decimal("100"),
            covered_call_quantity=Decimal("1"),
            uncovered_call_quantity=Decimal("0"),
            excess_share_quantity=Decimal("0"),
        ),
        occurred_at=occurred_at,
    )


def _history(
    transitions: tuple[PositionLifecycleTransition, ...],
) -> LifecycleHistory:
    period = PersistedLifecycleTransitions(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        transitions=transitions,
    )
    return LifecycleHistory(
        periods=(period,),
        transitions=transitions,
    )


def test_empty_history_returns_empty_summary() -> None:
    summary = summarize_lifecycle_history(
        LifecycleHistory(
            periods=(),
            transitions=(),
        )
    )

    assert summary == LifecycleAnalyticsSummary(
        transition_count=0,
        symbol_count=0,
        corporate_action_count=0,
        assignment_count=0,
        covered_position_count=0,
        roll_count=0,
        exit_count=0,
        first_transition_at=None,
        last_transition_at=None,
        symbols=(),
    )


def test_summarizes_overall_and_symbol_lifecycle_activity() -> None:
    nflx_covered = _covered(
        "NFLX",
        datetime(2026, 1, 1, 9),
    )
    aapl_exit = _exit(
        "AAPL",
        datetime(2026, 2, 5, 12),
    )
    nflx_exit = _exit(
        "NFLX",
        datetime(2026, 4, 16, 10, 52, 1),
    )

    # Deliberately unordered input proves analytics ordering is deterministic.
    summary = summarize_lifecycle_history(
        _history(
            (
                nflx_exit,
                aapl_exit,
                nflx_covered,
            )
        )
    )

    assert summary.transition_count == 3
    assert summary.symbol_count == 2
    assert summary.corporate_action_count == 0
    assert summary.assignment_count == 0
    assert summary.covered_position_count == 1
    assert summary.roll_count == 0
    assert summary.exit_count == 2
    assert summary.first_transition_at == datetime(2026, 1, 1, 9)
    assert summary.last_transition_at == datetime(
        2026,
        4,
        16,
        10,
        52,
        1,
    )

    assert tuple(item.symbol for item in summary.symbols) == (
        "AAPL",
        "NFLX",
    )

    aapl, nflx = summary.symbols

    assert aapl == SymbolLifecycleSummary(
        symbol="AAPL",
        transition_count=1,
        corporate_action_count=0,
        assignment_count=0,
        covered_position_count=0,
        roll_count=0,
        exit_count=1,
        first_transition_at=datetime(2026, 2, 5, 12),
        last_transition_at=datetime(2026, 2, 5, 12),
        transitions=(aapl_exit,),
    )

    assert nflx.transition_count == 2
    assert nflx.covered_position_count == 1
    assert nflx.exit_count == 1
    assert nflx.first_transition_at == datetime(2026, 1, 1, 9)
    assert nflx.last_transition_at == datetime(
        2026,
        4,
        16,
        10,
        52,
        1,
    )
    assert nflx.transitions == (
        nflx_covered,
        nflx_exit,
    )

def test_groups_normalized_symbols_deterministically() -> None:
    aapl_exit = _exit(
        "aapl",
        datetime(2026, 3, 2, 12),
    )
    msft_exit = _exit(
        "MSFT",
        datetime(2026, 3, 1, 12),
    )

    summary = summarize_lifecycle_history(
        _history(
            (
                aapl_exit,
                msft_exit,
            )
        )
    )

    assert tuple(
        item.symbol for item in summary.symbols
    ) == (
        "AAPL",
        "MSFT",
    )
    assert all(
        item.exit_count == 1
        for item in summary.symbols
    )
