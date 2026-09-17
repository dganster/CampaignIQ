"""Underlying-level realized performance from authoritative campaign attributions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median
from collections.abc import Mapping

from campaigniq.domain.lot_attribution import RealizedAttribution


@dataclass(frozen=True, slots=True)
class UnderlyingPerformance:
    """Realized equity/options performance for one underlying."""

    underlying: str
    realized_pnl: Decimal
    campaign_count: int
    winning_campaign_count: int
    losing_campaign_count: int
    breakeven_campaign_count: int
    win_rate: Decimal | None
    average_campaign_pnl: Decimal | None
    median_campaign_pnl: Decimal | None
    best_campaign_id: str | None
    best_campaign_pnl: Decimal | None
    worst_campaign_id: str | None
    worst_campaign_pnl: Decimal | None


def _underlying(attribution: RealizedAttribution) -> str:
    instrument = attribution.record.instrument
    value = getattr(instrument, "underlying", None)
    if value is not None:
        return str(value)
    value = getattr(instrument, "symbol", None)
    if value is not None:
        return str(value)
    return str(instrument)


def summarize_underlying_performance(
    monthly_attributions: Mapping[
        tuple[date, date],
        tuple[RealizedAttribution, ...],
    ],
) -> tuple[UnderlyingPerformance, ...]:
    """Summarize fully reconciled, unambiguous equity/options results by underlying.

    Campaign IDs are period-qualified before aggregation so month-local campaign IDs
    cannot collide. A realized record contributes only when all allocations have
    known provenance, map to exactly one campaign, and the record is fully
    reconciled. Multi-underlying campaigns are split only by authoritative realized
    records; no campaign P&L is duplicated across underlyings.

    FOREX is intentionally outside this v1 analysis because its settlement
    attribution currently carries campaign identity but not the currency-pair
    identity needed by this analytics boundary.
    """
    campaign_pnl: dict[tuple[str, str], Decimal] = {}

    for (period_start, _period_end), attributions in sorted(monthly_attributions.items()):
        prefix = period_start.strftime("%Y-%m")
        for attribution in attributions:
            if attribution.has_unassigned_campaign_allocation:
                continue
            campaign_ids = attribution.campaign_ids
            if len(campaign_ids) != 1:
                continue
            if not attribution.basis_reconciled or not attribution.gain_loss_reconciled:
                continue

            underlying = _underlying(attribution)
            campaign_id = f"{prefix}/{campaign_ids[0]}"
            key = (underlying, campaign_id)
            campaign_pnl[key] = (
                campaign_pnl.get(key, Decimal("0"))
                + attribution.record.gain_loss
            )

    by_underlying: dict[str, list[tuple[str, Decimal]]] = {}
    for (underlying, campaign_id), pnl in campaign_pnl.items():
        by_underlying.setdefault(underlying, []).append((campaign_id, pnl))

    results: list[UnderlyingPerformance] = []
    for underlying in sorted(by_underlying):
        campaigns = sorted(by_underlying[underlying])
        pnl_values = [pnl for _campaign_id, pnl in campaigns]
        wins = [pnl for pnl in pnl_values if pnl > 0]
        losses = [pnl for pnl in pnl_values if pnl < 0]
        breakevens = [pnl for pnl in pnl_values if pnl == 0]
        best_id, best_pnl = max(campaigns, key=lambda item: item[1])
        worst_id, worst_pnl = min(campaigns, key=lambda item: item[1])
        campaign_count = len(campaigns)

        results.append(
            UnderlyingPerformance(
                underlying=underlying,
                realized_pnl=sum(pnl_values, Decimal("0")),
                campaign_count=campaign_count,
                winning_campaign_count=len(wins),
                losing_campaign_count=len(losses),
                breakeven_campaign_count=len(breakevens),
                win_rate=(
                    Decimal(len(wins)) / Decimal(campaign_count)
                    if campaign_count
                    else None
                ),
                average_campaign_pnl=(
                    sum(pnl_values, Decimal("0")) / Decimal(campaign_count)
                    if campaign_count
                    else None
                ),
                median_campaign_pnl=median(pnl_values) if pnl_values else None,
                best_campaign_id=best_id,
                best_campaign_pnl=best_pnl,
                worst_campaign_id=worst_id,
                worst_campaign_pnl=worst_pnl,
            )
        )

    return tuple(results)
