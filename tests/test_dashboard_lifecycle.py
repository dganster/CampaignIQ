from datetime import datetime
from decimal import Decimal

from campaigniq.analytics.lifecycle_analytics import SymbolLifecycleSummary
from campaigniq.domain.covered_position import CoveredCallPosition
from campaigniq.domain.position_lifecycle_transition import (
    PositionLifecycleTransition,
)
from campaigniq.ui.dashboard import lifecycle_timeline_rows


def test_lifecycle_timeline_rows_preserve_authoritative_order() -> None:
    first = PositionLifecycleTransition.from_covered_position(
        evidence=CoveredCallPosition(
            underlying="NFLX",
            share_quantity=Decimal("5000"),
            short_call_quantity=Decimal("50"),
            required_share_quantity=Decimal("5000"),
            covered_call_quantity=Decimal("50"),
            uncovered_call_quantity=Decimal("0"),
            excess_share_quantity=Decimal("0"),
        ),
        occurred_at=datetime(2025, 12, 31),
    )

    second = PositionLifecycleTransition.from_covered_position(
        evidence=CoveredCallPosition(
            underlying="NFLX",
            share_quantity=Decimal("5000"),
            short_call_quantity=Decimal("50"),
            required_share_quantity=Decimal("5000"),
            covered_call_quantity=Decimal("50"),
            uncovered_call_quantity=Decimal("0"),
            excess_share_quantity=Decimal("0"),
        ),
        occurred_at=datetime(2026, 1, 31, 15, 30),
    )

    summary = SymbolLifecycleSummary(
        symbol="NFLX",
        transition_count=2,
        corporate_action_count=0,
        assignment_count=0,
        covered_position_count=2,
        roll_count=0,
        exit_count=0,
        first_transition_at=first.occurred_at,
        last_transition_at=second.occurred_at,
        transitions=(first, second),
    )

    assert lifecycle_timeline_rows(summary) == [
        {
            "Date": first.occurred_at.date(),
            "Time": first.occurred_at.time(),
            "Transition": "COVERED POSITION",
        },
        {
            "Date": second.occurred_at.date(),
            "Time": second.occurred_at.time(),
            "Transition": "COVERED POSITION",
        },
    ]
