from datetime import datetime
from decimal import Decimal

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.schwab.option_assignment_reader import (
    read_option_assignment,
)
from campaigniq.importers.schwab.translator import to_position_event


def test_schwab_assignment_becomes_position_event() -> None:
    assignment = read_option_assignment(
        date_value="07/20/2026",
        symbol="APD",
        contract="07/17/2026 270.00 C",
        quantity="1.0000",
    )

    event = to_position_event(assignment)

    assert event.kind == PositionEventKind.ASSIGNMENT
    assert event.occurred_at == datetime(2026, 7, 20)

    change = event.changes[0]

    assert change.instrument == OptionContract(
        underlying="APD",
        expiration=assignment.expiration,
        strike=Decimal("270"),
        option_type=OptionType.CALL,
    )
    assert change.quantity == Decimal("1")
