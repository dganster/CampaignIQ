"""Helper functions for parsing Thinkorswim CSV fields."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal


_DATETIME_FORMAT = "%m/%d/%y %H:%M:%S"
_DATE_FORMAT = "%d %b %y"


def parse_datetime(value: str) -> datetime | None:
    """Parse a Thinkorswim execution timestamp."""

    value = value.strip()

    if not value:
        return None

    return datetime.strptime(value, _DATETIME_FORMAT)


def parse_date(value: str) -> date | None:
    """Parse an option expiration date."""

    value = value.strip()

    if not value:
        return None

    return datetime.strptime(value, _DATE_FORMAT).date()


def parse_decimal(value: str) -> Decimal | None:
    """Parse a decimal value."""

    value = value.strip()

    if not value:
        return None

    return Decimal(value)
