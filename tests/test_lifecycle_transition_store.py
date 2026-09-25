from datetime import date, datetime
from decimal import Decimal
import json

import pytest

from campaigniq.domain.corporate_action import (
    CorporateActionEvidence,
    CorporateActionType,
)
from campaigniq.domain.covered_position import CoveredCallPosition
from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_roll import OptionRoll
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_exit import PositionExit
from campaigniq.domain.position_lifecycle_transition import (
    PositionLifecycleTransition,
    PositionLifecycleTransitionKind,
)
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.artifact_storage import (
    LocalFilesystemArtifactStorage,
)
from campaigniq.persistence.lifecycle_transition_store import (
    deserialize_lifecycle_transitions,
    load_lifecycle_transitions,
    load_lifecycle_transitions_from_storage,
    save_lifecycle_transitions,
    save_lifecycle_transitions_to_storage,
    serialize_lifecycle_transitions,
)


PERIOD_START = date(2026, 3, 1)
PERIOD_END = date(2026, 3, 31)


def option(
    expiration: date,
    strike: str = "74",
    option_type: OptionType = OptionType.CALL,
) -> OptionContract:
    return OptionContract(
        underlying="NFLX",
        expiration=expiration,
        strike=Decimal(strike),
        option_type=option_type,
    )


def leg(
    *,
    instrument,
    side: Side,
    position_effect: PositionEffect,
    quantity: str,
    price: str,
    occurred_at: datetime,
) -> Leg:
    return Leg(
        instrument=instrument,
        side=side,
        position_effect=position_effect,
        executions=(
            Execution(
                quantity=Decimal(quantity),
                execution_price=Decimal(price),
                executed_at=occurred_at,
            ),
        ),
    )


def all_transitions() -> tuple[PositionLifecycleTransition, ...]:
    split = CorporateActionEvidence(
        symbol="NFLX",
        effective_date=date(2025, 11, 17),
        action_type=CorporateActionType.FORWARD_SPLIT,
        new_units=Decimal("10"),
        old_units=Decimal("1"),
        source="SCHWAB_TRANSACTION_HISTORY",
        source_reference="Options Frwd Split",
    )

    assignment_contract = option(
        date(2025, 12, 19),
        "114",
        OptionType.PUT,
    )
    assignment = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=assignment_contract,
                quantity=Decimal("50"),
            ),
            PositionChange(
                instrument=Instrument("NFLX"),
                quantity=Decimal("5000"),
            ),
        ),
        occurred_at=datetime(2025, 12, 19),
    )

    covered = CoveredCallPosition(
        underlying="NFLX",
        share_quantity=Decimal("5000"),
        short_call_quantity=Decimal("50"),
        required_share_quantity=Decimal("5000"),
        covered_call_quantity=Decimal("50"),
        uncovered_call_quantity=Decimal("0"),
        excess_share_quantity=Decimal("0"),
    )

    roll_at = datetime(2026, 3, 10, 12, 2, 32)
    closed_contract = option(date(2026, 3, 20))
    opened_contract = option(date(2026, 4, 17))
    closed_leg = leg(
        instrument=closed_contract,
        side=Side.BUY,
        position_effect=PositionEffect.CLOSE,
        quantity="50",
        price="23.23",
        occurred_at=roll_at,
    )
    opened_leg = leg(
        instrument=opened_contract,
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        quantity="-50",
        price="23.63",
        occurred_at=roll_at,
    )
    roll = OptionRoll(
        underlying="NFLX",
        closed_contract=closed_contract,
        opened_contract=opened_contract,
        quantity=Decimal("50"),
        closed_leg=closed_leg,
        opened_leg=opened_leg,
    )

    exit_at = datetime(2026, 4, 16, 10, 52, 1)
    stock = Instrument("NFLX")
    exit_trade = Trade(
        legs=(
            leg(
                instrument=stock,
                side=Side.SELL,
                position_effect=PositionEffect.CLOSE,
                quantity="-5000",
                price="107.24",
                occurred_at=exit_at,
            ),
            leg(
                instrument=option(date(2026, 5, 15)),
                side=Side.BUY,
                position_effect=PositionEffect.CLOSE,
                quantity="50",
                price="33.61",
                occurred_at=exit_at,
            ),
        )
    )
    exit_ = PositionExit(
        underlying="NFLX",
        trade=exit_trade,
        before_positions=(
            (stock, Decimal("5000")),
            (option(date(2026, 5, 15)), Decimal("-50")),
        ),
        after_positions=(),
    )

    return (
        PositionLifecycleTransition.from_corporate_action(split),
        PositionLifecycleTransition.from_assignment(
            symbol="NFLX",
            event=assignment,
        ),
        PositionLifecycleTransition.from_covered_position(
            evidence=covered,
            occurred_at=datetime(2025, 12, 31, 23, 59, 59),
        ),
        PositionLifecycleTransition.from_option_roll(roll),
        PositionLifecycleTransition.from_position_exit(exit_),
    )


def test_round_trip_preserves_all_transition_kinds_and_evidence() -> None:
    transitions = all_transitions()

    content = serialize_lifecycle_transitions(
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        transitions=transitions,
    )
    persisted = deserialize_lifecycle_transitions(content)

    assert persisted.period_start == PERIOD_START
    assert persisted.period_end == PERIOD_END
    assert [
        transition.kind
        for transition in persisted.transitions
    ] == [
        PositionLifecycleTransitionKind.CORPORATE_ACTION,
        PositionLifecycleTransitionKind.ASSIGNMENT,
        PositionLifecycleTransitionKind.COVERED_POSITION,
        PositionLifecycleTransitionKind.ROLL,
        PositionLifecycleTransitionKind.EXIT,
    ]

    corporate = persisted.transitions[0]
    assert corporate.corporate_action is not None
    assert corporate.corporate_action.new_units == Decimal("10")
    assert corporate.corporate_action.old_units == Decimal("1")
    assert (
        corporate.corporate_action.source_reference
        == "Options Frwd Split"
    )

    assignment = persisted.transitions[1]
    assert assignment.position_event is not None
    assert len(assignment.position_event.changes) == 2
    assert isinstance(
        assignment.position_event.changes[0].instrument,
        OptionContract,
    )
    assert (
        assignment.position_event.changes[1].quantity
        == Decimal("5000")
    )

    covered = persisted.transitions[2]
    assert covered.covered_position is not None
    assert (
        covered.covered_position.share_quantity
        == Decimal("5000")
    )
    assert covered.covered_position.fully_covered

    roll = persisted.transitions[3]
    assert roll.option_roll is not None
    assert roll.option_roll.quantity == Decimal("50")
    assert (
        roll.option_roll.closed_contract.expiration
        == date(2026, 3, 20)
    )
    assert (
        roll.option_roll.opened_contract.expiration
        == date(2026, 4, 17)
    )
    assert roll.option_roll.closed_leg.side is Side.BUY
    assert roll.option_roll.opened_leg.side is Side.SELL
    assert (
        roll.option_roll.closed_leg.executions[0].execution_price
        == Decimal("23.23")
    )

    exit_transition = persisted.transitions[4]
    assert exit_transition.position_exit is not None
    assert (
        exit_transition.position_exit.before_positions[0][1]
        == Decimal("5000")
    )
    assert exit_transition.position_exit.after_positions == ()
    assert len(exit_transition.position_exit.trade.legs) == 2


def test_serialization_is_deterministic() -> None:
    transitions = all_transitions()

    first = serialize_lifecycle_transitions(
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        transitions=transitions,
    )
    second = serialize_lifecycle_transitions(
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        transitions=transitions,
    )

    assert first == second
    assert first.endswith("\n")


def test_filesystem_round_trip(tmp_path) -> None:
    path = tmp_path / "2026-03-lifecycle-transitions.json"

    save_lifecycle_transitions(
        path,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        transitions=all_transitions(),
    )

    persisted = load_lifecycle_transitions(path)

    assert persisted.period_start == PERIOD_START
    assert persisted.period_end == PERIOD_END
    assert len(persisted.transitions) == 5


def test_storage_round_trip(tmp_path) -> None:
    storage = LocalFilesystemArtifactStorage(tmp_path)
    key = "2026-03-lifecycle-transitions.json"

    save_lifecycle_transitions_to_storage(
        storage,
        key,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        transitions=all_transitions(),
    )

    persisted = load_lifecycle_transitions_from_storage(
        storage,
        key,
    )

    assert len(persisted.transitions) == 5
    assert (
        persisted.transitions[-1].kind
        is PositionLifecycleTransitionKind.EXIT
    )


def test_rejects_unsupported_format() -> None:
    content = serialize_lifecycle_transitions(
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        transitions=(),
    )
    payload = json.loads(content)
    payload["format"] = "wrong"

    with pytest.raises(
        ValueError,
        match="Unsupported lifecycle transition persistence format",
    ):
        deserialize_lifecycle_transitions(json.dumps(payload))


def test_rejects_unsupported_version() -> None:
    content = serialize_lifecycle_transitions(
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        transitions=(),
    )
    payload = json.loads(content)
    payload["version"] = 999

    with pytest.raises(
        ValueError,
        match="Unsupported lifecycle transition persistence version",
    ):
        deserialize_lifecycle_transitions(json.dumps(payload))


def test_rejects_missing_transition_list() -> None:
    content = serialize_lifecycle_transitions(
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        transitions=(),
    )
    payload = json.loads(content)
    del payload["transitions"]

    with pytest.raises(
        ValueError,
        match="'transitions' list",
    ):
        deserialize_lifecycle_transitions(json.dumps(payload))


def test_rejects_transition_symbol_tampering() -> None:
    content = serialize_lifecycle_transitions(
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        transitions=(all_transitions()[3],),
    )
    payload = json.loads(content)
    payload["transitions"][0]["symbol"] = "AAPL"

    with pytest.raises(
        ValueError,
        match="symbol does not match",
    ):
        deserialize_lifecycle_transitions(json.dumps(payload))


def test_rejects_transition_timestamp_tampering() -> None:
    content = serialize_lifecycle_transitions(
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        transitions=(all_transitions()[4],),
    )
    payload = json.loads(content)
    payload["transitions"][0]["occurred_at"] = (
        "2026-04-16T10:53:01"
    )

    with pytest.raises(
        ValueError,
        match="timestamp does not match",
    ):
        deserialize_lifecycle_transitions(json.dumps(payload))
