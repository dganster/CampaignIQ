"""Campaign-level aggregation of realized broker results."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from campaigniq.domain.lot_attribution import RealizedAttribution


@dataclass(frozen=True, slots=True)
class CampaignRealizedPnl:
    """Realized result attributable to one campaign."""

    campaign_id: str
    proceeds: Decimal
    cost_basis: Decimal
    gain_loss: Decimal
    allocation_count: int
    record_count: int
    fully_reconciled: bool


def aggregate_campaign_realized_pnl(
    attributions: list[RealizedAttribution],
) -> tuple[CampaignRealizedPnl, ...]:
    """Aggregate realized broker results that map unambiguously to campaigns.

    A broker realized record is included only when every allocation has known
    provenance and all allocations belong to the same campaign. Records that
    span campaigns, or have unknown provenance, are deliberately excluded
    rather than silently split.
    """
    totals: dict[str, dict[str, object]] = {}

    for attribution in attributions:
        campaign_ids = attribution.campaign_ids

        if attribution.has_unassigned_campaign_allocation:
            continue
        if len(campaign_ids) != 1:
            continue

        campaign_id = campaign_ids[0]
        total = totals.setdefault(
            campaign_id,
            {
                "proceeds": Decimal("0"),
                "cost_basis": Decimal("0"),
                "gain_loss": Decimal("0"),
                "allocation_count": 0,
                "record_count": 0,
                "fully_reconciled": True,
            },
        )

        total["proceeds"] += attribution.record.proceeds
        total["cost_basis"] += attribution.record.cost_basis
        total["gain_loss"] += attribution.record.gain_loss
        total["allocation_count"] += len(attribution.allocations)
        total["record_count"] += 1
        total["fully_reconciled"] = (
            total["fully_reconciled"]
            and attribution.basis_reconciled
            and attribution.gain_loss_reconciled
        )

    return tuple(
        CampaignRealizedPnl(
            campaign_id=campaign_id,
            proceeds=values["proceeds"],
            cost_basis=values["cost_basis"],
            gain_loss=values["gain_loss"],
            allocation_count=values["allocation_count"],
            record_count=values["record_count"],
            fully_reconciled=values["fully_reconciled"],
        )
        for campaign_id, values in totals.items()
    )
