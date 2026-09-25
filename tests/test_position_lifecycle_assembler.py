from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.corporate_action import (
    CorporateActionEvidence,
    CorporateActionType,
)
from campaigniq.domain.covered_position import CoveredCallPosition
from campaigniq.domain.execution import Execution
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_roll import OptionRoll
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_lifecycle_assembler import (
    ObservedCoveredPosition,
    PositionLifecycleAssembler,
)
from campaigniq.domain.position_lifecycle_transition import (
    PositionLifecycleTransitionKind,
)
from campaigniq.domain.side import Side
from campaigniq.domain.value_objects.instrument import Instrument


def split(symbol: str = "NFLX") -> CorporateActionEvidence:
    return CorporateActionEvidence(
        symbol=symbol,
        effective_date=date(2025, 11, 17),
        action_type=CorporateActionType.FORWARD_SPLIT,
        new_units=Decimal("10"),
        old_units=Decimal("1"),
        source="TEST",
    )


def assignment(symbol: str = "NFLX") -> PositionEvent:
    return PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=Instrument(symbol),
                quantity=Decimal("5000"),
            ),
        ),
        occurred_at=datetime(2025, 12, 19),
    )


def covered(symbol: str = "NFLX") -> ObservedCoveredPosition:
    return ObservedCoveredPosition(
        position=CoveredCallPosition(
            underlying=symbol,
            share_quantity=Decimal("5000"),
            short_call_quantity=Decimal("50"),
            required_share_quantity=Decimal("5000"),
            covered_call_quantity=Decimal("50"),
            uncovered_call_quantity=Decimal("0"),
            excess_share_quantity=Decimal("0"),
        ),
        observed_at=datetime(2025, 12, 31),
    )


def roll(
    *,
    symbol: str = "NFLX",
    when: datetime = datetime(2026, 3, 10, 12, 2, 32),
) -> OptionRoll:
    closed_contract = OptionContract(
        underlying=symbol,
        expiration=date(2026, 3, 20),
        strike=Decimal("74"),
        option_type=OptionType.CALL,
    )
    opened_contract = OptionContract(
        underlying=symbol,
        expiration=date(2026, 4, 17),
        strike=Decimal("74"),
        option_type=OptionType.CALL,
    )

    closed_leg = OptionLeg(
        contract=closed_contract,
        side=Side.BUY,
        position_effect=PositionEffect.CLOSE,
        executions=(
            Execution(
                quantity=Decimal("50"),
                execution_price=Decimal("23.23"),
                executed_at=when,
            ),
        ),
        broker_strategy="CALENDAR",
    )
    opened_leg = OptionLeg(
        contract=opened_contract,
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        executions=(
            Execution(
                quantity=Decimal("-50"),
                execution_price=Decimal("23.63"),
                executed_at=when,
            ),
        ),
        broker_strategy="CALENDAR",
    )

    return OptionRoll(
        underlying=symbol,
        closed_contract=closed_contract,
        opened_contract=opened_contract,
        quantity=Decimal("50"),
        closed_leg=closed_leg,
        opened_leg=opened_leg,
    )


def test_assembles_evidence_in_chronological_order() -> None:
    transitions = PositionLifecycleAssembler().assemble(
        symbol="NFLX",
        option_rolls=(roll(),),
        covered_positions=(covered(),),
        corporate_actions=(split(),),
        assignments=(assignment(),),
    )

    assert [
        transition.kind
        for transition in transitions
    ] == [
        PositionLifecycleTransitionKind.CORPORATE_ACTION,
        PositionLifecycleTransitionKind.ASSIGNMENT,
        PositionLifecycleTransitionKind.COVERED_POSITION,
        PositionLifecycleTransitionKind.ROLL,
    ]

    assert [
        transition.occurred_at
        for transition in transitions
    ] == sorted(
        transition.occurred_at
        for transition in transitions
    )


def test_assembly_filters_unrelated_evidence() -> None:
    transitions = PositionLifecycleAssembler().assemble(
        symbol="NFLX",
        corporate_actions=(
            split("AAPL"),
            split("NFLX"),
        ),
        assignments=(
            assignment("AAPL"),
            assignment("NFLX"),
        ),
        covered_positions=(
            covered("AAPL"),
            covered("NFLX"),
        ),
        option_rolls=(
            roll(symbol="AAPL"),
            roll(symbol="NFLX"),
        ),
    )

    assert len(transitions) == 4
    assert all(
        transition.symbol == "NFLX"
        for transition in transitions
    )


def test_assembly_ignores_non_assignment_position_events() -> None:
    event = PositionEvent(
        kind=PositionEventKind.EXPIRATION,
        changes=(
            PositionChange(
                instrument=Instrument("NFLX"),
                quantity=Decimal("-100"),
            ),
        ),
        occurred_at=datetime(2026, 1, 16),
    )

    transitions = PositionLifecycleAssembler().assemble(
        symbol="NFLX",
        assignments=(event,),
    )

    assert transitions == ()


def test_empty_symbol_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="symbol must not be empty",
    ):
        PositionLifecycleAssembler().assemble(symbol=" ")
