"""Directional analysis for option legs."""

from campaigniq.domain.directional_bias import DirectionalBias
from campaigniq.domain.leg import Leg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side


def option_leg_directional_bias(leg: Leg) -> DirectionalBias:
    """Return the directional character of an opening option leg."""

    if not isinstance(leg.instrument, OptionContract):
        raise TypeError("Leg instrument is not an option contract.")

    if leg.position_effect == PositionEffect.CLOSE:
        raise ValueError(
            "Directional bias of a closing leg depends on the "
            "position being closed."
        )

    if leg.instrument.option_type == OptionType.CALL:
        return (
            DirectionalBias.BULLISH
            if leg.side == Side.BUY
            else DirectionalBias.BEARISH
        )

    return (
        DirectionalBias.BEARISH
        if leg.side == Side.BUY
        else DirectionalBias.BULLISH
    )
