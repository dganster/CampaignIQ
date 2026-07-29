"""Maps CSV column names to indexes."""


class CsvColumns:
    """Lookup indexes by column name."""

    def __init__(self, header: list[str]) -> None:
        self._columns = {
            name: index
            for index, name in enumerate(header)
        }

    def __getitem__(self, name: str) -> int:
        return self._columns[name]
