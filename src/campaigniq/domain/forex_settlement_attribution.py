"""Attribute authoritative Schwab FOREX settlements to reconstructed FX campaigns."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.importers.schwab.forex_transaction_reader import SchwabForexSettlement


@dataclass(frozen=True, slots=True)
class ForexSettlementAttribution:
    settlement: SchwabForexSettlement
    campaign_id: str

    @property
    def gain_loss(self) -> Decimal:
        return self.settlement.settlement_pl_usd


def attribute_forex_settlements(
    campaigns: Iterable[Campaign],
    settlements: Iterable[SchwabForexSettlement],
) -> tuple[ForexSettlementAttribution, ...]:
    """Attribute settlements only when one FX campaign is defensibly identifiable.

    Matching is deliberately conservative:
    - same currency pair;
    - campaign contains a CLOSE trade;
    - CLOSE execution is on/before the Schwab trade timestamp;
    - choose the latest such CLOSE;
    - reject an exact-time ambiguity rather than guessing.
    """

    candidates_by_pair: dict[str, list[tuple[object, str]]] = {}

    for campaign in campaigns:
        for trade in campaign.trades:
            for leg in trade.legs:
                if not isinstance(leg.instrument, ForexPair):
                    continue
                if leg.position_effect.value != "CLOSE":
                    continue
                for execution in leg.executions:
                    candidates_by_pair.setdefault(
                        leg.instrument.symbol, []
                    ).append((execution.executed_at, campaign.campaign_id))

    result: list[ForexSettlementAttribution] = []

    for settlement in settlements:
        eligible = [
            candidate
            for candidate in candidates_by_pair.get(
                settlement.instrument.upper(), []
            )
            if candidate[0] <= settlement.trade_at
        ]
        if not eligible:
            continue

        latest_at = max(candidate[0] for candidate in eligible)
        campaign_ids = {
            campaign_id
            for executed_at, campaign_id in eligible
            if executed_at == latest_at
        }
        if len(campaign_ids) != 1:
            continue

        result.append(
            ForexSettlementAttribution(
                settlement=settlement,
                campaign_id=next(iter(campaign_ids)),
            )
        )

    return tuple(result)
