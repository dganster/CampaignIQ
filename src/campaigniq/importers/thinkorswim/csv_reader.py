"""Read raw Thinkorswim CSV exports."""

from __future__ import annotations

import csv
from pathlib import Path


class CsvReader:
    """Reads Thinkorswim CSV exports."""

    def read(self, filename: str | Path) -> list[list[str]]:
        """Return every row from a CSV file."""

        path = Path(filename)

        with path.open(newline="", encoding="utf-8-sig") as csv_file:
            return list(csv.reader(csv_file))

    def trade_history_rows(self, rows: list[list[str]]) -> list[list[str]]:
        """Return only the Account Trade History table."""

        start = None

        for index, row in enumerate(rows):
            if len(row) > 1 and row[1] == "Exec Time":
                start = index
                break

        if start is None:
            raise ValueError("Account Trade History section not found.")

        table: list[list[str]] = []

        for row in rows[start:]:
            # A completely blank row marks the end of the table.
            if not row:
                break

            table.append(row)

        return table
