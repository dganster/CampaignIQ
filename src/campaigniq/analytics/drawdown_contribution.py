"""Underlying contribution analysis for realized maximum drawdown."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Mapping

from campaigniq.analytics.realized_drawdown import (
    summarize_period_qualified_realized_drawdown,
)
from campaigniq.domain.lot_attribution import RealizedAttribution


ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class DrawdownUnderlyingContribution:
    """Broker-fact contribution of one underlying to maximum drawdown."""

    underlying: str
    record_count: int
    winning_record_count: int
    losing_record_count: int
    breakeven_record_count: int
    gross_gain: Decimal
    gross_loss: Decimal
    net_realized_pnl: Decimal
    largest_gain: Decimal | None
    largest_loss: Decimal | None
    drawdown_contribution: Decimal


@dataclass(frozen=True, slots=True)
class MaximumDrawdownContributionSummary:
    """Underlying decomposition of the maximum realized drawdown interval."""

    peak_date: date | None
    trough_date: date | None
    maximum_drawdown: Decimal
    interval_realized_pnl: Decimal
    record_count: int
    contributions: tuple[DrawdownUnderlyingContribution, ...]


def _underlying(attribution: RealizedAttribution) -> str:
    instrument = attribution.record.instrument

    value = getattr(instrument, "underlying", None)
    if value is not None:
        return str(value)

    value = getattr(instrument, "symbol", None)
    if value is not None:
        return str(value)

    return str(instrument)


def summarize_maximum_drawdown_contributions(
    monthly_attributions: Mapping[
        tuple[date, date],
        tuple[RealizedAttribution, ...],
    ],
) -> MaximumDrawdownContributionSummary:
    """Decompose the period-qualified maximum drawdown by underlying.

    The analysis is intentionally based on broker realized records rather
    than campaign-qualified records. Unassigned, ambiguous, or unreconciled
    campaign provenance therefore does not cause realized P&L to disappear.

    The drawdown interval begins strictly after the high-water-mark date and
    includes the trough date. If the curve's high-water mark is the starting
    zero baseline, ``peak_date`` is None and all qualified records through the
    trough participate.

    ``drawdown_contribution`` is signed net realized P&L divided by the
    absolute maximum drawdown. Negative values contributed to drawdown;
    positive values offset losses elsewhere.
    """

    drawdown = summarize_period_qualified_realized_drawdown(
        monthly_attributions
    )

    peak_date = drawdown.maximum_drawdown_peak_date
    trough_date = drawdown.maximum_drawdown_trough_date

    if trough_date is None or drawdown.maximum_drawdown == ZERO:
        return MaximumDrawdownContributionSummary(
            peak_date=peak_date,
            trough_date=trough_date,
            maximum_drawdown=drawdown.maximum_drawdown,
            interval_realized_pnl=ZERO,
            record_count=0,
            contributions=(),
        )

    interval: list[RealizedAttribution] = []

    for (period_start, period_end), attributions in sorted(
        monthly_attributions.items()
    ):
        for attribution in attributions:
            closed_date = attribution.record.closed_date

            if not period_start <= closed_date <= period_end:
                continue

            after_peak = peak_date is None or closed_date > peak_date
            if after_peak and closed_date <= trough_date:
                interval.append(attribution)

    totals: dict[str, dict[str, object]] = {}

    for attribution in interval:
        underlying = _underlying(attribution)
        pnl = attribution.record.gain_loss

        values = totals.setdefault(
            underlying,
            {
                "record_count": 0,
                "winning_record_count": 0,
                "losing_record_count": 0,
                "breakeven_record_count": 0,
                "gross_gain": ZERO,
                "gross_loss": ZERO,
                "net_realized_pnl": ZERO,
                "largest_gain": None,
                "largest_loss": None,
            },
        )

        values["record_count"] += 1
        values["net_realized_pnl"] += pnl

        if pnl > ZERO:
            values["winning_record_count"] += 1
            values["gross_gain"] += pnl
            largest_gain = values["largest_gain"]
            if largest_gain is None or pnl > largest_gain:
                values["largest_gain"] = pnl
        elif pnl < ZERO:
            values["losing_record_count"] += 1
            values["gross_loss"] += pnl
            largest_loss = values["largest_loss"]
            if largest_loss is None or pnl < largest_loss:
                values["largest_loss"] = pnl
        else:
            values["breakeven_record_count"] += 1

    denominator = abs(drawdown.maximum_drawdown)

    contributions = tuple(
        DrawdownUnderlyingContribution(
            underlying=underlying,
            record_count=values["record_count"],
            winning_record_count=values["winning_record_count"],
            losing_record_count=values["losing_record_count"],
            breakeven_record_count=values["breakeven_record_count"],
            gross_gain=values["gross_gain"],
            gross_loss=values["gross_loss"],
            net_realized_pnl=values["net_realized_pnl"],
            largest_gain=values["largest_gain"],
            largest_loss=values["largest_loss"],
            drawdown_contribution=(
                values["net_realized_pnl"] / denominator
            ),
        )
        for underlying, values in sorted(
            totals.items(),
            key=lambda item: (item[1]["net_realized_pnl"], item[0]),
        )
    )

    interval_realized_pnl = sum(
        (
            contribution.net_realized_pnl
            for contribution in contributions
        ),
        ZERO,
    )

    return MaximumDrawdownContributionSummary(
        peak_date=peak_date,
        trough_date=trough_date,
        maximum_drawdown=drawdown.maximum_drawdown,
        interval_realized_pnl=interval_realized_pnl,
        record_count=len(interval),
        contributions=contributions,
    )
