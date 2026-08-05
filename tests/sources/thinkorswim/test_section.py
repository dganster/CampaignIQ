from campaigniq.sources.thinkorswim.section import Section


def test_returns_header_line() -> None:
    section = Section("Test")

    section.lines = [
        "A,B,C",
        "1,2,3",
    ]

    assert section.header() == ["A", "B", "C"]

def test_returns_data_lines() -> None:
    section = Section("Test")

    section.lines = [
        "A,B,C",
        "1,2,3",
        "4,5,6",
    ]

    assert section.data_lines() == [
        "1,2,3",
        "4,5,6",
    ]
    
def test_returns_rows() -> None:
    section = Section("Test")

    section.lines = [
        "A,B,C",
        "1,2,3",
        "4,5,6",
    ]

    assert section.rows() == [
        ["1", "2", "3"],
        ["4", "5", "6"],
    ]