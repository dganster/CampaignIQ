"""Explicit transitions in the economic lifecycle of a position."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from campaigniq.domain.corporate_action import CorporateActionEvidence
from campaigniq.domain.covered_position import CoveredCallPosition
from campaigniq.domain.option_roll import OptionRoll
from campaigniq.domain.position_event import PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_exit import PositionExit


class PositionLifecycleTransitionKind(str, Enum):
    """Classify a meaningful transition in economic position state."""

    CORPORATE_ACTION = "CORPORATE_ACTION"
    ASSIGNMENT = "ASSIGNMENT"
    COVERED_POSITION = "COVERED_POSITION"
    ROLL = "ROLL"
    EXIT = "EXIT"


@dataclass(frozen=True, slots=True)
class PositionLifecycleTransition:
    """One evidence-backed transition in a position lifecycle.

    This object classifies evidence that already exists elsewhere in
    CampaignIQ.  It does not replace broker trades, position events, corporate
    action evidence, lots, campaigns, or realized P&L.

    Exactly one specialized evidence field is required for transition kinds
    whose authoritative evidence already has a domain representation:

    * CORPORATE_ACTION -> CorporateActionEvidence
    * ASSIGNMENT       -> PositionEvent(kind=ASSIGNMENT)
    * COVERED_POSITION -> CoveredCallPosition
    * ROLL             -> OptionRoll
    * EXIT             -> PositionExit
    """

    kind: PositionLifecycleTransitionKind
    symbol: str
    occurred_at: datetime
    corporate_action: CorporateActionEvidence | None = None
    position_event: PositionEvent | None = None
    covered_position: CoveredCallPosition | None = None
    option_roll: OptionRoll | None = None
    position_exit: PositionExit | None = None

    def __post_init__(self) -> None:
        normalized_symbol = self.symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("Lifecycle transition symbol must not be empty.")
        object.__setattr__(self, "symbol", normalized_symbol)

        if self.kind is PositionLifecycleTransitionKind.CORPORATE_ACTION:
            if self.corporate_action is None:
                raise ValueError(
                    "CORPORATE_ACTION transition requires corporate-action "
                    "evidence."
                )
            if (
                self.position_event is not None
                or self.covered_position is not None
                or self.option_roll is not None
                or self.position_exit is not None
            ):
                raise ValueError(
                    "CORPORATE_ACTION transition must not contain other "
                    "specialized evidence."
                )
            if self.corporate_action.symbol != normalized_symbol:
                raise ValueError(
                    "Corporate-action evidence symbol must match lifecycle "
                    "transition symbol."
                )
            if self.corporate_action.effective_date != self.occurred_at.date():
                raise ValueError(
                    "Corporate-action effective date must match lifecycle "
                    "transition date."
                )
            return

        if self.kind is PositionLifecycleTransitionKind.ASSIGNMENT:
            if self.position_event is None:
                raise ValueError(
                    "ASSIGNMENT transition requires a position event."
                )
            if (
                self.corporate_action is not None
                or self.covered_position is not None
                or self.option_roll is not None
                or self.position_exit is not None
            ):
                raise ValueError(
                    "ASSIGNMENT transition must not contain other "
                    "specialized evidence."
                )
            if self.position_event.kind is not PositionEventKind.ASSIGNMENT:
                raise ValueError(
                    "ASSIGNMENT transition requires an ASSIGNMENT "
                    "PositionEvent."
                )
            if self.position_event.occurred_at != self.occurred_at:
                raise ValueError(
                    "Assignment event timestamp must match lifecycle "
                    "transition timestamp."
                )

            event_symbols = {
                getattr(change.instrument, "underlying", None)
                or getattr(change.instrument, "symbol", None)
                for change in self.position_event.changes
            }
            event_symbols.discard(None)

            if normalized_symbol not in event_symbols:
                raise ValueError(
                    "Assignment event must affect the lifecycle transition "
                    "symbol."
                )
            return

        if self.kind is PositionLifecycleTransitionKind.COVERED_POSITION:
            if self.covered_position is None:
                raise ValueError(
                    "COVERED_POSITION transition requires covered-position "
                    "evidence."
                )
            if (
                self.corporate_action is not None
                or self.position_event is not None
                or self.option_roll is not None
                or self.position_exit is not None
            ):
                raise ValueError(
                    "COVERED_POSITION transition must not contain other "
                    "specialized evidence."
                )
            if self.covered_position.underlying.upper() != normalized_symbol:
                raise ValueError(
                    "Covered-position evidence underlying must match "
                    "lifecycle transition symbol."
                )
            return

        if self.kind is PositionLifecycleTransitionKind.ROLL:
            if self.option_roll is None:
                raise ValueError(
                    "ROLL transition requires option-roll evidence."
                )
            if (
                self.corporate_action is not None
                or self.position_event is not None
                or self.covered_position is not None
                or self.position_exit is not None
            ):
                raise ValueError(
                    "ROLL transition must not contain other specialized "
                    "evidence."
                )
            if self.option_roll.underlying.upper() != normalized_symbol:
                raise ValueError(
                    "Option-roll evidence underlying must match lifecycle "
                    "transition symbol."
                )

            roll_times = [
                execution.executed_at
                for leg in (
                    self.option_roll.closed_leg,
                    self.option_roll.opened_leg,
                )
                for execution in leg.executions
            ]
            if not roll_times:
                raise ValueError(
                    "Option-roll evidence must contain executions."
                )
            if min(roll_times) != self.occurred_at:
                raise ValueError(
                    "Option-roll timestamp must match lifecycle transition "
                    "timestamp."
                )
            return

        if self.kind is PositionLifecycleTransitionKind.EXIT:
            if (
                self.corporate_action is not None
                or self.position_event is not None
                or self.covered_position is not None
                or self.option_roll is not None
            ):
                raise ValueError(
                    "EXIT transition must not contain other specialized "
                    "evidence."
                )
            if self.position_exit is None:
                raise ValueError(
                    "EXIT transition requires position-exit evidence."
                )
            if self.position_exit.underlying.upper() != normalized_symbol:
                raise ValueError(
                    "Position-exit evidence underlying must match lifecycle "
                    "transition symbol."
                )

            exit_times = [
                execution.executed_at
                for leg in self.position_exit.trade.legs
                for execution in leg.executions
            ]
            if not exit_times:
                raise ValueError(
                    "Position-exit evidence trade must contain executions."
                )

            if min(exit_times) != self.occurred_at:
                raise ValueError(
                    "Position-exit trade timestamp must match lifecycle "
                    "transition timestamp."
                )
            return

        if (
            self.corporate_action is not None
            or self.position_event is not None
            or self.covered_position is not None
            or self.option_roll is not None
            or self.position_exit is not None
        ):
            raise ValueError(
                f"{self.kind.value} transition does not yet accept "
                "specialized evidence."
            )

    @classmethod
    def from_corporate_action(
        cls,
        evidence: CorporateActionEvidence,
    ) -> "PositionLifecycleTransition":
        """Classify authoritative corporate-action evidence."""

        return cls(
            kind=PositionLifecycleTransitionKind.CORPORATE_ACTION,
            symbol=evidence.symbol,
            occurred_at=datetime.combine(
                evidence.effective_date,
                datetime.min.time(),
            ),
            corporate_action=evidence,
        )

    @classmethod
    def from_covered_position(
        cls,
        *,
        evidence: CoveredCallPosition,
        occurred_at: datetime,
    ) -> "PositionLifecycleTransition":
        """Classify authoritative covered-position state."""
        return cls(
            kind=PositionLifecycleTransitionKind.COVERED_POSITION,
            symbol=evidence.underlying,
            occurred_at=occurred_at,
            covered_position=evidence,
        )

    @classmethod
    def from_option_roll(
        cls,
        evidence: OptionRoll,
    ) -> "PositionLifecycleTransition":
        """Classify an authoritative same-trade option roll."""
        execution_times = [
            execution.executed_at
            for leg in (evidence.closed_leg, evidence.opened_leg)
            for execution in leg.executions
        ]
        if not execution_times:
            raise ValueError(
                "Option-roll evidence must contain executions."
            )

        return cls(
            kind=PositionLifecycleTransitionKind.ROLL,
            symbol=evidence.underlying,
            occurred_at=min(execution_times),
            option_roll=evidence,
        )

    @classmethod
    def from_position_exit(
        cls,
        evidence: PositionExit,
    ) -> "PositionLifecycleTransition":
        """Classify an authoritative complete-position exit."""
        execution_times = [
            execution.executed_at
            for leg in evidence.trade.legs
            for execution in leg.executions
        ]

        if not execution_times:
            raise ValueError(
                "Position-exit evidence trade must contain executions."
            )

        return cls(
            kind=PositionLifecycleTransitionKind.EXIT,
            symbol=evidence.underlying,
            occurred_at=min(execution_times),
            position_exit=evidence,
        )

    @classmethod
    def from_assignment(
        cls,
        *,
        symbol: str,
        event: PositionEvent,
    ) -> "PositionLifecycleTransition":
        """Classify an existing CampaignIQ assignment position event."""

        return cls(
            kind=PositionLifecycleTransitionKind.ASSIGNMENT,
            symbol=symbol,
            occurred_at=event.occurred_at,
            position_event=event,
        )
