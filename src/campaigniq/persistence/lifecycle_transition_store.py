"""Persistence for evidence-backed position lifecycle transitions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from campaigniq.domain.corporate_action import (
    CorporateActionEvidence,
    CorporateActionType,
)
from campaigniq.domain.covered_position import CoveredCallPosition
from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
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
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.artifact_storage import ArtifactStorage


_FORMAT = "campaigniq.lifecycle_transitions"
_VERSION = 1


@dataclass(frozen=True, slots=True)
class PersistedLifecycleTransitions:
    period_start: date
    period_end: date
    transitions: tuple[PositionLifecycleTransition, ...]


def serialize_lifecycle_transitions(
    *,
    period_start: date,
    period_end: date,
    transitions: Iterable[PositionLifecycleTransition],
) -> str:
    payload = {
        "format": _FORMAT,
        "version": _VERSION,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "transitions": [
            _serialize_transition(transition)
            for transition in transitions
        ],
    }
    return json.dumps(
        payload,
        indent=2,
        sort_keys=True,
    ) + "\n"


def deserialize_lifecycle_transitions(
    content: str,
) -> PersistedLifecycleTransitions:
    payload = json.loads(content)

    if payload.get("format") != _FORMAT:
        raise ValueError(
            "Unsupported lifecycle transition persistence format."
        )

    if payload.get("version") != _VERSION:
        raise ValueError(
            "Unsupported lifecycle transition persistence version: "
            f"{payload.get('version')!r}"
        )

    transitions = payload.get("transitions")
    if not isinstance(transitions, list):
        raise ValueError(
            "Lifecycle transition persistence payload must contain "
            "a 'transitions' list."
        )

    period_start = payload.get("period_start")
    period_end = payload.get("period_end")

    if not isinstance(period_start, str):
        raise ValueError(
            "Lifecycle transition persistence payload must contain "
            "a 'period_start' date."
        )

    if not isinstance(period_end, str):
        raise ValueError(
            "Lifecycle transition persistence payload must contain "
            "a 'period_end' date."
        )

    return PersistedLifecycleTransitions(
        period_start=date.fromisoformat(period_start),
        period_end=date.fromisoformat(period_end),
        transitions=tuple(
            _deserialize_transition(item)
            for item in transitions
        ),
    )


def save_lifecycle_transitions_to_storage(
    storage: ArtifactStorage,
    key: str,
    *,
    period_start: date,
    period_end: date,
    transitions: Iterable[PositionLifecycleTransition],
) -> None:
    storage.write_text(
        key,
        serialize_lifecycle_transitions(
            period_start=period_start,
            period_end=period_end,
            transitions=transitions,
        ),
    )


def load_lifecycle_transitions_from_storage(
    storage: ArtifactStorage,
    key: str,
) -> PersistedLifecycleTransitions:
    return deserialize_lifecycle_transitions(
        storage.read_text(key)
    )


def save_lifecycle_transitions(
    path: str | Path,
    *,
    period_start: date,
    period_end: date,
    transitions: Iterable[PositionLifecycleTransition],
) -> None:
    Path(path).write_text(
        serialize_lifecycle_transitions(
            period_start=period_start,
            period_end=period_end,
            transitions=transitions,
        ),
        encoding="utf-8",
    )


def load_lifecycle_transitions(
    path: str | Path,
) -> PersistedLifecycleTransitions:
    return deserialize_lifecycle_transitions(
        Path(path).read_text(encoding="utf-8")
    )


def _serialize_transition(
    transition: PositionLifecycleTransition,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": transition.kind.value,
        "symbol": transition.symbol,
        "occurred_at": transition.occurred_at.isoformat(),
    }

    if (
        transition.kind
        is PositionLifecycleTransitionKind.CORPORATE_ACTION
    ):
        assert transition.corporate_action is not None
        payload["corporate_action"] = _serialize_corporate_action(
            transition.corporate_action
        )
        return payload

    if (
        transition.kind
        is PositionLifecycleTransitionKind.ASSIGNMENT
    ):
        assert transition.position_event is not None
        payload["position_event"] = _serialize_position_event(
            transition.position_event
        )
        return payload

    if (
        transition.kind
        is PositionLifecycleTransitionKind.COVERED_POSITION
    ):
        assert transition.covered_position is not None
        payload["covered_position"] = _serialize_covered_position(
            transition.covered_position
        )
        return payload

    if transition.kind is PositionLifecycleTransitionKind.ROLL:
        assert transition.option_roll is not None
        payload["option_roll"] = _serialize_option_roll(
            transition.option_roll
        )
        return payload

    if transition.kind is PositionLifecycleTransitionKind.EXIT:
        assert transition.position_exit is not None
        payload["position_exit"] = _serialize_position_exit(
            transition.position_exit
        )
        return payload

    raise ValueError(
        f"Unsupported lifecycle transition kind: {transition.kind!r}"
    )


def _deserialize_transition(
    payload: dict[str, object],
) -> PositionLifecycleTransition:
    kind = PositionLifecycleTransitionKind(payload["kind"])
    symbol = str(payload["symbol"])
    occurred_at = datetime.fromisoformat(str(payload["occurred_at"]))

    if kind is PositionLifecycleTransitionKind.CORPORATE_ACTION:
        transition = PositionLifecycleTransition.from_corporate_action(
            _deserialize_corporate_action(payload["corporate_action"])
        )

    elif kind is PositionLifecycleTransitionKind.ASSIGNMENT:
        transition = PositionLifecycleTransition.from_assignment(
            symbol=symbol,
            event=_deserialize_position_event(
                payload["position_event"]
            ),
        )

    elif kind is PositionLifecycleTransitionKind.COVERED_POSITION:
        transition = PositionLifecycleTransition.from_covered_position(
            evidence=_deserialize_covered_position(
                payload["covered_position"]
            ),
            occurred_at=occurred_at,
        )

    elif kind is PositionLifecycleTransitionKind.ROLL:
        transition = PositionLifecycleTransition.from_option_roll(
            _deserialize_option_roll(payload["option_roll"])
        )

    elif kind is PositionLifecycleTransitionKind.EXIT:
        transition = PositionLifecycleTransition.from_position_exit(
            _deserialize_position_exit(payload["position_exit"])
        )

    else:
        raise ValueError(
            f"Unsupported lifecycle transition kind: {kind!r}"
        )

    if transition.symbol != symbol:
        raise ValueError(
            "Persisted lifecycle transition symbol does not match "
            "its specialized evidence."
        )

    if transition.occurred_at != occurred_at:
        raise ValueError(
            "Persisted lifecycle transition timestamp does not match "
            "its specialized evidence."
        )

    return transition


def _serialize_corporate_action(
    evidence: CorporateActionEvidence,
) -> dict[str, object]:
    return {
        "symbol": evidence.symbol,
        "effective_date": evidence.effective_date.isoformat(),
        "action_type": evidence.action_type.value,
        "new_units": str(evidence.new_units),
        "old_units": str(evidence.old_units),
        "source": evidence.source,
        "source_reference": evidence.source_reference,
    }


def _deserialize_corporate_action(
    payload: dict[str, object],
) -> CorporateActionEvidence:
    return CorporateActionEvidence(
        symbol=str(payload["symbol"]),
        effective_date=date.fromisoformat(
            str(payload["effective_date"])
        ),
        action_type=CorporateActionType(payload["action_type"]),
        new_units=Decimal(str(payload["new_units"])),
        old_units=Decimal(str(payload["old_units"])),
        source=str(payload["source"]),
        source_reference=str(payload["source_reference"]),
    )


def _serialize_covered_position(
    evidence: CoveredCallPosition,
) -> dict[str, object]:
    return {
        "underlying": evidence.underlying,
        "share_quantity": str(evidence.share_quantity),
        "short_call_quantity": str(evidence.short_call_quantity),
        "required_share_quantity": str(
            evidence.required_share_quantity
        ),
        "covered_call_quantity": str(
            evidence.covered_call_quantity
        ),
        "uncovered_call_quantity": str(
            evidence.uncovered_call_quantity
        ),
        "excess_share_quantity": str(
            evidence.excess_share_quantity
        ),
    }


def _deserialize_covered_position(
    payload: dict[str, object],
) -> CoveredCallPosition:
    return CoveredCallPosition(
        underlying=str(payload["underlying"]),
        share_quantity=Decimal(str(payload["share_quantity"])),
        short_call_quantity=Decimal(
            str(payload["short_call_quantity"])
        ),
        required_share_quantity=Decimal(
            str(payload["required_share_quantity"])
        ),
        covered_call_quantity=Decimal(
            str(payload["covered_call_quantity"])
        ),
        uncovered_call_quantity=Decimal(
            str(payload["uncovered_call_quantity"])
        ),
        excess_share_quantity=Decimal(
            str(payload["excess_share_quantity"])
        ),
    )


def _serialize_position_event(
    event: PositionEvent,
) -> dict[str, object]:
    return {
        "kind": event.kind.value,
        "occurred_at": event.occurred_at.isoformat(),
        "changes": [
            {
                "instrument": _serialize_instrument(
                    change.instrument
                ),
                "quantity": str(change.quantity),
            }
            for change in event.changes
        ],
    }


def _deserialize_position_event(
    payload: dict[str, object],
) -> PositionEvent:
    return PositionEvent(
        kind=PositionEventKind(payload["kind"]),
        changes=tuple(
            PositionChange(
                instrument=_deserialize_instrument(
                    item["instrument"]
                ),
                quantity=Decimal(str(item["quantity"])),
            )
            for item in payload["changes"]
        ),
        occurred_at=datetime.fromisoformat(
            str(payload["occurred_at"])
        ),
    )


def _serialize_option_roll(
    roll: OptionRoll,
) -> dict[str, object]:
    return {
        "underlying": roll.underlying,
        "closed_contract": _serialize_instrument(
            roll.closed_contract
        ),
        "opened_contract": _serialize_instrument(
            roll.opened_contract
        ),
        "quantity": str(roll.quantity),
        "closed_leg": _serialize_leg(roll.closed_leg),
        "opened_leg": _serialize_leg(roll.opened_leg),
    }


def _deserialize_option_roll(
    payload: dict[str, object],
) -> OptionRoll:
    closed_contract = _deserialize_instrument(
        payload["closed_contract"]
    )
    opened_contract = _deserialize_instrument(
        payload["opened_contract"]
    )

    if not isinstance(closed_contract, OptionContract):
        raise ValueError(
            "Persisted roll closed contract must be an option."
        )

    if not isinstance(opened_contract, OptionContract):
        raise ValueError(
            "Persisted roll opened contract must be an option."
        )

    return OptionRoll(
        underlying=str(payload["underlying"]),
        closed_contract=closed_contract,
        opened_contract=opened_contract,
        quantity=Decimal(str(payload["quantity"])),
        closed_leg=_deserialize_leg(payload["closed_leg"]),
        opened_leg=_deserialize_leg(payload["opened_leg"]),
    )


def _serialize_position_exit(
    exit_: PositionExit,
) -> dict[str, object]:
    return {
        "underlying": exit_.underlying,
        "trade": _serialize_trade(exit_.trade),
        "before_positions": _serialize_position_snapshot(
            exit_.before_positions
        ),
        "after_positions": _serialize_position_snapshot(
            exit_.after_positions
        ),
    }


def _deserialize_position_exit(
    payload: dict[str, object],
) -> PositionExit:
    return PositionExit(
        underlying=str(payload["underlying"]),
        trade=_deserialize_trade(payload["trade"]),
        before_positions=_deserialize_position_snapshot(
            payload["before_positions"]
        ),
        after_positions=_deserialize_position_snapshot(
            payload["after_positions"]
        ),
    )


def _serialize_position_snapshot(
    positions,
) -> list[dict[str, object]]:
    return [
        {
            "instrument": _serialize_instrument(instrument),
            "quantity": str(quantity),
        }
        for instrument, quantity in positions
    ]


def _deserialize_position_snapshot(
    payload,
):
    return tuple(
        (
            _deserialize_instrument(item["instrument"]),
            Decimal(str(item["quantity"])),
        )
        for item in payload
    )


def _serialize_trade(
    trade: Trade,
) -> dict[str, object]:
    return {
        "legs": [
            _serialize_leg(leg)
            for leg in trade.legs
        ]
    }


def _deserialize_trade(
    payload: dict[str, object],
) -> Trade:
    return Trade(
        legs=tuple(
            _deserialize_leg(item)
            for item in payload["legs"]
        )
    )


def _serialize_leg(
    leg,
) -> dict[str, object]:
    if isinstance(leg, OptionLeg):
        return {
            "type": "option_leg",
            "instrument": _serialize_instrument(leg.instrument),
            "side": leg.side.value,
            "position_effect": leg.position_effect.value,
            "executions": [
                _serialize_execution(execution)
                for execution in leg.executions
            ],
            "broker_strategy": leg.broker_strategy,
        }

    if isinstance(leg, InstrumentLeg):
        return {
            "type": "instrument_leg",
            "instrument": _serialize_instrument(leg.instrument),
            "side": leg.side.value,
            "position_effect": leg.position_effect.value,
            "executions": [
                _serialize_execution(execution)
                for execution in leg.executions
            ],
        }

    if isinstance(leg, Leg):
        return {
            "type": "leg",
            "instrument": _serialize_instrument(leg.instrument),
            "side": leg.side.value,
            "position_effect": leg.position_effect.value,
            "executions": [
                _serialize_execution(execution)
                for execution in leg.executions
            ],
        }

    raise TypeError(
        "Unsupported lifecycle leg type: "
        f"{type(leg).__name__}"
    )


def _deserialize_leg(
    payload: dict[str, object],
):
    leg_type = payload.get("type", "leg")
    instrument = _deserialize_instrument(
        payload["instrument"]
    )
    side = Side(payload["side"])
    position_effect = PositionEffect(
        payload["position_effect"]
    )
    executions = tuple(
        _deserialize_execution(item)
        for item in payload["executions"]
    )

    if leg_type == "option_leg":
        if not isinstance(instrument, OptionContract):
            raise ValueError(
                "Persisted option leg must contain "
                "an option contract."
            )

        return OptionLeg(
            contract=instrument,
            side=side,
            position_effect=position_effect,
            executions=executions,
            broker_strategy=str(
                payload.get("broker_strategy", "")
            ),
        )

    if leg_type == "instrument_leg":
        return InstrumentLeg(
            instrument=instrument,
            side=side,
            position_effect=position_effect,
            executions=executions,
        )

    if leg_type == "leg":
        return Leg(
            instrument=instrument,
            side=side,
            position_effect=position_effect,
            executions=executions,
        )

    raise ValueError(
        "Unsupported lifecycle leg type: "
        f"{leg_type!r}"
    )


def _serialize_execution(
    execution: Execution,
) -> dict[str, object]:
    return {
        "quantity": str(execution.quantity),
        "execution_price": str(execution.execution_price),
        "executed_at": execution.executed_at.isoformat(),
    }


def _deserialize_execution(
    payload: dict[str, object],
) -> Execution:
    return Execution(
        quantity=Decimal(str(payload["quantity"])),
        execution_price=Decimal(
            str(payload["execution_price"])
        ),
        executed_at=datetime.fromisoformat(
            str(payload["executed_at"])
        ),
    )


def _serialize_instrument(
    instrument: Instrument | OptionContract,
) -> dict[str, object]:
    if isinstance(instrument, OptionContract):
        return {
            "type": "option",
            "underlying": instrument.underlying,
            "expiration": instrument.expiration.isoformat(),
            "strike": str(instrument.strike),
            "option_type": instrument.option_type.value,
        }

    if isinstance(instrument, ForexPair):
        return {
            "type": "forex_pair",
            "symbol": instrument.symbol,
            "base_currency": instrument.base_currency,
            "quote_currency": instrument.quote_currency,
        }

    if isinstance(instrument, Instrument):
        return {
            "type": "instrument",
            "symbol": instrument.symbol,
        }

    raise TypeError(
        "Unsupported lifecycle instrument type: "
        f"{type(instrument).__name__}"
    )


def _deserialize_instrument(
    payload: dict[str, object],
) -> Instrument | OptionContract:
    instrument_type = payload["type"]

    if instrument_type == "instrument":
        return Instrument(symbol=str(payload["symbol"]))

    if instrument_type == "forex_pair":
        return ForexPair(
            base_currency=str(payload["base_currency"]),
            quote_currency=str(payload["quote_currency"]),
        )

    if instrument_type == "option":
        return OptionContract(
            underlying=str(payload["underlying"]),
            expiration=date.fromisoformat(
                str(payload["expiration"])
            ),
            strike=Decimal(str(payload["strike"])),
            option_type=OptionType(payload["option_type"]),
        )

    raise ValueError(
        "Unsupported lifecycle instrument type: "
        f"{instrument_type!r}"
    )
