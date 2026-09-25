"""Assemble evidence-backed position lifecycle transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from campaigniq.domain.corporate_action import CorporateActionEvidence
from campaigniq.domain.covered_position import CoveredCallPosition
from campaigniq.domain.option_roll import OptionRoll
from campaigniq.domain.position_event import PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_exit import PositionExit
from campaigniq.domain.position_lifecycle_transition import (
    PositionLifecycleTransition,
)


@dataclass(frozen=True, slots=True)
class ObservedCoveredPosition:
    """A covered-position observation with its authoritative timestamp."""

    position: CoveredCallPosition
    observed_at: datetime


class PositionLifecycleAssembler:
    """
    Assemble already-established evidence into one ordered lifecycle.

    This class does not detect corporate actions, assignments, covered
    positions, rolls, or exits.  Those facts must already have been
    established by their authoritative domain mechanisms.
    """

    def assemble(
        self,
        *,
        symbol: str,
        corporate_actions: tuple[CorporateActionEvidence, ...] = (),
        assignments: tuple[PositionEvent, ...] = (),
        covered_positions: tuple[ObservedCoveredPosition, ...] = (),
        option_rolls: tuple[OptionRoll, ...] = (),
        position_exits: tuple[PositionExit, ...] = (),
    ) -> tuple[PositionLifecycleTransition, ...]:
        normalized_symbol = symbol.strip().upper()

        if not normalized_symbol:
            raise ValueError(
                "Lifecycle assembly symbol must not be empty."
            )

        transitions: list[PositionLifecycleTransition] = []

        for evidence in corporate_actions:
            if evidence.symbol.upper() != normalized_symbol:
                continue

            transitions.append(
                PositionLifecycleTransition.from_corporate_action(
                    evidence
                )
            )

        for event in assignments:
            if event.kind is not PositionEventKind.ASSIGNMENT:
                continue

            event_symbols = {
                getattr(change.instrument, "underlying", None)
                or getattr(change.instrument, "symbol", None)
                for change in event.changes
            }
            event_symbols = {
                event_symbol.strip().upper()
                for event_symbol in event_symbols
                if event_symbol
            }

            if normalized_symbol not in event_symbols:
                continue

            transitions.append(
                PositionLifecycleTransition.from_assignment(
                    symbol=normalized_symbol,
                    event=event,
                )
            )

        for observation in covered_positions:
            if (
                observation.position.underlying.upper()
                != normalized_symbol
            ):
                continue

            transitions.append(
                PositionLifecycleTransition.from_covered_position(
                    evidence=observation.position,
                    occurred_at=observation.observed_at,
                )
            )

        for evidence in option_rolls:
            if evidence.underlying.upper() != normalized_symbol:
                continue

            transitions.append(
                PositionLifecycleTransition.from_option_roll(
                    evidence
                )
            )

        for evidence in position_exits:
            if evidence.underlying.upper() != normalized_symbol:
                continue

            transitions.append(
                PositionLifecycleTransition.from_position_exit(
                    evidence
                )
            )

        return tuple(
            sorted(
                transitions,
                key=lambda transition: transition.occurred_at,
            )
        )
