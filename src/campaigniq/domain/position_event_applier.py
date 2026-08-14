"""Apply economic position events to derived position state."""

from decimal import Decimal

from campaigniq.domain.position_book import PositionBook
from campaigniq.domain.position_event import PositionEvent
from campaigniq.domain.trade import Trade


class PositionEventApplier:
    """Apply trades and non-trade economic events to position state."""

    def __init__(self) -> None:
        self._book = PositionBook()

    def apply_trade(self, trade: Trade) -> None:
        """Apply one trade."""

        self._book.apply(trade)

    def apply_event(self, event: PositionEvent) -> None:
        """Apply all position changes in one economic event."""

        for change in event.changes:
            current = self._book.state(change.instrument)

            self._book.set_state(
                instrument=change.instrument,
                quantity=current.quantity + change.quantity,
                quantity_known=current.quantity_known,
                started_before_data=current.started_before_data,
            )

    def state(self, instrument):
        """Return the current position state."""

        return self._book.state(instrument)

    def quantity(self, instrument) -> Decimal:
        """Return the current position quantity."""

        return self._book.quantity(instrument)

    def started_before_data(self, instrument) -> bool:
        """Return whether the position started before available data."""

        return self._book.started_before_data(instrument)
