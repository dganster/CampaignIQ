"""Persist an explicit operator decision for a closing-inventory discrepancy.

A reconciliation decision does not make an unreconciled closing inventory
reconciled.  It records a narrowly defined, reviewed boundary exception so
later finalization policy can distinguish a documented exception from an
unexplained mismatch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from campaigniq.closing_inventory_reconciliation import (
    ClosingInventoryMismatch,
    ClosingInventoryReconciliation,
)
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument


_FORMAT = "campaigniq.reconciliation_decision"
_VERSION = 1

BOUNDARY_TIMING_EXCEPTION = "BOUNDARY_TIMING_EXCEPTION"
ACCEPT_TRANSACTION_DERIVED_STATE = "ACCEPT_TRANSACTION_DERIVED_STATE"


@dataclass(frozen=True, slots=True)
class PersistedReconciliationMismatch:
    """One exact closing-inventory mismatch reviewed by the operator."""

    instrument: Instrument | OptionContract
    computed_quantity: Decimal
    snapshot_quantity: Decimal

    @property
    def difference(self) -> Decimal:
        return self.computed_quantity - self.snapshot_quantity


@dataclass(frozen=True, slots=True)
class ReconciliationDecision:
    """Reviewed resolution of an otherwise blocking closing mismatch."""

    period_start: date
    period_end: date
    decision_type: str
    resolution: str
    reason: str
    evidence: tuple[str, ...]
    mismatches: tuple[PersistedReconciliationMismatch, ...]
    approved: bool

    @property
    def permits_exceptional_finalization(self) -> bool:
        """Whether this V1 decision is eligible for later finalization policy."""
        return (
            self.approved
            and self.decision_type == BOUNDARY_TIMING_EXCEPTION
            and self.resolution == ACCEPT_TRANSACTION_DERIVED_STATE
            and bool(self.reason.strip())
            and bool(self.evidence)
            and bool(self.mismatches)
        )


def reconciliation_decision_key(*, period_end: date) -> str:
    """Return the canonical artifact key for one month's decision."""
    return f"{period_end:%Y-%m}-reconciliation-decision.json"


def capture_reconciliation_mismatches(
    mismatches: Iterable[ClosingInventoryMismatch],
) -> tuple[PersistedReconciliationMismatch, ...]:
    """Capture immutable economic mismatch facts from reconciliation."""
    return tuple(
        PersistedReconciliationMismatch(
            instrument=item.instrument,
            computed_quantity=item.computed_quantity,
            snapshot_quantity=item.snapshot_quantity,
        )
        for item in mismatches
    )


def decision_exactly_matches_reconciliation(
    *,
    decision: ReconciliationDecision,
    reconciliation: ClosingInventoryReconciliation,
    period_start: date,
    period_end: date,
) -> bool:
    """Return whether one decision exactly covers the current reconciliation.

    This is intentionally stricter than the decision's own validity.  A
    previously approved decision must not authorize a later run whose period
    or economic mismatch facts differ in any way.

    A reconciled month never needs an exception decision.
    """
    if reconciliation.reconciled:
        return False

    if decision.period_start != period_start:
        return False

    if decision.period_end != period_end:
        return False

    if not decision.permits_exceptional_finalization:
        return False

    current = capture_reconciliation_mismatches(
        reconciliation.mismatches
    )

    return decision.mismatches == current


def _serialize_instrument(instrument: Instrument | OptionContract) -> dict:
    if isinstance(instrument, Instrument):
        return {
            "kind": "EQUITY",
            "symbol": instrument.symbol,
        }

    if isinstance(instrument, OptionContract):
        return {
            "kind": "OPTION",
            "underlying": instrument.underlying,
            "expiration": instrument.expiration.isoformat(),
            "strike": str(instrument.strike),
            "option_type": instrument.option_type.value,
        }

    raise TypeError(
        "Unsupported reconciliation-decision instrument: "
        f"{type(instrument).__name__}"
    )


def _deserialize_instrument(payload: object) -> Instrument | OptionContract:
    if not isinstance(payload, dict):
        raise ValueError("Reconciliation mismatch instrument must be an object.")

    kind = payload.get("kind")

    if kind == "EQUITY":
        symbol = payload.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("Equity reconciliation instrument requires symbol.")
        return Instrument(symbol.strip())

    if kind == "OPTION":
        underlying = payload.get("underlying")
        expiration = payload.get("expiration")
        strike = payload.get("strike")
        option_type = payload.get("option_type")

        if not isinstance(underlying, str) or not underlying.strip():
            raise ValueError("Option reconciliation instrument requires underlying.")
        if not isinstance(expiration, str):
            raise ValueError("Option reconciliation instrument requires expiration.")
        if not isinstance(strike, str):
            raise ValueError("Option reconciliation instrument requires strike.")
        if option_type not in {item.value for item in OptionType}:
            raise ValueError(
                "Option reconciliation instrument has unsupported option_type: "
                f"{option_type!r}"
            )

        return OptionContract(
            underlying=underlying.strip(),
            expiration=date.fromisoformat(expiration),
            strike=Decimal(strike),
            option_type=OptionType(option_type),
        )

    raise ValueError(
        f"Unsupported reconciliation instrument kind: {kind!r}"
    )


def _validate_decision(decision: ReconciliationDecision) -> None:
    if decision.period_end < decision.period_start:
        raise ValueError(
            "Reconciliation decision period_end cannot precede period_start."
        )

    if decision.decision_type != BOUNDARY_TIMING_EXCEPTION:
        raise ValueError(
            "Unsupported reconciliation decision_type: "
            f"{decision.decision_type!r}"
        )

    if decision.resolution != ACCEPT_TRANSACTION_DERIVED_STATE:
        raise ValueError(
            "Unsupported reconciliation resolution: "
            f"{decision.resolution!r}"
        )

    if not isinstance(decision.approved, bool):
        raise ValueError("Reconciliation decision approved must be boolean.")

    if not decision.reason.strip():
        raise ValueError("Reconciliation decision requires a reason.")

    if not decision.evidence:
        raise ValueError(
            "Reconciliation decision requires supporting evidence."
        )

    if any(not item.strip() for item in decision.evidence):
        raise ValueError(
            "Reconciliation decision evidence entries must be non-empty."
        )

    if not decision.mismatches:
        raise ValueError(
            "Reconciliation decision requires at least one mismatch."
        )


def serialize_reconciliation_decision(
    decision: ReconciliationDecision,
) -> str:
    """Serialize a validated reconciliation decision deterministically."""
    _validate_decision(decision)

    payload = {
        "format": _FORMAT,
        "version": _VERSION,
        "period_start": decision.period_start.isoformat(),
        "period_end": decision.period_end.isoformat(),
        "decision_type": decision.decision_type,
        "resolution": decision.resolution,
        "reason": decision.reason,
        "evidence": list(decision.evidence),
        "approved": decision.approved,
        "mismatches": [
            {
                "instrument": _serialize_instrument(item.instrument),
                "computed_quantity": str(item.computed_quantity),
                "snapshot_quantity": str(item.snapshot_quantity),
            }
            for item in decision.mismatches
        ],
    }

    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def deserialize_reconciliation_decision(
    text: str,
) -> ReconciliationDecision:
    """Deserialize and strictly validate persisted decision metadata."""
    payload = json.loads(text)

    if not isinstance(payload, dict):
        raise ValueError("Reconciliation decision payload must be an object.")

    if payload.get("format") != _FORMAT:
        raise ValueError(
            "Unsupported reconciliation decision persistence format."
        )

    if payload.get("version") != _VERSION:
        raise ValueError(
            "Unsupported reconciliation decision persistence version: "
            f"{payload.get('version')!r}"
        )

    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not all(
        isinstance(item, str) for item in evidence
    ):
        raise ValueError(
            "Reconciliation decision payload must contain an evidence list."
        )

    mismatch_payloads = payload.get("mismatches")
    if not isinstance(mismatch_payloads, list):
        raise ValueError(
            "Reconciliation decision payload must contain a mismatches list."
        )

    mismatches: list[PersistedReconciliationMismatch] = []

    for item in mismatch_payloads:
        if not isinstance(item, dict):
            raise ValueError(
                "Reconciliation decision mismatch must be an object."
            )

        try:
            computed = Decimal(item["computed_quantity"])
            snapshot = Decimal(item["snapshot_quantity"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "Reconciliation decision mismatch has invalid quantities."
            ) from exc

        mismatches.append(
            PersistedReconciliationMismatch(
                instrument=_deserialize_instrument(item.get("instrument")),
                computed_quantity=computed,
                snapshot_quantity=snapshot,
            )
        )

    approved = payload.get("approved")
    if not isinstance(approved, bool):
        raise ValueError(
            "Reconciliation decision payload requires boolean approved."
        )

    try:
        period_start = date.fromisoformat(payload["period_start"])
        period_end = date.fromisoformat(payload["period_end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "Reconciliation decision payload has invalid period dates."
        ) from exc

    reason = payload.get("reason")
    if not isinstance(reason, str):
        raise ValueError(
            "Reconciliation decision payload requires a reason."
        )

    decision_type = payload.get("decision_type")
    resolution = payload.get("resolution")

    if not isinstance(decision_type, str):
        raise ValueError(
            "Reconciliation decision payload requires decision_type."
        )
    if not isinstance(resolution, str):
        raise ValueError(
            "Reconciliation decision payload requires resolution."
        )

    decision = ReconciliationDecision(
        period_start=period_start,
        period_end=period_end,
        decision_type=decision_type,
        resolution=resolution,
        reason=reason,
        evidence=tuple(evidence),
        mismatches=tuple(mismatches),
        approved=approved,
    )

    _validate_decision(decision)
    return decision
