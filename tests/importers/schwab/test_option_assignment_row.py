from datetime import date
from decimal import Decimal

from campaigniq.importers.schwab.option_assignment_row import (
    SchwabOptionAssignmentRow,
)


def test_option_assignment_row_holds_schwab_fields() -> None:
    row = SchwabOptionAssignmentRow(
        transaction_date=date(2026, 7, 20),
        trade_date=None,
        symbol="APD",
        expiration=date(2026, 7, 17),
        strike=Decimal("270"),
        option_type="CALL",
        quantity=Decimal("1"),
    )

    assert row.transaction_date == date(2026, 7, 20)
    assert row.symbol == "APD"
    assert row.expiration == date(2026, 7, 17)
    assert row.strike == Decimal("270")
    assert row.option_type == "CALL"
    assert row.quantity == Decimal("1")
