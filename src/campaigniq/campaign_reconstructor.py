"""Reconstruct investment campaigns from brokerage trades."""

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.trade import Trade


class CampaignReconstructor:
    """Reconstruct investment campaigns."""

    def reconstruct(self, trades: list[Trade]) -> list[Campaign]:
        """Return reconstructed campaigns."""
        return [Campaign(trades=tuple(trades))] if trades else []