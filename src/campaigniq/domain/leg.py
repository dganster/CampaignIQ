"""A generic trade leg."""

from dataclasses import dataclass

from campaigniq.domain.execution import Execution
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.value_objects.instrument import Instrument


@dataclass(frozen=True, slots=True)
class Leg:
    """One component of a trade."""

    instrument: Instrument
    side: Side
    position_effect: PositionEffect
    executions: tuple[Execution, ...]
    