"""Domain representation of an investment campaign."""

from dataclasses import dataclass

from campaigniq.domain.trade import Trade


@dataclass(frozen=True, slots=True)
class Campaign:
    """A reconstructed investment campaign."""

    trades: tuple[Trade, ...]
    started_before_data: bool = False
    