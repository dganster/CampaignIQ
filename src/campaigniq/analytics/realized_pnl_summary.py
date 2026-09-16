"""Period-level realized P&L coverage summary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from campaigniq.domain.lot_attribution import RealizedAttribution


@dataclass(frozen=True, slots=True)
class RealizedPnlSummary:
    """Broker realized P&L and campaign-attribution coverage for a period."""

    period_start: date
    period_end: date
    broker_realized_pnl: Decimal
    attributed_realized_pnl: Decimal
    unattributed_realized_pnl: Decimal
    broker_record_count: int
    attributed_record_count: int
    unattributed_record_count: int

    @property
    def attribution_complete(self) -> bool:
        return self.unattributed_record_count == 0


def summarize_realized_pnl(
    *,
    period_start: date,
    period_end: date,
    attributions: Iterable[RealizedAttribution],
) -> RealizedPnlSummary:
    """Summarize realized P&L whose broker close date falls within the period."""

    if period_end < period_start:
        raise ValueError("period_end must be on or after period_start")

    broker_realized_pnl = Decimal("0")
    attributed_realized_pnl = Decimal("0")
    unattributed_realized_pnl = Decimal("0")

    broker_record_count = 0
    attributed_record_count = 0
    unattributed_record_count = 0

    for attribution in attributions:
        record = attribution.record

        if not period_start <= record.closed_date <= period_end:
            continue

        broker_realized_pnl += record.gain_loss
        broker_record_count += 1

        campaign_attributable = (
            not attribution.has_unassigned_campaign_allocation
            and len(attribution.campaign_ids) == 1
        )

        if campaign_attributable:
            attributed_realized_pnl += record.gain_loss
            attributed_record_count += 1
        else:
            unattributed_realized_pnl += record.gain_loss
            unattributed_record_count += 1

    return RealizedPnlSummary(
        period_start=period_start,
        period_end=period_end,
        broker_realized_pnl=broker_realized_pnl,
        attributed_realized_pnl=attributed_realized_pnl,
        unattributed_realized_pnl=unattributed_realized_pnl,
        broker_record_count=broker_record_count,
        attributed_record_count=attributed_record_count,
        unattributed_record_count=unattributed_record_count,
    )
