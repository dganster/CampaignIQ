from datetime import datetime
from decimal import Decimal

import pytest

from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.value_objects.instrument import Instrument


def test_position_event_records_an_assignment() -> None:
    event = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=Instrument("ADP"),
                quantity=Decimal("100"),
            ),
        ),
        occurred_at=datetime(2026, 7, 1, 0, 0),
    )

    assert event.kind == PositionEventKind.ASSIGNMENT
    assert event.changes[0].instrument == Instrument("ADP")
    assert event.changes[0].quantity == Decimal("100")
    assert event.occurred_at == datetime(2026, 7, 1, 0, 0)


def test_assignment_can_change_multiple_positions() -> None:
    option = Instrument("ADP 2026-07-17 200 PUT")
    stock = Instrument("ADP")

    event = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=option,
                quantity=Decimal("2"),
            ),
            PositionChange(
                instrument=stock,
                quantity=Decimal("200"),
            ),
        ),
        occurred_at=datetime(2026, 7, 1, 0, 0),
    )

    assert len(event.changes) == 2
    assert event.changes[0].quantity == Decimal("2")
    assert event.changes[1].quantity == Decimal("200")


def test_position_event_requires_at_least_one_change() -> None:
    with pytest.raises(
        ValueError,
        match="at least one position change",
    ):
        PositionEvent(
            kind=PositionEventKind.ASSIGNMENT,
            changes=(),
            occurred_at=datetime(2026, 7, 1, 0, 0),
        )
