"""An economic event affecting one or more positions."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.value_objects.instrument import Instrument


@dataclass(frozen=True, slots=True)
class PositionChange:
    """A signed change to one instrument position."""

    instrument: Instrument
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class PositionEvent:
    """One economic event that changes one or more positions."""

    kind: PositionEventKind
    changes: tuple[PositionChange, ...]
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not self.changes:
            raise ValueError(
                "PositionEvent must contain at least one position change."
            )
