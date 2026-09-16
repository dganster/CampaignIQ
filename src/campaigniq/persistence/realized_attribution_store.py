"""JSON persistence for realized campaign attributions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument

_FORMAT = "campaigniq.realized_attributions"
_VERSION = 1


@dataclass(frozen=True, slots=True)
class PersistedRealizedAttributions:
    """One durable period of reconciled realized attributions."""

    period_start: date
    period_end: date
    attributions: tuple[RealizedAttribution, ...]

    def __post_init__(self) -> None:
        if self.period_end < self.period_start:
            raise ValueError(
                "period_end must be on or after period_start"
            )


def save_realized_attributions(
    path: str | Path,
    *,
    period_start: date,
    period_end: date,
    attributions: Iterable[RealizedAttribution],
) -> None:
    """Persist one period of realized attributions."""

    persisted = PersistedRealizedAttributions(
        period_start=period_start,
        period_end=period_end,
        attributions=tuple(attributions),
    )

    destination = Path(path)
    payload = {
        "format": _FORMAT,
        "version": _VERSION,
        "period_start": persisted.period_start.isoformat(),
        "period_end": persisted.period_end.isoformat(),
        "attributions": [
            _serialize_attribution(attribution)
            for attribution in persisted.attributions
        ],
    }

    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_realized_attributions(
    path: str | Path,
) -> PersistedRealizedAttributions:
    """Load one persisted period of realized attributions."""

    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))

    if payload.get("format") != _FORMAT:
        raise ValueError(
            "Unsupported realized attribution persistence format."
        )

    if payload.get("version") != _VERSION:
        raise ValueError(
            "Unsupported realized attribution persistence version: "
            f"{payload.get('version')!r}"
        )

    serialized_attributions = payload.get("attributions")
    if not isinstance(serialized_attributions, list):
        raise ValueError(
            "Realized attribution persistence payload must contain "
            "an 'attributions' list."
        )

    period_start_raw = payload.get("period_start")
    period_end_raw = payload.get("period_end")

    if not isinstance(period_start_raw, str):
        raise ValueError(
            "Realized attribution persistence payload must contain "
            "a 'period_start' date."
        )

    if not isinstance(period_end_raw, str):
        raise ValueError(
            "Realized attribution persistence payload must contain "
            "a 'period_end' date."
        )

    return PersistedRealizedAttributions(
        period_start=date.fromisoformat(period_start_raw),
        period_end=date.fromisoformat(period_end_raw),
        attributions=tuple(
            _deserialize_attribution(item)
            for item in serialized_attributions
        ),
    )


def _serialize_attribution(
    attribution: RealizedAttribution,
) -> dict[str, object]:
    return {
        "record": _serialize_record(attribution.record),
        "allocations": [
            {
                "lot_id": allocation.lot_id,
                "quantity": str(allocation.quantity),
                "broker_basis": (
                    None
                    if allocation.broker_basis is None
                    else str(allocation.broker_basis)
                ),
                "basis_source": allocation.basis_source,
                "campaign_id": allocation.campaign_id,
            }
            for allocation in attribution.allocations
        ],
    }


def _deserialize_attribution(
    payload: dict[str, object],
) -> RealizedAttribution:
    allocations_payload = payload["allocations"]

    if not isinstance(allocations_payload, list):
        raise ValueError(
            "Realized attribution allocations must be a list."
        )

    return RealizedAttribution(
        record=_deserialize_record(payload["record"]),
        allocations=tuple(
            LotAllocation(
                lot_id=item["lot_id"],
                quantity=Decimal(item["quantity"]),
                broker_basis=(
                    None
                    if item["broker_basis"] is None
                    else Decimal(item["broker_basis"])
                ),
                basis_source=item["basis_source"],
                campaign_id=item["campaign_id"],
            )
            for item in allocations_payload
        ),
    )


def _serialize_record(
    record: RealizedGainLossRecord,
) -> dict[str, object]:
    return {
        "closed_date": record.closed_date.isoformat(),
        "instrument": _serialize_instrument(record.instrument),
        "quantity": str(record.quantity),
        "closing_price": str(record.closing_price),
        "proceeds": str(record.proceeds),
        "cost_basis": str(record.cost_basis),
        "gain_loss": str(record.gain_loss),
        "basis_method": record.basis_method,
        "term": record.term,
        "disallowed_loss": str(record.disallowed_loss),
    }


def _deserialize_record(
    payload: dict[str, object],
) -> RealizedGainLossRecord:
    return RealizedGainLossRecord(
        closed_date=date.fromisoformat(payload["closed_date"]),
        instrument=_deserialize_instrument(payload["instrument"]),
        quantity=Decimal(payload["quantity"]),
        closing_price=Decimal(payload["closing_price"]),
        proceeds=Decimal(payload["proceeds"]),
        cost_basis=Decimal(payload["cost_basis"]),
        gain_loss=Decimal(payload["gain_loss"]),
        basis_method=payload["basis_method"],
        term=payload["term"],
        disallowed_loss=Decimal(payload["disallowed_loss"]),
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

    if isinstance(instrument, Instrument):
        return {
            "type": "instrument",
            "symbol": instrument.symbol,
        }

    raise TypeError(
        "Unsupported realized attribution instrument type: "
        f"{type(instrument).__name__}"
    )


def _deserialize_instrument(
    payload: dict[str, object],
) -> Instrument | OptionContract:
    instrument_type = payload["type"]

    if instrument_type == "instrument":
        return Instrument(symbol=payload["symbol"])

    if instrument_type == "option":
        return OptionContract(
            underlying=payload["underlying"],
            expiration=date.fromisoformat(payload["expiration"]),
            strike=Decimal(payload["strike"]),
            option_type=OptionType(payload["option_type"]),
        )

    raise ValueError(
        "Unsupported realized attribution instrument type: "
        f"{instrument_type!r}"
    )
