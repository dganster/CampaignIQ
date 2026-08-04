"""A section of a Thinkorswim statement."""


class Section:
    """A section of a Thinkorswim statement."""

    def __init__(self, name: str):
        self.name = name
        self.lines: list[str] = []
