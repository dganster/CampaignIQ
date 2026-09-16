# tests/domain/test_position_event_reconciler.py
from datetime import datetime
from decimal import Decimal

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_event_reconciler import (
    PositionEventReconciler,
)
from campaigniq.domain.value_objects.instrument import Instrument


def _ibm_assignment() -> PositionEvent:
    return PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=OptionContract(
                    underlying="IBM",
                    expiration=datetime(2026, 8, 21).date(),
                    strike=Decimal("195"),
                    option_type=OptionType.CALL,
                ),
                quantity=Decimal("1"),
            ),
            PositionChange(
                instrument=Instrument("IBM"),
                quantity=Decimal("-100"),
            ),
        ),
        occurred_at=datetime(2026, 8, 7),
    )


def test_suppresses_next_day_equity_expiration_matching_assignment() -> None:
    assignment = _ibm_assignment()

    expiration = PositionEvent(
        kind=PositionEventKind.EXPIRATION,
        changes=(
            PositionChange(
                instrument=Instrument("IBM"),
                quantity=Decimal("-100"),
            ),
        ),
        occurred_at=datetime(2026, 8, 8, 1, 34, 35),
    )

    result = PositionEventReconciler().reconcile(
        (assignment, expiration)
    )

    assert result == (assignment,)


def test_preserves_expiration_with_different_quantity() -> None:
    assignment = _ibm_assignment()

    expiration = PositionEvent(
        kind=PositionEventKind.EXPIRATION,
        changes=(
            PositionChange(
                instrument=Instrument("IBM"),
                quantity=Decimal("-200"),
            ),
        ),
        occurred_at=datetime(2026, 8, 8, 1, 34, 35),
    )

    result = PositionEventReconciler().reconcile(
        (assignment, expiration)
    )

    assert result == (
        assignment,
        expiration,
    )


def test_preserves_expiration_for_different_symbol() -> None:
    assignment = _ibm_assignment()

    expiration = PositionEvent(
        kind=PositionEventKind.EXPIRATION,
        changes=(
            PositionChange(
                instrument=Instrument("MSFT"),
                quantity=Decimal("-100"),
            ),
        ),
        occurred_at=datetime(2026, 8, 8, 1, 34, 35),
    )

    result = PositionEventReconciler().reconcile(
        (assignment, expiration)
    )

    assert result == (
        assignment,
        expiration,
    )


def test_boundary_assignment_can_suppress_prior_weekend_equity_settlement() -> None:
    assignment = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=OptionContract(
                    underlying="GS",
                    expiration=datetime(2026, 6, 18).date(),
                    strike=Decimal("720"),
                    option_type=OptionType.CALL,
                ),
                quantity=Decimal("1"),
            ),
            PositionChange(
                instrument=Instrument("GS"),
                quantity=Decimal("-100"),
            ),
        ),
        occurred_at=datetime(2026, 6, 1),
    )

    settlement = PositionEvent(
        kind=PositionEventKind.EXPIRATION,
        changes=(
            PositionChange(
                instrument=Instrument("GS"),
                quantity=Decimal("-100"),
            ),
        ),
        occurred_at=datetime(2026, 5, 30, 4, 5, 52),
    )

    result = PositionEventReconciler().reconcile(
        (settlement,),
        corroborating_assignments=(assignment,),
    )

    assert result == ()


def test_preserves_matching_expiration_more_than_one_day_later() -> None:
    assignment = _ibm_assignment()

    expiration = PositionEvent(
        kind=PositionEventKind.EXPIRATION,
        changes=(
            PositionChange(
                instrument=Instrument("IBM"),
                quantity=Decimal("-100"),
            ),
        ),
        occurred_at=datetime(2026, 8, 9, 1, 34, 35),
    )

    result = PositionEventReconciler().reconcile(
        (assignment, expiration)
    )

    assert result == (
        assignment,
        expiration,
    )