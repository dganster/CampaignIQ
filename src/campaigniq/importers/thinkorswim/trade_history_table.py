"""Trade history table extracted from a Thinkorswim CSV export."""

from __future__ import annotations


class TradeHistoryTable:
    """Represents the Account Trade History table."""

    def __init__(self, rows: list[list[str]]) -> None:
        self._rows = rows

    @property
    def header(self) -> list[str]:
        """Return the table header row."""
        return self._rows[0]

    @property
    def rows(self) -> list[list[str]]:
        """Return all rows, including the header."""
        return self._rows
