import pytest

from campaigniq.sources.thinkorswim.source_reader import (
    ThinkorswimSourceReader,
)


def test_can_create_source_reader() -> None:
    reader = ThinkorswimSourceReader()

    assert reader is not None


def test_source_reader_has_read_method() -> None:
    reader = ThinkorswimSourceReader()

    assert hasattr(reader, "read")


def test_read_missing_file_raises_error() -> None:
    reader = ThinkorswimSourceReader()

    with pytest.raises(FileNotFoundError):
        reader.read("does_not_exist.csv")
