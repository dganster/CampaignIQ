"""JSON persistence for authoritative CampaignIQ month-end lot state."""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument

_FORMAT = "campaigniq.lot_book"
_VERSION = 1

@dataclass(frozen=True, slots=True)
class PersistedLotBook:
    period_end: date
    lot_book: LotBook

def save_lot_book(path: str | Path, *, period_end: date, lot_book: LotBook) -> None:
    destination = Path(path)
    payload = {
        "format": _FORMAT,
        "version": _VERSION,
        "period_end": period_end.isoformat(),
        "next_id": lot_book._next_id,
        "lots": [
            _serialize_lot(lot)
            for instrument in sorted(lot_book._lots, key=_instrument_sort_key)
            for lot in lot_book._lots[instrument]
        ],
    }
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

def load_lot_book(path: str | Path) -> PersistedLotBook:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("format") != _FORMAT:
        raise ValueError("Unsupported lot book persistence format.")
    if payload.get("version") != _VERSION:
        raise ValueError(
            "Unsupported lot book persistence version: "
            f"{payload.get('version')!r}"
        )
    period_end_raw = payload.get("period_end")
    if not isinstance(period_end_raw, str):
        raise ValueError("Lot book persistence payload must contain a 'period_end' date.")
    next_id = payload.get("next_id")
    if not isinstance(next_id, int) or next_id < 1:
        raise ValueError("Lot book persistence payload must contain a positive 'next_id'.")
    lots_payload = payload.get("lots")
    if not isinstance(lots_payload, list):
        raise ValueError("Lot book persistence payload must contain a 'lots' list.")
    lot_book = LotBook()
    for item in lots_payload:
        if not isinstance(item, dict):
            raise ValueError("Persisted lot entries must be objects.")
        lot_book.seed(_deserialize_lot(item))
    lot_book._next_id = next_id
    return PersistedLotBook(
        period_end=date.fromisoformat(period_end_raw),
        lot_book=lot_book,
    )

def _serialize_lot(lot: Lot) -> dict[str, object]:
    return {
        "lot_id": lot.lot_id,
        "instrument": _serialize_instrument(lot.instrument),
        "quantity": str(lot.quantity),
        "opened_at": lot.opened_at.isoformat(),
        "basis_total": None if lot.basis_total is None else str(lot.basis_total),
        "basis_source": lot.basis_source,
        "campaign_id": lot.campaign_id,
    }

def _deserialize_lot(payload: dict[str, object]) -> Lot:
    basis_total = payload["basis_total"]
    return Lot(
        lot_id=str(payload["lot_id"]),
        instrument=_deserialize_instrument(payload["instrument"]),
        quantity=Decimal(str(payload["quantity"])),
        opened_at=datetime.fromisoformat(str(payload["opened_at"])),
        basis_total=None if basis_total is None else Decimal(str(basis_total)),
        basis_source=None if payload["basis_source"] is None else str(payload["basis_source"]),
        campaign_id=None if payload["campaign_id"] is None else str(payload["campaign_id"]),
    )

def _serialize_instrument(instrument: Instrument | OptionContract) -> dict[str, object]:
    if isinstance(instrument, OptionContract):
        return {
            "type": "option",
            "underlying": instrument.underlying,
            "expiration": instrument.expiration.isoformat(),
            "strike": str(instrument.strike),
            "option_type": instrument.option_type.value,
        }
    if isinstance(instrument, Instrument):
        return {"type": "instrument", "symbol": instrument.symbol}
    raise TypeError(
        "Unsupported lot book instrument type: "
        f"{type(instrument).__name__}"
    )

def _deserialize_instrument(payload: object) -> Instrument | OptionContract:
    if not isinstance(payload, dict):
        raise ValueError("Persisted lot instrument must be an object.")
    instrument_type = payload["type"]
    if instrument_type == "instrument":
        return Instrument(symbol=str(payload["symbol"]))
    if instrument_type == "option":
        return OptionContract(
            underlying=str(payload["underlying"]),
            expiration=date.fromisoformat(str(payload["expiration"])),
            strike=Decimal(str(payload["strike"])),
            option_type=OptionType(str(payload["option_type"])),
        )
    raise ValueError(f"Unsupported lot book instrument type: {instrument_type!r}")

def _instrument_sort_key(instrument: Instrument | OptionContract) -> tuple[str, ...]:
    if isinstance(instrument, OptionContract):
        return (
            "option",
            instrument.underlying,
            instrument.expiration.isoformat(),
            str(instrument.strike),
            instrument.option_type.value,
        )
    return ("instrument", instrument.symbol)
