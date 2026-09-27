from datetime import date
from decimal import Decimal
import json

import pytest

from campaigniq.closing_inventory_reconciliation import (
    ClosingInventoryMismatch,
)
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.reconciliation_decision import (
    ACCEPT_TRANSACTION_DERIVED_STATE,
    BOUNDARY_TIMING_EXCEPTION,
    ReconciliationDecision,
    capture_reconciliation_mismatches,
    decision_exactly_matches_reconciliation,
    deserialize_reconciliation_decision,
    reconciliation_decision_key,
    serialize_reconciliation_decision,
)


def _lin_decision(*, approved: bool = True) -> ReconciliationDecision:
    mismatches = capture_reconciliation_mismatches(
        (
            ClosingInventoryMismatch(
                instrument=Instrument("LIN"),
                computed_quantity=Decimal("0"),
                snapshot_quantity=Decimal("500"),
            ),
            ClosingInventoryMismatch(
                instrument=OptionContract(
                    underlying="LIN",
                    expiration=date(2026, 2, 20),
                    strike=Decimal("365"),
                    option_type=OptionType.CALL,
                ),
                computed_quantity=Decimal("0"),
                snapshot_quantity=Decimal("-5"),
            ),
        )
    )

    return ReconciliationDecision(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        decision_type=BOUNDARY_TIMING_EXCEPTION,
        resolution=ACCEPT_TRANSACTION_DERIVED_STATE,
        reason=(
            "Broker transaction evidence records the LIN covered position "
            "closed before month-end while the supplied closing snapshot "
            "still reports the position."
        ),
        evidence=(
            "Schwab LIN transaction history: 2026-01-30 sell 500 LIN",
            "Schwab LIN transaction history: 2026-01-30 buy to close 5 "
            "LIN 2026-02-20 365 CALL",
        ),
        mismatches=mismatches,
        approved=approved,
    )


def test_reconciliation_decision_key_is_month_scoped() -> None:
    assert (
        reconciliation_decision_key(period_end=date(2026, 1, 31))
        == "2026-01-reconciliation-decision.json"
    )


def test_lin_decision_round_trips_deterministically() -> None:
    decision = _lin_decision()

    serialized = serialize_reconciliation_decision(decision)
    restored = deserialize_reconciliation_decision(serialized)

    assert restored == decision
    assert serialize_reconciliation_decision(restored) == serialized
    assert restored.permits_exceptional_finalization


def test_serialized_lin_decision_preserves_original_mismatch_facts() -> None:
    payload = json.loads(
        serialize_reconciliation_decision(_lin_decision())
    )

    assert payload["format"] == "campaigniq.reconciliation_decision"
    assert payload["version"] == 1
    assert payload["decision_type"] == "BOUNDARY_TIMING_EXCEPTION"
    assert payload["resolution"] == "ACCEPT_TRANSACTION_DERIVED_STATE"
    assert payload["approved"] is True

    equity, option = payload["mismatches"]

    assert equity == {
        "instrument": {
            "kind": "EQUITY",
            "symbol": "LIN",
        },
        "computed_quantity": "0",
        "snapshot_quantity": "500",
    }

    assert option == {
        "instrument": {
            "kind": "OPTION",
            "underlying": "LIN",
            "expiration": "2026-02-20",
            "strike": "365",
            "option_type": "CALL",
        },
        "computed_quantity": "0",
        "snapshot_quantity": "-5",
    }


def test_unapproved_decision_does_not_permit_exceptional_finalization() -> None:
    decision = _lin_decision(approved=False)

    assert not decision.permits_exceptional_finalization

    restored = deserialize_reconciliation_decision(
        serialize_reconciliation_decision(decision)
    )
    assert not restored.permits_exceptional_finalization


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("decision_type", "IGNORE_MISMATCH"),
        ("resolution", "ACCEPT_SNAPSHOT_ANYWAY"),
        ("reason", ""),
        ("evidence", []),
        ("mismatches", []),
    ),
)
def test_deserializer_rejects_invalid_or_overbroad_decisions(
    field: str,
    value,
) -> None:
    payload = json.loads(
        serialize_reconciliation_decision(_lin_decision())
    )
    payload[field] = value

    with pytest.raises(ValueError):
        deserialize_reconciliation_decision(json.dumps(payload))


def test_deserializer_rejects_unsupported_version() -> None:
    payload = json.loads(
        serialize_reconciliation_decision(_lin_decision())
    )
    payload["version"] = 2

    with pytest.raises(
        ValueError,
        match="Unsupported reconciliation decision persistence version",
    ):
        deserialize_reconciliation_decision(json.dumps(payload))


def test_decision_is_metadata_only_and_does_not_redefine_reconciliation() -> None:
    original = (
        ClosingInventoryMismatch(
            instrument=Instrument("LIN"),
            computed_quantity=Decimal("0"),
            snapshot_quantity=Decimal("500"),
        ),
    )

    captured = capture_reconciliation_mismatches(original)

    assert captured[0].computed_quantity == Decimal("0")
    assert captured[0].snapshot_quantity == Decimal("500")
    assert captured[0].difference == Decimal("-500")



def _lin_reconciliation():
    from campaigniq.closing_inventory_reconciliation import (
        ClosingInventoryReconciliation,
    )

    return ClosingInventoryReconciliation(
        mismatches=(
            ClosingInventoryMismatch(
                instrument=Instrument("LIN"),
                computed_quantity=Decimal("0"),
                snapshot_quantity=Decimal("500"),
            ),
            ClosingInventoryMismatch(
                instrument=OptionContract(
                    underlying="LIN",
                    expiration=date(2026, 2, 20),
                    strike=Decimal("365"),
                    option_type=OptionType.CALL,
                ),
                computed_quantity=Decimal("0"),
                snapshot_quantity=Decimal("-5"),
            ),
        )
    )


def test_exact_decision_matches_current_reconciliation() -> None:
    assert decision_exactly_matches_reconciliation(
        decision=_lin_decision(),
        reconciliation=_lin_reconciliation(),
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )


def test_decision_does_not_match_when_current_reconciliation_has_extra_mismatch() -> None:
    from campaigniq.closing_inventory_reconciliation import (
        ClosingInventoryReconciliation,
    )

    lin = _lin_reconciliation()

    reconciliation = ClosingInventoryReconciliation(
        mismatches=lin.mismatches
        + (
            ClosingInventoryMismatch(
                instrument=Instrument("SPCX"),
                computed_quantity=Decimal("63"),
                snapshot_quantity=Decimal("100"),
            ),
        )
    )

    assert not decision_exactly_matches_reconciliation(
        decision=_lin_decision(),
        reconciliation=reconciliation,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )


def test_decision_does_not_match_when_current_quantity_changes() -> None:
    from campaigniq.closing_inventory_reconciliation import (
        ClosingInventoryReconciliation,
    )

    original = _lin_reconciliation()

    reconciliation = ClosingInventoryReconciliation(
        mismatches=(
            ClosingInventoryMismatch(
                instrument=original.mismatches[0].instrument,
                computed_quantity=Decimal("0"),
                snapshot_quantity=Decimal("400"),
            ),
            original.mismatches[1],
        )
    )

    assert not decision_exactly_matches_reconciliation(
        decision=_lin_decision(),
        reconciliation=reconciliation,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )


def test_decision_does_not_match_different_period() -> None:
    assert not decision_exactly_matches_reconciliation(
        decision=_lin_decision(),
        reconciliation=_lin_reconciliation(),
        period_start=date(2026, 2, 1),
        period_end=date(2026, 2, 28),
    )


def test_unapproved_decision_does_not_match_reconciliation() -> None:
    assert not decision_exactly_matches_reconciliation(
        decision=_lin_decision(approved=False),
        reconciliation=_lin_reconciliation(),
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )


def test_reconciled_month_never_uses_exception_decision() -> None:
    from campaigniq.closing_inventory_reconciliation import (
        ClosingInventoryReconciliation,
    )

    reconciliation = ClosingInventoryReconciliation(mismatches=())

    assert reconciliation.reconciled

    assert not decision_exactly_matches_reconciliation(
        decision=_lin_decision(),
        reconciliation=reconciliation,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )


def test_mismatch_order_is_part_of_exact_persisted_decision() -> None:
    """Persisted decisions must match deterministic reconciliation output."""
    from campaigniq.closing_inventory_reconciliation import (
        ClosingInventoryReconciliation,
    )

    original = _lin_reconciliation()

    reconciliation = ClosingInventoryReconciliation(
        mismatches=tuple(reversed(original.mismatches))
    )

    assert not decision_exactly_matches_reconciliation(
        decision=_lin_decision(),
        reconciliation=reconciliation,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )
