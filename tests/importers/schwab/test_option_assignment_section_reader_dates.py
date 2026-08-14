from datetime import date
from decimal import Decimal

from campaigniq.importers.schwab.option_assignment_section_reader import (
    read_option_assignment_section,
)


def test_assignments_use_their_preceding_transaction_dates() -> None:
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

    rows = read_option_assignment_section(lines)

    assert len(rows) == 2

    assert rows[0].transaction_date == date(2026, 7, 1)
    assert rows[0].symbol == "APD"
    assert rows[0].quantity == Decimal("1")

    assert rows[1].transaction_date == date(2026, 7, 20)
    assert rows[1].symbol == "META"
    assert rows[1].quantity == Decimal("5")
