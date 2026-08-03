"""Read Thinkorswim source data."""

from campaigniq.sources.thinkorswim.statement import (
    ThinkorswimStatement,
)


class ThinkorswimSourceReader:
    """Reads Thinkorswim export files."""

    def read(self, filename: str):
        with open(filename):
            return ThinkorswimStatement()
