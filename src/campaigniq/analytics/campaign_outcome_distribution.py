"""Distribution statistics for realized campaign outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Iterable

from campaigniq.domain.campaign_realized_pnl import CampaignRealizedPnl


@dataclass(frozen=True, slots=True)
class CampaignOutcomeDistribution:
    """Distribution-level statistics for fully reconciled campaigns."""

    campaign_count: int
    excluded_campaign_count: int

    median_campaign_pnl: Decimal | None
    median_win: Decimal | None
    median_loss: Decimal | None

    best_campaign_id: str | None
    best_campaign_pnl: Decimal | None

    worst_campaign_id: str | None
    worst_campaign_pnl: Decimal | None

    top_3_winner_pnl: Decimal
    bottom_3_loser_pnl: Decimal


def summarize_campaign_outcomes(
    campaign_results: Iterable[CampaignRealizedPnl],
) -> CampaignOutcomeDistribution:
    """Summarize the distribution of fully reconciled campaign outcomes."""

    included: list[CampaignRealizedPnl] = []
    excluded_campaign_count = 0

    for result in campaign_results:
        if result.fully_reconciled:
            included.append(result)
        else:
            excluded_campaign_count += 1

    if not included:
        return CampaignOutcomeDistribution(
            campaign_count=0,
            excluded_campaign_count=excluded_campaign_count,
            median_campaign_pnl=None,
            median_win=None,
            median_loss=None,
            best_campaign_id=None,
            best_campaign_pnl=None,
            worst_campaign_id=None,
            worst_campaign_pnl=None,
            top_3_winner_pnl=Decimal("0"),
            bottom_3_loser_pnl=Decimal("0"),
        )

    pnl_values = [result.gain_loss for result in included]
    wins = sorted(
        (result for result in included if result.gain_loss > 0),
        key=lambda result: result.gain_loss,
        reverse=True,
    )
    losses = sorted(
        (result for result in included if result.gain_loss < 0),
        key=lambda result: result.gain_loss,
    )

    best = max(included, key=lambda result: result.gain_loss)
    worst = min(included, key=lambda result: result.gain_loss)

    return CampaignOutcomeDistribution(
        campaign_count=len(included),
        excluded_campaign_count=excluded_campaign_count,
        median_campaign_pnl=median(pnl_values),
        median_win=(
            median([result.gain_loss for result in wins])
            if wins
            else None
        ),
        median_loss=(
            median([result.gain_loss for result in losses])
            if losses
            else None
        ),
        best_campaign_id=best.campaign_id,
        best_campaign_pnl=best.gain_loss,
        worst_campaign_id=worst.campaign_id,
        worst_campaign_pnl=worst.gain_loss,
        top_3_winner_pnl=sum(
            (result.gain_loss for result in wins[:3]),
            Decimal("0"),
        ),
        bottom_3_loser_pnl=sum(
            (result.gain_loss for result in losses[:3]),
            Decimal("0"),
        ),
    )
