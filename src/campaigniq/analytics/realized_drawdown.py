"""Realized P&L equity-curve and drawdown analytics."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Mapping

from campaigniq.domain.lot_attribution import RealizedAttribution


ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class RealizedEquityPoint:
    """One daily point on the broker-realized P&L equity curve."""

    closed_date: date
    realized_pnl: Decimal
    cumulative_pnl: Decimal
    running_peak_pnl: Decimal
    drawdown: Decimal


@dataclass(frozen=True, slots=True)
class RealizedDrawdownSummary:
    """Dollar drawdown statistics derived from broker-realized P&L."""

    points: tuple[RealizedEquityPoint, ...]
    total_realized_pnl: Decimal

    maximum_drawdown: Decimal
    maximum_drawdown_peak_date: date | None
    maximum_drawdown_trough_date: date | None
    recovery_date: date | None

    current_drawdown: Decimal
    current_peak_date: date | None
    recovered: bool


def summarize_realized_drawdown(
    attributions: Iterable[RealizedAttribution],
) -> RealizedDrawdownSummary:
    """Build a daily realized-P&L curve and summarize its drawdowns.

    Broker realized records are aggregated by ``closed_date`` before the
    curve is calculated. This avoids inventing an intraday ordering when
    multiple broker records share the same close date.

    The curve begins with a zero-dollar high-water mark. Drawdowns are
    represented as non-positive dollar amounts:

        drawdown = cumulative_pnl - running_peak_pnl

    This summary intentionally does not calculate percentage drawdown.
    CampaignIQ does not yet have a defensible capital/equity denominator
    for that calculation.
    """

    pnl_by_date: dict[date, Decimal] = defaultdict(lambda: ZERO)

    for attribution in attributions:
        record = attribution.record
        pnl_by_date[record.closed_date] += record.gain_loss

    if not pnl_by_date:
        return RealizedDrawdownSummary(
            points=(),
            total_realized_pnl=ZERO,
            maximum_drawdown=ZERO,
            maximum_drawdown_peak_date=None,
            maximum_drawdown_trough_date=None,
            recovery_date=None,
            current_drawdown=ZERO,
            current_peak_date=None,
            recovered=True,
        )

    points: list[RealizedEquityPoint] = []

    cumulative_pnl = ZERO
    running_peak_pnl = ZERO
    running_peak_date: date | None = None

    maximum_drawdown = ZERO
    maximum_drawdown_peak_date: date | None = None
    maximum_drawdown_trough_date: date | None = None
    maximum_drawdown_peak_pnl = ZERO

    for closed_date in sorted(pnl_by_date):
        realized_pnl = pnl_by_date[closed_date]
        cumulative_pnl += realized_pnl

        # A strictly higher cumulative value establishes a new high-water
        # mark. Returning exactly to an old high-water mark is a recovery,
        # not a new peak.
        if cumulative_pnl > running_peak_pnl:
            running_peak_pnl = cumulative_pnl
            running_peak_date = closed_date

        drawdown = cumulative_pnl - running_peak_pnl

        point = RealizedEquityPoint(
            closed_date=closed_date,
            realized_pnl=realized_pnl,
            cumulative_pnl=cumulative_pnl,
            running_peak_pnl=running_peak_pnl,
            drawdown=drawdown,
        )
        points.append(point)

        if drawdown < maximum_drawdown:
            maximum_drawdown = drawdown
            maximum_drawdown_peak_date = running_peak_date
            maximum_drawdown_trough_date = closed_date
            maximum_drawdown_peak_pnl = running_peak_pnl

    recovery_date: date | None = None

    if maximum_drawdown < ZERO:
        assert maximum_drawdown_trough_date is not None

        for point in points:
            if (
                point.closed_date > maximum_drawdown_trough_date
                and point.cumulative_pnl >= maximum_drawdown_peak_pnl
            ):
                recovery_date = point.closed_date
                break

    current_drawdown = points[-1].drawdown
    recovered = current_drawdown == ZERO

    return RealizedDrawdownSummary(
        points=tuple(points),
        total_realized_pnl=cumulative_pnl,
        maximum_drawdown=maximum_drawdown,
        maximum_drawdown_peak_date=maximum_drawdown_peak_date,
        maximum_drawdown_trough_date=maximum_drawdown_trough_date,
        recovery_date=recovery_date,
        current_drawdown=current_drawdown,
        current_peak_date=running_peak_date,
        recovered=recovered,
    )

def summarize_period_qualified_realized_drawdown(
    monthly_attributions: Mapping[
        tuple[date, date],
        Iterable[RealizedAttribution],
    ],
) -> RealizedDrawdownSummary:
    """Summarize drawdown across period-keyed realized attributions.

    Only broker records whose ``closed_date`` falls within the period
    containing the attribution are included. Persisted attribution
    artifacts may contain adjacent-period records required for
    reconciliation, so flattening their raw contents would overstate or
    understate reporting-period realized P&L.

    Period ranges must not overlap. Overlapping ranges would make ownership
    of a broker close date ambiguous and could double-count realized P&L.
    """

    periods = sorted(monthly_attributions)

    previous_end: date | None = None
    for period_start, period_end in periods:
        if period_end < period_start:
            raise ValueError("period_end must be on or after period_start")

        if previous_end is not None and period_start <= previous_end:
            raise ValueError(
                "Realized drawdown periods must not overlap"
            )

        previous_end = period_end

    qualified = (
        attribution
        for period_start, period_end in periods
        for attribution in monthly_attributions[(period_start, period_end)]
        if period_start <= attribution.record.closed_date <= period_end
    )

    return summarize_realized_drawdown(qualified)

