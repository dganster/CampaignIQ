"""Historical-completeness validation at a reconstruction boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class BoundaryStatus(str, Enum):
    """Completeness status for one reconstruction input."""

    COMPLETE = "COMPLETE"
    MISSING = "MISSING"
    PARTIAL = "PARTIAL"


class PendingActivityStatus(str, Enum):
    """Whether activity is legitimately pending the period boundary."""

    NONE = "NONE"
    PRESENT = "PRESENT"


@dataclass(frozen=True, slots=True)
class HistoricalRequirement:
    """One actionable request for additional historical evidence."""

    case_id: str
    earliest_unresolved_date: date
    months: tuple[str, ...]
    document_types: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class BoundaryValidation:
    """Evidence-completeness result for one reconstructed period."""

    period: str
    opening_inventory: BoundaryStatus
    opening_inventory_source: str | None
    trading_activity: BoundaryStatus
    ending_inventory: BoundaryStatus
    ending_inventory_source: str | None
    realized_pnl: BoundaryStatus
    pending_activity: PendingActivityStatus
    unresolved_positions: tuple[str, ...]
    unresolved_campaigns: tuple[str, ...]
    historical_requirements: tuple[HistoricalRequirement, ...]
    excluded_cases: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """Return whether the reconstruction is complete and nothing is excluded."""
        return (
            self.opening_inventory is BoundaryStatus.COMPLETE
            and self.trading_activity is BoundaryStatus.COMPLETE
            and self.ending_inventory is BoundaryStatus.COMPLETE
            and self.realized_pnl is BoundaryStatus.COMPLETE
            and not self.unresolved_positions
            and not self.unresolved_campaigns
            and not self.excluded_cases
        )

    @property
    def has_action_required(self) -> bool:
        """Return whether the user must supply history or choose exclusions."""
        return bool(self.historical_requirements)


class BoundaryValidator:
    """Validate the evidence available at a monthly reconstruction boundary."""

    ACCOUNT_TRADE_HISTORY = "Account Trade History"
    BROKERAGE_STATEMENT = "Brokerage Statement"
    REALIZED_GAIN_LOSS = "Realized Gain/Loss report"

    def validate(
        self,
        *,
        period: str,
        opening_inventory: BoundaryStatus,
        opening_inventory_source: str | None,
        trading_activity: BoundaryStatus,
        ending_inventory: BoundaryStatus,
        ending_inventory_source: str | None,
        realized_pnl: BoundaryStatus,
        pending_activity: PendingActivityStatus = PendingActivityStatus.NONE,
        unresolved_positions: tuple[str, ...] = (),
        unresolved_campaigns: tuple[str, ...] = (),
        historical_requirements: tuple[HistoricalRequirement, ...] = (),
        excluded_cases: tuple[str, ...] = (),
    ) -> BoundaryValidation:
        """Return a boundary report without silently treating gaps as complete."""
        warnings: list[str] = []

        if pending_activity is PendingActivityStatus.PRESENT:
            warnings.append(
                "Pending/unsettled activity is a legitimate period-boundary "
                "condition and is not itself missing data."
            )

        if excluded_cases:
            warnings.append(
                f"{len(excluded_cases)} case(s) were explicitly excluded by the user."
            )

        if unresolved_positions or unresolved_campaigns:
            warnings.append(
                "Historical ancestry remains unresolved for one or more cases."
            )

        return BoundaryValidation(
            period=period,
            opening_inventory=opening_inventory,
            opening_inventory_source=opening_inventory_source,
            trading_activity=trading_activity,
            ending_inventory=ending_inventory,
            ending_inventory_source=ending_inventory_source,
            realized_pnl=realized_pnl,
            pending_activity=pending_activity,
            unresolved_positions=tuple(unresolved_positions),
            unresolved_campaigns=tuple(unresolved_campaigns),
            historical_requirements=tuple(historical_requirements),
            excluded_cases=tuple(excluded_cases),
            warnings=tuple(warnings),
        )


def month_labels(
    earliest: date,
    latest: date,
) -> tuple[str, ...]:
    """Return inclusive YYYY-MM month labels for a historical date range."""
    if earliest > latest:
        raise ValueError("Earliest date cannot be after latest date.")

    year, month = earliest.year, earliest.month
    labels: list[str] = []

    while (year, month) <= (latest.year, latest.month):
        labels.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1

    return tuple(labels)
