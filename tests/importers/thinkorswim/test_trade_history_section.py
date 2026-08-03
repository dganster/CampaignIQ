"""Tests for locating the Account Trade History section."""

from campaigniq.importers.thinkorswim.csv_reader import CsvReader


def test_finds_trade_history_header() -> None:
    reader = CsvReader()

    rows = reader.read("tests/data/thinkorswim/Account Trading History.csv")
    trade_rows = reader.trade_history_rows(rows)

    assert trade_rows
    assert trade_rows[0][1] == "Exec Time"
    assert trade_rows[0][2] == "Spread"
    assert trade_rows[0][3] == "Side"
