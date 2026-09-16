"""Multi-month realized performance statistics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Iterable

from campaigniq.analytics.monthly_analytics_summary import MonthlyAnalyticsSummary


@dataclass(frozen=True, slots=True)
class MultiMonthPerformanceSummary:
    """Performance statistics across monthly analytics summaries."""

    month_count: int
    profitable_month_count: int
    losing_month_count: int
    breakeven_month_count: int

    profitable_month_rate: Decimal | None

    total_realized_pnl: Decimal
    average_monthly_pnl: Decimal | None
    median_monthly_pnl: Decimal | None

    best_month_start: date | None
    best_month_pnl: Decimal | None

    worst_month_start: date | None
    worst_month_pnl: Decimal | None


def summarize_multi_month_performance(
    summaries: Iterable[MonthlyAnalyticsSummary],
) -> MultiMonthPerformanceSummary:
    """Summarize realized performance across monthly analytics summaries."""

    monthly_summaries = tuple(summaries)

    if not monthly_summaries:
        return MultiMonthPerformanceSummary(
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

    monthly_results = [
        (
            summary.period_start,
            summary.combined_realized_pnl,
        )
        for summary in monthly_summaries
    ]

    pnl_values = [pnl for _, pnl in monthly_results]

    profitable_month_count = sum(pnl > 0 for pnl in pnl_values)
    losing_month_count = sum(pnl < 0 for pnl in pnl_values)
    breakeven_month_count = sum(pnl == 0 for pnl in pnl_values)

    month_count = len(pnl_values)
    total_realized_pnl = sum(pnl_values, Decimal("0"))

    best_month_start, best_month_pnl = max(
        monthly_results,
        key=lambda item: item[1],
    )
    worst_month_start, worst_month_pnl = min(
        monthly_results,
        key=lambda item: item[1],
    )

    return MultiMonthPerformanceSummary(
        month_count=month_count,
        profitable_month_count=profitable_month_count,
        losing_month_count=losing_month_count,
        breakeven_month_count=breakeven_month_count,
        profitable_month_rate=(
            Decimal(profitable_month_count)
            / Decimal(month_count)
        ),
        total_realized_pnl=total_realized_pnl,
        average_monthly_pnl=(
            total_realized_pnl
            / Decimal(month_count)
        ),
        median_monthly_pnl=median(pnl_values),
        best_month_start=best_month_start,
        best_month_pnl=best_month_pnl,
        worst_month_start=worst_month_start,
        worst_month_pnl=worst_month_pnl,
    )
