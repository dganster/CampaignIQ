"""Representation of a single Thinkorswim cash balance row."""

from dataclasses import dataclass
from datetime import date, time

from campaigniq.importers.thinkorswim.csv_columns import CsvColumns
from campaigniq.importers.thinkorswim.parsers import (
    parse_csv_date,
    parse_time,
)


@dataclass(slots=True)
class ThinkorswimCashBalanceRow:
    """One row from the Cash Balance table."""

    transaction_date: date
    transaction_time: time
    transaction_type: str

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
        )
