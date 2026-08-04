"""A Thinkorswim account statement."""

from campaigniq.sources.thinkorswim.section import Section


class ThinkorswimStatement:
    """A Thinkorswim account statement."""

    def __init__(self) -> None:
        self.sections: list[Section] = []

    def section(self, name: str) -> Section:
        """Return the section with the given name."""

        for section in self.sections:
            if section.name == name:
                return section

        raise KeyError(name)
