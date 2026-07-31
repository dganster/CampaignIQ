from dataclasses import dataclass

from campaigniq.domain.account import Account
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.trade import Trade


@dataclass(frozen=True, slots=True)
class Position:
    """An open position in one underlying within one account."""

    account: Account
    underlying: Instrument
    trades: tuple[Trade, ...]

    def __post_init__(self) -> None:
        if not self.trades:
            raise ValueError("Position must contain at least one trade.")
