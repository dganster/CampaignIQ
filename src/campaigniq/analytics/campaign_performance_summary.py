"""Campaign-level realized performance statistics."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Iterable
from datetime import date

from campaigniq.domain.campaign_realized_pnl import (
    CampaignRealizedPnl,
    aggregate_campaign_realized_pnl,
)
from campaigniq.domain.lot_attribution import RealizedAttribution

@dataclass(frozen=True, slots=True)
class CampaignPerformanceSummary:
    """Performance statistics for fully reconciled realized campaigns."""

    campaign_count: int
    excluded_campaign_count: int

    winning_campaign_count: int
    losing_campaign_count: int
    breakeven_campaign_count: int

    win_rate: Decimal | None

    average_win: Decimal | None
    median_win: Decimal | None

    average_loss: Decimal | None
    median_loss: Decimal | None

    best_campaign_id: str | None
    best_campaign_pnl: Decimal | None

    worst_campaign_id: str | None
    worst_campaign_pnl: Decimal | None


def _average(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None

    return sum(values, Decimal("0")) / Decimal(len(values))


def summarize_campaign_performance(
    campaign_results: Iterable[CampaignRealizedPnl],
) -> CampaignPerformanceSummary:
    """Summarize performance of fully reconciled campaign results."""

    included: list[CampaignRealizedPnl] = []
    excluded_campaign_count = 0

    for result in campaign_results:
        if result.fully_reconciled:
            included.append(result)
        else:
            excluded_campaign_count += 1

    wins = [
        result.gain_loss
        for result in included
        if result.gain_loss > 0
    ]
    losses = [
        result.gain_loss
        for result in included
        if result.gain_loss < 0
    ]
    breakeven_campaign_count = sum(
        1
        for result in included
        if result.gain_loss == 0
    )

    campaign_count = len(included)

    if campaign_count:
        win_rate = (
            Decimal(len(wins))
            / Decimal(campaign_count)
        )
        best = max(included, key=lambda result: result.gain_loss)
        worst = min(included, key=lambda result: result.gain_loss)
    else:
        win_rate = None
        best = None
        worst = None

    return CampaignPerformanceSummary(
        campaign_count=campaign_count,
        excluded_campaign_count=excluded_campaign_count,
        winning_campaign_count=len(wins),
        losing_campaign_count=len(losses),
        breakeven_campaign_count=breakeven_campaign_count,
        win_rate=win_rate,
        average_win=_average(wins),
        median_win=median(wins) if wins else None,
        average_loss=_average(losses),
        median_loss=median(losses) if losses else None,
        best_campaign_id=best.campaign_id if best else None,
        best_campaign_pnl=best.gain_loss if best else None,
        worst_campaign_id=worst.campaign_id if worst else None,
        worst_campaign_pnl=worst.gain_loss if worst else None,
    )


def summarize_campaign_performance_for_period(
    *,
    period_start: date,
    period_end: date,
    attributions: Iterable[RealizedAttribution],
) -> CampaignPerformanceSummary:
    """Summarize campaign performance for broker closes within a period."""

    if period_end < period_start:
        raise ValueError("period_end must be on or after period_start")

    period_attributions = [
        attribution
        for attribution in attributions
        if period_start <= attribution.record.closed_date <= period_end
    ]

    campaign_results = aggregate_campaign_realized_pnl(
        period_attributions
    )

    return summarize_campaign_performance(campaign_results)
