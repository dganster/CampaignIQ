"""Domain representation of an option contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from campaigniq.domain.option_type import OptionType


@dataclass(frozen=True, slots=True)
class OptionContract:
    """A uniquely identifiable option contract."""

    underlying: str
    expiration: date
    strike: Decimal
    option_type: OptionType
