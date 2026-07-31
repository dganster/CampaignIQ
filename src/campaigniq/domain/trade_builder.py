"""Build Trade objects from option legs."""

from campaigniq.domain.leg import Leg
from campaigniq.domain.trade import Trade


class TradeBuilder:
    """Build trades from one or more legs."""

    def build(self, legs: list[Leg]) -> list[Trade]:
        return [Trade(legs=(leg,)) for leg in legs]
