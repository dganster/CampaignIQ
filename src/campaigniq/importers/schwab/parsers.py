"""Helper functions for parsing Schwab statement fields."""

from datetime import date, datetime
from decimal import Decimal


_DATE_FORMAT = "%m/%d/%Y"
_DATETIME_FORMAT = "%m/%d/%Y %H:%M:%S"


def parse_date(value: str) -> date:
    """Parse a Schwab statement date."""

    value = value.strip()

    return datetime.strptime(value, _DATE_FORMAT).date()


def parse_datetime(value: str) -> datetime:
    """Parse a Schwab statement timestamp."""

    value = value.strip()

    return datetime.strptime(value, _DATETIME_FORMAT)


def parse_decimal(value: str) -> Decimal:
    """Parse a Schwab statement decimal."""

    value = value.strip()

    return Decimal(value.replace(",", ""))
