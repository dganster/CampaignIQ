from decimal import Decimal

from campaigniq.analytics.campaign_performance_summary import CampaignPerformanceSummary
from campaigniq.analytics.multi_month_campaign_performance import (
    MultiMonthCampaignPerformance,
    summarize_multi_month_campaign_performance,
)


def campaign_summary(
    *,
    campaigns: int,
    excluded: int,
    wins: int,
    losses: int,
    breakeven: int,
    average_win: str | None,
    average_loss: str | None,
) -> CampaignPerformanceSummary:
    return CampaignPerformanceSummary(
        campaign_count=campaigns,
        excluded_campaign_count=excluded,
        winning_campaign_count=wins,
        losing_campaign_count=losses,
        breakeven_campaign_count=breakeven,
        win_rate=Decimal(wins) / Decimal(campaigns) if campaigns else None,
        average_win=Decimal(average_win) if average_win is not None else None,
        median_win=None,
        average_loss=Decimal(average_loss) if average_loss is not None else None,
        median_loss=None,
        best_campaign_id=None,
        best_campaign_pnl=None,
        worst_campaign_id=None,
        worst_campaign_pnl=None,
    )


def test_summarizes_cross_period_campaign_performance() -> None:
    summaries = (
        campaign_summary(
            campaigns=4, excluded=1, wins=2, losses=1, breakeven=1,
            average_win="150.00", average_loss="-100.00",
        ),
        campaign_summary(
            campaigns=3, excluded=0, wins=1, losses=2, breakeven=0,
            average_win="300.00", average_loss="-50.00",
        ),
    )

    result = summarize_multi_month_campaign_performance(summaries)

    assert result == MultiMonthCampaignPerformance(
        campaign_count=7,
        excluded_campaign_count=1,
        winning_campaign_count=3,
        losing_campaign_count=3,
        breakeven_campaign_count=1,
        win_rate=Decimal(3) / Decimal(7),
        average_win=Decimal("200.00"),
        average_loss=Decimal("-66.66666666666666666666666667"),
        payoff_ratio=Decimal("3.000000000000000000000000000"),
    )


def test_empty_cross_period_campaign_performance() -> None:
    result = summarize_multi_month_campaign_performance(())

    assert result == MultiMonthCampaignPerformance(
        campaign_count=0,
        excluded_campaign_count=0,
        winning_campaign_count=0,
        losing_campaign_count=0,
        breakeven_campaign_count=0,
        win_rate=None,
        average_win=None,
        average_loss=None,
        payoff_ratio=None,
    )


def test_accepts_generator_input() -> None:
    summaries = (
        summary
        for summary in (
            campaign_summary(
                campaigns=2, excluded=0, wins=2, losses=0, breakeven=0,
                average_win="50.00", average_loss=None,
            ),
            campaign_summary(
                campaigns=1, excluded=0, wins=0, losses=1, breakeven=0,
                average_win=None, average_loss="-25.00",
            ),
        )
    )

    result = summarize_multi_month_campaign_performance(summaries)

    assert result.campaign_count == 3
    assert result.winning_campaign_count == 2
    assert result.losing_campaign_count == 1
    assert result.average_win == Decimal("50.00")
    assert result.average_loss == Decimal("-25.00")
    assert result.payoff_ratio == Decimal("2")
