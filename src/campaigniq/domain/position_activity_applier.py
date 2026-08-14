"""Apply position activities to a position book."""

from campaigniq.domain.position_activity import PositionActivity
from campaigniq.domain.position_book import PositionBook
from campaigniq.domain.trade import Trade


class PositionActivityApplier:
    """Apply position activities using PositionBook as the state engine."""

    def __init__(self) -> None:
        self._book = PositionBook()

    def apply(self, activity: PositionActivity) -> None:
        """Apply all trades in a position activity."""

        for trade in activity.trades:
            self._book.apply(trade)

    def apply_trade(self, trade: Trade) -> None:
        """Apply one trade directly."""

        self._book.apply(trade)

    def state(self, instrument):
        """Return the current position state."""

        return self._book.state(instrument)

    def quantity(self, instrument):
        """Return the current position quantity."""

        return self._book.quantity(instrument)

    def started_before_data(self, instrument):
        """Return whether the position started before available data."""

        return self._book.started_before_data(instrument)
