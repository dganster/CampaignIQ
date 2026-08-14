from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.importers.schwab.option_assignment_reader import (
    read_option_assignment,
)


def test_reads_apd_option_assignment() -> None:
    assignment = read_option_assignment(
        date_value="07/20/2026",
        symbol="APD",
        contract="07/17/2026 270.00 C",
        quantity="1.0000",
    )

    assert assignment.occurred_at == datetime(2026, 7, 20)
    assert assignment.symbol == "APD"
    assert assignment.expiration == date(2026, 7, 17)
    assert assignment.strike == Decimal("270.00")
    assert assignment.option_type == "CALL"
    assert assignment.quantity == Decimal("1.0000")


def test_reads_multiple_contract_meta_assignment() -> None:
    assignment = read_option_assignment(
        date_value="07/17/2026",
        symbol="META",
        contract="07/17/2026 530.00 P",
        quantity="5.0000",
    )

    assert assignment.symbol == "META"
    assert assignment.strike == Decimal("530.00")
    assert assignment.option_type == "PUT"
    assert assignment.quantity == Decimal("5.0000")


def test_rejects_unknown_option_type() -> None:
    with pytest.raises(ValueError, match="Unsupported option type"):
        read_option_assignment(
            date_value="07/17/2026",
            symbol="APD",
            contract="07/17/2026 270.00 X",
            quantity="1.0000",
        )
