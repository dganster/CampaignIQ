from datetime import datetime
from decimal import Decimal

import pytest

from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.value_objects.instrument import Instrument


def test_assign_campaign_to_existing_lot() -> None:
    book = LotBook()

    book.seed(
        Lot(
            lot_id="DEC-IBM",
            instrument=Instrument("IBM"),
            quantity=Decimal("100"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("9000"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    book.assign_campaign("DEC-IBM", "CAMP-000001")

    lot = book.lots(Instrument("IBM"))[0]

    assert lot.lot_id == "DEC-IBM"
    assert lot.quantity == Decimal("100")
    assert lot.basis_total == Decimal("9000")
    assert lot.basis_source == "DECEMBER_SNAPSHOT"
    assert lot.campaign_id == "CAMP-000001"


def test_assign_campaign_preserves_existing_campaign() -> None:
    book = LotBook()

    book.seed(
        Lot(
            lot_id="DEC-IBM",
            instrument=Instrument("IBM"),
            quantity=Decimal("100"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("9000"),
            basis_source="DECEMBER_SNAPSHOT",
            campaign_id="CAMP-000001",
        )
    )

    with pytest.raises(ValueError, match="already assigned"):
        book.assign_campaign("DEC-IBM", "CAMP-000002")


def test_assign_campaign_rejects_unknown_lot() -> None:
    book = LotBook()

    with pytest.raises(ValueError, match="not found"):
        book.assign_campaign("DEC-MISSING", "CAMP-000001")


def test_assign_unassigned_lots_splits_partial_lot_proportionally() -> None:
    book = LotBook()
    instrument = Instrument("IBM")

    book.seed(
        Lot(
            lot_id="DEC-IBM",
            instrument=instrument,
            quantity=Decimal("500"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("50000"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    book.assign_unassigned_lots_to_campaign(
        instrument,
        quantity=Decimal("100"),
        campaign_id="CAMP-ASSIGNMENT",
    )

    lots = book.lots(instrument)

    assert len(lots) == 2

    assigned, remainder = lots

    assert assigned.lot_id == "DEC-IBM"
    assert assigned.quantity == Decimal("100")
    assert assigned.basis_total == Decimal("10000")
    assert assigned.basis_source == "DECEMBER_SNAPSHOT"
    assert assigned.campaign_id == "CAMP-ASSIGNMENT"

    assert remainder.quantity == Decimal("400")
    assert remainder.basis_total == Decimal("40000")
    assert remainder.basis_source == "DECEMBER_SNAPSHOT"
    assert remainder.campaign_id is None
