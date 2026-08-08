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
            if len(trade.legs) != 1:
                raise ValueError(
                    "Campaign reconstruction currently requires single-leg trades."
                )

            underlying = trade.legs[0].contract.underlying
            executed_at = trade.legs[0].executed_at

            for campaign_trades in campaigns:
                last_trade = campaign_trades[-1]
                last_executed_at = last_trade.legs[0].executed_at
                last_underlying = last_trade.legs[0].contract.underlying

                if (
                    underlying == last_underlying
                    and executed_at - last_executed_at <= timedelta(days=7)
                ):
                    campaign_trades.append(trade)
                    break
            else:
                campaigns.append([trade])

        return [Campaign(trades=tuple(trades)) for trades in campaigns]