from datetime import date, datetime
from decimal import Decimal

from campaigniq.closing_inventory_reconciliation import reconcile_closing_inventory
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.schwab.position_snapshot import SchwabPositionSnapshotRow


SNAPSHOT_AT = datetime(2026, 8, 31, 23, 59, 59)


def _equity_row(symbol: str, quantity: str) -> SchwabPositionSnapshotRow:
    return SchwabPositionSnapshotRow(
        symbol=symbol,
        quantity=Decimal(quantity),
        snapshot_at=SNAPSHOT_AT,
        basis_total=None,
    )


def _option_row(
    symbol: str,
    quantity: str,
    *,
    expiration: date,
    strike: str,
    option_type: OptionType,
) -> SchwabPositionSnapshotRow:
    return SchwabPositionSnapshotRow(
        symbol=symbol,
        quantity=Decimal(quantity),
        snapshot_at=SNAPSHOT_AT,
        basis_total=None,
        expiration=expiration,
        strike=Decimal(strike),
        option_type=option_type,
    )


def _seed(
    book: LotBook,
    *,
    lot_id: str,
    instrument,
    quantity: str,
) -> None:
    book.seed(
        Lot(
            lot_id=lot_id,
            instrument=instrument,
            quantity=Decimal(quantity),
            opened_at=datetime(2026, 8, 1),
            basis_total=None,
        )
    )


def test_exact_equity_and_option_inventory_reconciles() -> None:
    book = LotBook()
    _seed(book, lot_id="IBM-1", instrument=Instrument("IBM"), quantity="100")
    call = OptionContract(
        underlying="IBM",
        expiration=date(2026, 9, 18),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )
    _seed(book, lot_id="IBM-CALL-1", instrument=call, quantity="-1")

    result = reconcile_closing_inventory(
        ending_lot_book=book,
        snapshot_rows=[
            _equity_row("IBM", "100"),
            _option_row(
                "IBM",
                "-1",
                expiration=date(2026, 9, 18),
                strike="250",
                option_type=OptionType.CALL,
            ),
        ],
    )

    assert result.reconciled is True
    assert result.mismatches == ()


def test_multiple_lots_are_aggregated_before_comparison() -> None:
    book = LotBook()
    _seed(book, lot_id="IBM-1", instrument=Instrument("IBM"), quantity="40")
    _seed(book, lot_id="IBM-2", instrument=Instrument("IBM"), quantity="60")

    result = reconcile_closing_inventory(
        ending_lot_book=book,
        snapshot_rows=[_equity_row("IBM", "100")],
    )

    assert result.reconciled is True


def test_quantity_difference_is_reported() -> None:
    book = LotBook()
    _seed(book, lot_id="IBM-1", instrument=Instrument("IBM"), quantity="100")

    result = reconcile_closing_inventory(
        ending_lot_book=book,
        snapshot_rows=[_equity_row("IBM", "200")],
    )

    assert result.reconciled is False
    assert len(result.mismatches) == 1
    mismatch = result.mismatches[0]
    assert mismatch.instrument == Instrument("IBM")
    assert mismatch.computed_quantity == Decimal("100")
    assert mismatch.snapshot_quantity == Decimal("200")
    assert mismatch.difference == Decimal("-100")


def test_snapshot_only_position_is_reported_as_missing_computed_inventory() -> None:
    result = reconcile_closing_inventory(
        ending_lot_book=LotBook(),
        snapshot_rows=[_equity_row("IBM", "100")],
    )

    mismatch = result.mismatches[0]
    assert mismatch.instrument == Instrument("IBM")
    assert mismatch.computed_quantity == Decimal("0")
    assert mismatch.snapshot_quantity == Decimal("100")


def test_computed_only_position_is_reported_as_absent_from_snapshot() -> None:
    book = LotBook()
    _seed(book, lot_id="IBM-1", instrument=Instrument("IBM"), quantity="100")

    result = reconcile_closing_inventory(
        ending_lot_book=book,
        snapshot_rows=[],
    )

    mismatch = result.mismatches[0]
    assert mismatch.instrument == Instrument("IBM")
    assert mismatch.computed_quantity == Decimal("100")
    assert mismatch.snapshot_quantity == Decimal("0")


def test_full_option_identity_is_used() -> None:
    book = LotBook()
    september_call = OptionContract(
        underlying="IBM",
        expiration=date(2026, 9, 18),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )
    _seed(book, lot_id="IBM-CALL-1", instrument=september_call, quantity="-1")

    result = reconcile_closing_inventory(
        ending_lot_book=book,
        snapshot_rows=[
            _option_row(
                "IBM",
                "-1",
                expiration=date(2026, 10, 16),
                strike="250",
                option_type=OptionType.CALL,
            )
        ],
    )

    assert result.reconciled is False
    assert len(result.mismatches) == 2
    assert {mismatch.computed_quantity for mismatch in result.mismatches} == {
        Decimal("-1"),
        Decimal("0"),
    }
    assert {mismatch.snapshot_quantity for mismatch in result.mismatches} == {
        Decimal("-1"),
        Decimal("0"),
    }
