"""Analytics derived from authoritative position lifecycle history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from campaigniq.domain.position_lifecycle_transition import (
    PositionLifecycleTransition,
    PositionLifecycleTransitionKind,
)
from campaigniq.persistence.lifecycle_history import LifecycleHistory


@dataclass(frozen=True, slots=True)
class SymbolLifecycleSummary:
    """Lifecycle activity observed for one underlying symbol."""

    symbol: str
    transition_count: int
    corporate_action_count: int
    assignment_count: int
    covered_position_count: int
    roll_count: int
    exit_count: int
    first_transition_at: datetime
    last_transition_at: datetime
    transitions: tuple[PositionLifecycleTransition, ...]


@dataclass(frozen=True, slots=True)
class LifecycleAnalyticsSummary:
    """Cross-period lifecycle activity summary."""

    transition_count: int
    symbol_count: int
    corporate_action_count: int
    assignment_count: int
    covered_position_count: int
    roll_count: int
    exit_count: int
    first_transition_at: datetime | None
    last_transition_at: datetime | None
    symbols: tuple[SymbolLifecycleSummary, ...]


def _count_kind(
    transitions: Iterable[PositionLifecycleTransition],
    kind: PositionLifecycleTransitionKind,
) -> int:
    return sum(
        transition.kind is kind
        for transition in transitions
    )


def _symbol_summary(
    symbol: str,
    transitions: tuple[PositionLifecycleTransition, ...],
) -> SymbolLifecycleSummary:
    ordered = tuple(
        sorted(
            transitions,
            key=lambda transition: (
                transition.occurred_at,
                transition.kind.value,
            ),
        )
    )

    return SymbolLifecycleSummary(
        symbol=symbol,
        transition_count=len(ordered),
        corporate_action_count=_count_kind(
            ordered,
            PositionLifecycleTransitionKind.CORPORATE_ACTION,
        ),
        assignment_count=_count_kind(
            ordered,
            PositionLifecycleTransitionKind.ASSIGNMENT,
        ),
        covered_position_count=_count_kind(
            ordered,
            PositionLifecycleTransitionKind.COVERED_POSITION,
        ),
        roll_count=_count_kind(
            ordered,
            PositionLifecycleTransitionKind.ROLL,
        ),
        exit_count=_count_kind(
            ordered,
            PositionLifecycleTransitionKind.EXIT,
        ),
        first_transition_at=ordered[0].occurred_at,
        last_transition_at=ordered[-1].occurred_at,
        transitions=ordered,
    )


def summarize_lifecycle_history(
    history: LifecycleHistory,
) -> LifecycleAnalyticsSummary:
    """Summarize persisted lifecycle evidence without re-detecting events."""

    transitions = tuple(
        sorted(
            history.transitions,
            key=lambda transition: (
                transition.occurred_at,
                transition.symbol,
                transition.kind.value,
            ),
        )
    )

    if not transitions:
        return LifecycleAnalyticsSummary(
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

    by_symbol: dict[
        str,
        list[PositionLifecycleTransition],
    ] = {}

    for transition in transitions:
        by_symbol.setdefault(
            transition.symbol,
            [],
        ).append(transition)

    symbols = tuple(
        _symbol_summary(
            symbol,
            tuple(by_symbol[symbol]),
        )
        for symbol in sorted(by_symbol)
    )

    return LifecycleAnalyticsSummary(
        transition_count=len(transitions),
        symbol_count=len(symbols),
        corporate_action_count=_count_kind(
            transitions,
            PositionLifecycleTransitionKind.CORPORATE_ACTION,
        ),
        assignment_count=_count_kind(
            transitions,
            PositionLifecycleTransitionKind.ASSIGNMENT,
        ),
        covered_position_count=_count_kind(
            transitions,
            PositionLifecycleTransitionKind.COVERED_POSITION,
        ),
        roll_count=_count_kind(
            transitions,
            PositionLifecycleTransitionKind.ROLL,
        ),
        exit_count=_count_kind(
            transitions,
            PositionLifecycleTransitionKind.EXIT,
        ),
        first_transition_at=transitions[0].occurred_at,
        last_transition_at=transitions[-1].occurred_at,
        symbols=symbols,
    )
