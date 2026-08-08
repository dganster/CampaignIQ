from campaigniq.domain.trade import Trade
from dataclasses import dataclass


@dataclass
class Campaign:
    """A reconstructed investment campaign."""
    trades: tuple[Trade, ...]