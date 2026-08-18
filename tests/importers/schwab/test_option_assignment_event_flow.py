from datetime import datetime
from decimal import Decimal

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.importers.schwab.option_assignment_flow import (
    read_option_assignment_events,
)


def test_section_becomes_position_events() -> None:
    lines = [
        "07/01",
        "Other Activity",
        "Option Assignment",
        "APD",
        "07/17/2026 270.00 C",
        "CALL AIR PRODS & CHEMS",
        "1.0000",
        "07/20",
        "Other Activity",
        "Option Assignment",
        "META",
        "07/17/2026 530.00 P",
        "PUT META PLATFORMS INC",
        "5.0000",
    ]

    events = read_option_assignment_events(lines)

    assert len(events) == 2

    assert events[0].kind == PositionEventKind.ASSIGNMENT
    assert events[0].occurred_at == datetime(2026, 7, 1)

    assert events[0].changes[0].instrument == OptionContract(
        underlying="APD",
        expiration=datetime(2026, 7, 17).date(),
        strike=Decimal("270"),
        option_type=OptionType.CALL,
    )

    assert events[0].changes[0].quantity == Decimal("1")

    assert events[1].kind == PositionEventKind.ASSIGNMENT
    assert events[1].occurred_at == datetime(2026, 7, 20)

    assert events[1].changes[0].instrument == OptionContract(
        underlying="META",
        expiration=datetime(2026, 7, 17).date(),
        strike=Decimal("530"),
        option_type=OptionType.PUT,
    )

    assert events[1].changes[0].quantity == Decimal("5")


def test_december_assignment_uses_contract_year_for_transaction_date() -> None:
    lines = [
        "12/04",
        "Other Activity",
        "Option Assignment",
        "LIN",
        "12/19/2025 455.00 P",
        "PUT LINDE PLC",
        "4.0000",
    ]

    events = read_option_assignment_events(lines)

    assert events[0].occurred_at == datetime(2025, 12, 4)
