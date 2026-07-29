"""Representation of a single Thinkorswim trade row."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from campaigniq.importers.thinkorswim.csv_columns import CsvColumns
from campaigniq.importers.thinkorswim.parsers import (
    parse_date,
    parse_datetime,
    parse_decimal,
)


@dataclass(slots=True)
class ThinkorswimTradeRow:
    """One row from the Account Trade History table."""

    exec_time: datetime | None
    spread: str
    side: str
    qty: Decimal
    pos_effect: str
    symbol: str
    exp: date | None
    strike: Decimal | None
    option_type: str
    price: Decimal
    net_price: str
    order_type: str

    @classmethod
    def from_csv(
        cls,
        columns: CsvColumns,
        row: list[str],
    ) -> "ThinkorswimTradeRow":

        return cls(
            exec_time=parse_datetime(row[columns["Exec Time"]]),
            spread=row[columns["Spread"]],
            side=row[columns["Side"]],
            qty=parse_decimal(row[columns["Qty"]]),
            pos_effect=row[columns["Pos Effect"]],
            symbol=row[columns["Symbol"]],
            exp=parse_date(row[columns["Exp"]]),
            strike=parse_decimal(row[columns["Strike"]]),
            option_type=row[columns["Type"]],
            price=parse_decimal(row[columns["Price"]]),
            net_price=row[columns["Net Price"]],
            order_type=row[columns["Order Type"]],
        )
