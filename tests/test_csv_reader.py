"""Tests for the Thinkorswim CSV reader."""

from pathlib import Path

from campaigniq.importers.thinkorswim.csv_reader import CsvReader


def test_reads_all_rows(tmp_path: Path) -> None:
    """The reader should faithfully return every row."""

    csv_file = tmp_path / "sample.csv"

    csv_file.write_text(
        "A,B,C\n"
        "1,2,3\n"
        "4,5,6\n",
        encoding="utf-8",
    )

    reader = CsvReader()

    rows = reader.read(csv_file)

    assert rows == [
        ["A", "B", "C"],
        ["1", "2", "3"],
        ["4", "5", "6"],
    ]
