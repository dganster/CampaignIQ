"""Load persisted realized-attribution periods for multi-month analytics."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path

from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.persistence.realized_attribution_store import (
    load_realized_attributions,
)


def load_persisted_monthly_attributions(
    paths: Iterable[str | Path],
) -> Mapping[
    tuple[date, date],
    tuple[RealizedAttribution, ...],
]:
    """Load persisted periods keyed by their embedded date range."""

    periods: dict[
        tuple[date, date],
        tuple[RealizedAttribution, ...],
    ] = {}

    for path in paths:
        persisted = load_realized_attributions(path)
        key = (persisted.period_start, persisted.period_end)

        if key in periods:
            raise ValueError(
                "Duplicate persisted realized attribution period: "
                f"{persisted.period_start.isoformat()} "
                f"through {persisted.period_end.isoformat()}"
            )

        periods[key] = persisted.attributions

    return dict(sorted(periods.items()))


def load_persisted_monthly_campaign_attributions(
    *,
    realized_paths: Iterable[str | Path],
    forex_paths: Iterable[str | Path],
) -> tuple[
    Mapping[tuple[date, date], tuple[RealizedAttribution, ...]],
    Mapping[tuple[date, date], tuple["ForexSettlementAttribution", ...]],
]:
    """Load equity/options and FOREX campaign attributions by period.

    FOREX coverage is explicit: a realized period without a matching FOREX
    persistence artifact is not silently represented by an empty tuple.
    """
    from campaigniq.domain.forex_settlement_attribution import (
        ForexSettlementAttribution,
    )
    from campaigniq.persistence.forex_settlement_attribution_store import (
        load_forex_settlement_attributions,
    )

    realized = load_persisted_monthly_attributions(realized_paths)

    forex: dict[
        tuple[date, date],
        tuple[ForexSettlementAttribution, ...],
    ] = {}
    for path in forex_paths:
        persisted = load_forex_settlement_attributions(path)
        key = (persisted.period_start, persisted.period_end)
        if key in forex:
            raise ValueError(
                "Duplicate persisted FOREX settlement attribution period: "
                f"{persisted.period_start.isoformat()} "
                f"through {persisted.period_end.isoformat()}"
            )
        if key not in realized:
            raise ValueError(
                "Persisted FOREX settlement attribution period has no matching "
                "realized attribution period: "
                f"{persisted.period_start.isoformat()} "
                f"through {persisted.period_end.isoformat()}"
            )
        forex[key] = persisted.attributions

    return realized, dict(sorted(forex.items()))
