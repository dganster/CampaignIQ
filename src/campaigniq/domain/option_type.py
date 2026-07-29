"""Option contract types."""

from enum import Enum


class OptionType(Enum):
    """The type of an option contract."""

    CALL = "CALL"
    PUT = "PUT"
