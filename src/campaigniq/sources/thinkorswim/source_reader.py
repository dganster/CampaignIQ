"""Read Thinkorswim source data."""

from campaigniq.sources.thinkorswim.section import Section
from campaigniq.sources.thinkorswim.statement import (
    ThinkorswimStatement,
)


class ThinkorswimSourceReader:
    """Reads Thinkorswim export files."""

    HEADER_PREFIXES = (
        "DATE,",
        "Trade Date,",
        ",Date,",
        "Notes,",
        ",Exec Time,",
        "Symbol,",
        "Forex Cash,",
        "Net Liquidating Value,",
    )

    def read(self, filename: str) -> ThinkorswimStatement:
        statement = ThinkorswimStatement()

        with open(filename, encoding="utf-8-sig") as file:
            lines = [line.strip() for line in file]

        for i in range(1, len(lines) - 1):
            previous = lines[i - 1]
            current = lines[i]
            next_line = lines[i + 1]

            if not current:
                continue

            if current.startswith("Account Statement"):
                continue

            if current.startswith('"Total Cash'):
                continue

            if (
                previous == ""
                and next_line.startswith(self.HEADER_PREFIXES)
            ):
                statement.sections.append(Section(current))

        return statement
