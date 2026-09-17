from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.authoritative_lot_state import (
    load_preceding_authoritative_state_from_storage,
    lot_state_key,
    save_authoritative_lot_state_to_storage,
)
from campaigniq.persistence.lot_book_store import (
    deserialize_lot_book,
    load_lot_book_from_storage,
    save_lot_book_to_storage,
    serialize_lot_book,
)


class MemoryStorage:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def exists(self, key: str) -> bool:
        return key in self.values

    def read_text(self, key: str) -> str:
        return self.values[key]

    def write_text(self, key: str, content: str) -> None:
        self.values[key] = content


def _book() -> LotBook:
    book = LotBook()
    book.seed(
        Lot(
            lot_id="LOT-IBM-001",
            instrument=Instrument("IBM"),
            quantity=Decimal("100"),
            opened_at=datetime(2026, 8, 12, 10, 30),
            basis_total=Decimal("25000.00"),
            basis_source="SCHWAB_REALIZED_GAIN_LOSS",
            campaign_id="CAMP-000001",
        )
    )
    book._next_id = 17
    return book


def test_lot_book_serializer_round_trips_without_filesystem_io() -> None:
    original = _book()
    text = serialize_lot_book(period_end=date(2026, 8, 31), lot_book=original)
    persisted = deserialize_lot_book(text)
    assert persisted.period_end == date(2026, 8, 31)
    assert persisted.lot_book._lots == original._lots
    assert persisted.lot_book._next_id == original._next_id
    assert persisted.lot_book is not original


def test_lot_book_storage_round_trip_uses_logical_key() -> None:
    storage = MemoryStorage()
    original = _book()
    save_lot_book_to_storage(
        storage,
        "state/2026-08-lot-book.json",
        period_end=date(2026, 8, 31),
        lot_book=original,
    )
    persisted = load_lot_book_from_storage(storage, "state/2026-08-lot-book.json")
    assert persisted.lot_book._lots == original._lots
    assert tuple(storage.values) == ("state/2026-08-lot-book.json",)


def test_authoritative_storage_uses_deterministic_logical_key() -> None:
    storage = MemoryStorage()
    key = save_authoritative_lot_state_to_storage(
        storage,
        period_end=date(2026, 8, 31),
        lot_book=_book(),
    )
    assert key == "2026-08-lot-book.json"
    assert key == lot_state_key(period_end=date(2026, 8, 31))


def test_storage_predecessor_discovery_is_exact() -> None:
    storage = MemoryStorage()
    save_authoritative_lot_state_to_storage(
        storage,
        period_end=date(2026, 7, 31),
        lot_book=_book(),
    )
    assert load_preceding_authoritative_state_from_storage(
        storage,
        period_start=date(2026, 9, 1),
    ) is None

    save_authoritative_lot_state_to_storage(
        storage,
        period_end=date(2026, 8, 31),
        lot_book=_book(),
    )
    loaded = load_preceding_authoritative_state_from_storage(
        storage,
        period_start=date(2026, 9, 1),
    )
    assert loaded is not None
    period_end, key, book = loaded
    assert period_end == date(2026, 8, 31)
    assert key == "2026-08-lot-book.json"
    assert book._next_id == 17


def test_storage_predecessor_rejects_embedded_period_mismatch() -> None:
    storage = MemoryStorage()
    storage.write_text(
        "2026-08-lot-book.json",
        serialize_lot_book(period_end=date(2026, 7, 31), lot_book=_book()),
    )
    with pytest.raises(ValueError, match="period mismatch"):
        load_preceding_authoritative_state_from_storage(
            storage,
            period_start=date(2026, 9, 1),
        )
