"""An economic activity affecting a position."""

from dataclasses import dataclass

from campaigniq.domain.position_activity_kind import PositionActivityKind
from campaigniq.domain.trade import Trade


@dataclass(frozen=True, slots=True)
class PositionActivity:
    """A group of trades representing one position activity."""

    trades: tuple[Trade, ...]
    kind: PositionActivityKind

    def __post_init__(self) -> None:
        if not self.trades:
            raise ValueError(
                "PositionActivity must contain at least one trade."
            )

        underlyings = {
            leg.instrument.underlying
            for trade in self.trades
            for leg in trade.legs
        }

        if len(underlyings) > 1:
            raise ValueError(
                "All trades in a PositionActivity must belong to "
                "the same underlying."
            )
