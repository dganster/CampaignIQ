"""Representation of a single Thinkorswim trade row."""

from dataclasses import dataclass

from campaigniq.importers.thinkorswim.csv_columns import CsvColumns


@dataclass(slots=True)
class ThinkorswimTradeRow:
    """One row from the Account Trade History table."""

    exec_time: str
    spread: str
    side: str
    qty: str
    pos_effect: str
    symbol: str
    exp: str
    strike: str
    option_type: str
    price: str
    net_price: str
    order_type: str

    @classmethod
    def from_csv(
        cls,
        columns: CsvColumns,
        row: list[str],
    ) -> "ThinkorswimTradeRow":
        """Create a trade row from a CSV row."""

        return cls(
            exec_time=row[columns["Exec Time"]],
            spread=row[columns["Spread"]],
            side=row[columns["Side"]],
            qty=row[columns["Qty"]],
            pos_effect=row[columns["Pos Effect"]],
            symbol=row[columns["Symbol"]],
            exp=row[columns["Exp"]],
            strike=row[columns["Strike"]],
            option_type=row[columns["Type"]],
            price=row[columns["Price"]],
            net_price=row[columns["Net Price"]],
            order_type=row[columns["Order Type"]],
        )
