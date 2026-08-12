"""Domain representation of a trade."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from campaigniq.domain.directional_bias import DirectionalBias
from campaigniq.domain.leg import Leg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_directional_bias import (
    option_leg_directional_bias,
)
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side


@dataclass(frozen=True, slots=True)
class Trade:
    """One completed trade consisting of one or more legs."""

    legs: tuple[Leg, ...]

    def directional_bias(self) -> DirectionalBias:
        """Return the directional character of this trade."""

        opening_legs = [
            leg
            for leg in self.legs
            if leg.position_effect == PositionEffect.OPEN
        ]

        if not opening_legs:
            return DirectionalBias.NEUTRAL

        option_opening_legs = [
            leg
            for leg in opening_legs
            if isinstance(leg.instrument, OptionContract)
        ]

        if not option_opening_legs:
            return DirectionalBias.NEUTRAL

        biases = {
            option_leg_directional_bias(leg)
            for leg in option_opening_legs
        }

        if len(biases) == 1:
            return biases.pop()

        if len(self.legs) == 2:
            first, second = self.legs

            if not isinstance(first.instrument, OptionContract):
                return DirectionalBias.NEUTRAL

            if not isinstance(second.instrument, OptionContract):
                return DirectionalBias.NEUTRAL

            first_contract = first.instrument
            second_contract = second.instrument

            first_quantity = sum(
                (execution.quantity for execution in first.executions),
                Decimal("0"),
            )
            second_quantity = sum(
                (execution.quantity for execution in second.executions),
                Decimal("0"),
            )

            if (
                first_contract.underlying == second_contract.underlying
                and first_contract.expiration == second_contract.expiration
                and first_contract.option_type == second_contract.option_type
                and first_quantity == second_quantity
                and first.position_effect == PositionEffect.OPEN
                and second.position_effect == PositionEffect.OPEN
                and first_contract.strike != second_contract.strike
            ):
                lower, higher = sorted(
                    (first, second),
                    key=lambda leg: leg.instrument.strike,
                )

                lower_contract = lower.instrument
                higher_contract = higher.instrument

                if not isinstance(lower_contract, OptionContract):
                    return DirectionalBias.NEUTRAL

                if not isinstance(higher_contract, OptionContract):
                    return DirectionalBias.NEUTRAL

                if lower_contract.option_type == OptionType.CALL:
                    if lower.side == Side.BUY and higher.side == Side.SELL:
                        return DirectionalBias.BULLISH

                    if lower.side == Side.SELL and higher.side == Side.BUY:
                        return DirectionalBias.BEARISH

                if lower_contract.option_type == OptionType.PUT:
                    if lower.side == Side.SELL and higher.side == Side.BUY:
                        return DirectionalBias.BULLISH

                    if lower.side == Side.BUY and higher.side == Side.SELL:
                        return DirectionalBias.BEARISH

        return DirectionalBias.NEUTRAL
    