"""Read Thinkorswim expiration events from Cash Balance records."""

from __future__ import annotations

from datetime import date

from campaigniq.domain.position_event import PositionEvent
from campaigniq.importers.thinkorswim.cash_balance_event import (
    to_expiration_event,
)
from campaigniq.importers.thinkorswim.cash_balance_reader import (
    ThinkorswimCashBalanceReader,
)
from campaigniq.sources.thinkorswim.section import Section


class ThinkorswimExpirationEventReader:
    """Read EXP Cash Balance records as position events."""

    def __init__(
        self,
        cash_balance_reader: ThinkorswimCashBalanceReader,
    ) -> None:
        self._cash_balance_reader = cash_balance_reader

    def read(
        self,
        section: Section,
        *,
        start: date,
        end: date,
    ) -> list[PositionEvent]:
        """Read EXP records within a date range."""

        rows = self._cash_balance_reader.read(section)

        events: list[PositionEvent] = []

        for row in rows:
            if row.transaction_type != "EXP":
                continue

            if row.transaction_date < start:
                continue

            if row.transaction_date > end:
                continue

            events.append(to_expiration_event(row))

        return sorted(
            events,
            key=lambda event: event.occurred_at,
        )
