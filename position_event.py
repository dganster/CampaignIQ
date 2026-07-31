from abc import ABC
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class PositionEvent(ABC):
    """
    An immutable business event affecting one or more Position Histories.
    """

    event_id: UUID
    occurred_at: datetime
