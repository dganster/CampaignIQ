"""Multi-month analytics summary."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable, Mapping

from campaigniq.analytics.monthly_analytics_summary import (
    MonthlyAnalyticsSummary,
    summarize_monthly_analytics,
)
from campaigniq.domain.forex_settlement_attribution import ForexSettlementAttribution
from campaigniq.domain.lot_attribution import RealizedAttribution


def summarize_multi_month_analytics(
    *,
    monthly_attributions: Mapping[
        tuple[date, date],
        Iterable[RealizedAttribution],
    ],
    monthly_forex_attributions: Mapping[
        tuple[date, date],
        Iterable[ForexSettlementAttribution],
    ] | None = None,
) -> tuple[MonthlyAnalyticsSummary, ...]:
    """Summarize monthly analytics in chronological order."""

    summaries: list[MonthlyAnalyticsSummary] = []
    forex_by_period = monthly_forex_attributions or {}

    for period_start, period_end in sorted(monthly_attributions):
        period = (period_start, period_end)
        summary = summarize_monthly_analytics(
            period_start=period_start,
            period_end=period_end,
            attributions=monthly_attributions[period],
        )
        forex_settled_pnl = sum(
            (attribution.gain_loss for attribution in forex_by_period.get(period, ())),
            Decimal("0"),
        )
        summaries.append(
            MonthlyAnalyticsSummary(
                period_start=summary.period_start,
                period_end=summary.period_end,
                realized_pnl=summary.realized_pnl,
                campaign_performance=summary.campaign_performance,
                forex_settled_pnl=forex_settled_pnl,
            )
        )

    return tuple(summaries)
