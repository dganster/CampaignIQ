from datetime import date, datetime
from decimal import Decimal

from campaigniq.importers.schwab.option_assignment import (
    SchwabOptionAssignment,
)


def test_schwab_option_assignment_records_assignment_details() -> None:
    assignment = SchwabOptionAssignment(
        occurred_at=datetime(2026, 7, 17, 0, 0),
        symbol="APD",
        expiration=date(2026, 7, 17),
        strike=Decimal("270"),
        option_type="CALL",
        quantity=Decimal("1"),
    )

    assert assignment.occurred_at == datetime(2026, 7, 17, 0, 0)
    assert assignment.symbol == "APD"
    assert assignment.expiration == date(2026, 7, 17)
    assert assignment.strike == Decimal("270")
    assert assignment.option_type == "CALL"
    assert assignment.quantity == Decimal("1")


def test_schwab_option_assignment_can_contain_multiple_contracts() -> None:
    assignment = SchwabOptionAssignment(
        occurred_at=datetime(2026, 7, 17, 0, 0),
        symbol="META",
        expiration=date(2026, 7, 17),
        strike=Decimal("530"),
        option_type="CALL",
        quantity=Decimal("5"),
    )

    assert assignment.quantity == Decimal("5")
