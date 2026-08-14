"""Derived state of one instrument position."""

from dataclasses import dataclass
from decimal import Decimal

from campaigniq.domain.value_objects.instrument import Instrument


@dataclass(frozen=True, slots=True)
class PositionState:
    """Current derived state for one instrument."""

    instrument: Instrument
    quantity: Decimal
    quantity_known: bool
    started_before_data: bool

