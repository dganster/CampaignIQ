"""A single option leg."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.directional_bias import DirectionalBias
from campaigniq.domain.option_type import OptionType


@dataclass(frozen=True, slots=True)
class OptionLeg:
    """One executed option leg."""

    contract: OptionContract
    side: Side
    position_effect: PositionEffect
    quantity: Decimal
    execution_price: Decimal
    executed_at: datetime
    broker_strategy: str

    def directional_bias(self) -> DirectionalBias:
        """Return the directional character of this option leg."""

        if self.position_effect == PositionEffect.CLOSE:
            raise ValueError(
                "Directional bias of a closing leg depends on the position being closed."
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