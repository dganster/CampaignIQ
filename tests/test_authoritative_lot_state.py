import json
from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.authoritative_lot_state import (
    load_preceding_authoritative_state,
    lot_state_path,
    save_authoritative_lot_state,
)


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


def test_authoritative_lot_state_uses_deterministic_month_path(tmp_path) -> None:
    path = lot_state_path(tmp_path, period_end=date(2026, 8, 31))
    assert path == tmp_path / "2026-08-lot-book.json"


def test_september_discovers_august_31_authoritative_state(tmp_path) -> None:
    original = _book()
    saved_path = save_authoritative_lot_state(
        tmp_path,
        period_end=date(2026, 8, 31),
        lot_book=original,
    )

    opening = load_preceding_authoritative_state(
        tmp_path,
        period_start=date(2026, 9, 1),
    )

    assert opening is not None
    assert opening.period_end == date(2026, 8, 31)
    assert opening.path == saved_path
    assert opening.lot_book._lots == original._lots
    assert opening.lot_book._next_id == original._next_id
    assert opening.lot_book is not original


def test_discovery_does_not_fall_back_to_older_month(tmp_path) -> None:
    save_authoritative_lot_state(
        tmp_path,
        period_end=date(2026, 7, 31),
        lot_book=_book(),
    )

    opening = load_preceding_authoritative_state(
        tmp_path,
        period_start=date(2026, 9, 1),
    )

    assert opening is None


def test_discovery_rejects_artifact_with_wrong_embedded_period(tmp_path) -> None:
    canonical = lot_state_path(tmp_path, period_end=date(2026, 8, 31))
    save_authoritative_lot_state(
        tmp_path,
        period_end=date(2026, 7, 31),
        lot_book=_book(),
    )
    july = lot_state_path(tmp_path, period_end=date(2026, 7, 31))
    canonical.write_text(july.read_text())

    try:
        load_preceding_authoritative_state(
            tmp_path,
            period_start=date(2026, 9, 1),
        )
    except ValueError as exc:
        assert "period mismatch" in str(exc)
    else:
        raise AssertionError("Expected mismatched predecessor artifact to fail.")


def test_saved_artifact_uses_existing_lot_book_format(tmp_path) -> None:
    path = save_authoritative_lot_state(
        tmp_path,
        period_end=date(2026, 8, 31),
        lot_book=_book(),
    )

    payload = json.loads(path.read_text())
    assert payload["format"] == "campaigniq.lot_book"
    assert payload["version"] == 1
    assert payload["period_end"] == "2026-08-31"
