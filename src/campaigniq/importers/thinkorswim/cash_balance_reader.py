"""Read the Cash Balance section of a Thinkorswim statement."""

from campaigniq.importers.thinkorswim.csv_columns import CsvColumns
from campaigniq.sources.thinkorswim.section import Section


class ThinkorswimCashBalanceReader:
    """Reads the Cash Balance section."""

    def read(self, section: Section) -> CsvColumns:
        """Read a Cash Balance section."""

        header = section.lines[0].split(",")

        return CsvColumns(header)
