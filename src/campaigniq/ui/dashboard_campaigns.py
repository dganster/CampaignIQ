from __future__ import annotations
from collections.abc import Mapping
from datetime import date
from campaigniq.analytics.campaign_drilldown_summary import CampaignDrilldownSummary, summarize_campaign_drilldowns
from campaigniq.domain.campaign_realized_pnl import CampaignRealizedPnl, aggregate_campaign_realized_pnl
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.campaign_identity import separate_campaign_collisions
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.forex_settlement_attribution import ForexSettlementAttribution

def _qualified(a: RealizedAttribution, period_start: date) -> RealizedAttribution:
    prefix = period_start.strftime("%Y-%m")
    return RealizedAttribution(
        record=a.record,
        allocations=tuple(
            LotAllocation(
                lot_id=x.lot_id, quantity=x.quantity, broker_basis=x.broker_basis,
                basis_source=x.basis_source,
                campaign_id=f"{prefix}/{x.campaign_id}" if x.campaign_id else None,
            )
            for x in a.allocations
        ),
    )

def _qualified_forex(attribution: ForexSettlementAttribution, period_start: date) -> ForexSettlementAttribution:
    prefix = period_start.strftime("%Y-%m")
    return ForexSettlementAttribution(
        settlement=attribution.settlement,
        campaign_id=f"{prefix}/{attribution.campaign_id}",
    )


def aggregate_period_qualified_campaigns(
    monthly_attributions: Mapping[tuple[date, date], tuple[RealizedAttribution, ...]],
    monthly_forex_attributions: Mapping[tuple[date, date], tuple[ForexSettlementAttribution, ...]] | None = None,
) -> tuple[tuple[CampaignRealizedPnl, ...], tuple[CampaignDrilldownSummary, ...]]:
    qualified = [
        _qualified(a, start)
        for (start, end), attrs in sorted(monthly_attributions.items())
        for a in attrs
        if start <= a.record.closed_date <= end
    ]
    qualified = separate_campaign_collisions(qualified)
    qualified_forex = [
        _qualified_forex(a, start)
        for (start, _end), attrs in sorted((monthly_forex_attributions or {}).items())
        for a in attrs
    ]
    return (
        aggregate_campaign_realized_pnl(qualified, qualified_forex),
        summarize_campaign_drilldowns(qualified),
    )


def campaign_realized_attributions(
    monthly_attributions: Mapping[tuple[date, date], tuple[RealizedAttribution, ...]],
    campaign_id: str,
) -> tuple[RealizedAttribution, ...]:
    """Return the exact realized records counted by one campaign drilldown.

    A record shared by campaigns or containing an unassigned allocation is
    excluded, matching summarize_campaign_drilldowns. Period qualification
    prevents identical local campaign IDs in different months from mixing.
    """
    qualified_records = separate_campaign_collisions(
        _qualified(attribution, start)
        for (start, end), attributions in sorted(monthly_attributions.items())
        for attribution in attributions
        if start <= attribution.record.closed_date <= end
    )
    records = [
        attribution for attribution in qualified_records
        if not attribution.has_unassigned_campaign_allocation
        and attribution.campaign_ids == (campaign_id,)
    ]
    return tuple(sorted(records, key=lambda item: item.record.closed_date))
