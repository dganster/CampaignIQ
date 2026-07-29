"""Read raw Thinkorswim CSV exports."""

from __future__ import annotations

import csv
from pathlib import Path

from campaigniq.importers.thinkorswim.trade_history_table import TradeHistoryTable


class CsvReader:
    """Reads Thinkorswim CSV exports."""

    def read(self, filename: str | Path) -> list[list[str]]:
        """Return every row from a CSV file."""

        path = Path(filename)

        with path.open(newline="", encoding="utf-8-sig") as csv_file:
            return list(csv.reader(csv_file))

    def trade_history_table(self, rows: list[list[str]]) -> TradeHistoryTable:
        """Return the Account Trade History table."""

        for index, row in enumerate(rows):
            if len(row) > 1 and row[1] == "Exec Time":
                return TradeHistoryTable(rows[index:])

        raise ValueError("Account Trade History section not found.")
