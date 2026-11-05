"""Combined monthly analytics summary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from campaigniq.analytics.campaign_performance_summary import (
    CampaignPerformanceSummary,
    summarize_campaign_performance_for_period,
)
from campaigniq.analytics.realized_pnl_summary import (
    RealizedPnlSummary,
    summarize_realized_pnl,
)
from campaigniq.domain.lot_attribution import RealizedAttribution


@dataclass(frozen=True, slots=True)
class MonthlyAnalyticsSummary:
    """Combined realized P&L and campaign-performance analytics for a period."""

    period_start: date
    period_end: date
    realized_pnl: RealizedPnlSummary
    campaign_performance: CampaignPerformanceSummary
    forex_settled_pnl: Decimal = Decimal("0")
    futures_realized_pnl: Decimal = Decimal("0")
    futures_applicable: bool = False
    forex_financing_pnl: Decimal = Decimal("0")
    forex_policy_approved: bool = False

    @property
    def combined_with_forex_financing(self) -> Decimal:
        return self.combined_realized_pnl + self.forex_financing_pnl

    @property
    def equity_options_realized_pnl(self) -> Decimal:
        return self.realized_pnl.broker_realized_pnl

    @property
    def combined_realized_pnl(self) -> Decimal:
        return self.equity_options_realized_pnl + self.forex_settled_pnl + self.futures_realized_pnl


def summarize_monthly_analytics(
    *,
    period_start: date,
    period_end: date,
    attributions: Iterable[RealizedAttribution],
) -> MonthlyAnalyticsSummary:
    """Summarize realized P&L and campaign performance for a period."""

    if period_end < period_start:
        raise ValueError("period_end must be on or after period_start")

    attribution_list = list(attributions)

    realized_pnl = summarize_realized_pnl(
        period_start=period_start,
        period_end=period_end,
        attributions=attribution_list,
    )

    campaign_performance = summarize_campaign_performance_for_period(
        period_start=period_start,
        period_end=period_end,
        attributions=attribution_list,
    )

    return MonthlyAnalyticsSummary(
        period_start=period_start,
        period_end=period_end,
        realized_pnl=realized_pnl,
        campaign_performance=campaign_performance,
    )
