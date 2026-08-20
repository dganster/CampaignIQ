from datetime import date, time
from decimal import Decimal

from campaigniq.importers.thinkorswim.cash_balance_row import (
    ThinkorswimCashBalanceRow,
)
from campaigniq.importers.thinkorswim.csv_columns import CsvColumns


def test_stores_cash_balance_row_fields() -> None:
    row = ThinkorswimCashBalanceRow(
        transaction_date=date(2026, 3, 7),
        transaction_time=time(2, 4, 21),
        transaction_type="EXP",
        reference='="113827559387"',
        description="SOLD -500.0 UNH UPON UNITEDHEALTH GROUP INC",
        misc_fees=Decimal("-0.10"),
        commissions_and_fees=None,
        amount=Decimal("125000.00"),
        balance=Decimal("311575.07"),
    )

    assert row.transaction_date == date(2026, 3, 7)
    assert row.transaction_time == time(2, 4, 21)
    assert row.transaction_type == "EXP"
    assert row.reference == '="113827559387"'
    assert row.description == (
        "SOLD -500.0 UNH UPON UNITEDHEALTH GROUP INC"
    )
    assert row.misc_fees == Decimal("-0.10")
    assert row.commissions_and_fees is None
    assert row.amount == Decimal("125000.00")
    assert row.balance == Decimal("311575.07")


def test_creates_row_from_csv() -> None:
    header = [
        "DATE",
        "TIME",
        "TYPE",
        "REF #",
        "DESCRIPTION",
        "Misc Fees",
        "Commissions & Fees",
        "AMOUNT",
        "BALANCE",
    ]

    columns = CsvColumns(header)

    csv_row = [
        "3/7/26",
        "02:04:21",
        "EXP",
        '="113827559387"',
        "SOLD -500.0 UNH UPON UNITEDHEALTH GROUP INC",
        "-0.10",
        "",
        "125,000.00",
        "311,575.07",
    ]

    row = ThinkorswimCashBalanceRow.from_csv(
        columns,
        csv_row,
    )

    assert row.transaction_date == date(2026, 3, 7)
    assert row.transaction_type == "EXP"
    assert row.description.startswith("SOLD -500.0 UNH")
    assert row.amount == Decimal("125000.00")
    assert row.balance == Decimal("311575.07")
    