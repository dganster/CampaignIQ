from datetime import date
from decimal import Decimal

from campaigniq.importers.schwab.option_assignment_section_reader import (
    read_option_assignment_section,
)


def test_reads_option_assignment_from_statement_lines() -> None:
    lines = [
        "07/20",
        "Other Activity",
        "Option Assignment",
        "APD",
        "07/17/2026 270.00 C",
        "CALL AIR PRODS & CHEMS",
        "1.0000",
    ]

    rows = read_option_assignment_section(lines)

    assert len(rows) == 1

    row = rows[0]

    assert row.transaction_date == date(2026, 7, 20)
    assert row.symbol == "APD"
    assert row.expiration == date(2026, 7, 17)
    assert row.strike == Decimal("270.00")
    assert row.option_type == "CALL"
    assert row.quantity == Decimal("1.0000")


def test_reads_multiple_option_assignments() -> None:
    lines = [
        "07/20",
        "Other Activity",
        "Option Assignment",
        "APD",
        "07/17/2026 270.00 C",
        "CALL AIR PRODS & CHEMS",
        "1.0000",
        "Option Assignment",
        "META",
        "07/17/2026 530.00 P",
        "PUT META PLATFORMS INC",
        "5.0000",
    ]

    rows = read_option_assignment_section(lines)

    assert len(rows) == 2

    assert rows[0].symbol == "APD"
    assert rows[0].quantity == Decimal("1")

    assert rows[1].symbol == "META"
    assert rows[1].expiration == date(2026, 7, 17)
    assert rows[1].strike == Decimal("530")
    assert rows[1].option_type == "PUT"
    assert rows[1].quantity == Decimal("5")
