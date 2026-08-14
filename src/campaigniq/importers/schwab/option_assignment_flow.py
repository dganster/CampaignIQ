"""Translate Schwab option-assignment sections into position events."""

from datetime import datetime

from campaigniq.domain.position_event import PositionEvent
from campaigniq.importers.schwab.option_assignment import (
    SchwabOptionAssignment,
)
from campaigniq.importers.schwab.option_assignment_section_reader import (
    read_option_assignment_section,
)
from campaigniq.importers.schwab.translator import to_position_event


def read_option_assignment_events(
    lines: list[str],
) -> list[PositionEvent]:
    """Read Schwab option assignments and translate them to domain events."""

    rows = read_option_assignment_section(lines)

    assignments = [
        SchwabOptionAssignment(
            occurred_at=datetime.combine(
                row.transaction_date,
                datetime.min.time(),
            ),
            symbol=row.symbol,
            expiration=row.expiration,
            strike=row.strike,
            option_type=row.option_type,
            quantity=row.quantity,
        )
        for row in rows
    ]

    return [
        to_position_event(assignment)
        for assignment in assignments
    ]
