class ThinkorswimStatement:
    """A Thinkorswim account statement."""

    def __init__(self) -> None:
        self.sections = []

def test_statement_contains_sections() -> None:
    reader = ThinkorswimSourceReader()

    statement = reader.read(
        "tests/data/thinkorswim/Account Trading History.csv"
    )

    assert len(statement.sections) > 0
