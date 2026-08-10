# TODO

# Current Trade assumes option-based trading.

# Generalize Trade to support all investment types.

from __future__ import annotations

from dataclasses import dataclass

from campaigniq.domain.directional_bias import DirectionalBias
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side




@dataclass(frozen=True, slots=True)
class Trade:
    """One completed trade consisting of one or more option legs."""
    def directional_bias(self) -> DirectionalBias:
        """Return the directional character of this trade."""

        opening_legs = [
            leg
            for leg in self.legs
            if leg.position_effect == PositionEffect.OPEN
        ]

        if not opening_legs:
            return DirectionalBias.NEUTRAL

        biases = {
            leg.directional_bias()
            for leg in opening_legs
        }

        if len(biases) == 1:
            return biases.pop()

        if len(self.legs) == 2:
            first, second = self.legs

            if (
                first.contract.underlying == second.contract.underlying
                and first.contract.expiration == second.contract.expiration
                and first.contract.option_type == second.contract.option_type
                and first.quantity == second.quantity
                and first.position_effect == PositionEffect.OPEN
                and second.position_effect == PositionEffect.OPEN
                and first.contract.strike != second.contract.strike
            ):
                lower, higher = sorted(
                    (first, second),
                    key=lambda leg: leg.contract.strike,
                )

                if first.contract.option_type == OptionType.CALL:
                    if lower.side == Side.BUY and higher.side == Side.SELL:
                        return DirectionalBias.BULLISH
                    if lower.side == Side.SELL and higher.side == Side.BUY:
                        return DirectionalBias.BEARISH

                if first.contract.option_type == OptionType.PUT:
                    if lower.side == Side.SELL and higher.side == Side.BUY:
                        return DirectionalBias.BULLISH
                    if lower.side == Side.BUY and higher.side == Side.SELL:
                        return DirectionalBias.BEARISH

        return DirectionalBias.NEUTRAL
    legs: tuple[OptionLeg, ...]
