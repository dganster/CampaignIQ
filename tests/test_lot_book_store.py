from datetime import date, datetime
from decimal import Decimal
import json
import pytest
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.lot_book_store import load_lot_book, save_lot_book

def _book() -> LotBook:
    book = LotBook()
    book.seed(Lot(
        lot_id="LOT-000007",
        instrument=Instrument("IBM"),
        quantity=Decimal("100"),
        opened_at=datetime(2026, 8, 7, 9, 30),
        basis_total=Decimal("22500.50"),
        basis_source="SCHWAB_REALIZED_GAIN_LOSS",
        campaign_id="CAMP-000003",
    ))
    book.seed(Lot(
        lot_id="HIST-LOT-000014",
        instrument=OptionContract(
            underlying="LMT",
            expiration=date(2026, 9, 18),
            strike=Decimal("600"),
            option_type=OptionType.CALL,
        ),
        quantity=Decimal("-1"),
        opened_at=datetime(2026, 8, 21, 10, 15),
        basis_total=None,
        basis_source=None,
        campaign_id="HIST-CAMP-000009",
    ))
    book._next_id = 23
    return book

def test_lot_book_round_trip_preserves_authoritative_state(tmp_path) -> None:
    original = _book()
    path = tmp_path / "2026-08-ending-lot-book.json"
    save_lot_book(path, period_end=date(2026, 8, 31), lot_book=original)
    persisted = load_lot_book(path)
    assert persisted.period_end == date(2026, 8, 31)
    assert persisted.lot_book._lots == original._lots
    assert persisted.lot_book._next_id == original._next_id
    assert persisted.lot_book is not original

def test_loaded_lot_book_is_safe_to_use_as_carried_state(tmp_path) -> None:
    original = _book()
    path = tmp_path / "state.json"
    save_lot_book(path, period_end=date(2026, 8, 31), lot_book=original)
    loaded = load_lot_book(path).lot_book
    loaded.apply_signed_change(
        instrument=Instrument("IBM"),
        quantity=Decimal("-100"),
        occurred_at=datetime(2026, 9, 1),
    )
    assert loaded.lots(Instrument("IBM")) == ()
    assert len(original.lots(Instrument("IBM"))) == 1

def test_persisted_lot_book_has_explicit_format_and_version(tmp_path) -> None:
    path = tmp_path / "state.json"
    save_lot_book(path, period_end=date(2026, 8, 31), lot_book=_book())
    payload = json.loads(path.read_text())
    assert payload["format"] == "campaigniq.lot_book"
    assert payload["version"] == 1
    assert payload["period_end"] == "2026-08-31"
    assert payload["next_id"] == 23

def test_loader_rejects_unknown_version(tmp_path) -> None:
    path = tmp_path / "state.json"
    save_lot_book(path, period_end=date(2026, 8, 31), lot_book=_book())
    payload = json.loads(path.read_text())
    payload["version"] = 999
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Unsupported lot book persistence version"):
        load_lot_book(path)
