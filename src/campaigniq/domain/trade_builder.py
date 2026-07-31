"""Build Trade objects from option legs."""

from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.trade import Trade


class TradeBuilder:
    """Build trades from one or more legs."""

    def build(self, legs: list[OptionLeg]) -> list[Trade]:
        return [Trade(legs=(leg,)) for leg in legs]
