from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.corporate_action import (
    CorporateActionEvidence,
    CorporateActionType,
)
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_lifecycle_transition import (
    PositionLifecycleTransition,
    PositionLifecycleTransitionKind,
)
from campaigniq.domain.value_objects.instrument import Instrument


def nflx_split() -> CorporateActionEvidence:
    return CorporateActionEvidence(
        symbol="NFLX",
        effective_date=date(2025, 11, 17),
        action_type=CorporateActionType.FORWARD_SPLIT,
        new_units=Decimal("10"),
        old_units=Decimal("1"),
        source="SCHWAB_TRANSACTION_HISTORY",
        source_reference="Options Frwd Split",
    )


def nflx_put_assignment() -> PositionEvent:
    contract = OptionContract(
        underlying="NFLX",
        expiration=date(2025, 12, 19),
        strike=Decimal("114"),
        option_type=OptionType.PUT,
    )
    return PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=contract,
                quantity=Decimal("50"),
            ),
            PositionChange(
                instrument=Instrument("NFLX"),
                quantity=Decimal("5000"),
            ),
        ),
        occurred_at=datetime(2025, 12, 19),
    )


def test_corporate_action_transition_reuses_authoritative_evidence() -> None:
    evidence = nflx_split()

    transition = PositionLifecycleTransition.from_corporate_action(evidence)

    assert transition.kind is PositionLifecycleTransitionKind.CORPORATE_ACTION
    assert transition.symbol == "NFLX"
    assert transition.occurred_at == datetime(2025, 11, 17)
    assert transition.corporate_action is evidence
    assert transition.position_event is None


def test_assignment_transition_reuses_existing_position_event() -> None:
    event = nflx_put_assignment()

    transition = PositionLifecycleTransition.from_assignment(
        symbol="NFLX",
        event=event,
    )

    assert transition.kind is PositionLifecycleTransitionKind.ASSIGNMENT
    assert transition.symbol == "NFLX"
    assert transition.occurred_at == datetime(2025, 12, 19)
    assert transition.position_event is event
    assert transition.corporate_action is None


def test_assignment_preserves_option_and_equity_changes() -> None:
    transition = PositionLifecycleTransition.from_assignment(
        symbol="NFLX",
        event=nflx_put_assignment(),
    )

    option_change, equity_change = transition.position_event.changes

    assert isinstance(option_change.instrument, OptionContract)
    assert option_change.instrument.underlying == "NFLX"
    assert option_change.instrument.option_type is OptionType.PUT
    assert option_change.instrument.strike == Decimal("114")
    assert option_change.quantity == Decimal("50")

    assert equity_change.instrument == Instrument("NFLX")
    assert equity_change.quantity == Decimal("5000")


def test_symbol_is_normalized() -> None:
    transition = PositionLifecycleTransition.from_assignment(
        symbol=" nflx ",
        event=nflx_put_assignment(),
    )

    assert transition.symbol == "NFLX"


def test_corporate_action_requires_evidence() -> None:
    with pytest.raises(ValueError, match="requires corporate-action evidence"):
        PositionLifecycleTransition(
            kind=PositionLifecycleTransitionKind.CORPORATE_ACTION,
            symbol="NFLX",
            occurred_at=datetime(2025, 11, 17),
        )


def test_corporate_action_rejects_mismatched_symbol() -> None:
    with pytest.raises(ValueError, match="symbol must match"):
        PositionLifecycleTransition(
            kind=PositionLifecycleTransitionKind.CORPORATE_ACTION,
            symbol="AAPL",
            occurred_at=datetime(2025, 11, 17),
            corporate_action=nflx_split(),
        )


def test_corporate_action_rejects_mismatched_date() -> None:
    with pytest.raises(ValueError, match="effective date must match"):
        PositionLifecycleTransition(
            kind=PositionLifecycleTransitionKind.CORPORATE_ACTION,
            symbol="NFLX",
            occurred_at=datetime(2025, 11, 18),
            corporate_action=nflx_split(),
        )


def test_assignment_requires_position_event() -> None:
    with pytest.raises(ValueError, match="requires a position event"):
        PositionLifecycleTransition(
            kind=PositionLifecycleTransitionKind.ASSIGNMENT,
            symbol="NFLX",
            occurred_at=datetime(2025, 12, 19),
        )


def test_assignment_rejects_non_assignment_event() -> None:
    event = PositionEvent(
        kind=PositionEventKind.EXPIRATION,
        changes=(
            PositionChange(
                instrument=Instrument("NFLX"),
                quantity=Decimal("5000"),
            ),
        ),
        occurred_at=datetime(2025, 12, 19),
    )

    with pytest.raises(ValueError, match="requires an ASSIGNMENT"):
        PositionLifecycleTransition.from_assignment(
            symbol="NFLX",
            event=event,
        )


def test_assignment_rejects_unrelated_symbol() -> None:
    with pytest.raises(ValueError, match="must affect"):
        PositionLifecycleTransition.from_assignment(
            symbol="AAPL",
            event=nflx_put_assignment(),
        )


def test_assignment_rejects_mismatched_timestamp() -> None:
    event = nflx_put_assignment()

    with pytest.raises(ValueError, match="timestamp must match"):
        PositionLifecycleTransition(
            kind=PositionLifecycleTransitionKind.ASSIGNMENT,
            symbol="NFLX",
            occurred_at=datetime(2025, 12, 20),
            position_event=event,
        )


def test_non_evidence_transition_rejects_assignment_event() -> None:
    with pytest.raises(ValueError, match="does not yet accept"):
        PositionLifecycleTransition(
            kind=PositionLifecycleTransitionKind.EXIT,
            symbol="NFLX",
            occurred_at=datetime(2026, 4, 16),
            position_event=nflx_put_assignment(),
        )


def test_empty_symbol_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        PositionLifecycleTransition(
            kind=PositionLifecycleTransitionKind.EXIT,
            symbol=" ",
            occurred_at=datetime(2026, 4, 16),
        )
