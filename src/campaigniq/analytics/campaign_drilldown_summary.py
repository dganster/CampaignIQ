"""Campaign-level drill-down summaries from realized attributions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from campaigniq.domain.lot_attribution import RealizedAttribution


@dataclass(frozen=True, slots=True)
class CampaignDrilldownSummary:
    """Human-readable realized summary for one campaign."""

    campaign_id: str
    symbols: tuple[str, ...]
    realized_pnl: Decimal
    record_count: int
    allocation_count: int
    first_closed_date: date
    last_closed_date: date
    fully_reconciled: bool


def _symbol(attribution: RealizedAttribution) -> str:
    instrument = attribution.record.instrument
    underlying = getattr(instrument, "underlying", None)
    if underlying is not None:
        return str(underlying)

    symbol = getattr(instrument, "symbol", None)
    if symbol is not None:
        return str(symbol)

    return str(instrument)


def summarize_campaign_drilldowns(
    attributions: Iterable[RealizedAttribution],
) -> tuple[CampaignDrilldownSummary, ...]:
    """Aggregate authoritative realized attributions by unambiguous campaign."""

    totals: dict[str, dict[str, object]] = {}

    for attribution in attributions:
        if attribution.has_unassigned_campaign_allocation:
            continue

        campaign_ids = attribution.campaign_ids
        if len(campaign_ids) != 1:
            continue

        campaign_id = campaign_ids[0]
        total = totals.setdefault(
            campaign_id,
            {
                "symbols": set(),
                "realized_pnl": Decimal("0"),
                "record_count": 0,
                "allocation_count": 0,
                "first_closed_date": attribution.record.closed_date,
                "last_closed_date": attribution.record.closed_date,
                "fully_reconciled": True,
            },
        )

        symbols = total["symbols"]
        assert isinstance(symbols, set)
        symbols.add(_symbol(attribution))

        total["realized_pnl"] += attribution.record.gain_loss
        total["record_count"] += 1
        total["allocation_count"] += len(attribution.allocations)
        total["first_closed_date"] = min(
            total["first_closed_date"],
            attribution.record.closed_date,
        )
        total["last_closed_date"] = max(
            total["last_closed_date"],
            attribution.record.closed_date,
        )
        total["fully_reconciled"] = (
            total["fully_reconciled"]
            and attribution.basis_reconciled
            and attribution.gain_loss_reconciled
        )

    summaries: list[CampaignDrilldownSummary] = []

    for campaign_id in sorted(totals):
        values = totals[campaign_id]
        summaries.append(
            CampaignDrilldownSummary(
                campaign_id=campaign_id,
                symbols=tuple(sorted(values["symbols"])),
                realized_pnl=values["realized_pnl"],
                record_count=values["record_count"],
                allocation_count=values["allocation_count"],
                first_closed_date=values["first_closed_date"],
                last_closed_date=values["last_closed_date"],
                fully_reconciled=values["fully_reconciled"],
            )
        )

    return tuple(summaries)
