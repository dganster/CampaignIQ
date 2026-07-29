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

from campaigniq.domain.option_type import OptionType
from campaigniq.domain.side import Side


def parse_option_type(value: str) -> OptionType:
    """Convert a Thinkorswim option type to a domain OptionType."""

    match value.upper():
        case "CALL":
            return OptionType.CALL
        case "PUT":
            return OptionType.PUT
        case _:
            raise ValueError(f"Unknown option type: {value!r}")


def parse_side(value: str) -> Side:
    """Convert a Thinkorswim side to a domain Side."""

    match value.upper():
        case "BUY":
            return Side.BUY
        case "SELL":
            return Side.SELL
        case _:
            raise ValueError(f"Unknown side: {value!r}")
