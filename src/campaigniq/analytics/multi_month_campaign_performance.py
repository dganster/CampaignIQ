"""Cross-period campaign performance statistics."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from campaigniq.analytics.campaign_performance_summary import (
    CampaignPerformanceSummary,
)


@dataclass(frozen=True, slots=True)
class MultiMonthCampaignPerformance:
    """Campaign statistics aggregated across monthly summaries."""

    campaign_count: int
    excluded_campaign_count: int

    winning_campaign_count: int
    losing_campaign_count: int
    breakeven_campaign_count: int

    win_rate: Decimal | None

    average_win: Decimal | None
    average_loss: Decimal | None
    payoff_ratio: Decimal | None


def _weighted_average(
    values: Iterable[tuple[Decimal | None, int]],
) -> Decimal | None:
    total = Decimal("0")
    count = 0

    for value, weight in values:
        if value is None or weight == 0:
            continue

        total += value * Decimal(weight)
        count += weight

    if count == 0:
        return None

    return total / Decimal(count)


def summarize_multi_month_campaign_performance(
    summaries: Iterable[CampaignPerformanceSummary],
) -> MultiMonthCampaignPerformance:
    """Aggregate campaign performance across monthly summaries."""

    monthly = tuple(summaries)

    campaign_count = sum(summary.campaign_count for summary in monthly)
    excluded_campaign_count = sum(
        summary.excluded_campaign_count
        for summary in monthly
    )
    winning_campaign_count = sum(
        summary.winning_campaign_count
        for summary in monthly
    )
    losing_campaign_count = sum(
        summary.losing_campaign_count
        for summary in monthly
    )
    breakeven_campaign_count = sum(
        summary.breakeven_campaign_count
        for summary in monthly
    )

    win_rate = (
        Decimal(winning_campaign_count) / Decimal(campaign_count)
        if campaign_count
        else None
    )

    average_win = _weighted_average(
        (
            summary.average_win,
            summary.winning_campaign_count,
        )
        for summary in monthly
    )
    average_loss = _weighted_average(
        (
            summary.average_loss,
            summary.losing_campaign_count,
        )
        for summary in monthly
    )

    payoff_ratio = (
        average_win / abs(average_loss)
        if average_win is not None
        and average_loss is not None
        and average_loss != 0
        else None
    )

    return MultiMonthCampaignPerformance(
        campaign_count=campaign_count,
        excluded_campaign_count=excluded_campaign_count,
        winning_campaign_count=winning_campaign_count,
        losing_campaign_count=losing_campaign_count,
        breakeven_campaign_count=breakeven_campaign_count,
        win_rate=win_rate,
        average_win=average_win,
        average_loss=average_loss,
        payoff_ratio=payoff_ratio,
    )
