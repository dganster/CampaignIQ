"""Repeated realized-campaign performance by underlying."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median
from collections.abc import Mapping

from campaigniq.domain.lot_attribution import RealizedAttribution


@dataclass(frozen=True, slots=True)
class RepeatedCampaignPerformance:
    """First-versus-subsequent realized campaign results for one underlying."""

    underlying: str
    campaign_count: int
    first_campaign_id: str
    first_realized_date: date
    first_campaign_pnl: Decimal
    subsequent_campaign_count: int
    subsequent_realized_pnl: Decimal
    subsequent_winning_campaign_count: int
    subsequent_losing_campaign_count: int
    subsequent_breakeven_campaign_count: int
    subsequent_win_rate: Decimal | None
    subsequent_average_campaign_pnl: Decimal | None
    subsequent_median_campaign_pnl: Decimal | None


def _underlying(attribution: RealizedAttribution) -> str:
    instrument = attribution.record.instrument
    value = getattr(instrument, "underlying", None)
    if value is not None:
        return str(value)
    value = getattr(instrument, "symbol", None)
    if value is not None:
        return str(value)
    return str(instrument)


def summarize_repeated_campaign_performance(
    monthly_attributions: Mapping[
        tuple[date, date],
        tuple[RealizedAttribution, ...],
    ],
) -> tuple[RepeatedCampaignPerformance, ...]:
    """Compare first and subsequent realized campaigns for repeated underlyings.

    This is realized-campaign chronology, not true campaign-start chronology.
    Campaigns are ordered by their earliest authoritative realized close date,
    with period-qualified campaign ID as a deterministic tie-breaker.

    Only fully reconciled, unambiguous equity/options realized records are used.
    Underlyings with fewer than two qualifying campaigns are omitted.
    """

    campaign_data: dict[tuple[str, str], dict[str, object]] = {}

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
            values = campaign_data.setdefault(
                key,
                {
                    "pnl": Decimal("0"),
                    "first_realized_date": attribution.record.closed_date,
                },
            )
            values["pnl"] += attribution.record.gain_loss
            values["first_realized_date"] = min(
                values["first_realized_date"],
                attribution.record.closed_date,
            )

    by_underlying: dict[str, list[tuple[date, str, Decimal]]] = {}
    for (underlying, campaign_id), values in campaign_data.items():
        by_underlying.setdefault(underlying, []).append(
            (
                values["first_realized_date"],
                campaign_id,
                values["pnl"],
            )
        )

    results: list[RepeatedCampaignPerformance] = []
    for underlying in sorted(by_underlying):
        campaigns = sorted(by_underlying[underlying], key=lambda item: (item[0], item[1]))
        if len(campaigns) < 2:
            continue

        first_date, first_id, first_pnl = campaigns[0]
        subsequent = campaigns[1:]
        subsequent_pnls = [pnl for _day, _campaign_id, pnl in subsequent]
        wins = [pnl for pnl in subsequent_pnls if pnl > 0]
        losses = [pnl for pnl in subsequent_pnls if pnl < 0]
        breakevens = [pnl for pnl in subsequent_pnls if pnl == 0]
        count = len(subsequent)

        results.append(
            RepeatedCampaignPerformance(
                underlying=underlying,
                campaign_count=len(campaigns),
                first_campaign_id=first_id,
                first_realized_date=first_date,
                first_campaign_pnl=first_pnl,
                subsequent_campaign_count=count,
                subsequent_realized_pnl=sum(subsequent_pnls, Decimal("0")),
                subsequent_winning_campaign_count=len(wins),
                subsequent_losing_campaign_count=len(losses),
                subsequent_breakeven_campaign_count=len(breakevens),
                subsequent_win_rate=Decimal(len(wins)) / Decimal(count),
                subsequent_average_campaign_pnl=(
                    sum(subsequent_pnls, Decimal("0")) / Decimal(count)
                ),
                subsequent_median_campaign_pnl=median(subsequent_pnls),
            )
        )

    return tuple(results)
