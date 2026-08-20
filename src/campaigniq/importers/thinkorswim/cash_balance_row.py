"""Representation of a single Thinkorswim cash balance row."""

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal

from campaigniq.importers.thinkorswim.csv_columns import CsvColumns
from campaigniq.importers.thinkorswim.parsers import (
    parse_csv_date,
    parse_decimal,
    parse_time,
)


@dataclass(slots=True)
class ThinkorswimCashBalanceRow:
    """One row from the Cash Balance table."""

    transaction_date: date
    transaction_time: time
    transaction_type: str
    reference: str
    description: str
    misc_fees: Decimal | None
    commissions_and_fees: Decimal | None
    amount: Decimal | None
    balance: Decimal | None

    @classmethod
    def from_csv(
        cls,
        columns: CsvColumns,
        row: list[str],
    ) -> "ThinkorswimCashBalanceRow":
        return cls(
            transaction_date=parse_csv_date(row[columns["DATE"]]),
            transaction_time=parse_time(row[columns["TIME"]]),
            transaction_type=row[columns["TYPE"]],
            reference=row[columns["REF #"]],
            description=row[columns["DESCRIPTION"]],
            misc_fees=parse_decimal(row[columns["Misc Fees"]]),
            commissions_and_fees=parse_decimal(
                row[columns["Commissions & Fees"]]
            ),
            amount=parse_decimal(row[columns["AMOUNT"]]),
            balance=parse_decimal(row[columns["BALANCE"]]),
        )
