"""Read raw Thinkorswim CSV exports."""

from __future__ import annotations

import csv
from pathlib import Path


class CsvReader:
    """Reads a Thinkorswim CSV file.

    This class intentionally performs **no business logic**. It simply
    returns every CSV row exactly as it appears in the file.
    """

    def read(self, filename: str | Path) -> list[list[str]]:
        """Return every row from a CSV file."""

        path = Path(filename)

        with path.open(newline="", encoding="utf-8-sig") as csv_file:
            return list(csv.reader(csv_file))
