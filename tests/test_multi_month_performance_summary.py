from datetime import date
from decimal import Decimal

from campaigniq.analytics.multi_month_performance_summary import (
    MultiMonthPerformanceSummary,
    summarize_multi_month_performance,
)
from campaigniq.analytics.monthly_analytics_summary import MonthlyAnalyticsSummary
from campaigniq.analytics.realized_pnl_summary import RealizedPnlSummary
from campaigniq.analytics.campaign_performance_summary import (
    CampaignPerformanceSummary,
)


def monthly_summary(
    *,
    year: int,
    month: int,
    period_end_day: int,
    realized_pnl: str,
) -> MonthlyAnalyticsSummary:
    period_start = date(year, month, 1)
    period_end = date(year, month, period_end_day)
    pnl = Decimal(realized_pnl)

    return MonthlyAnalyticsSummary(
        period_start=period_start,
        period_end=period_end,
        realized_pnl=RealizedPnlSummary(
            period_start=period_start,
            period_end=period_end,
            broker_realized_pnl=pnl,
            attributed_realized_pnl=pnl,
            unattributed_realized_pnl=Decimal("0"),
            broker_record_count=1,
            attributed_record_count=1,
            unattributed_record_count=0,
        ),
        campaign_performance=CampaignPerformanceSummary(
            campaign_count=0,
            excluded_campaign_count=0,
            winning_campaign_count=0,
            losing_campaign_count=0,
            breakeven_campaign_count=0,
            win_rate=None,
            average_win=None,
            median_win=None,
            average_loss=None,
            median_loss=None,
            best_campaign_id=None,
            best_campaign_pnl=None,
            worst_campaign_id=None,
            worst_campaign_pnl=None,
        ),
    )


def test_summarizes_multi_month_performance() -> None:
    summaries = (
        monthly_summary(
            year=2026,
            month=1,
            period_end_day=31,
            realized_pnl="100.00",
        ),
        monthly_summary(
            year=2026,
            month=2,
            period_end_day=28,
            realized_pnl="-50.00",
        ),
        monthly_summary(
            year=2026,
            month=3,
            period_end_day=31,
            realized_pnl="0.00",
        ),
        monthly_summary(
            year=2026,
            month=4,
            period_end_day=30,
            realized_pnl="25.00",
        ),
    )

    result = summarize_multi_month_performance(summaries)

    assert result == MultiMonthPerformanceSummary(
        month_count=4,
        profitable_month_count=2,
        losing_month_count=1,
        breakeven_month_count=1,
        profitable_month_rate=Decimal("0.5"),
        total_realized_pnl=Decimal("75.00"),
        average_monthly_pnl=Decimal("18.75"),
        median_monthly_pnl=Decimal("12.50"),
        best_month_start=date(2026, 1, 1),
        best_month_pnl=Decimal("100.00"),
        worst_month_start=date(2026, 2, 1),
        worst_month_pnl=Decimal("-50.00"),
    )


def test_empty_multi_month_performance() -> None:
    result = summarize_multi_month_performance(())

    assert result == MultiMonthPerformanceSummary(
        month_count=0,
        profitable_month_count=0,
        losing_month_count=0,
        breakeven_month_count=0,
        profitable_month_rate=None,
        total_realized_pnl=Decimal("0"),
        average_monthly_pnl=None,
        median_monthly_pnl=None,
        best_month_start=None,
        best_month_pnl=None,
        worst_month_start=None,
        worst_month_pnl=None,
    )


def test_accepts_generator_input() -> None:
    summaries = (
        summary
        for summary in (
            monthly_summary(
                year=2026,
                month=1,
                period_end_day=31,
                realized_pnl="10.00",
            ),
            monthly_summary(
                year=2026,
                month=2,
                period_end_day=28,
                realized_pnl="30.00",
            ),
        )
    )

    result = summarize_multi_month_performance(summaries)

    assert result.month_count == 2
    assert result.total_realized_pnl == Decimal("40.00")
    assert result.average_monthly_pnl == Decimal("20.00")
    assert result.median_monthly_pnl == Decimal("20.00")
    assert result.best_month_start == date(2026, 2, 1)
    assert result.worst_month_start == date(2026, 1, 1)


def test_combines_forex_settled_pnl_into_monthly_performance() -> None:
    january = monthly_summary(
        year=2026, month=1, period_end_day=31, realized_pnl="100.00"
    )
    february = monthly_summary(
        year=2026, month=2, period_end_day=28, realized_pnl="-50.00"
    )

    january = MonthlyAnalyticsSummary(
        period_start=january.period_start,
        period_end=january.period_end,
        realized_pnl=january.realized_pnl,
        campaign_performance=january.campaign_performance,
        forex_settled_pnl=Decimal("-25.00"),
    )
    february = MonthlyAnalyticsSummary(
        period_start=february.period_start,
        period_end=february.period_end,
        realized_pnl=february.realized_pnl,
        campaign_performance=february.campaign_performance,
        forex_settled_pnl=Decimal("75.00"),
    )

    assert january.equity_options_realized_pnl == Decimal("100.00")
    assert january.combined_realized_pnl == Decimal("75.00")
    assert february.combined_realized_pnl == Decimal("25.00")

    result = summarize_multi_month_performance((january, february))

    assert result.total_realized_pnl == Decimal("100.00")
    assert result.profitable_month_count == 2
    assert result.losing_month_count == 0
    assert result.average_monthly_pnl == Decimal("50.00")
    assert result.best_month_start == date(2026, 1, 1)
    assert result.best_month_pnl == Decimal("75.00")
    assert result.worst_month_start == date(2026, 2, 1)
    assert result.worst_month_pnl == Decimal("25.00")
