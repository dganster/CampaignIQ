"""Read the Cash Balance section of a Thinkorswim statement."""

from campaigniq.importers.thinkorswim.cash_balance_row import (
    ThinkorswimCashBalanceRow,
)
from campaigniq.importers.thinkorswim.csv_columns import CsvColumns
from campaigniq.sources.thinkorswim.section import Section


class ThinkorswimCashBalanceReader:
    """Reads the Cash Balance section."""

    def read(
        self,
        section: Section,   
    ) -> list[ThinkorswimCashBalanceRow]:
        header = section.header()
        columns = CsvColumns(header)

        return [
            ThinkorswimCashBalanceRow.from_csv(
                columns,
                row,
            )
            for row in section.rows()
            if row[4] != "TOTAL"
        ]
    