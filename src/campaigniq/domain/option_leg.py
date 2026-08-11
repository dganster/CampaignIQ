"""Domain representation of an option leg."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from campaigniq.domain.directional_bias import DirectionalBias
from campaigniq.domain.execution import Execution
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side


@dataclass(frozen=True, slots=True)
class OptionLeg:
    """One leg of an option trade."""

    contract: OptionContract
    side: Side
    position_effect: PositionEffect
    executions: tuple[Execution, ...]
    broker_strategy: str

    @property
    def instrument(self) -> OptionContract:
        """Return the option contract as the generic instrument."""

        return self.contract

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
                "Option leg must contain at least one execution."
            )

        return min(
            execution.executed_at
            for execution in self.executions
        )

    def directional_bias(self) -> DirectionalBias:
        """Return the directional character of this option leg."""

        if self.position_effect == PositionEffect.CLOSE:
            raise ValueError(
                "Directional bias of a closing leg depends on the "
                "position being closed."
            )

        if self.contract.option_type == OptionType.CALL:
            return (
                DirectionalBias.BULLISH
                if self.side == Side.BUY
                else DirectionalBias.BEARISH
            )

        return (
            DirectionalBias.BEARISH
            if self.side == Side.BUY
            else DirectionalBias.BULLISH
        )
    