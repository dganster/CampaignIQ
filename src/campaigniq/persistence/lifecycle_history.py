"""Cross-period history assembled from persisted lifecycle evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re

from campaigniq.domain.position_lifecycle_transition import (
    PositionLifecycleTransition,
    PositionLifecycleTransitionKind,
)
from campaigniq.persistence.artifact_storage import ArtifactStorage
from campaigniq.persistence.lifecycle_transition_store import (
    PersistedLifecycleTransitions,
    load_lifecycle_transitions_from_storage,
)
from campaigniq.persistence.monthly_publication import (
    is_month_published_in_storage,
)


_LIFECYCLE_SUFFIX = "-lifecycle-transitions.json"
_LIFECYCLE_KEY = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})"
    r"-lifecycle-transitions\.json$"
)


@dataclass(frozen=True, slots=True)
class LifecycleHistory:
    """Authoritative lifecycle evidence across persisted periods."""

    periods: tuple[PersistedLifecycleTransitions, ...]
    transitions: tuple[PositionLifecycleTransition, ...]

    def for_symbol(
        self,
        symbol: str,
    ) -> tuple[PositionLifecycleTransition, ...]:
        """Return transitions for one underlying symbol."""

        normalized = symbol.upper()
        return tuple(
            transition
            for transition in self.transitions
            if transition.symbol.upper() == normalized
        )

    def of_kind(
        self,
        kind: PositionLifecycleTransitionKind,
    ) -> tuple[PositionLifecycleTransition, ...]:
        """Return transitions of one lifecycle kind."""

        return tuple(
            transition
            for transition in self.transitions
            if transition.kind is kind
        )


def _period_end_from_key(key: str) -> date:
    match = _LIFECYCLE_KEY.fullmatch(key)
    if match is None:
        raise ValueError(
            f"Invalid lifecycle artifact key: {key!r}"
        )

    year = int(match.group("year"))
    month = int(match.group("month"))

    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)

    return date.fromordinal(next_month.toordinal() - 1)


def _validate_periods(
    periods: tuple[PersistedLifecycleTransitions, ...],
) -> None:
    previous: PersistedLifecycleTransitions | None = None

    for period in periods:
        if period.period_start > period.period_end:
            raise ValueError(
                "Lifecycle period start must not be after period end."
            )

        if (
            previous is not None
            and period.period_start <= previous.period_end
        ):
            raise ValueError(
                "Persisted lifecycle periods must not overlap."
            )

        previous = period


def load_lifecycle_history_from_storage(
    storage: ArtifactStorage,
) -> LifecycleHistory:
    """Load authoritative lifecycle history from published monthly artifacts."""

    keys = storage.list_keys(suffix=_LIFECYCLE_SUFFIX)
    periods: list[PersistedLifecycleTransitions] = []

    for key in keys:
        period_end = _period_end_from_key(key)

        if not is_month_published_in_storage(
            storage,
            period_end=period_end,
        ):
            continue

        persisted = load_lifecycle_transitions_from_storage(
            storage,
            key,
        )

        if persisted.period_end != period_end:
            raise ValueError(
                "Lifecycle artifact period end does not match "
                f"its key: {key!r}"
            )

        expected_start = period_end.replace(day=1)
        if persisted.period_start != expected_start:
            raise ValueError(
                "Lifecycle artifact period start does not match "
                f"its key: {key!r}"
            )

        periods.append(persisted)

    ordered_periods = tuple(
        sorted(
            periods,
            key=lambda period: (
                period.period_start,
                period.period_end,
            ),
        )
    )
    _validate_periods(ordered_periods)

    transitions = tuple(
        sorted(
            (
                transition
                for period in ordered_periods
                for transition in period.transitions
            ),
            key=lambda transition: (
                transition.occurred_at,
                transition.symbol,
                transition.kind.value,
            ),
        )
    )

    return LifecycleHistory(
        periods=ordered_periods,
        transitions=transitions,
    )
