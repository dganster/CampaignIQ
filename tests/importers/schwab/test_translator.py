from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.importers.schwab.option_assignment import (
    SchwabOptionAssignment,
)
from campaigniq.importers.schwab.translator import to_position_event


def test_option_assignment_translates_to_position_event() -> None:
    assignment = SchwabOptionAssignment(
        occurred_at=datetime(2026, 7, 17, 0, 0),
        symbol="APD",
        expiration=date(2026, 7, 17),
        strike=Decimal("270"),
        option_type="CALL",
        quantity=Decimal("1"),
    )

    event = to_position_event(assignment)

    expected_contract = OptionContract(
        underlying="APD",
        expiration=date(2026, 7, 17),
        strike=Decimal("270"),
        option_type=OptionType.CALL,
    )

    assert event.kind == PositionEventKind.ASSIGNMENT
    assert event.occurred_at == datetime(2026, 7, 17, 0, 0)
    assert len(event.changes) == 1
    assert event.changes[0].instrument == expected_contract
    assert event.changes[0].quantity == Decimal("1")


def test_put_assignment_translates_to_put_contract() -> None:
    assignment = SchwabOptionAssignment(
        occurred_at=datetime(2026, 7, 17, 0, 0),
        symbol="META",
        expiration=date(2026, 7, 17),
        strike=Decimal("530"),
        option_type="PUT",
        quantity=Decimal("5"),
    )

    event = to_position_event(assignment)

    change = event.changes[0]

    assert change.instrument == OptionContract(
        underlying="META",
        expiration=date(2026, 7, 17),
        strike=Decimal("530"),
        option_type=OptionType.PUT,
    )
    assert change.quantity == Decimal("5")
