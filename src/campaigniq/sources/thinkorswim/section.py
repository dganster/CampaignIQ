"""A section of a Thinkorswim statement."""

import csv

from campaigniq.importers.thinkorswim.csv_columns import CsvColumns


class Section:
    """A section of a Thinkorswim statement."""

    def __init__(self, name: str):
        self.name = name
        self.lines: list[str] = []

    def header(self) -> list[str]:
        """Return the CSV header as a list of column names."""

        return next(csv.reader([self.lines[0]]))

    def data_lines(self) -> list[str]:
        """Return all lines after the header."""

        return self.lines[1:]

    def rows(self) -> list[list[str]]:
        """Return the CSV data rows as parsed fields."""

        return [
            row
            for row in csv.reader(self.data_lines())
        ]

    def columns(self) -> CsvColumns:
        """Return the CSV columns."""

        return CsvColumns(self.header())
