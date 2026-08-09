"""Reconstruct investment campaigns from brokerage trades."""

from datetime import timedelta

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.trade import Trade


class CampaignReconstructor:
    """Reconstruct investment campaigns."""

    def reconstruct(self, trades: list[Trade]) -> list[Campaign]:
        """Return reconstructed campaigns."""

        campaigns: list[list[Trade]] = []

        for trade in trades:
            underlying = trade.legs[0].contract.underlying
            executed_at = min(leg.executed_at for leg in trade.legs)

            for campaign_trades in campaigns:
                last_trade = campaign_trades[-1]
                last_executed_at = min(
                    leg.executed_at for leg in last_trade.legs
                )
                last_underlying = (
                    last_trade.legs[0].contract.underlying
                )

                if (
                    underlying == last_underlying
                    and executed_at - last_executed_at
                    <= timedelta(days=30)
                ):
                    campaign_trades.append(trade)
                    break
            else:
                campaigns.append([trade])

        return [
            Campaign(trades=tuple(campaign_trades))
            for campaign_trades in campaigns
        ]
    