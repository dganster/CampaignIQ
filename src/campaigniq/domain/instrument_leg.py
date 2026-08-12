"""Domain representation of an equity/instrument leg."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.value_objects.instrument import Instrument


@dataclass(frozen=True, slots=True)
class InstrumentLeg:
    """One leg of a trade involving an equity instrument."""

    instrument: Instrument
    side: Side
    position_effect: PositionEffect
    executions: tuple[Execution, ...]

    @property
    def quantity(self) -> Decimal:
        """Return the total quantity represented by the executions."""

        return sum(
            (execution.quantity for execution in self.executions),
            Decimal("0"),
        )

    @property
    def execution_price(self) -> Decimal:
        """Return the quantity-weighted average execution price."""

        total_quantity = self.quantity

        if total_quantity == 0:
            return Decimal("0")

        total_value = sum(
            (
                execution.quantity * execution.execution_price
                for execution in self.executions
            ),
            Decimal("0"),
        )

        return total_value / total_quantity

    @property
    def executed_at(self) -> datetime:
        """Return the time of the earliest execution."""

        if not self.executions:
            raise ValueError(
                "Instrument leg must contain at least one execution."
            )

        return min(
            execution.executed_at
            for execution in self.executions
        )
