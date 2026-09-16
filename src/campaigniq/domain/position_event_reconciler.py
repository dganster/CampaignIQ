"""Reconcile overlapping economic position events."""

from __future__ import annotations

from datetime import date, timedelta

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_event import PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind


class PositionEventReconciler:
    """Remove duplicate broker representations of one economic event."""

    def reconcile(
        self,
        events: tuple[PositionEvent, ...],
        *,
        corroborating_assignments: tuple[PositionEvent, ...] = (),
    ) -> tuple[PositionEvent, ...]:
        """Return events with duplicate assignment equity effects removed."""

        assignments = (
            *(
                event
                for event in events
                if event.kind == PositionEventKind.ASSIGNMENT
            ),
            *corroborating_assignments,
        )

        reconciled: list[PositionEvent] = []

        for event in events:
            if (
                event.kind == PositionEventKind.EXPIRATION
                and self._duplicates_assignment_equity(
                    event,
                    assignments,
                )
            ):
                continue

            reconciled.append(event)

        return tuple(
            sorted(
                reconciled,
                key=lambda event: event.occurred_at,
            )
        )

    def _duplicates_assignment_equity(
        self,
        expiration: PositionEvent,
        assignments: tuple[PositionEvent, ...],
    ) -> bool:
        """Whether an expiration duplicates an assignment's equity leg."""

        if len(expiration.changes) != 1:
            return False

        expiration_change = expiration.changes[0]

        if isinstance(
            expiration_change.instrument,
            OptionContract,
        ):
            return False

        expiration_date = expiration.occurred_at.date()

        for assignment in assignments:
            assignment_date = assignment.occurred_at.date()

            if (
                expiration_date
                not in (
                    assignment_date,
                    assignment_date + timedelta(days=1),
                )
                and self._next_business_day(expiration_date)
                != assignment_date
            ):
                continue

            for change in assignment.changes:
                if isinstance(change.instrument, OptionContract):
                    continue

                if change.instrument != expiration_change.instrument:
                    continue

                if change.quantity != expiration_change.quantity:
                    continue

                return True

        return False

    @staticmethod
    def _next_business_day(value: date) -> date:
        """Return the next Monday-Friday date after value."""
        candidate = value + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate
