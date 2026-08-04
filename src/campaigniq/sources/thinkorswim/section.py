"""A section of a Thinkorswim statement."""


class Section:
    """A section of a Thinkorswim statement."""

    def __init__(self, name: str):
        self.name = name
        self.lines: list[str] = []

    def header(self) -> list[str]:
        """Return the CSV header as a list of column names."""
    
        return self.lines[0].split(",")

    def data_lines(self) -> list[str]:
        """Return all lines after the header."""

        return self.lines[1:]

        
