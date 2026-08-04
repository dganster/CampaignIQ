"""Read Thinkorswim trade rows from a statement section."""

from campaigniq.sources.thinkorswim.section import Section

from .csv_columns import CsvColumns
from .trade_row import ThinkorswimTradeRow


class ThinkorswimTradeRowReader:
    """Reads Thinkorswim trade rows."""

    def read(self, section: Section) -> list[ThinkorswimTradeRow]:
        """Read trade rows from a section."""

        header = section.lines[0].split(",")

        data_rows = [
            row.split(",")
            for row in section.lines[1:]
        ]

        columns = CsvColumns(header)

        return [
            ThinkorswimTradeRow.from_csv(columns, row)
            for row in data_rows
        ]
