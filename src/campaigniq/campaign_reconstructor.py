"""Reconstruct investment campaigns from brokerage trades."""

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.trade import Trade


class CampaignReconstructor:
    """Reconstruct investment campaigns."""

    def reconstruct(self, trades: list[Trade]) -> list[Campaign]:
        """Return reconstructed campaigns."""

        campaigns: dict[object, list[Trade]] = {}

        for trade in trades:
            contract = trade.legs[0].contract
            campaigns.setdefault(contract, []).append(trade)

        return [Campaign(trades=tuple(trades)) for trades in campaigns.values()]
    