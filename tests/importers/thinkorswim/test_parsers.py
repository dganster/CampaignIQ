"""Tests for parser helpers."""

from datetime import date, datetime
from decimal import Decimal

from campaigniq.importers.thinkorswim.parsers import (
    parse_date,
    parse_datetime,
    parse_decimal,
)


def test_parse_decimal() -> None:
    assert parse_decimal("123.45") == Decimal("123.45")
    assert parse_decimal("") is None


def test_parse_date() -> None:
    assert parse_date("") is None
    assert parse_date("20 FEB 26") == date(2026, 2, 20)


def test_parse_datetime() -> None:
    assert parse_datetime("") is None
    assert parse_datetime(
        "1/5/26 11:06:04"
    ) == datetime(2026, 1, 5, 11, 6, 4)
