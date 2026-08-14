"""Chronological economic history affecting positions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from campaigniq.domain.position_event import PositionEvent
from campaigniq.domain.position_event_applier import PositionEventApplier
from campaigniq.domain.trade import Trade


@dataclass(frozen=True, slots=True)
class PositionHistoryItem:
    """One chronological event affecting position state."""

    occurred_at: datetime
    trade: Trade | None = None
    event: PositionEvent | None = None

    def __post_init__(self) -> None:
        if (self.trade is None) == (self.event is None):
            raise ValueError(
                "PositionHistoryItem must contain exactly one "
                "trade or event."
            )


class PositionHistory:
    """Chronological economic history affecting positions."""

    def __init__(self) -> None:
        self._items: list[PositionHistoryItem] = []

    def add_trade(self, trade: Trade) -> None:
        """Add one trade to the history."""

        occurred_at = min(
            execution.executed_at
            for leg in trade.legs
            for execution in leg.executions
        )

        self._items.append(
            PositionHistoryItem(
                occurred_at=occurred_at,
                trade=trade,
            )
        )

    def add_event(self, event: PositionEvent) -> None:
        """Add one position event to the history."""

        self._items.append(
            PositionHistoryItem(
                occurred_at=event.occurred_at,
                event=event,
            )
        )

    def items(self) -> tuple[PositionHistoryItem, ...]:
        """Return history items in chronological order."""

        return tuple(
            sorted(
                self._items,
                key=lambda item: item.occurred_at,
            )
        )

    def apply(self, applier: PositionEventApplier) -> None:
        """Apply the history to position state in chronological order."""

        for item in self.items():
            if item.trade is not None:
                applier.apply_trade(item.trade)
            else:
                assert item.event is not None
                applier.apply_event(item.event)
