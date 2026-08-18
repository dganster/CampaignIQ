from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.schwab.position_snapshot import (
    SchwabPositionSnapshotRow,
    to_lot,
    to_lots,
)


def test_equity_snapshot_becomes_preperiod_lot() -> None:
    row = SchwabPositionSnapshotRow(
        symbol="DXCM",
        quantity=Decimal("500"),
        snapshot_at=datetime(2025, 12, 31, 16),
        basis_total=None,
    )

    lot = to_lot(row)

    assert lot.instrument == Instrument("DXCM")
    assert lot.quantity == Decimal("500")
    assert lot.opened_at == datetime(2025, 12, 31, 16)
    assert lot.basis_total is None
    assert lot.basis_source is None


def test_option_snapshot_preserves_contract_identity() -> None:
    row = SchwabPositionSnapshotRow(
        symbol="DXCM",
        quantity=Decimal("5"),
        snapshot_at=datetime(2025, 12, 31, 16),
        expiration=date(2026, 1, 16),
        strike=Decimal("70"),
        option_type=OptionType.CALL,
    )

    lot = to_lot(row)

    assert lot.instrument == OptionContract(
        underlying="DXCM",
        expiration=date(2026, 1, 16),
        strike=Decimal("70"),
        option_type=OptionType.CALL,
    )
    assert lot.quantity == Decimal("5")


def test_snapshot_basis_is_preserved_only_when_supplied() -> None:
    row = SchwabPositionSnapshotRow(
        symbol="EL",
        quantity=Decimal("500"),
        snapshot_at=datetime(2025, 12, 31, 16),
        basis_total=Decimal("82500"),
    )

    lot = to_lot(row)

    assert lot.basis_total == Decimal("82500")
    assert lot.basis_source == "SCHWAB_POSITION_SNAPSHOT"


def test_partial_option_identity_is_rejected() -> None:
    row = SchwabPositionSnapshotRow(
        symbol="DXCM",
        quantity=Decimal("5"),
        snapshot_at=datetime(2025, 12, 31, 16),
        strike=Decimal("70"),
    )

    with pytest.raises(ValueError, match="require expiration, strike"):
        to_lot(row)


def test_snapshot_rows_preserve_order() -> None:
    rows = [
        SchwabPositionSnapshotRow(
            symbol="DXCM",
            quantity=Decimal("500"),
            snapshot_at=datetime(2025, 12, 31, 16),
        ),
        SchwabPositionSnapshotRow(
            symbol="EL",
            quantity=Decimal("500"),
            snapshot_at=datetime(2025, 12, 31, 16),
        ),
    ]

    lots = to_lots(rows)

    assert [lot.instrument for lot in lots] == [Instrument("DXCM"), Instrument("EL")]


def test_snapshot_lots_can_seed_assignment_closures() -> None:
    from campaigniq.domain.lot_book import LotBook
    from campaigniq.importers.schwab.option_assignment import SchwabOptionAssignment
    from campaigniq.importers.schwab.translator import to_position_event

    snapshot_at = datetime(2025, 12, 31, 16)
    option_row = SchwabPositionSnapshotRow(
        symbol="DXCM",
        quantity=Decimal("-5"),
        snapshot_at=snapshot_at,
        expiration=date(2026, 1, 16),
        strike=Decimal("70"),
        option_type=OptionType.CALL,
    )
    stock_row = SchwabPositionSnapshotRow(
        symbol="DXCM",
        quantity=Decimal("500"),
        snapshot_at=snapshot_at,
    )

    book = LotBook()
    book.seed(to_lot(option_row))
    book.seed(to_lot(stock_row))

    event = to_position_event(
        SchwabOptionAssignment(
            occurred_at=datetime(2026, 1, 9, 0, 0),
            symbol="DXCM",
            expiration=date(2026, 1, 16),
            strike=Decimal("70"),
            option_type="CALL",
            quantity=Decimal("5"),
        )
    )

    allocations = book.apply_event(event)

    assert {allocation.quantity for allocation in allocations} == {
        Decimal("5"),
        Decimal("500"),
    }
    assert book.lots(Instrument("DXCM")) == ()
    assert book.lots(
        OptionContract(
            underlying="DXCM",
            expiration=date(2026, 1, 16),
            strike=Decimal("70"),
            option_type=OptionType.CALL,
        )
    ) == ()
