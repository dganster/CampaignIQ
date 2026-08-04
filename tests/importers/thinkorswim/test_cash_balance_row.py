from datetime import date, time

from campaigniq.importers.thinkorswim.cash_balance_row import (
    ThinkorswimCashBalanceRow,
)


def test_stores_cash_balance_row_fields() -> None:
    row = ThinkorswimCashBalanceRow(
        transaction_date=date(2026, 1, 10),
        transaction_time=time(1, 15, 58),
        transaction_type="EXP",
    )

    assert row.transaction_date == date(2026, 1, 10)
    assert row.transaction_time == time(1, 15, 58)
    assert row.transaction_type == "EXP"

from campaigniq.importers.thinkorswim.csv_columns import CsvColumns


def test_creates_row_from_csv() -> None:
    header = [
        "DATE",
        "TIME",
        "TYPE",
    ]

    columns = CsvColumns(header)

    csv_row = [
        "1/10/26",
        "01:15:58",
        "EXP",
    ]

    row = ThinkorswimCashBalanceRow.from_csv(
        columns,
        csv_row,
    )

    assert row.transaction_type == "EXP"
