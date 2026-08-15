"""Reconstruct investment campaigns from brokerage trades."""

from datetime import timedelta

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.directional_bias import DirectionalBias
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.trade import Trade


class CampaignReconstructor:
    """Reconstruct investment campaigns."""

    def _underlying(self, trade: Trade) -> str:
        """Return the underlying symbol for a trade."""

        instrument = trade.legs[0].instrument

        if isinstance(instrument, OptionContract):
            return instrument.underlying

        return instrument.symbol

    def _campaign_directional_bias(
        self,
        campaign_trades: list[Trade],
    ) -> DirectionalBias:
        """Return the most recent meaningful directional bias."""

        for trade in reversed(campaign_trades):
            bias = trade.directional_bias()

            if bias != DirectionalBias.NEUTRAL:
                return bias

        return DirectionalBias.NEUTRAL

    def _started_before_data(
        self,
        campaign_trades: list[Trade],
    ) -> bool:
        """Return whether the campaign was already open in the data."""

        first_trade = campaign_trades[0]

        return not any(
            leg.position_effect == PositionEffect.OPEN
            for leg in first_trade.legs
        )

    def reconstruct(self, trades: list[Trade]) -> list[Campaign]:
        """Return reconstructed campaigns."""

        campaigns: list[list[Trade]] = []

        for trade in trades:
            underlying = self._underlying(trade)
            executed_at = min(
                execution.executed_at
                for leg in trade.legs
                for execution in leg.executions
            )

            for campaign_trades in campaigns:
                last_trade = campaign_trades[-1]
                last_executed_at = min(
                    execution.executed_at
                    for leg in last_trade.legs
                    for execution in leg.executions
                )
                last_underlying = self._underlying(last_trade)

                campaign_bias = self._campaign_directional_bias(
                    campaign_trades
                )

                trade_bias = trade.directional_bias()

                if (
                    underlying == last_underlying
                    and executed_at - last_executed_at
                    <= timedelta(days=30)
                    and (
                        trade_bias == DirectionalBias.NEUTRAL
                        or campaign_bias == DirectionalBias.NEUTRAL
                        or trade_bias == campaign_bias
                    )
                ):
                    campaign_trades.append(trade)
                    break
            else:
                campaigns.append([trade])

        return [
            Campaign(
                campaign_id=f"CAMP-{index:06d}",
                trades=tuple(campaign_trades),
                started_before_data=self._started_before_data(
                    campaign_trades
                ),
            )
            for index, campaign_trades in enumerate(campaigns, start=1)
        ]
